"""The LRCLIB step: the exact lookup by the track's tags, then the search, on its public API.

No test here reaches the network: each one replaces the HTTP transport's ``send`` with a
fake that answers from memory, so the real adapter, its session and the status hook all
still run. Every name and lyric is invented.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import requests
from hypothesis import given

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_lrclib as ll  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_match as lmatch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
#: A track with every tag the exact lookup needs.
QUERY = lm.TrackQuery(
    "Invented Artist", "The Keeper Counts", album="Lamp Room", duration_s=200.6
)
#: The same track with no album tag, so only the search can be asked.
NO_ALBUM = lm.TrackQuery("Invented Artist", "The Keeper Counts", duration_s=200.6)
SYNCED_TEXT = (
    "[00:01.00]the keeper counts the lamps\n[00:04.00]and none of them are lit"
)
PLAIN_TEXT = "the keeper counts the lamps\nand none of them are lit"
#: JSON-like values keyed by the names LRCLIB's records use, for the property tests.
_JSON = http_fakes.json_values(
    (
        "trackName",
        "artistName",
        "duration",
        "syncedLyrics",
        "plainLyrics",
        "instrumental",
    ),
    max_size=8,
    max_leaves=12,
)


def record(
    *,
    title: str = "The Keeper Counts",
    artist: str = "Invented Artist",
    duration: float | None = 200.0,
    synced: str | None = SYNCED_TEXT,
    plain: str | None = PLAIN_TEXT,
    instrumental: bool = False,
) -> dict[str, object]:
    """One LRCLIB record, in the shape both endpoints answer with."""
    return {
        "id": 7,
        "trackName": title,
        "artistName": artist,
        "albumName": "Lamp Room",
        "duration": duration,
        "instrumental": instrumental,
        "plainLyrics": plain,
        "syncedLyrics": synced,
    }


class FakeLrclib(http_fakes.RecordingServer):
    """LRCLIB from memory: the exact lookup's record or a status, and the search's list."""

    def __init__(
        self,
        exact: dict[str, object] | int = 404,
        hits: object = None,
    ) -> None:
        """Answer the lookup with *exact* and the search with *hits*, none when None.

        Either is a status alone when an int; *hits* may also be raw bytes or any
        other body, so a refusal test can answer in a shape the tool does not know.
        """
        super().__init__()
        self.exact = exact
        self.hits: object = [] if hits is None else hits

    def reply(self, path: str) -> object:
        """The lookup's answer for its path, the search's for any other."""
        return self.exact if path == "/api/get" else self.hits


def _step(kind: lm.LyricsKind) -> lm.Provider:
    """The real table's LRCLIB step of *kind*."""
    return genius_fakes.instant_table()[lm.ProviderKey(lm.Source.LRCLIB, kind)]


def _ask(
    server: FakeLrclib, kind: lm.LyricsKind, query: lm.TrackQuery = QUERY
) -> str | None:
    """Ask the real LRCLIB step of *kind* for *query*, answered by *server*."""
    with http_fakes.transport(server):
        return _step(kind)(query)


class ExactLookupTest(fixtures.IsolatedCase):
    """The first request is the exact lookup by title, artist, album and whole seconds."""

    def test_the_lookup_sends_every_tag_and_whole_seconds(self) -> None:
        server = FakeLrclib(exact=record())
        self.assertEqual(_ask(server, S), SYNCED_TEXT)
        self.assertEqual(server.paths(), ["/api/get"])
        self.assertEqual(
            server.params(0),
            {
                "track_name": ["The Keeper Counts"],
                "artist_name": ["Invented Artist"],
                "album_name": ["Lamp Room"],
                "duration": ["201"],
            },
        )

    def test_every_request_names_the_tool_and_its_version(self) -> None:
        server = FakeLrclib(exact=record())
        _ask(server, S)
        agent = str(server.requests[0].headers["User-Agent"])
        self.assertRegex(agent, r"^lyricsmith/\S+$")

    def test_a_plain_step_takes_the_plain_text(self) -> None:
        self.assertEqual(_ask(FakeLrclib(exact=record()), U), PLAIN_TEXT)

    def test_a_plain_step_falls_back_to_the_timed_text(self) -> None:
        self.assertEqual(_ask(FakeLrclib(exact=record(plain="")), U), SYNCED_TEXT)

    def test_an_instrumental_is_nothing_found_and_no_search_follows(self) -> None:
        server = FakeLrclib(exact=record(instrumental=True, synced=None, plain=None))
        self.assertIsNone(_ask(server, U))
        self.assertEqual(server.paths(), ["/api/get"])

    def test_a_404_falls_back_to_the_search(self) -> None:
        server = FakeLrclib(exact=404, hits=[record()])
        self.assertEqual(_ask(server, S), SYNCED_TEXT)
        self.assertEqual(server.paths(), ["/api/get", "/api/search"])
        self.assertEqual(
            server.params(1),
            {"track_name": ["The Keeper Counts"], "artist_name": ["Invented Artist"]},
        )

    def test_a_server_error_from_the_lookup_falls_back_to_the_search(self) -> None:
        for status in (500, 503, 599):
            with self.subTest(status=status):
                server = FakeLrclib(exact=status, hits=[record()])
                self.assertEqual(_ask(server, S), SYNCED_TEXT)
                self.assertEqual(server.paths(), ["/api/get", "/api/search"])

    def test_a_server_error_from_the_lookup_then_no_match_finds_nothing(self) -> None:
        server = FakeLrclib(exact=503, hits=[record(artist="Other Singer")])
        self.assertIsNone(_ask(server, S))
        self.assertEqual(server.paths(), ["/api/get", "/api/search"])

    def test_a_record_without_the_steps_kind_falls_back_to_the_search(self) -> None:
        server = FakeLrclib(exact=record(synced=None), hits=[record()])
        self.assertEqual(_ask(server, S), SYNCED_TEXT)
        self.assertEqual(server.paths(), ["/api/get", "/api/search"])

    def test_a_record_the_matcher_refuses_falls_back_to_the_search(self) -> None:
        server = FakeLrclib(exact=record(artist="Other Singer"), hits=[])
        self.assertIsNone(_ask(server, S))
        self.assertEqual(server.paths(), ["/api/get", "/api/search"])

    def test_with_no_album_or_no_length_only_the_search_is_asked(self) -> None:
        no_length = lm.TrackQuery(
            "Invented Artist", "The Keeper Counts", album="Lamp Room"
        )
        unusable = (
            dataclasses.replace(QUERY, duration_s=length)
            for length in (float("nan"), 0.0, -5.0)
        )
        blank_album = dataclasses.replace(QUERY, album="  ")
        for query in (NO_ALBUM, no_length, *unusable, blank_album):
            with self.subTest(query=query):
                server = FakeLrclib(hits=[record()])
                self.assertEqual(_ask(server, S, query), SYNCED_TEXT)
                self.assertEqual(server.paths(), ["/api/search"])

    def test_the_lookups_plain_text_beats_the_searchs(self) -> None:
        server = FakeLrclib(
            exact=record(synced=None, plain=PLAIN_TEXT),
            hits=[record(plain="the search's own plain words")],
        )
        with http_fakes.transport(server):
            answer = ll.LrclibProvider(lm.LyricsKind.SYNCED).answer_all_kinds(QUERY)
        self.assertEqual(server.paths(), ["/api/get", "/api/search"])
        self.assertEqual(answer, ll.LrcAnswer(synced=SYNCED_TEXT, plain=PLAIN_TEXT))

    def test_a_query_with_no_artist_asks_nothing(self) -> None:
        server = FakeLrclib(exact=record())
        self.assertIsNone(_ask(server, S, lm.TrackQuery("", "01 The Keeper Counts")))
        self.assertEqual(server.requests, [])


class SearchTest(fixtures.IsolatedCase):
    """The search's hits go through the matcher; the best with the step's kind wins."""

    def test_no_hit_finds_nothing(self) -> None:
        self.assertIsNone(_ask(FakeLrclib(hits=[]), U, NO_ALBUM))

    def test_a_hit_by_another_artist_is_never_taken(self) -> None:
        server = FakeLrclib(hits=[record(artist="Other Singer")])
        self.assertIsNone(_ask(server, S, NO_ALBUM))

    def test_a_live_version_of_another_length_is_never_taken(self) -> None:
        hits = [record(title="The Keeper Counts (Live)", duration=251.0)]
        self.assertIsNone(_ask(FakeLrclib(hits=hits), S, NO_ALBUM))

    def test_the_best_hit_holding_timed_lines_wins_a_synced_step(self) -> None:
        untimed = record(synced=None, plain="untimed words")
        timed = record(title="The Keeper Count", synced=SYNCED_TEXT)
        self.assertEqual(
            _ask(FakeLrclib(hits=[untimed, timed]), S, NO_ALBUM), SYNCED_TEXT
        )

    def test_an_instrumental_hit_is_passed_over(self) -> None:
        hits = [record(instrumental=True), record(synced=None, plain=PLAIN_TEXT)]
        self.assertEqual(_ask(FakeLrclib(hits=hits), U, NO_ALBUM), PLAIN_TEXT)

    def test_matching_hits_without_the_kind_find_nothing(self) -> None:
        hits = [record(synced=None, plain=PLAIN_TEXT)]
        self.assertIsNone(_ask(FakeLrclib(hits=hits), S, NO_ALBUM))


class StatusTest(fixtures.IsolatedCase):
    """A rate limit raises once retried; the lookup's 404 or 5xx falls to the search.

    The search's errors raise, a transient one after its retries.
    """

    def test_a_429_from_the_lookup_is_a_status_error(self) -> None:
        with self.assertRaises(lp.RetriedError) as raised:
            _ask(FakeLrclib(exact=429), S)
        last = raised.exception.last
        self.assertEqual(lp.http_status(last), 429)
        self.assertEqual(getattr(last, "source", None), lm.Source.LRCLIB)
        self.assertEqual(str(last), "LRCLIB answered HTTP 429")
        self.assertEqual(raised.exception.detail, "HTTP 429 after 3 retries")

    def test_a_500_from_the_search_is_a_status_error_once_retried(self) -> None:
        with self.assertRaises(lp.RetriedError) as raised:
            _ask(FakeLrclib(hits=500), S, NO_ALBUM)
        self.assertEqual(lp.http_status(raised.exception.last), 500)

    def test_a_503_from_the_search_after_a_503_lookup_is_a_status_error(self) -> None:
        with self.assertRaises(lp.RetriedError) as raised:
            _ask(FakeLrclib(exact=503, hits=503), S)
        self.assertEqual(lp.http_status(raised.exception.last), 503)

    def test_a_404_from_the_search_is_a_status_error(self) -> None:
        with self.assertRaises(lp.HttpStatusError):
            _ask(FakeLrclib(hits=404), S, NO_ALBUM)

    def test_the_chain_records_a_429_as_an_error_attempt(self) -> None:
        table = {lm.ProviderKey(lm.Source.LRCLIB, S): _step(S)}
        with http_fakes.transport(http_fakes.always(429)):
            result = lf.fetch_lyrics(
                QUERY, lm.SidecarState(None), table, deadline_s=lf.PROVIDER_DEADLINE_S
            )
        self.assertEqual(
            [(a.outcome, a.detail) for a in result.attempts],
            [(lm.AttemptOutcome.ERROR, "HTTP 429 after 3 retries")],
        )


class ReplyShapeTest(fixtures.IsolatedCase):
    """A reply in any shape but the documented one is LRCLIB's error, never a crash."""

    def test_a_body_that_is_not_json_is_a_provider_error(self) -> None:
        def answer(request: requests.PreparedRequest) -> requests.Response:
            return http_fakes.response(request, 200, b"<html>busy</html>")

        with (
            http_fakes.transport(answer),
            self.assertRaises(lp.ProviderError) as raised,
        ):
            _step(S)(QUERY)
        self.assertIn(str(lm.Source.LRCLIB), str(raised.exception))

    def test_unexpected_shapes_are_provider_errors(self) -> None:
        shapes: list[object] = [
            [],
            {"trackName": 3, "artistName": "A"},
            {"trackName": "T", "artistName": "A", "duration": "long"},
            {"trackName": "T", "artistName": "A", "duration": True},
            {"trackName": "T", "artistName": "A", "syncedLyrics": 5},
            {"trackName": "T", "artistName": "A", "instrumental": "no"},
        ]
        for shape in shapes:
            with self.subTest(shape=shape), self.assertRaises(lp.ProviderError):
                ll.parse_record(shape)

    def test_a_search_reply_that_is_not_a_list_is_a_provider_error(self) -> None:
        with self.assertRaises(lp.ProviderError):
            ll.parse_records({"error": "x"})

    def test_absent_optional_fields_read_as_none(self) -> None:
        parsed = ll.parse_record({"trackName": "T", "artistName": "A"})
        self.assertEqual(
            (parsed.hit, parsed.synced, parsed.plain, parsed.instrumental),
            (lmatch.Hit("T", ("A",), None), None, None, False),
        )

    @given(_JSON)
    def test_fuzz_parse_record(self, value: object) -> None:
        try:
            parsed = ll.parse_record(value)
        except lp.ProviderError:
            return
        self.assertIsInstance(parsed.hit.title, str)
        self.assertEqual(len(parsed.hit.artists), 1)

    @given(_JSON)
    def test_fuzz_parse_records(self, value: object) -> None:
        try:
            parsed = ll.parse_records(value)
        except lp.ProviderError:
            return
        self.assertIsInstance(value, list)
        self.assertEqual(len(parsed), len(value) if isinstance(value, list) else -1)
