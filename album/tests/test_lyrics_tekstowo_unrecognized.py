"""Tekstowo's unrecognized pages: each one that track's error, a stop only after a run of them.

One page not shaped as expected is weak evidence of a block or a change to the site, so
it fails only its track; `lt.UNRECOGNIZED_STOP_RUN` of one kind in a row stop Tekstowo
for the run and write the cool-down, and a page of that kind read as expected starts its
count again. An instrumental's page, which has no lyrics block, is nothing
found. Every request is answered by `tekstowo_fakes.TekstowoServer` through
`http_fakes.transport`, so only the socket is never reached; every page is invented.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tekstowo as lt  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_fakes as tf  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_pages as tp  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import QUERY, FakeClock  # noqa: E402 - import follows the sys.path bootstrap above

HOST = lt.TEKSTOWO_HOST
SEARCH = tf.search_path(QUERY.artist, QUERY.title)
SEARCH_URL = lt.search_url(QUERY.artist, QUERY.title)
KEY = lm.ProviderKey(lm.Source.TEKSTOWO, lm.LyricsKind.UNSYNCED)
#: The original's row, as the search lists it.
ORIGINAL = (tp.HREF, f"{QUERY.artist} - {QUERY.title}")
NO_SIDECAR = lm.SidecarState(None)
#: A page the readers below recognize, and one they do not.
KNOWN, ODD = "<p>known</p>", "<p>odd</p>"
ROOM_S = 100.0


def _known_only(text: str) -> str | None:
    """Recognize `KNOWN` alone."""
    return text if text == KNOWN else None


class _Case(fixtures.IsolatedCase):
    """A fake clock, and a server answering the search path from a list of pages."""

    def setUp(self) -> None:
        """Start a fresh fake clock."""
        super().setUp()
        self.clock = FakeClock()

    def serve(self, routes: Mapping[tuple[str, str], object]) -> tf.TekstowoServer:
        """Install a server answering *routes*; build a client on it."""
        server = tf.TekstowoServer(routes, self.clock)
        self.enterContext(http_fakes.transport(server))
        self.client = tf.client(self.tmp, self.clock)
        return server

    def get(self) -> str:
        """One get of the search page, recognizing `KNOWN` alone."""
        return self.client.get(
            SEARCH_URL,
            self.clock() + ROOM_S,
            read=_known_only,
            unrecognized=lt.SONG_NOT_RECOGNIZED,
        )


class CountTest(_Case):
    """Each unrecognized page fails its track; only a run of them stops."""

    def test_the_run_that_stops_is_three(self) -> None:
        self.assertEqual(lt.UNRECOGNIZED_STOP_RUN, 3)

    def test_one_unrecognized_page_is_an_error_and_no_stop(self) -> None:
        self.serve({(HOST, SEARCH): ODD})
        with self.assertRaises(lt.TekstowoUnrecognized) as raised:
            self.get()
        self.assertNotIsInstance(raised.exception, lt.TekstowoStopped)
        self.assertIsInstance(raised.exception, lp.ProviderError)
        self.assertEqual(raised.exception.detail, lt.SONG_NOT_RECOGNIZED)
        self.assertIsNone(self.client.stop)
        self.assertFalse(tf.state_path(self.tmp).exists())

    def test_a_run_of_them_stops_for_the_run_and_cools_down(self) -> None:
        server = self.serve({(HOST, SEARCH): ODD})
        for _ in range(lt.UNRECOGNIZED_STOP_RUN - 1):
            with self.assertRaises(lt.TekstowoUnrecognized):
                self.get()
        with self.assertRaises(lt.TekstowoStopped) as raised:
            self.get()
        self.assertEqual(raised.exception.detail, lt.SONG_NOT_RECOGNIZED)
        assert self.client.stop is not None
        self.assertEqual(self.client.stop.what, lt.SONG_NOT_RECOGNIZED)
        self.assertTrue(tf.state_path(self.tmp).exists())
        sent = len(server.requests)
        with self.assertRaises(lt.TekstowoStopped):
            self.get()
        self.assertEqual(len(server.requests), sent)

    def test_a_recognized_page_starts_the_count_again(self) -> None:
        server = self.serve({(HOST, SEARCH): ODD})
        for page in (ODD, ODD, KNOWN, ODD, ODD):
            server.routes[(HOST, SEARCH)] = page
            if page == KNOWN:
                self.assertEqual(self.get(), KNOWN)
            else:
                with self.assertRaises(lt.TekstowoUnrecognized):
                    self.get()
        self.assertIsNone(self.client.stop)

    def test_each_kind_of_page_is_counted_apart(self) -> None:
        self.serve({(HOST, SEARCH): ODD})
        self.client.unrecognized_runs[lt.SEARCH_NOT_RECOGNIZED] = 1
        for _ in range(lt.UNRECOGNIZED_STOP_RUN - 1):
            with self.assertRaises(lt.TekstowoUnrecognized):
                self.get()
        self.assertIsNone(self.client.stop)

    def test_a_challenge_page_still_stops_at_once(self) -> None:
        self.serve({(HOST, SEARCH): "<div class='cf-chl-box'></div>"})
        with self.assertRaises(lt.TekstowoStopped) as raised:
            self.get()
        self.assertEqual(raised.exception.detail, lt.CHALLENGE)


class ChainTest(_Case):
    """In the chain: an unrecognized page is that track's error; the next is still asked."""

    def setUp(self) -> None:
        """A table with Tekstowo alone answering; every other step finds nothing."""
        super().setUp()
        self.provider: lt.TekstowoProvider | None = None

    def fetch(self, routes: Mapping[tuple[str, str], object]) -> lm.FetchResult:
        """Serve *routes* (once), then run the chain for the query over the table."""
        provider = self.provider
        if provider is None:
            self.serve(routes)
            provider = self.provider = lt.TekstowoProvider(self.client)
        table: lm.ProviderTable = {
            key: (provider if key == KEY else fixtures.empty()) for key in lf.CHAIN
        }
        return lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=30.0)

    def _tekstowo(self, result: lm.FetchResult) -> list[lm.ProviderAttempt]:
        """The Tekstowo attempts of *result*."""
        return [a for a in result.attempts if a.key == KEY]

    def test_an_unrecognized_song_page_is_that_tracks_error(self) -> None:
        routes = {
            (HOST, SEARCH): tp.search_page(ORIGINAL),
            (HOST, tp.HREF): tp.song_page(song_text=False),
        }
        first = self.fetch(routes)
        self.assertEqual(
            self._tekstowo(first),
            [
                lm.ProviderAttempt(
                    KEY,
                    False,
                    lm.AttemptOutcome.ERROR,
                    "song page not recognized",
                    lrf.RefusalFacts(lrf.Refusal.UNRECOGNIZED),
                )
            ],
        )
        second = self.fetch(routes)
        self.assertEqual(len(self._tekstowo(second)), 1)
        self.assertIsNone(self.provider and self.provider.provider_stop())

    def test_song_pages_alone_stop_it_though_each_search_page_reads(self) -> None:
        routes = {
            (HOST, SEARCH): tp.search_page(ORIGINAL),
            (HOST, tp.HREF): tp.song_page(song_text=False),
        }
        for _ in range(lt.UNRECOGNIZED_STOP_RUN):
            last = self.fetch(routes)
        [attempt] = self._tekstowo(last)
        self.assertEqual(attempt.detail, lt.SONG_NOT_RECOGNIZED)
        assert self.provider is not None
        stop = self.provider.provider_stop()
        assert stop is not None
        self.assertEqual(stop.what, lt.SONG_NOT_RECOGNIZED)
        self.assertNotIn(KEY, [a.key for a in self.fetch(routes).attempts])

    def test_an_instrumentals_page_is_nothing_found(self) -> None:
        routes = {
            (HOST, SEARCH): tp.search_page(ORIGINAL),
            (HOST, tp.HREF): tp.song_page(song_text=False, instrumental=True),
        }
        for _ in range(lt.UNRECOGNIZED_STOP_RUN):
            result = self.fetch(routes)
            [attempt] = self._tekstowo(result)
            self.assertEqual(attempt.outcome, lm.AttemptOutcome.EMPTY)
        assert self.provider is not None
        self.assertIsNone(self.provider.provider_stop())
