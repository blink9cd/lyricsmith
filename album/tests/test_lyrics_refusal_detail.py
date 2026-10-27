"""An old manifest's refusal detail read back to its class, as `classify` classed it live.

A manifest written before the facts were kept holds only each refusal's provider, step
and detail, so `reason_from_detail` reads the class back from the provider and the
detail together: one detail means different refusals from different providers, such as
``too many redirects``, a block from Tekstowo and an unrecognized reply from Bandcamp.
The property tests here build the errors each provider really raises, the cooling
clients' through their real clients, and hold the reader to `classify`'s class for each.
Every reply and value is invented.
"""

from __future__ import annotations

import itertools
import sys
import unittest
from collections.abc import Callable
from datetime import date
from pathlib import Path

import requests
from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal_detail as lrd  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tekstowo as lt  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_music as lym  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_fakes as tf  # noqa: E402 - import follows the sys.path bootstrap above
import youtube_fakes as yf  # noqa: E402 - import follows the sys.path bootstrap above
from bandcamp_fakes import Redirect  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import FakeClock  # noqa: E402 - import follows the sys.path bootstrap above

R = lrf.Refusal
S = lm.Source
#: The providers whose details carry no meaning of their own.
_PLAIN_SOURCES = (S.LRCLIB, S.NETEASE, S.BANDCAMP)
#: Far enough off that no deadline is the point of a test here.
ROOM_S = 100.0
_BODY = lym.search_body("the keeper counts", date(2026, 10, 21))
_SEARCH = (lt.TEKSTOWO_HOST, tf.search_path(tf.ARTIST, tf.TITLE))
_SEARCH_URL = lt.search_url(tf.ARTIST, tf.TITLE)
_CHALLENGE_PAGE = "<html><title>Just a moment...</title></html>"
_OTHER_PAGE = "<html><body><p>An invented page.</p></body></html>"
#: The fixed words a provider's own error may record, none naming a class.
_FIXED_DETAILS = (
    "unexpected reply",
    lh.REPLY_NOT_JSON,
    lh.REPLY_TOO_LARGE,
    lh.REPLY_NOT_TEXT,
    lh.REDIRECT_REFUSED,
    lh.TOO_MANY_REDIRECTS,
    lh.URL_REFUSED,
    "code -460",
    "no track list",
)


def _detail(error: Exception) -> str:
    """The detail a refusal records for *error*: a timeout's is `TIMEOUT_DETAIL`."""
    if isinstance(error, lp.ProviderTimeout):
        return lp.TIMEOUT_DETAIL
    return lp.error_detail(error)


def _site(path: str) -> str:
    """An address on Tekstowo's own host."""
    return f"https://{lt.TEKSTOWO_HOST}{path}"


def _shared_errors(source: lm.Source) -> st.SearchStrategy[Exception]:
    """The errors any provider's step may end on: a timeout, a dropped connection,
    fixed words, or a single error status."""
    return st.one_of(
        st.just(lp.ProviderTimeout("the step's deadline passed")),
        st.sampled_from(
            (
                requests.ConnectionError("reset"),
                requests.ReadTimeout("slow"),
                requests.exceptions.ChunkedEncodingError("dropped mid-body"),
            )
        ),
        st.sampled_from(_FIXED_DETAILS).map(
            lambda detail: lp.ProviderError("an invented reply", detail=detail)
        ),
        st.integers(300, 599).map(lambda status: lp.HttpStatusError(source, status)),
    )


def _retried(source: lm.Source) -> st.SearchStrategy[Exception]:
    """An LRC or MusicBrainz request still failing after its retries."""
    last = st.one_of(
        st.sampled_from(sorted(lh.RETRY_STATUSES)).map(
            lambda status: lp.HttpStatusError(source, status)
        ),
        st.just(requests.ConnectionError("reset")),
    )
    return st.builds(lp.RetriedError, last, st.integers(1, 3))


def _challenged() -> st.SearchStrategy[Exception]:
    """Genius still challenged after its retries."""
    statuses = st.sampled_from(sorted(lg.GENIUS_CHALLENGE_STATUSES))
    return st.builds(lg.GeniusChallengeError, statuses, st.integers(1, 3))


class ReasonFromDetailTest(fixtures.IsolatedCase):
    """Each detail's class, for the providers it means something different from."""

    def assert_reads(self, source: lm.Source, cases: dict[str, lrf.Refusal]) -> None:
        """Assert each detail of *cases* reads, from *source*, as its class."""
        for detail, reason in cases.items():
            with self.subTest(source=source, detail=detail[:20]):
                self.assertIs(lrd.reason_from_detail(source, detail), reason)

    def test_a_status_detail_reads_as_its_class(self) -> None:
        cases = {
            "HTTP 429": R.RATE_LIMITED,
            "HTTP 429 after 3 retries": R.RATE_LIMITED,
            "HTTP 502": R.OVERLOADED,
            "HTTP 503 after 1 retry": R.OVERLOADED,
            "HTTP 504": R.OVERLOADED,
            "HTTP 403": R.BLOCKED,
            "HTTP 401": R.BLOCKED,
            "HTTP 407": R.BLOCKED,
            "HTTP 451": R.BLOCKED,
            "HTTP 500": R.OVERLOADED,
            "HTTP 501": R.OVERLOADED,
            "HTTP 505": R.OVERLOADED,
            "HTTP 500 after 3 retries": R.OVERLOADED,
            "HTTP 400": R.UNRECOGNIZED,
            "HTTP 404": R.UNRECOGNIZED,
            "HTTP 410": R.UNRECOGNIZED,
            "HTTP 422": R.UNRECOGNIZED,
            "HTTP 302": R.UNRECOGNIZED,
        }
        for source in (*_PLAIN_SOURCES, S.TEKSTOWO):
            self.assert_reads(source, cases)

    def test_a_timeout_or_a_connection_error_reads_as_unreachable(self) -> None:
        details = (
            lp.TIMEOUT_DETAIL,
            "ConnectionError",
            "ConnectionError after 1 retry",
            "ConnectTimeout",
            "ReadTimeout",
            "ChunkedEncodingError",
            "SSLError",
            "ConnectionResetError",
            "TimeoutError",
            "ProviderTimeout",
        )
        for source in S:
            self.assert_reads(source, dict.fromkeys(details, R.UNREACHABLE))

    def test_anything_else_reads_as_unrecognized(self) -> None:
        details = (
            "",
            "code -460",
            "unexpected reply",
            "reply not JSON",
            lh.TOO_MANY_REDIRECTS,
            lh.REDIRECT_REFUSED,
            "TooManyRedirects",
            lt.CHALLENGE,
            lym.TO_CONSENT,
            "HTTP",
            "HTTP 5030",
            "HTTP 50",
            "HTTP abc",
            "HTTP ５０３",  # fullwidth digits: decimal, but no status
            "http 429",
            "OSError",
            "HTTP " + "9" * 5000,
        )
        for source in _PLAIN_SOURCES:
            self.assert_reads(source, dict.fromkeys(details, R.UNRECOGNIZED))

    def test_tekstowo_s_challenge_and_third_redirect_read_as_blocked(self) -> None:
        cases = {
            lt.CHALLENGE: R.BLOCKED,
            lh.TOO_MANY_REDIRECTS: R.BLOCKED,
            lh.REDIRECT_REFUSED: R.UNRECOGNIZED,
            lt.SEARCH_NOT_RECOGNIZED: R.UNRECOGNIZED,
            lt.SONG_NOT_RECOGNIZED: R.UNRECOGNIZED,
        }
        self.assert_reads(S.TEKSTOWO, cases)

    def test_youtube_music_s_challenges_and_redirects_read_as_blocked(self) -> None:
        cases = {
            lym.TO_CONSENT: R.BLOCKED,
            lym.TO_SORRY: R.BLOCKED,
            "HTTP 302": R.BLOCKED,
            "HTTP 301": R.BLOCKED,
            "HTTP 429": R.RATE_LIMITED,
            "HTTP 503": R.OVERLOADED,
            "HTTP 500": R.OVERLOADED,
            "HTTP 403": R.BLOCKED,
            # A client error stopped YouTube Music for the run, so it reads as a block.
            "HTTP 400": R.BLOCKED,
            "HTTP 404": R.BLOCKED,
            lh.REPLY_NOT_JSON: R.UNRECOGNIZED,
            "unexpected reply": R.UNRECOGNIZED,
        }
        self.assert_reads(S.YOUTUBE_MUSIC, cases)

    def test_a_genius_challenge_after_retries_reads_as_blocked(self) -> None:
        cases = {
            "HTTP 429 after 3 retries": R.BLOCKED,
            "HTTP 403 after 1 retry": R.BLOCKED,
            "HTTP 429": R.RATE_LIMITED,
            "HTTP 403": R.BLOCKED,
            "HTTP 500": R.OVERLOADED,
            "HTTP 400": R.UNRECOGNIZED,
            lh.REDIRECT_REFUSED: R.UNRECOGNIZED,
            "TooManyRedirects": R.UNRECOGNIZED,
        }
        self.assert_reads(S.GENIUS, cases)

    @given(st.sampled_from(S), st.text())
    def test_fuzz_reason_from_detail(self, source: lm.Source, detail: str) -> None:
        self.assertIsInstance(lrd.reason_from_detail(source, detail), R)


class _AgreesCase(fixtures.IsolatedCase):
    """The one assertion every agreement test makes."""

    def assert_agrees(self, source: lm.Source, error: Exception) -> None:
        """Assert *error*'s detail reads, from *source*, as `classify` classes it."""
        self.assertIs(
            lrd.reason_from_detail(source, _detail(error)), lrf.classify(error).reason
        )


class AgreementTest(_AgreesCase):
    """The reader agrees with `classify` on every error each provider really raises."""

    @given(
        st.sampled_from(_PLAIN_SOURCES).flatmap(
            lambda source: st.tuples(
                st.just(source), st.one_of(_shared_errors(source), _retried(source))
            )
        )
    )
    def test_a_plain_provider_s_errors(self, case: tuple[lm.Source, Exception]) -> None:
        self.assert_agrees(*case)

    @given(st.one_of(_shared_errors(S.GENIUS), _challenged()))
    def test_genius_s_errors(self, error: Exception) -> None:
        self.assert_agrees(S.GENIUS, error)


class CoolingAgreementTest(_AgreesCase):
    """The cooling clients' errors, raised by the real clients, read back alike."""

    def setUp(self) -> None:
        """A fresh state folder for each client a test builds."""
        super().setUp()
        self.folders = itertools.count()

    def _folder(self) -> Path:
        """A state folder no other client in this test has used."""
        return self.tmp / str(next(self.folders))

    def _youtube_errors(self, answer: yf.Answer) -> list[lp.ProviderError]:
        """The stop the one reply *answer* draws, then the next call's, unsent."""
        clock = FakeClock()
        server = yf.ScriptedServer([answer], clock)
        client = yf.client(self._folder(), server, clock)
        errors: list[lp.ProviderError] = []
        with http_fakes.transport(server):
            for _ in range(2):
                with self.assertRaises(lym.YouTubeMusicStopped) as raised:
                    client.post(lym.SEARCH, _BODY, clock() + ROOM_S)
                errors.append(raised.exception)
        return errors

    def _tekstowo_errors(
        self, routes: dict[tuple[str, str], object], read: Callable[[str], object]
    ) -> list[lp.ProviderError]:
        """The errors two searches answered by *routes* and read by *read* raise."""
        clock = FakeClock()
        server = tf.TekstowoServer(routes, clock)
        client = tf.client(self._folder(), clock)
        errors: list[lp.ProviderError] = []
        with http_fakes.transport(server):
            for _ in range(2):
                with self.assertRaises(lp.ProviderError) as raised:
                    client.get(
                        _SEARCH_URL,
                        clock() + ROOM_S,
                        read=read,
                        unrecognized=lt.SEARCH_NOT_RECOGNIZED,
                    )
                errors.append(raised.exception)
        return errors

    @given(
        st.integers(300, 599),
        st.sampled_from(
            (
                None,
                "https://consent.youtube.com/m",
                "https://music.youtube.com/sorry/index",
                "https://elsewhere.invalid/moved",
            )
        ),
    )
    def test_youtube_music_s_stops(self, status: int, location: str | None) -> None:
        headers = {} if location is None else {"Location": location}
        for error in self._youtube_errors((status, b"", headers)):
            self.assert_agrees(S.YOUTUBE_MUSIC, error)

    def test_youtube_music_s_reply_not_json(self) -> None:
        for error in self._youtube_errors((200, b"not json", {})):
            self.assert_agrees(S.YOUTUBE_MUSIC, error)

    @given(st.integers(300, 599))
    def test_tekstowo_s_statuses(self, status: int) -> None:
        for error in self._tekstowo_errors({_SEARCH: status}, lambda text: text):
            self.assert_agrees(S.TEKSTOWO, error)

    def test_tekstowo_s_pages_and_redirects(self) -> None:
        redirects: dict[tuple[str, str], object] = {
            _SEARCH: Redirect(_site("/a")),
            (lt.TEKSTOWO_HOST, "/a"): Redirect(_site("/b")),
            (lt.TEKSTOWO_HOST, "/b"): Redirect(_site("/c")),
            (lt.TEKSTOWO_HOST, "/c"): _OTHER_PAGE,
        }
        cases: tuple[tuple[dict[tuple[str, str], object], Callable[[str], object]], ...]
        cases = (
            (redirects, lambda text: text),
            ({_SEARCH: _CHALLENGE_PAGE}, _none),
            ({_SEARCH: _OTHER_PAGE}, _none),
            ({_SEARCH: Redirect("https://elsewhere.invalid/x")}, lambda text: text),
        )
        for routes, read in cases:
            with self.subTest(routes=len(routes)):
                for error in self._tekstowo_errors(routes, read):
                    self.assert_agrees(S.TEKSTOWO, error)


def _none(text: str) -> None:
    """A reader that recognizes no page."""


if __name__ == "__main__":
    unittest.main()
