"""The Bandcamp step: an album's page, found through MusicBrainz, asked once per album.

No test here reaches the network: the HTTP transport's ``send`` answers from memory
(`bandcamp_fakes.BandcampServer`), on a fake clock that moves only when the MusicBrainz
client sleeps, so its pacing runs in no time. Every id, title, name, host and lyric is
invented.
"""

from __future__ import annotations

import dataclasses
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import bandcamp_fakes as bf  # noqa: E402 - import follows the sys.path bootstrap above
import bandcamp_pages as bp  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_bandcamp as lb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_musicbrainz as lmb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above

ALBUM = "The Lantern Years"
ARTIST = "Invented Band"
CONTACT = "https://example.com/lyricsmith-contact"
HOST = "invented-band.bandcamp.com"
SITE = f"https://{HOST}"
ALBUM_PATH = "/album/the-lantern-years"
OTHER_PATH = "/album/another-record"
ALBUM_URL = f"{SITE}{ALBUM_PATH}"
FORD, LIGHT = "the ford runs cold\nunder the lantern", "a light across the water"
KEY = lm.ProviderKey(lm.Source.BANDCAMP, lm.LyricsKind.UNSYNCED)
#: How long a test lets a call wait on a lock another call holds.
SHORT_S = 0.2
TRACKS = (
    bp.track("The Ford", lyrics=FORD),
    bp.track("Lantern Light", duration="P00H04M02S", lyrics=LIGHT),
    bp.track("Quiet Hour", lyrics=None),
)
PAGE = bp.album_page(TRACKS)
SEARCH = (
    (bf.MB_HOST, bf.SEARCH_PATH),
    bf.search_reply(bf.release(bf.RELEASE_1, ALBUM, ARTIST)),
)
LINKED = (
    (bf.MB_HOST, bf.release_path(bf.RELEASE_1)),
    bf.relations_reply(("purchase for download", ALBUM_URL)),
)
UNLINKED = ((bf.MB_HOST, bf.release_path(bf.RELEASE_1)), bf.relations_reply())
ARTIST_SITE = (
    (bf.MB_HOST, bf.artist_path(bf.ARTIST_1)),
    bf.relations_reply(("bandcamp", f"{SITE}/")),
)
NO_SITE = ((bf.MB_HOST, bf.artist_path(bf.ARTIST_1)), bf.relations_reply())


def query(
    title: str, duration_s: float | None = None, album: str | None = ALBUM
) -> lm.TrackQuery:
    """A track of *album* by `ARTIST`, titled *title*."""
    return lm.TrackQuery(ARTIST, title, album=album, duration_s=duration_s)


QUERY = query("The Ford", 201.0)


def routes(
    *pairs: tuple[tuple[str, str], object], page: object = PAGE
) -> dict[tuple[str, str], object]:
    """The album route answering *page*, and the MusicBrainz *pairs*."""
    return {(HOST, ALBUM_PATH): page, **dict(pairs)}


class _Case(fixtures.IsolatedCase):
    """A fake clock, and a provider whose MusicBrainz client is paced on it."""

    def setUp(self) -> None:
        """A fresh clock and provider for each test."""
        super().setUp()
        self.clock = genius_fakes.FakeClock()
        self.provider = self.build()

    def build(
        self, contact: str | None = None, retry: lh.RetryPolicy | None = None
    ) -> lb.BandcampProvider:
        """A provider on the fake clock, its client naming *contact*."""
        client = lmb.MusicBrainzClient(
            contact, clock=self.clock, sleeper=self.clock.sleep, retry=retry
        )
        return lb.BandcampProvider(client, clock=self.clock)

    def server(self, table: Mapping[tuple[str, str], object]) -> bf.BandcampServer:
        """A server answering *table*, stamping each request on the fake clock."""
        return bf.BandcampServer(table, clock=self.clock)

    def ask(
        self, server: bf.BandcampServer, *queries: lm.TrackQuery
    ) -> list[str | None]:
        """The provider's answer for each of *queries* in turn, through *server*."""
        with http_fakes.transport(server):
            return [self.provider(q) for q in queries]

    def raised(self, server: bf.BandcampServer, asked: lm.TrackQuery) -> Exception:
        """What the provider raised for *asked*; failing the test if it answered."""
        with http_fakes.transport(server), self.assertRaises(Exception) as caught:
            self.provider(asked)
        return caught.exception


class AlbumCacheTest(_Case):
    """One lookup serves every track of the album."""

    def test_three_tracks_send_one_search_one_lookup_and_one_page(self) -> None:
        server = self.server(routes(SEARCH, LINKED))
        answers = self.ask(
            server, QUERY, query("Lantern Light", 242.0), query("The Ford")
        )
        self.assertEqual(answers, [FORD, LIGHT, FORD])
        self.assertEqual(
            server.paths(), [bf.SEARCH_PATH, bf.release_path(bf.RELEASE_1), ALBUM_PATH]
        )

    def test_a_track_the_page_lacks_or_one_without_lyrics_is_none(self) -> None:
        server = self.server(routes(SEARCH, LINKED))
        self.assertEqual(
            self.ask(server, query("Elsewhere"), query("Quiet Hour")), [None, None]
        )

    def test_the_stripped_retry_is_answered_from_the_cache(self) -> None:
        server = self.server(routes(SEARCH, LINKED))
        live = query("The Ford (Live)", 201.0)
        stripped = dataclasses.replace(QUERY, stripped=True)
        self.assertEqual(self.ask(server, live, stripped)[1], FORD)
        self.assertEqual(len(server.requests), 3)

    def test_a_shared_failure_is_raised_again_with_no_request(self) -> None:
        server = self.server(routes(SEARCH, LINKED, page=503))
        first = self.raised(server, QUERY)
        self.assertIsInstance(first, lp.HttpStatusError)
        sent = len(server.requests)
        self.assertIs(self.raised(server, query("Lantern Light")), first)
        self.assertEqual(len(server.requests), sent)

    def test_no_link_is_none_for_every_track_in_one_round(self) -> None:
        server = self.server(routes(SEARCH, UNLINKED, NO_SITE))
        self.assertEqual(self.ask(server, QUERY, query("Lantern Light")), [None, None])
        self.assertEqual(len(server.requests), 3)
        self.assertNotIn(ALBUM_PATH, server.paths())

    def test_no_album_match_is_none_for_every_track_in_one_round(self) -> None:
        other = bf.search_reply(bf.release(bf.RELEASE_1, "Unrelated Record", ARTIST))
        server = self.server(routes((SEARCH[0], other)))
        self.assertEqual(self.ask(server, QUERY, query("Lantern Light")), [None, None])
        self.assertEqual(server.paths(), [bf.SEARCH_PATH])

    def test_a_page_with_no_track_list_is_an_error_kept_for_the_album(self) -> None:
        for page in (bp.album_page(TRACKS, ld=False), bp.album_page([])):
            with self.subTest(page=page[:60]):
                self.provider = self.build()
                server = self.server(routes(SEARCH, LINKED, page=page))
                first = self.raised(server, QUERY)
                self.assertIsInstance(first, lp.ProviderError)
                self.assertEqual(lp.error_detail(first), lb.NO_TRACK_LIST)
                self.assertIs(self.raised(server, query("Lantern Light")), first)
                self.assertEqual(len(server.requests), 3)

    def test_a_redirect_off_bandcamp_is_refused(self) -> None:
        moved = bf.Redirect("https://example.com/")
        server = self.server(routes(SEARCH, LINKED, page=moved))
        self.assertEqual(
            lp.error_detail(self.raised(server, QUERY)), lh.REDIRECT_REFUSED
        )
        self.assertEqual(server.paths()[-1], ALBUM_PATH)


class AsksTest(_Case):
    """A track without an album tag or an artist is not asked about."""

    def test_no_album_or_no_artist_is_not_asked(self) -> None:
        for asked in (
            query("The Ford", album=None),
            query("The Ford", album=" "),
            lm.TrackQuery(" ", "The Ford", album=ALBUM),
        ):
            with self.subTest(query=asked):
                self.assertFalse(self.provider.asks(asked))
                server = self.server({})
                self.assertEqual(self.ask(server, asked), [None])
                with http_fakes.transport(server):
                    result = lf.fetch_lyrics(
                        asked,
                        lm.SidecarState(None),
                        {KEY: self.provider},
                        deadline_s=30.0,
                    )
                self.assertEqual((result.attempts, server.requests), ((), []))

    def test_a_tagged_track_is_asked(self) -> None:
        self.assertTrue(self.provider.asks(QUERY))


class RefusalTest(_Case):
    """A MusicBrainz refusal is the step's error, recorded as a refusal."""

    def test_a_429_on_every_attempt_is_a_retried_error_recorded_as_refused(
        self,
    ) -> None:
        self.provider = self.build(retry=lh.RetryPolicy(self.clock, self.clock.sleep))
        server = self.server({SEARCH[0]: 429})
        error = self.raised(server, QUERY)
        self.assertIsInstance(error, lp.RetriedError)
        self.assertTrue(lp.error_detail(error).startswith("HTTP 429"))
        self.provider = self.build(retry=lh.RetryPolicy(self.clock, self.clock.sleep))
        with http_fakes.transport(server):
            result = lf.fetch_lyrics(
                QUERY, lm.SidecarState(None), {KEY: self.provider}, deadline_s=30.0
            )
        (tried,) = result.attempts
        self.assertIn(tried.outcome, lm.REFUSED_OUTCOMES)
        self.assertIs(tried.outcome, lm.AttemptOutcome.ERROR)
        self.assertTrue(tried.detail.startswith("HTTP 429"))


class ArtistRouteTest(_Case):
    """No release link: the artist's Bandcamp site, its /music page, then the album."""

    def _server(
        self, music: object, *extra: tuple[tuple[str, str], object]
    ) -> bf.BandcampServer:
        """The artist route, the site's /music page answering *music*."""
        return self.server(
            routes(SEARCH, UNLINKED, ARTIST_SITE, ((HOST, "/music"), music), *extra)
        )

    def _bandcamp_paths(self, server: bf.BandcampServer) -> list[str]:
        """The paths of the requests sent to Bandcamp, in order."""
        return [p for p in server.paths() if not p.startswith("/ws/2/")]

    def test_the_album_among_the_music_entries_is_asked(self) -> None:
        music = bp.music_page([(OTHER_PATH, "Another Record"), (ALBUM_PATH, ALBUM)])
        server = self._server(music)
        self.assertEqual(self.ask(server, QUERY, query("Lantern Light")), [FORD, LIGHT])
        self.assertEqual(self._bandcamp_paths(server), ["/music", ALBUM_PATH])

    def test_of_two_equal_entries_the_first_is_asked(self) -> None:
        deluxe = "/album/the-lantern-years-deluxe"
        music = bp.music_page([(ALBUM_PATH, ALBUM), (deluxe, f"{ALBUM} (Deluxe)")])
        self.assertEqual(self.ask(self._server(music), QUERY), [FORD])

    def test_no_entry_matching_is_none(self) -> None:
        server = self._server(bp.music_page([(OTHER_PATH, "Another Record")]))
        self.assertEqual(self.ask(server, QUERY, query("Lantern Light")), [None, None])
        self.assertEqual(self._bandcamp_paths(server), ["/music"])

    def test_a_music_page_redirecting_to_the_album_serves_it(self) -> None:
        server = self._server(bf.Redirect(ALBUM_URL))
        self.assertEqual(self.ask(server, QUERY), [FORD])
        # One page asked: its redirect hop, then the album page, not asked again.
        self.assertEqual(self._bandcamp_paths(server), ["/music", ALBUM_PATH])

    def test_a_music_page_redirecting_to_another_album_is_none(self) -> None:
        other = bp.album_page(TRACKS, name="Another Record")
        server = self._server(
            bf.Redirect(f"{SITE}{OTHER_PATH}"), ((HOST, OTHER_PATH), other)
        )
        self.assertEqual(self.ask(server, QUERY, query("Lantern Light")), [None, None])
        self.assertEqual(self._bandcamp_paths(server), ["/music", OTHER_PATH])

    def test_a_redirect_setting_a_cookie_sends_none_on_the_next_hop(self) -> None:
        moved = bf.Redirect(ALBUM_URL, 302, set_cookie="session=x")
        server = self._server(moved)
        self.assertEqual(self.ask(server, QUERY), [FORD])
        self.assertNotIn("Cookie", server.requests[-1].headers)


class HeldServer(bf.BandcampServer):
    """A server holding its first request until released, then answering as routed."""

    def __init__(
        self, table: Mapping[tuple[str, str], object], clock: genius_fakes.FakeClock
    ) -> None:
        """Answer *table* on *clock*, the first request held."""
        super().__init__(table, clock)
        self.arrived, self.released = threading.Event(), threading.Event()

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """Hold the first request until released; answer every request as routed."""
        if not self.arrived.is_set():
            self.arrived.set()
            self.released.wait(timeout=fixtures.HANG_LIMIT_S)
        return super().__call__(request)


class DeadlineAndLockTest(_Case):
    """The step's own bound, and its lock under a call the watchdog abandoned."""

    def test_the_step_sets_its_own_bound(self) -> None:
        self.assertEqual(self.provider.step_deadline_s(), lb.BANDCAMP_STEP_DEADLINE_S)
        self.assertEqual(
            lf._step_deadline_s(self.provider, 30.0), lb.BANDCAMP_STEP_DEADLINE_S
        )

    def test_an_abandoned_call_keeps_the_lock_and_still_fills_the_cache(self) -> None:
        server = HeldServer(routes(SEARCH, LINKED), self.clock)
        # Only a backstop: the abandoned call is released inside the transport below.
        self.addCleanup(server.released.set)
        with http_fakes.transport(server):
            with self.assertRaises(lp.ProviderTimeout):
                lf.run_with_deadline(lambda: self.provider(QUERY), SHORT_S)
            self.assertTrue(server.arrived.wait(fixtures.HANG_LIMIT_S))
            with mock.patch.object(lb, "BANDCAMP_STEP_DEADLINE_S", SHORT_S):
                with self.assertRaises(lp.ProviderTimeout):
                    self.provider(query("Lantern Light"))
            self.assertEqual(server.requests, [])
            server.released.set()
            self.assertEqual(self.provider(query("Lantern Light", 242.0)), LIGHT)
        self.assertEqual(len(server.requests), 3)
        self.assertEqual(len(self.provider._albums), 1)


class TimeoutTest(_Case):
    """A lookup that runs out of time is that call's timeout, never kept for the album."""

    def test_a_timed_out_lookup_is_asked_again_by_the_next_track(self) -> None:
        server = self.server(routes(SEARCH, LINKED))
        # Past the search, the paced release lookup could not start within the bound.
        with mock.patch.object(lb, "BANDCAMP_STEP_DEADLINE_S", SHORT_S):
            self.assertIsInstance(self.raised(server, QUERY), lp.ProviderTimeout)
        self.assertEqual(
            (server.paths(), self.provider._albums), ([bf.SEARCH_PATH], {})
        )
        self.assertEqual(self.ask(server, query("Lantern Light", 242.0)), [LIGHT])
        self.assertEqual(
            server.paths()[1:],
            [bf.SEARCH_PATH, bf.release_path(bf.RELEASE_1), ALBUM_PATH],
        )


class UserAgentTest(_Case):
    """The contact goes to MusicBrainz only; Bandcamp sees the plain User-Agent."""

    def test_only_musicbrainz_requests_carry_the_contact(self) -> None:
        self.provider = self.build(contact=CONTACT)
        server = self.server(routes(SEARCH, LINKED))
        self.assertEqual(self.ask(server, QUERY), [FORD])
        sent_as = [r.headers["User-Agent"] for r in server.requests]
        self.assertEqual(sent_as[:2], [f"{lh.user_agent()} ({CONTACT})"] * 2)
        self.assertEqual(sent_as[2], lh.user_agent())


class ChainAndTableTest(fixtures.IsolatedCase):
    """The Bandcamp step's place in the chain, and in the real table when turned on."""

    def test_the_bandcamp_step_comes_right_after_neteases_plain_step(self) -> None:
        netease = lf.CHAIN.index(
            lm.ProviderKey(lm.Source.NETEASE, lm.LyricsKind.UNSYNCED)
        )
        self.assertEqual(lf.CHAIN[netease + 1], KEY)
        self.assertEqual(lf.CHAIN.count(KEY), 1)

    def test_the_default_table_holds_no_bandcamp_step(self) -> None:
        self.assertNotIn(KEY, ltable.build_provider_table(None))

    def test_a_table_with_bandcamp_on_holds_it_in_chain_order(self) -> None:
        settings = ltable.ProviderSettings(bandcamp=True, musicbrainz_contact=CONTACT)
        table = ltable.build_provider_table(None, settings=settings)
        step = table[KEY]
        self.assertIsInstance(step, lb.BandcampProvider)
        assert isinstance(step, lb.BandcampProvider)
        self.assertEqual(step.musicbrainz.contact, CONTACT)
        unbuilt = {lm.Source.GENIUS, lm.Source.YOUTUBE_MUSIC, lm.Source.TEKSTOWO}
        self.assertEqual(list(table), [k for k in lf.CHAIN if k.source not in unbuilt])

    def test_a_track_holding_untimed_lyrics_never_asks_bandcamp(self) -> None:
        recording = fixtures.empty()
        held = lm.SidecarState(lm.PlainLyrics(fixtures.ORCHARD_PLAIN))
        result = lf.fetch_lyrics(QUERY, held, {KEY: recording}, deadline_s=30.0)
        self.assertEqual(recording.calls, [])
        self.assertIs(result.source, lm.Source.SIDECAR)
