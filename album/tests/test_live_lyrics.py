"""The live boundary tests: five real providers' lookups, shape only.

LRCLIB, NetEase, Bandcamp (through MusicBrainz), YouTube Music and Tekstowo, each by its
own test.

Skipped when LYRICSMITH_OFFLINE=1, as every live test in this project is. Each still
derives from the isolated base, so HOME, the config directory and the state directory
stay redirected into the test's temporary folder and no Genius key is set; only for the
one lookup does it put back the real socket functions, captured when this module was
imported, before any isolation had replaced them.

The lyrics are third-party text: no test stores, prints or quotes them, and no
assertion message can carry them. Each asserts only that text, or timed lines, came
back. Genius has no live test, since it would need a key.

Each asks the adapter the real table holds, through the deadline watchdog, so the
request shape, the reply parsing and the matcher all meet the live endpoint. The
LRCLIB query carries no album or length, so LRCLIB answers through its search and the
matcher; the exact lookup is covered by the faked replies in `test_lyrics_lrclib`.

A provider rate-limits a burst of lookups, which the adapter raises. Each test retries
such a refusal on a fixed escalating schedule and skips once it runs out: a rate limit
says nothing about the adapter. LRCLIB and NetEase refuse with HTTP 429. The Bandcamp
step, which reaches MusicBrainz and then Bandcamp, retries every failure on the way
instead: a timeout, any HTTP status, a refused or reset connection, each also as its
client's own retries wrap it; once the schedule runs out it skips, naming the error's
class and status. A page that arrives without the text asked for, and every other
error, still fail, NetEase refusing a request in an OK body included; the offline
`BandcampRetryTest` holds the Bandcamp test to that split.

YouTube Music is never retried: a refusal stops it, so that test skips with the stop's
detail. The cool-down the stop writes is the test's temporary one, never the real
state directory's. Tekstowo is never retried either, for the same reason: it asks only for
Queen's "Bohemian Rhapsody", asserts only that a non-empty text came back, and prints,
quotes and keeps none of it.
"""

from __future__ import annotations

import os
import socket
import sys
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_bandcamp as lbc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tekstowo as ltk  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_music as lym  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_provider as lyp  # noqa: E402 - import follows the sys.path bootstrap above

OFFLINE = os.environ.get("LYRICSMITH_OFFLINE") == "1"
#: The socket functions as they were before any test isolated them.
_REAL_SOCKET = {
    "connect": socket.socket.connect,
    "connect_ex": socket.socket.connect_ex,
}
_REAL_GETADDRINFO = socket.getaddrinfo
#: A widely known track LRCLIB, NetEase and YouTube Music hold synced lyrics for,
#: and the one Tekstowo is asked for: the research probe's song.
_WELL_KNOWN = lm.TrackQuery("Queen", "Bohemian Rhapsody")
#: A track of the album the 2026-10-21 research probe found on Bandcamp with lyrics.
_ON_BANDCAMP = lm.TrackQuery(
    "American Football", "Never Meant", album="American Football"
)
#: The status an LRC provider rate-limits with.
_RATE_LIMITED = 429
#: The waits before each retry of a lookup, escalating; then it skips.
_RETRY_WAITS_S: tuple[float, ...] = (2.0, 8.0, 20.0)


def _real_network(stack: ExitStack) -> None:
    """Put back, until *stack* closes, the socket functions the isolation replaced."""
    for name, real in _REAL_SOCKET.items():
        stack.enter_context(mock.patch.object(socket.socket, name, real))
    stack.enter_context(mock.patch.object(socket, "getaddrinfo", _REAL_GETADDRINFO))


def _refused_status(error: lp.ProviderError) -> int | None:
    """The HTTP status *error* refused with, a retried one's last included, or None."""
    if isinstance(error, lp.RetriedError) and isinstance(error.last, lp.ProviderError):
        return _refused_status(error.last)
    return error.status if isinstance(error, lp.HttpStatusError) else None


def _network_failure(error: Exception) -> str | None:
    """*error*'s class, and its status for an HTTP one, when it failed on the way.

    A timeout, an HTTP status, or a connection error (`requests`' errors are each an
    `OSError`), a retried one's last failure included. None for anything else, such
    as a reply the reader could not read, which is the reader's failure to report.
    """
    if isinstance(error, lp.RetriedError):
        last = _network_failure(error.last)
        return None if last is None else f"{last} after {error.retries} retries"
    if isinstance(error, lp.HttpStatusError):
        return f"{type(error).__name__} (HTTP {error.status})"
    if isinstance(error, (lp.ProviderTimeout, OSError)):
        return type(error).__name__
    return None


def _ask(
    provider: lm.Provider, kind: lm.LyricsKind, query: lm.TrackQuery
) -> str | None:
    """*provider*'s text of *kind* for *query*, asked as the chain asks it."""
    if isinstance(provider, lf.AnswersEveryKind):
        return provider.answer_all_kinds(query).text(kind)
    return provider(query)


class _LiveLookup(fixtures.IsolatedCase):
    """One provider's real step answers a known track; rate limits retried, then skipped."""

    #: The provider, the step's kind, the track asked and the call's bound.
    source: lm.Source
    kind = lm.LyricsKind.SYNCED
    query = _WELL_KNOWN
    deadline_s = lf.PROVIDER_DEADLINE_S
    #: The statuses read as a rate limit rather than a failure.
    rate_limits: frozenset[int] = frozenset({_RATE_LIMITED})

    def settings(self) -> ltable.ProviderSettings:
        """The table's settings: the default table holds the LRC steps."""
        return ltable.ProviderSettings()

    def assert_timed_lines(self) -> None:
        """The lookup found text, and the text has a timed line."""
        text = self._lookup_past_rate_limits()
        self.assertTrue(text is not None, f"{self.source} found nothing")
        parsed = lm.parse_lrc(text or "")
        self.assertTrue(isinstance(parsed, lm.SyncedLyrics), "no timed line came back")

    def retry_reason(self, error: Exception) -> str | None:
        """Why *error* is worth a retry, for the skip's reason; None to raise it now."""
        status = _refused_status(error) if isinstance(error, lp.ProviderError) else None
        return f"rate-limited (HTTP {status})" if status in self.rate_limits else None

    def _lookup_past_rate_limits(self) -> str | None:
        """The lookup's answer, retrying what `retry_reason` names; then a skip."""
        for scheduled in (*_RETRY_WAITS_S, None):
            try:
                return self._lookup()
            except (lp.ProviderError, lp.ProviderTimeout, OSError) as error:
                reason = self.retry_reason(error)
                if reason is None:
                    raise
                if scheduled is None:
                    self.skipTest(f"{self.source} {reason}")
                time.sleep(scheduled)
        raise AssertionError("the retry loop ends by returning or skipping")

    def _lookup(self) -> str | None:
        """One lookup of the class's track by its step, on the real socket layer."""
        with ExitStack() as stack:
            _real_network(stack)
            table = ltable.build_provider_table(None, settings=self.settings())
            provider = table[lm.ProviderKey(self.source, self.kind)]
            kind, query = self.kind, self.query
            return lf.run_with_deadline(
                lambda: _ask(provider, kind, query), self.deadline_s
            )


@unittest.skipIf(OFFLINE, "LYRICSMITH_OFFLINE=1 - live LRCLIB lookup skipped")
class LiveLrclibTest(_LiveLookup):
    """The real LRCLIB adapter answers a well-known track with timed lines."""

    source = lm.Source.LRCLIB

    def test_a_well_known_track_has_timed_lines(self) -> None:
        self.assert_timed_lines()


@unittest.skipIf(OFFLINE, "LYRICSMITH_OFFLINE=1 - live NetEase lookup skipped")
class LiveNeteaseTest(_LiveLookup):
    """The real NetEase adapter answers a well-known track with timed lines."""

    source = lm.Source.NETEASE

    def test_a_well_known_track_has_timed_lines(self) -> None:
        self.assert_timed_lines()


@unittest.skipIf(OFFLINE, "LYRICSMITH_OFFLINE=1 - live Bandcamp lookup skipped")
class LiveBandcampTest(_LiveLookup):
    """The real Bandcamp step finds the album through MusicBrainz and answers its track.

    The track is "Never Meant" from the album "American Football", the research probe's
    album. Only a non-empty text is asserted; nothing of it is printed or kept.
    """

    source = lm.Source.BANDCAMP
    kind = lm.LyricsKind.UNSYNCED
    query = _ON_BANDCAMP
    deadline_s = lbc.BANDCAMP_STEP_DEADLINE_S

    def settings(self) -> ltable.ProviderSettings:
        """The table with its Bandcamp step on; its MusicBrainz User-Agent names no contact."""
        return ltable.ProviderSettings(bandcamp=True)

    def retry_reason(self, error: Exception) -> str | None:
        """Every failure on the way to the page: two sites behind one step fail often."""
        failure = _network_failure(error)
        return None if failure is None else f"unreachable: {failure}"

    def test_the_album_page_answers_its_track(self) -> None:
        text = self._lookup_past_rate_limits()
        self.assertTrue(isinstance(text, str), "Bandcamp found nothing")
        self.assertTrue(bool((text or "").strip()), "Bandcamp's text was blank")


@unittest.skipIf(OFFLINE, "LYRICSMITH_OFFLINE=1 - live YouTube Music lookup skipped")
class LiveYouTubeMusicTest(_LiveLookup):
    """The real YouTube Music step answers a well-known track with timed lines.

    A refusal stops the step for the run and skips the test with the stop's detail,
    which says YouTube's state, not the client's. The cool-down the stop writes goes to
    this test's temporary folder.
    """

    source = lm.Source.YOUTUBE_MUSIC
    deadline_s = lyp.YOUTUBE_MUSIC_STEP_DEADLINE_S

    def setUp(self) -> None:
        """Name the cool-down file inside the test's temporary folder."""
        super().setUp()
        self.state_path = self.tmp / "state" / "youtube_music.json"

    def settings(self) -> ltable.ProviderSettings:
        """The table with its YouTube Music step on, its cool-down the temporary one."""
        return ltable.ProviderSettings(youtube_music=lyc.CoolingSetup(self.state_path))

    def test_a_well_known_track_has_timed_lines(self) -> None:
        try:
            self.assert_timed_lines()
        except lym.YouTubeMusicStopped as stopped:
            self.skipTest(f"YouTube Music refused: {stopped.detail}")


@unittest.skipIf(OFFLINE, "LYRICSMITH_OFFLINE=1 - live Tekstowo lookup skipped")
class LiveTekstowoTest(_LiveLookup):
    """The real Tekstowo step answers a well-known track with untimed text.

    A refusal or a challenge stops the step for the run and skips the test with the
    stop's detail; it is never retried. Only a non-empty text is asserted; nothing of it
    is printed or kept. The cool-down the stop writes goes to this test's temporary folder.
    """

    source = lm.Source.TEKSTOWO
    kind = lm.LyricsKind.UNSYNCED
    deadline_s = ltk.TEKSTOWO_STEP_DEADLINE_S

    def setUp(self) -> None:
        """Name the cool-down file inside the test's temporary folder."""
        super().setUp()
        self.state_path = self.tmp / "state" / "tekstowo.json"

    def settings(self) -> ltable.ProviderSettings:
        """The table with its Tekstowo step on, its cool-down the temporary one."""
        return ltable.ProviderSettings(tekstowo=lyc.CoolingSetup(self.state_path))

    def test_a_well_known_track_has_text(self) -> None:
        try:
            text = self._lookup()
        except ltk.TekstowoStopped as stopped:
            self.skipTest(f"Tekstowo refused: {stopped.detail}")
        self.assertTrue(isinstance(text, str), "Tekstowo found nothing")
        self.assertTrue(bool((text or "").strip()), "Tekstowo's text was blank")


class BandcampRetryTest(fixtures.IsolatedCase):
    """Offline: the Bandcamp test retries every network failure, then skips; no text fails.

    Each test drives the live class's own retry loop and assertions on an instance
    that never runs, its one lookup faked and the waits patched out, so nothing here
    reaches the network or sleeps.
    """

    def setUp(self) -> None:
        """An unrun live Bandcamp case, and a recorder for the waits it would take."""
        super().setUp()
        self.live = LiveBandcampTest("test_the_album_page_answers_its_track")
        self.waits: list[float] = []
        sleep = mock.patch.object(time, "sleep", self.waits.append)
        sleep.start()
        self.addCleanup(sleep.stop)

    def answer(self, *answers: str | None | Exception) -> None:
        """Make the live case's lookups give *answers* in turn, raising an error."""
        lookup = mock.patch.object(self.live, "_lookup", side_effect=answers)
        lookup.start()
        self.addCleanup(lookup.stop)

    def assert_skipped_after_every_wait(self, error: Exception, named: str) -> None:
        """*error* on every attempt is retried on the schedule, then skipped naming it."""
        self.answer(*([error] * (len(_RETRY_WAITS_S) + 1)))
        with self.assertRaises(unittest.SkipTest) as skipped:
            self.live.test_the_album_page_answers_its_track()
        self.assertIn(named, str(skipped.exception))
        self.assertEqual(self.waits, list(_RETRY_WAITS_S))

    def test_a_timeout_is_retried_then_skipped(self) -> None:
        timeout = lp.ProviderTimeout("no answer")
        self.assert_skipped_after_every_wait(timeout, "ProviderTimeout")

    def test_a_refused_connection_is_retried_then_skipped(self) -> None:
        refused = requests.ConnectionError("refused")
        self.assert_skipped_after_every_wait(refused, "ConnectionError")

    def test_a_wrapped_connection_error_is_retried_then_skipped(self) -> None:
        wrapped = lp.RetriedError(requests.ConnectionError("reset"), 3)
        self.assert_skipped_after_every_wait(wrapped, "ConnectionError")

    def test_any_http_status_is_retried_then_skipped_naming_it(self) -> None:
        refused = lp.HttpStatusError(lm.Source.BANDCAMP, 404)
        self.assert_skipped_after_every_wait(refused, "HTTP 404")

    def test_a_wrapped_http_status_is_retried_then_skipped_naming_it(self) -> None:
        wrapped = lp.RetriedError(lp.HttpStatusError(lm.Source.BANDCAMP, 503), 3)
        self.assert_skipped_after_every_wait(
            wrapped, "HttpStatusError (HTTP 503) after 3 retries"
        )

    def test_a_failure_then_an_answer_passes(self) -> None:
        self.answer(requests.ConnectionError("reset"), "invented words")
        self.live.test_the_album_page_answers_its_track()
        self.assertEqual(self.waits, [_RETRY_WAITS_S[0]])

    def test_a_page_with_no_text_fails(self) -> None:
        for answer in (None, " \n"):
            with self.subTest(answer=answer):
                self.answer(answer)
                with self.assertRaises(AssertionError):
                    self.live.test_the_album_page_answers_its_track()
        self.assertEqual(self.waits, [])

    def test_a_reply_the_reader_cannot_read_fails_unretried(self) -> None:
        unreadable = lp.ProviderError("unexpected", detail="unexpected reply")
        self.answer(unreadable)
        with self.assertRaises(lp.ProviderError):
            self.live.test_the_album_page_answers_its_track()
        self.assertEqual(self.waits, [])
