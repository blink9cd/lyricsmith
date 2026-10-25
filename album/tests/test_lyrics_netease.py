"""The NetEase step: its search, our matcher's choice of hit, then that song's lyric.

Every reply's HTTP status is read, and a reply in an unexpected shape is NetEase's error,
so a throttled or changed endpoint shows as an error in the tally rather than as a run of
"nothing found". No test here reaches the network: the HTTP transport's ``send`` answers
from memory, so the real adapter, its session and the status hook all still run. Every
name and lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests
from hypothesis import given

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import bandcamp_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_match as lmatch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_netease as ln  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
QUERY = lm.TrackQuery("Invented Artist", "The Keeper Counts", duration_s=200.6)
SYNCED_TEXT = (
    "[00:01.00]the keeper counts the lamps\n[00:04.00]and none of them are lit"
)
SEARCH_PATH = "/api/search/get"
LYRIC_PATH = "/api/song/lyric"
#: The invented cookie a faked search reply sets.
SEARCH_COOKIE = "NMTID=invented; Path=/"
#: JSON-like values keyed by the names NetEase's replies use, for the property tests.
_JSON = http_fakes.json_values(
    (
        "code",
        "result",
        "songs",
        "id",
        "name",
        "artists",
        "duration",
        "lrc",
        "lyric",
        "nolyric",
        "uncollected",
    ),
    max_size=6,
    max_leaves=16,
)


def song(
    song_id: int = 11,
    name: str = "The Keeper Counts",
    artists: tuple[str, ...] = ("Invented Artist",),
    duration_ms: int | None = 200_000,
) -> dict[str, object]:
    """One search hit, in the shape NetEase's search answers with."""
    hit: dict[str, object] = {
        "id": song_id,
        "name": name,
        "artists": [{"id": 1, "name": artist} for artist in artists],
    }
    if duration_ms is not None:
        hit["duration"] = duration_ms
    return hit


def search_reply(*songs: dict[str, object]) -> dict[str, object]:
    """A search reply holding *songs*."""
    return {"result": {"songs": list(songs), "songCount": len(songs)}, "code": 200}


def lyric_reply(text: str | None = SYNCED_TEXT) -> dict[str, object]:
    """A lyric reply holding *text*."""
    return {"lrc": {"version": 3, "lyric": text}, "code": 200}


class FakeNetEase(http_fakes.RecordingServer):
    """NetEase from memory: a search reply and a lyric reply, each a body or a status."""

    def __init__(
        self,
        search: object = None,
        lyric: object = None,
    ) -> None:
        """Answer the search with *search* and the lyric with *lyric*; an int is a status."""
        super().__init__()
        self.search = search_reply(song()) if search is None else search
        self.lyric = lyric_reply() if lyric is None else lyric

    def reply(self, path: str) -> object:
        """The search's answer for its path, the lyric's for any other."""
        return self.search if path == SEARCH_PATH else self.lyric


class CookieNetEase(FakeNetEase):
    """`FakeNetEase`, its search reply setting `SEARCH_COOKIE`."""

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """Answer *request* as `FakeNetEase` does; a search's reply sets the cookie."""
        built = super().__call__(request)
        if urlsplit(request.url or "").path != SEARCH_PATH:
            return built
        return bandcamp_fakes.with_cookie(built, SEARCH_COOKIE)


def _step(kind: lm.LyricsKind) -> lm.Provider:
    """The real table's NetEase step of *kind*."""
    return genius_fakes.instant_table()[lm.ProviderKey(lm.Source.NETEASE, kind)]


def _ask(
    server: FakeNetEase, kind: lm.LyricsKind = S, query: lm.TrackQuery = QUERY
) -> str | None:
    """Ask the real NetEase step of *kind* for *query*, answered by *server*."""
    with http_fakes.transport(server):
        return _step(kind)(query)


class RequestTest(fixtures.IsolatedCase):
    """The search, then the chosen song's lyric, asked the way the endpoints expect."""

    def test_the_search_then_the_lyric_of_the_matched_song(self) -> None:
        server = FakeNetEase()
        self.assertEqual(_ask(server), SYNCED_TEXT)
        self.assertEqual(server.paths(), [SEARCH_PATH, LYRIC_PATH])
        self.assertEqual(
            server.params(0),
            {
                "limit": ["10"],
                "type": ["1"],
                "offset": ["0"],
                "s": ["The Keeper Counts Invented Artist"],
            },
        )
        self.assertEqual(server.params(1), {"id": ["11"], "lv": ["1"]})

    def test_the_lyric_request_carries_no_referer_and_no_cookie(self) -> None:
        server = CookieNetEase()
        self.assertEqual(_ask(server), SYNCED_TEXT)
        for request in server.requests:
            with self.subTest(path=urlsplit(request.url or "").path):
                self.assertNotIn("Referer", request.headers)
                self.assertNotIn("Cookie", request.headers)

    def test_the_fake_search_does_set_a_cookie(self) -> None:
        # The proof the test above can fail: a session keeping cookies sends it back.
        server = CookieNetEase()
        with http_fakes.transport(server):
            ln.NeteaseProvider(session_factory=requests.Session)(QUERY)
        self.assertEqual(server.requests[1].headers["Cookie"], "NMTID=invented")

    def test_the_step_session_is_cookie_free(self) -> None:
        self.assertIs(ln.NeteaseProvider().session_factory, lh.cookie_free_session)

    def test_the_search_is_asked_anonymously_with_no_cookie(self) -> None:
        server = FakeNetEase()
        _ask(server)
        self.assertEqual(urlsplit(server.requests[0].url or "").netloc, "music.163.com")
        self.assertNotIn("Cookie", server.requests[0].headers)

    def test_a_plain_step_gets_the_same_text_for_the_chain_to_untime(self) -> None:
        self.assertEqual(_ask(FakeNetEase(), U), SYNCED_TEXT)

    def test_a_query_with_no_artist_asks_nothing(self) -> None:
        server = FakeNetEase()
        self.assertIsNone(_ask(server, S, lm.TrackQuery("", "01 The Keeper Counts")))
        self.assertEqual(server.requests, [])


class ChoiceTest(fixtures.IsolatedCase):
    """The matcher picks the hit; with none accepted, no lyric is asked for."""

    def test_the_same_title_by_another_artist_is_never_taken(self) -> None:
        server = FakeNetEase(search=search_reply(song(artists=("Other Singer",))))
        self.assertIsNone(_ask(server))
        self.assertEqual(server.paths(), [SEARCH_PATH])

    def test_a_live_version_of_another_length_is_never_taken(self) -> None:
        live = song(name="The Keeper Counts (Live)", duration_ms=262_000)
        self.assertIsNone(_ask(FakeNetEase(search=search_reply(live))))

    def test_the_matching_hit_is_chosen_over_an_earlier_near_miss(self) -> None:
        near_miss = song(song_id=5, name="The Keeper Counts (Remix)")
        server = FakeNetEase(search=search_reply(near_miss, song(song_id=12)))
        _ask(server)
        self.assertEqual(server.params(1)["id"], ["12"])

    def test_a_length_in_milliseconds_is_compared_in_seconds(self) -> None:
        server = FakeNetEase(search=search_reply(song(duration_ms=201_000)))
        self.assertEqual(_ask(server), SYNCED_TEXT)

    def test_no_result_finds_nothing(self) -> None:
        for reply in (
            {"result": {"songCount": 0}, "code": 200},
            {"result": {"songs": None}, "code": 200},
            {"code": 200},
            search_reply(),
        ):
            with self.subTest(reply=reply):
                self.assertIsNone(_ask(FakeNetEase(search=reply)))


class LyricReplyTest(fixtures.IsolatedCase):
    """The lyric reply: its text, or nothing for a song NetEase holds none for."""

    def test_no_lyric_reads_as_nothing_found(self) -> None:
        for reply in (
            {"nolyric": True, "code": 200},
            {"uncollected": True, "code": 200},
            {"code": 200},
            {"lrc": None, "code": 200},
            lyric_reply(None),
            lyric_reply(""),
        ):
            with self.subTest(reply=reply):
                self.assertIsNone(_ask(FakeNetEase(lyric=reply)))


class StatusTest(fixtures.IsolatedCase):
    """Every reply's status is read: an error status is NetEase's error, never nothing."""

    def test_an_error_status_from_either_endpoint_is_a_status_error(self) -> None:
        for server in (FakeNetEase(search=429), FakeNetEase(lyric=503)):
            with (
                self.subTest(paths=server.search),
                self.assertRaises(lp.RetriedError) as raised,
            ):
                _ask(server)
            last = raised.exception.last
            self.assertIsInstance(last, lp.HttpStatusError)
            self.assertEqual(getattr(last, "source", None), lm.Source.NETEASE)

    def test_a_refusing_code_in_an_ok_body_is_a_provider_error(self) -> None:
        for server in (
            FakeNetEase(search={"code": -460, "message": "refused"}),
            FakeNetEase(lyric={"code": 405}),
        ):
            with (
                self.subTest(search=server.search),
                self.assertRaises(lp.ProviderError) as raised,
            ):
                _ask(server)
            self.assertIn("NetEase", str(raised.exception))

    def test_a_risk_control_refusal_names_netease_and_its_code(self) -> None:
        server = FakeNetEase(search={"code": -462, "message": "refused"})
        with self.assertRaises(lp.ProviderError) as raised:
            _ask(server)
        self.assertIn("NetEase", str(raised.exception))
        self.assertIn("-462", str(raised.exception))
        self.assertEqual(server.paths(), [SEARCH_PATH])

    def test_a_code_past_its_digit_bound_is_a_fixed_detail(self) -> None:
        longest = int("9" * ln.MAX_CODE_DIGITS)
        for code, detail in (
            (longest, f"code {longest}"),
            (-longest, f"code {-longest}"),
            (10**ln.MAX_CODE_DIGITS, ln.CODE_OUT_OF_RANGE),
            (-(10**4000), ln.CODE_OUT_OF_RANGE),
        ):
            with (
                self.subTest(digits=len(str(code))),
                self.assertRaises(lp.ProviderError) as raised,
            ):
                _ask(FakeNetEase(search={"code": code}))
            self.assertEqual(raised.exception.detail, detail)
            self.assertLess(len(str(raised.exception)), 100)

    def test_the_chain_records_a_429_as_an_error_attempt(self) -> None:
        table = {lm.ProviderKey(lm.Source.NETEASE, S): _step(S)}
        with http_fakes.transport(FakeNetEase(search=429)):
            result = lf.fetch_lyrics(
                QUERY, lm.SidecarState(None), table, deadline_s=lf.PROVIDER_DEADLINE_S
            )
        self.assertEqual(
            [(a.outcome, a.detail) for a in result.attempts],
            [(lm.AttemptOutcome.ERROR, "HTTP 429 after 3 retries")],
        )


class ReplyShapeTest(fixtures.IsolatedCase):
    """A reply in any other shape is a ProviderError naming NetEase, never a crash."""

    def test_a_body_that_is_not_json_is_a_provider_error(self) -> None:
        for server in (FakeNetEase(search=b"<html>"), FakeNetEase(lyric=b"{")):
            with (
                self.subTest(search=server.search),
                self.assertRaises(lp.ProviderError) as raised,
            ):
                _ask(server)
            self.assertIn("NetEase", str(raised.exception))

    def test_unexpected_search_shapes_are_provider_errors(self) -> None:
        shapes: list[object] = [
            [],
            {"code": "200"},
            {"result": []},
            {"result": {"songs": {}}},
            {"result": {"songs": ["x"]}},
            {"result": {"songs": [{"id": "11", "name": "T", "artists": []}]}},
            {"result": {"songs": [{"id": True, "name": "T", "artists": []}]}},
            {"result": {"songs": [{"id": 1, "name": 2, "artists": []}]}},
            {"result": {"songs": [{"id": 1, "name": "T", "artists": "A"}]}},
            {"result": {"songs": [{"id": 1, "name": "T", "artists": ["A"]}]}},
            {"result": {"songs": [{"id": 1, "name": "T", "artists": [{"name": 3}]}]}},
            {
                "result": {
                    "songs": [{"id": 1, "name": "T", "artists": [], "duration": "x"}]
                }
            },
        ]
        for shape in shapes:
            with (
                self.subTest(shape=shape),
                self.assertRaises(lp.ProviderError) as raised,
            ):
                ln.parse_songs(shape)
            self.assertIn("NetEase", str(raised.exception))

    def test_unexpected_lyric_shapes_are_provider_errors(self) -> None:
        shapes: list[object] = [
            [],
            {"lrc": "words"},
            {"lrc": {"lyric": 5}},
            {"nolyric": "yes"},
        ]
        for shape in shapes:
            with self.subTest(shape=shape), self.assertRaises(lp.ProviderError):
                ln.parse_lyric(shape)

    def test_a_parsed_song_carries_its_id_and_hit(self) -> None:
        (parsed,) = ln.parse_songs(search_reply(song(duration_ms=None)))
        self.assertEqual(
            parsed,
            ln.NeteaseSong(
                11, lmatch.Hit("The Keeper Counts", ("Invented Artist",), None)
            ),
        )

    @given(_JSON)
    def test_fuzz_parse_songs(self, value: object) -> None:
        try:
            parsed = ln.parse_songs(value)
        except lp.ProviderError:
            return
        for found in parsed:
            self.assertIsInstance(found.song_id, int)

    @given(_JSON)
    def test_fuzz_parse_lyric(self, value: object) -> None:
        try:
            parsed = ln.parse_lyric(value)
        except lp.ProviderError:
            return
        self.assertTrue(parsed is None or isinstance(parsed, str))
