"""The MusicBrainz client: an album's Bandcamp link, by release search and lookups.

No test here reaches the network: the HTTP transport's ``send`` answers from memory
(`bandcamp_fakes.BandcampServer`), on a fake clock that moves only when the client
sleeps, so the pacing runs in no time. Every id, title, name and host is invented.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

import requests
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import bandcamp_fakes as bf  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_musicbrainz as lmb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_reply as lr  # noqa: E402 - import follows the sys.path bootstrap above

# The profile test_lyrics_genre registers, under the same name and settings, so the
# properties run alike whichever suite loads first: no per-example deadline, which
# measures machine load, and no too_slow check, which a cold text cache trips.
settings.register_profile(
    "album_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("album_properties")

ALBUM = "The Lantern Years"
ARTIST = "Invented Band"
CONTACT = "https://example.com/lyricsmith-contact"
ALBUM_URL = "https://invented-band.bandcamp.com/album/the-lantern-years"
SITE_URL = "https://invented-band.bandcamp.com"
SEARCH = (bf.MB_HOST, bf.SEARCH_PATH)
NO_LINKS = bf.relations_reply()
_RELEASES = (bf.RELEASE_1, bf.RELEASE_2, bf.RELEASE_3, bf.RELEASE_4, bf.RELEASE_5)
#: JSON-like values keyed by the names MusicBrainz's replies use, for the property tests.
_RELEASE_JSON = http_fakes.json_values(
    ("releases", "id", "title", "artist-credit", "name", "artist"),
    max_size=6,
    max_leaves=16,
)
_RELATION_JSON = http_fakes.json_values(
    ("relations", "type", "url", "resource"), max_size=4, max_leaves=12
)


def release_route(
    release_id: str, reply: object = NO_LINKS
) -> tuple[tuple[str, str], object]:
    """The route of *release_id*'s lookup, answering *reply*."""
    return (bf.MB_HOST, bf.release_path(release_id)), reply


def artist_route(reply: object = NO_LINKS) -> tuple[tuple[str, str], object]:
    """The route of `ARTIST_1`'s lookup, answering *reply*."""
    return (bf.MB_HOST, bf.artist_path(bf.ARTIST_1)), reply


class _Case(fixtures.IsolatedCase):
    """A fake clock, and a client paced on it."""

    def setUp(self) -> None:
        """A fresh clock and client for each test."""
        super().setUp()
        self.clock = genius_fakes.FakeClock()
        self.client = lmb.MusicBrainzClient(clock=self.clock, sleeper=self.clock.sleep)

    def server(self, routes: Mapping[tuple[str, str], object]) -> bf.BandcampServer:
        """A server answering *routes*, stamping each request on the fake clock."""
        return bf.BandcampServer(routes, clock=self.clock)

    def find(self, server: object, album: str = ALBUM) -> lmb.BandcampLink | lmb.Miss:
        """`find_bandcamp` for *album* through *server*, with 30 seconds to spare."""
        with http_fakes.transport(server):  # type: ignore[arg-type]
            return self.client.find_bandcamp(album, ARTIST, self.clock.now + 30)


class RouteTest(_Case):
    """The search, then up to three release lookups, then one artist lookup."""

    def test_a_release_link_wins(self) -> None:
        linked = bf.relations_reply(("purchase for download", ALBUM_URL))
        server = self.server(
            dict(
                [
                    (SEARCH, bf.search_reply(bf.release(bf.RELEASE_1, ALBUM, ARTIST))),
                    release_route(bf.RELEASE_1, linked),
                ]
            )
        )
        self.assertEqual(
            self.find(server), lmb.BandcampLink(lmb.LinkKind.ALBUM, ALBUM_URL)
        )
        self.assertEqual(server.params(0)["limit"], ["10"])
        self.assertEqual(server.params(0)["fmt"], ["json"])
        self.assertEqual(server.params(1)["inc"], ["url-rels"])

    def test_a_near_miss_album_is_no_match_after_the_search(self) -> None:
        server = self.server(
            {
                SEARCH: bf.search_reply(
                    bf.release(bf.RELEASE_1, "The Lantern Wards", ARTIST)
                )
            }
        )
        self.assertEqual(self.find(server), lmb.Miss.NO_ALBUM_MATCH)
        self.assertEqual(server.paths(), [bf.SEARCH_PATH])

    def test_matches_with_no_link_anywhere(self) -> None:
        server = self.server(
            dict(
                [
                    (SEARCH, bf.search_reply(bf.release(bf.RELEASE_1, ALBUM, ARTIST))),
                    release_route(bf.RELEASE_1),
                    artist_route(bf.relations_reply(("official homepage", SITE_URL))),
                ]
            )
        )
        self.assertEqual(self.find(server), lmb.Miss.NO_LINK)

    def test_the_artist_route(self) -> None:
        reply = bf.relations_reply(("bandcamp", f"{SITE_URL}/music"))
        server = self.server(
            dict(
                [
                    (SEARCH, bf.search_reply(bf.release(bf.RELEASE_1, ALBUM, ARTIST))),
                    release_route(bf.RELEASE_1),
                    artist_route(reply),
                ]
            )
        )
        site = lmb.BandcampLink(lmb.LinkKind.ARTIST_SITE, SITE_URL)
        self.assertEqual(self.find(server), site)

    def test_an_artist_link_off_bandcamp_is_no_link(self) -> None:
        reply = bf.relations_reply(("bandcamp", "https://example.com/invented-band"))
        server = self.server(
            dict(
                [
                    (SEARCH, bf.search_reply(bf.release(bf.RELEASE_1, ALBUM, ARTIST))),
                    release_route(bf.RELEASE_1),
                    artist_route(reply),
                ]
            )
        )
        self.assertEqual(self.find(server), lmb.Miss.NO_LINK)

    def test_three_lookups_at_most_then_the_artist(self) -> None:
        releases = [bf.release(rid, ALBUM, ARTIST) for rid in _RELEASES]
        routes = dict([release_route(rid) for rid in _RELEASES])
        server = self.server(
            {SEARCH: bf.search_reply(*releases), **routes, **dict([artist_route()])}
        )
        self.assertEqual(self.find(server), lmb.Miss.NO_LINK)
        expected = [bf.release_path(rid) for rid in _RELEASES[:3]]
        self.assertEqual(
            server.paths(), [bf.SEARCH_PATH, *expected, bf.artist_path(bf.ARTIST_1)]
        )

    def test_the_best_match_is_asked_first(self) -> None:
        # A near title listed first: a bracketed edition would tie, its group removed.
        server = self.server(
            dict(
                [
                    (
                        SEARCH,
                        bf.search_reply(
                            bf.release(bf.RELEASE_1, "The Lantern Year", ARTIST),
                            bf.release(bf.RELEASE_2, ALBUM, ARTIST),
                        ),
                    ),
                    release_route(bf.RELEASE_1),
                    release_route(bf.RELEASE_2),
                    artist_route(),
                ]
            )
        )
        self.find(server)
        self.assertEqual(server.paths()[1], bf.release_path(bf.RELEASE_2))
        self.assertEqual(server.paths()[2], bf.release_path(bf.RELEASE_1))


class LinkTest(_Case):
    """Which relation URLs count as an album link."""

    def found(self, *relations: tuple[str, str]) -> lmb.BandcampLink | lmb.Miss:
        """What the client finds when the one release holds *relations*."""
        server = self.server(
            dict(
                [
                    (SEARCH, bf.search_reply(bf.release(bf.RELEASE_1, ALBUM, ARTIST))),
                    release_route(bf.RELEASE_1, bf.relations_reply(*relations)),
                    artist_route(),
                ]
            )
        )
        return self.find(server)

    def test_an_http_link_comes_back_as_https(self) -> None:
        http_url = ALBUM_URL.replace("https:", "http:", 1)
        found = self.found(("free streaming", http_url))
        self.assertEqual(found, lmb.BandcampLink(lmb.LinkKind.ALBUM, ALBUM_URL))

    def test_another_host_is_passed_over(self) -> None:
        found = self.found(
            ("free streaming", "https://example.com/album/the-lantern-years"),
            ("purchase for download", ALBUM_URL),
        )
        self.assertEqual(found, lmb.BandcampLink(lmb.LinkKind.ALBUM, ALBUM_URL))

    def test_a_link_not_to_an_album_is_passed_over(self) -> None:
        self.assertEqual(
            self.found(("free streaming", f"{SITE_URL}/track/x")), lmb.Miss.NO_LINK
        )

    def test_another_relation_type_is_passed_over(self) -> None:
        self.assertEqual(self.found(("discogs", ALBUM_URL)), lmb.Miss.NO_LINK)


class PacingTest(_Case):
    """At most one request a second, and none past the call's deadline."""

    def test_five_requests_a_second_apart(self) -> None:
        releases = [bf.release(rid, ALBUM, ARTIST) for rid in _RELEASES[:3]]
        routes = dict([release_route(rid) for rid in _RELEASES[:3]])
        server = self.server(
            {SEARCH: bf.search_reply(*releases), **routes, **dict([artist_route()])}
        )
        self.find(server)
        self.assertEqual(len(server.times), 5)
        gaps = [
            later - earlier for earlier, later in zip(server.times, server.times[1:])
        ]
        self.assertTrue(
            all(gap >= lmb.MUSICBRAINZ_MIN_INTERVAL_S for gap in gaps), gaps
        )

    def test_a_deadline_already_past_sends_nothing(self) -> None:
        server = self.server({})
        with http_fakes.transport(server), self.assertRaises(lp.ProviderTimeout):
            self.client.find_bandcamp(ALBUM, ARTIST, self.clock.now - 1)
        self.assertEqual(server.requests, [])

    def test_a_pace_ending_past_the_deadline_sends_nothing_more(self) -> None:
        server = self.server(
            dict(
                [
                    (SEARCH, bf.search_reply(bf.release(bf.RELEASE_1, ALBUM, ARTIST))),
                    release_route(bf.RELEASE_1),
                ]
            )
        )
        with http_fakes.transport(server), self.assertRaises(lp.ProviderTimeout):
            self.client.find_bandcamp(ALBUM, ARTIST, self.clock.now + 0.5)
        self.assertEqual(len(server.requests), 1)

    def test_a_503_then_a_200_is_retried_once(self) -> None:
        server = self.server(
            {
                SEARCH: bf.search_reply(
                    bf.release(bf.RELEASE_1, "Something Else", ARTIST)
                )
            }
        )
        refused: list[bool] = []

        def answer(request: requests.PreparedRequest) -> requests.Response:
            if not refused:
                refused.append(True)
                return http_fakes.response(request, 503)
            return server(request)

        self.client.retry = lh.RetryPolicy(clock=self.clock, sleeper=self.clock.sleep)
        self.assertEqual(self.find(answer), lmb.Miss.NO_ALBUM_MATCH)
        self.assertEqual(self.clock.sleeps, [lh.RETRY_WAITS_S[0]])


class UserAgentTest(_Case):
    """The plain User-Agent, or with the contact in parentheses when one is set."""

    def sent(self) -> str:
        """The User-Agent the client's search sent."""
        server = self.server({SEARCH: bf.search_reply()})
        self.find(server)
        return str(server.requests[0].headers["User-Agent"])

    def test_no_contact(self) -> None:
        self.assertEqual(self.sent(), lh.user_agent())

    def test_a_contact(self) -> None:
        self.client.contact = CONTACT
        self.assertEqual(self.sent(), f"{lh.user_agent()} ({CONTACT})")


class ReplyShapeTest(_Case):
    """A reply in another shape is an unexpected reply, and never steers a path."""

    def test_a_release_id_not_a_uuid_sends_no_lookup(self) -> None:
        server = self.server(
            {SEARCH: bf.search_reply(bf.release("../../artist/x", ALBUM, ARTIST))}
        )
        with self.assertRaises(lp.ProviderError) as caught:
            self.find(server)
        self.assertEqual(caught.exception.detail, lr.UNEXPECTED_REPLY)
        self.assertEqual(len(server.requests), 1)

    def test_an_artist_id_not_a_uuid_sends_no_lookup(self) -> None:
        bad = bf.release(bf.RELEASE_1, ALBUM, ARTIST, artist_id="X")
        server = self.server(
            dict([(SEARCH, bf.search_reply(bad)), release_route(bf.RELEASE_1)])
        )
        with self.assertRaises(lp.ProviderError):
            self.find(server)
        self.assertEqual(len(server.requests), 2)

    def test_parse_releases_reads_each_field(self) -> None:
        releases = lmb.parse_releases(
            bf.search_reply(bf.release(bf.RELEASE_1, ALBUM, ARTIST))
        )
        credit = lmb.Credit(bf.ARTIST_1, ARTIST)
        self.assertEqual(releases, (lmb.Release(bf.RELEASE_1, ALBUM, (credit,)),))

    def test_a_release_with_no_credit(self) -> None:
        reply = {"releases": [{"id": bf.RELEASE_1, "title": ALBUM}]}
        self.assertEqual(lmb.parse_releases(reply)[0].credits, ())

    def test_shapes_parse_releases_refuses(self) -> None:
        reply: object
        for reply in (
            [],
            {},
            {"releases": [3]},
            {"releases": [{"id": 3, "title": ALBUM}]},
            {"releases": [{"id": bf.RELEASE_1, "title": ALBUM, "artist-credit": {}}]},
            {"releases": [{"id": bf.RELEASE_1, "title": ALBUM, "artist-credit": [3]}]},
            {
                "releases": [
                    {
                        "id": bf.RELEASE_1,
                        "title": ALBUM,
                        "artist-credit": [{"name": ARTIST}],
                    }
                ]
            },
            {
                "releases": [
                    {
                        "id": bf.RELEASE_1,
                        "title": ALBUM,
                        "artist-credit": [{"name": ARTIST, "artist": {}}],
                    }
                ]
            },
        ):
            with self.subTest(reply=reply), self.assertRaises(lp.ProviderError):
                lmb.parse_releases(reply)

    def test_parse_url_relations(self) -> None:
        reply = {
            "relations": [
                {"type": "member of band", "artist": {"id": bf.ARTIST_1}},
                {"type": "bandcamp", "url": {"resource": SITE_URL}},
            ]
        }
        self.assertEqual(lmb.parse_url_relations(reply), (("bandcamp", SITE_URL),))
        self.assertEqual(lmb.parse_url_relations({}), ())

    def test_shapes_parse_url_relations_refuses(self) -> None:
        reply: object
        for reply in (
            [],
            {"relations": {}},
            {"relations": [3]},
            {"relations": [{"type": 3, "url": {"resource": SITE_URL}}]},
            {"relations": [{"type": "bandcamp", "url": []}]},
            {"relations": [{"type": "bandcamp", "url": {"resource": 3}}]},
        ):
            with self.subTest(reply=reply), self.assertRaises(lp.ProviderError):
                lmb.parse_url_relations(reply)


class UrlRuleTest(fixtures.IsolatedCase):
    """Bandcamp's hosts over https, and an http link's https form."""

    def test_urls_allowed(self) -> None:
        for url in (
            "https://bandcamp.com/",
            "https://x.bandcamp.com/album/y",
            "https://X.Bandcamp.com:443/",
        ):
            with self.subTest(url=url):
                self.assertTrue(lmb.is_bandcamp_url(url))

    def test_urls_refused(self) -> None:
        for url in (
            "http://x.bandcamp.com/",
            "https://bandcamp.com.example.com/",
            "https://notbandcamp.com/",
            "https://@x.bandcamp.com/",
            "https://x.bandcamp.com:8443/",
            "https://x.bandcamp.com:bad/",
            "https://[::1/",
            "ftp://x.bandcamp.com/",
        ):
            with self.subTest(url=url):
                self.assertFalse(lmb.is_bandcamp_url(url))

    def test_bandcamp_https(self) -> None:
        self.assertEqual(
            lmb.bandcamp_https("http://x.bandcamp.com/a"), "https://x.bandcamp.com/a"
        )
        self.assertEqual(
            lmb.bandcamp_https("https://x.bandcamp.com/a"), "https://x.bandcamp.com/a"
        )
        self.assertIsNone(lmb.bandcamp_https("http://example.com/a"))
        self.assertIsNone(lmb.bandcamp_https("ftp://x.bandcamp.com/a"))
        self.assertIsNone(lmb.bandcamp_https("http://[::1/"))

    def test_the_contact_user_agent(self) -> None:
        self.assertEqual(lmb.musicbrainz_user_agent(None), lh.user_agent())
        self.assertEqual(
            lmb.musicbrainz_user_agent(CONTACT), f"{lh.user_agent()} ({CONTACT})"
        )

    def test_release_query(self) -> None:
        self.assertEqual(
            lmb.release_query('A "B" \\', "C"),
            'release:"A \\"B\\" \\\\" AND artist:"C"',
        )


def _phrase(text: str, start: int) -> tuple[str, int]:
    """The quoted phrase opening at *start*, unescaped, and the index past its end quote."""
    assert text[start] == '"'
    out, index = [], start + 1
    while text[index] != '"':
        if text[index] == "\\":
            index += 1
        out.append(text[index])
        index += 1
    return "".join(out), index + 1


class PropertyTest(fixtures.IsolatedCase):
    """The query and the readers never fail but as designed; the rewrite stays allowed."""

    @given(album=st.text(max_size=30), artist=st.text(max_size=30))
    def test_fuzz_release_query(self, album: str, artist: str) -> None:
        query = lmb.release_query(album, artist)
        prefix, middle = "release:", " AND artist:"
        self.assertTrue(query.startswith(prefix))
        read_album, after = _phrase(query, len(prefix))
        self.assertEqual(query[after : after + len(middle)], middle)
        read_artist, end = _phrase(query, after + len(middle))
        self.assertEqual((read_album, read_artist, end), (album, artist, len(query)))

    @given(value=_RELEASE_JSON)
    def test_fuzz_parse_releases(self, value: object) -> None:
        try:
            parsed = lmb.parse_releases(value)
        except lp.ProviderError:
            return
        for found in parsed:
            self.assertIsInstance(found.release_id, str)

    @given(value=_RELATION_JSON)
    def test_fuzz_parse_url_relations(self, value: object) -> None:
        try:
            parsed = lmb.parse_url_relations(value)
        except lp.ProviderError:
            return
        for kind, url in parsed:
            self.assertIsInstance(kind + url, str)

    @given(
        url=st.text(max_size=40)
        | st.builds(lambda s: f"http://{s}.bandcamp.com/", st.text(max_size=10))
    )
    def test_fuzz_bandcamp_https(self, url: str) -> None:
        rewritten = lmb.bandcamp_https(url)
        if rewritten is not None:
            self.assertTrue(lmb.is_bandcamp_url(rewritten))

    @given(url=st.text(max_size=40))
    def test_fuzz_is_bandcamp_url(self, url: str) -> None:
        self.assertIsInstance(lmb.is_bandcamp_url(url), bool)
