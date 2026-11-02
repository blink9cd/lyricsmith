"""Within one track's fetch, each LRC provider is asked at most once per query.

Each LRC provider's reply holds both its timed and its plain text, so the plain pass
reads the synced pass's answer, or its failure, instead of asking again. The
stripped-title retry is another query and asks afresh. Counted by the requests the
real adapters send to a fake transport: no test here reaches the network, and every
name and lyric is invented.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tally as lt  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB, NETEASE = lm.Source.LRCLIB, lm.Source.NETEASE
EMPTY, ANSWERED = lm.AttemptOutcome.EMPTY, lm.AttemptOutcome.ANSWERED
ERROR, TIMEOUT = lm.AttemptOutcome.ERROR, lm.AttemptOutcome.TIMEOUT
#: A track with every tag LRCLIB's exact lookup needs.
QUERY = lm.TrackQuery(
    "Invented Artist", "The Keeper Counts", album="Lamp Room", duration_s=200.6
)
PLAIN_TEXT = "the keeper counts the lamps\nand none of them are lit"
#: Each endpoint's path, as the fake server tells them apart.
LRCLIB_GET, LRCLIB_SEARCH = "/api/get", "/api/search"
NETEASE_SEARCH, NETEASE_LYRIC = "/api/search/get", "/api/song/lyric"
#: Short enough that a blocked request is abandoned in milliseconds.
SHORT_DEADLINE_S = 0.05


def lrclib_record(*, synced: str | None, plain: str | None) -> dict[str, object]:
    """An LRCLIB record for the invented track, holding *synced* and *plain*."""
    return {
        "trackName": "The Keeper Counts",
        "artistName": "Invented Artist",
        "duration": 200.0,
        "instrumental": False,
        "syncedLyrics": synced,
        "plainLyrics": plain,
    }


def netease_song() -> dict[str, object]:
    """A NetEase search reply holding the invented track."""
    hit = {"id": 11, "name": "The Keeper Counts", "duration": 200_000}
    hit["artists"] = [{"name": "Invented Artist"}]
    return {"result": {"songs": [hit]}, "code": 200}


class FakeProviders(http_fakes.RecordingServer):
    """LRCLIB and NetEase from memory, each endpoint answering as *replies* says.

    An endpoint *replies* does not name finds nothing: a 404 from the lookup, no hit
    from either search.
    """

    def __init__(self, replies: Mapping[str, object] | None = None) -> None:
        """Answer each endpoint path in *replies* with its value."""
        super().__init__()
        self.replies: dict[str, object] = {
            LRCLIB_GET: 404,
            LRCLIB_SEARCH: [],
            NETEASE_SEARCH: {"result": {"songs": []}, "code": 200},
            NETEASE_LYRIC: {"code": 200},
        }
        self.replies.update(replies or {})

    def reply(self, path: str) -> object:
        """The reply configured for *path*."""
        return self.replies[path]


def _fetch(server: FakeProviders, query: lm.TrackQuery = QUERY) -> lm.FetchResult:
    """Run the chain over the real LRC steps for *query*, answered by *server*."""
    with http_fakes.transport(server):
        return lf.fetch_lyrics(
            query,
            lm.SidecarState(None),
            genius_fakes.instant_table(),
            deadline_s=lf.PROVIDER_DEADLINE_S,
        )


def _steps(result: lm.FetchResult) -> list[tuple[lm.Source, lm.LyricsKind, object]]:
    """Each attempt's provider, kind and outcome, in order."""
    return [(a.key.source, a.key.kind, a.outcome) for a in result.attempts]


class PlainPassReuseTest(fixtures.IsolatedCase):
    """The plain pass sends no request: it reads what the synced pass was answered."""

    def test_a_track_found_nowhere_asks_each_provider_once(self) -> None:
        server = FakeProviders()
        result = _fetch(server)
        self.assertIsNone(result.lyrics)
        self.assertEqual(server.paths(), [LRCLIB_GET, LRCLIB_SEARCH, NETEASE_SEARCH])
        self.assertEqual(
            _steps(result),
            [(LRCLIB, S, EMPTY), (NETEASE, S, EMPTY)]
            + [(LRCLIB, U, EMPTY), (NETEASE, U, EMPTY)],
        )

    def test_lrclib_plain_text_from_the_synced_pass_is_taken_unasked(self) -> None:
        server = FakeProviders(
            {LRCLIB_GET: lrclib_record(synced=None, plain=PLAIN_TEXT)}
        )
        result = _fetch(server)
        self.assertEqual(result.lyrics, lm.PlainLyrics(PLAIN_TEXT))
        self.assertIs(result.source, LRCLIB)
        self.assertEqual(server.paths(), [LRCLIB_GET, LRCLIB_SEARCH, NETEASE_SEARCH])
        self.assertEqual(_steps(result)[-1], (LRCLIB, U, ANSWERED))

    def test_the_lookups_plain_text_beats_the_searchs_in_the_chain(self) -> None:
        timed = "[00:01.00]the keeper counts the lamps"
        server = FakeProviders(
            {
                LRCLIB_GET: lrclib_record(synced=None, plain=PLAIN_TEXT),
                LRCLIB_SEARCH: [lrclib_record(synced=timed, plain="other words")],
            }
        )
        synced = _fetch(server)
        self.assertIs(synced.source, LRCLIB)
        self.assertIsInstance(synced.lyrics, lm.SyncedLyrics)
        plain_key = lm.ProviderKey(LRCLIB, U)
        table = {plain_key: genius_fakes.instant_table()[plain_key]}
        with http_fakes.transport(server):
            plain = lf.fetch_lyrics(
                QUERY, lm.SidecarState(None), table, deadline_s=lf.PROVIDER_DEADLINE_S
            )
        self.assertEqual(plain.lyrics, lm.PlainLyrics(PLAIN_TEXT))
        self.assertEqual(server.paths()[-2:], [LRCLIB_GET, LRCLIB_SEARCH])

    def test_netease_untimed_text_from_the_synced_pass_is_taken_unasked(self) -> None:
        server = FakeProviders(
            {
                NETEASE_SEARCH: netease_song(),
                NETEASE_LYRIC: {"lrc": {"lyric": PLAIN_TEXT}, "code": 200},
            }
        )
        result = _fetch(server)
        self.assertEqual(result.lyrics, lm.PlainLyrics(PLAIN_TEXT))
        self.assertIs(result.source, NETEASE)
        self.assertEqual(
            server.paths(),
            [LRCLIB_GET, LRCLIB_SEARCH, NETEASE_SEARCH, NETEASE_LYRIC],
        )
        self.assertEqual(
            [a.detail for a in result.attempts],
            [lf.DETAIL_NOTHING, lf.DETAIL_UNTIMED, lf.DETAIL_NOTHING]
            + [lf.DETAIL_ACCEPTED],
        )


class FailureReuseTest(fixtures.IsolatedCase):
    """A provider that failed for the query is not asked again, yet each step records it."""

    def test_a_429_in_the_synced_pass_is_recorded_again_unasked(self) -> None:
        server = FakeProviders({LRCLIB_GET: 429})
        result = _fetch(server)
        # The synced step's call retried the lookup; the plain step sends nothing.
        self.assertEqual(server.paths(), [*[LRCLIB_GET] * 4, NETEASE_SEARCH])
        retried = "HTTP 429 after 3 retries"
        self.assertEqual(
            [
                (a.key, a.outcome, a.detail)
                for a in result.attempts
                if a.key.source is LRCLIB
            ],
            [
                (lm.ProviderKey(LRCLIB, S), ERROR, retried),
                (lm.ProviderKey(LRCLIB, U), ERROR, retried),
            ],
        )

    def test_the_failed_provider_still_counts_once_as_asked(self) -> None:
        result = _fetch(FakeProviders({LRCLIB_GET: 429}))
        fetch = lt.TrackFetch(result.attempts, result.source)
        tally = lt.tally_providers({LRCLIB, NETEASE}, [fetch])
        self.assertEqual(
            [(c.source, c.accepted, c.asked) for c in tally],
            [
                (LRCLIB, 0, 1),
                (NETEASE, 0, 1),
                (lm.Source.YOUTUBE_MUSIC, 0, None),
                (lm.Source.BANDCAMP, 0, None),
                (lm.Source.GENIUS, 0, None),
                (lm.Source.TEKSTOWO, 0, None),
            ],
        )

    def test_a_search_failure_keeps_the_plain_text_the_lookup_already_gave(
        self,
    ) -> None:
        server = FakeProviders(
            {
                LRCLIB_GET: lrclib_record(synced=None, plain=PLAIN_TEXT),
                LRCLIB_SEARCH: 503,
            }
        )
        result = _fetch(server)
        # The field search and its three retries, then the one search by ``q``.
        self.assertEqual(
            server.paths(), [LRCLIB_GET, *[LRCLIB_SEARCH] * 5, NETEASE_SEARCH]
        )
        self.assertEqual(
            _steps(result),
            [(LRCLIB, S, ERROR), (NETEASE, S, EMPTY), (LRCLIB, U, ANSWERED)],
        )
        self.assertEqual(result.lyrics, lm.PlainLyrics(PLAIN_TEXT))

    def test_a_timeout_in_the_synced_pass_is_recorded_again_unasked(self) -> None:
        held = http_fakes.HeldTransport(fixtures.HANG_LIMIT_S)
        # Only a backstop: the abandoned call is finished inside the transport below.
        self.addCleanup(held.released.set)
        table = {
            key: step
            for key, step in genius_fakes.instant_table().items()
            if key.source is LRCLIB
        }
        with http_fakes.transport(held):
            result = lf.fetch_lyrics(
                QUERY, lm.SidecarState(None), table, deadline_s=SHORT_DEADLINE_S
            )
            sent = len(held.sent)
            finished = held.finish()
        self.assertEqual(sent, 1)
        self.assertTrue(finished)
        self.assertEqual(_steps(result), [(LRCLIB, S, TIMEOUT), (LRCLIB, U, TIMEOUT)])
        self.assertEqual(result.attempts[0].detail, result.attempts[1].detail)


class RetryQueryTest(fixtures.IsolatedCase):
    """The stripped-title retry is another query, so it asks every provider afresh."""

    def test_the_retry_asks_again_by_the_stripped_title(self) -> None:
        server = FakeProviders()
        bracketed = lm.TrackQuery(
            "Invented Artist",
            "The Keeper Counts (Remastered)",
            album="Lamp Room",
            duration_s=200.6,
        )
        result = _fetch(server, bracketed)
        once = [LRCLIB_GET, LRCLIB_SEARCH, NETEASE_SEARCH]
        self.assertEqual(server.paths(), once + once)
        self.assertEqual(server.params(0)["track_name"], [bracketed.title])
        self.assertEqual(server.params(3)["track_name"], ["The Keeper Counts"])
        self.assertEqual(len(result.attempts), 8)
