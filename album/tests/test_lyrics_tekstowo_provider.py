"""The Tekstowo step: the search, the matched song's page, and its place last in the chain.

Every request is answered by `tekstowo_fakes.TekstowoServer` through
`http_fakes.transport`, so the real client, session and adapter run and only the socket
is never reached. The clock is a `FakeClock`, so the 5-second pace takes no time, and
each cool-down file is written under the test's own temporary directory. Every page,
title, artist and lyric is invented (`tekstowo_pages`).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tekstowo as lt  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_fakes as tf  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_pages as tp  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import QUERY, FakeClock  # noqa: E402 - import follows the sys.path bootstrap above

TEKSTOWO = lm.Source.TEKSTOWO
KEY = lm.ProviderKey(TEKSTOWO, lm.LyricsKind.UNSYNCED)
GENIUS_KEY = lm.ProviderKey(lm.Source.GENIUS, lm.LyricsKind.UNSYNCED)
HOST = lt.TEKSTOWO_HOST
SEARCH = tf.search_path(QUERY.artist, QUERY.title)
#: The original's row, as the search lists it.
ORIGINAL = (tp.HREF, f"{QUERY.artist} - {QUERY.title}")
#: Another track of the same album, asked after the first.
OTHER = lm.TrackQuery(QUERY.artist, "A Second Invented Song")
NO_SIDECAR = lm.SidecarState(None)
#: The lyrics a default song page's ``#songText`` holds, as the step answers them.
LYRICS = "\n".join(tp.LINES)


def _found(*entries: tuple[str, str]) -> dict[tuple[str, str], object]:
    """Routes: the search listing *entries* (the original alone by default), its page."""
    listed = entries or (ORIGINAL,)
    return {(HOST, SEARCH): tp.search_page(*listed), (HOST, tp.HREF): tp.song_page()}


class _ProviderCase(fixtures.IsolatedCase):
    """A fake clock, and the provider asking a fake Tekstowo through the transport."""

    def setUp(self) -> None:
        """Start a fresh fake clock."""
        super().setUp()
        self.clock = FakeClock()

    def serve(self, routes: Mapping[tuple[str, str], object]) -> tf.TekstowoServer:
        """Install a server answering *routes*; build the provider on it."""
        server = tf.TekstowoServer(routes, self.clock)
        self.enterContext(http_fakes.transport(server))
        self.provider = lt.TekstowoProvider(tf.client(self.tmp, self.clock))
        return server


class AnswerTest(_ProviderCase):
    """The matched song's original lines, after two paced requests."""

    def test_the_original_lines_with_no_translation_or_caption(self) -> None:
        server = self.serve(_found())
        text = self.provider(QUERY)
        self.assertEqual(text, LYRICS)
        assert text is not None
        self.assertNotIn(tp.TRANSLATION[0], text)
        self.assertNotIn("Tekst piosenki", text)
        self.assertEqual(server.paths(), [SEARCH, tp.HREF])
        self.assertEqual(server.times[1] - server.times[0], lt.TEKSTOWO_MIN_INTERVAL_S)
        self.assertEqual(lt.TEKSTOWO_MIN_INTERVAL_S, 5.0)

    def test_its_deadline_is_the_steps_own(self) -> None:
        self.serve({})
        self.assertEqual(self.provider.step_deadline_s(), lt.TEKSTOWO_STEP_DEADLINE_S)


class MatcherTest(_ProviderCase):
    """Only a listed song naming the track's own title and artist is opened."""

    def test_other_artists_listed_first_are_passed_over(self) -> None:
        cover = ("/cover-band/the-keeper-counts", f"Cover Band - {QUERY.title}")
        same_title = (
            "/someone-else/the-keeper-counts",
            f"Someone Else - {QUERY.title}",
        )
        server = self.serve(_found(cover, same_title, ORIGINAL))
        self.assertEqual(self.provider(QUERY), LYRICS)
        self.assertEqual(server.paths(), [SEARCH, tp.HREF])

    def test_an_added_version_word_is_refused(self) -> None:
        live = (tp.HREF, f"{QUERY.artist} - {QUERY.title} (Live)")
        server = self.serve(_found(live))
        self.assertIsNone(self.provider(QUERY))
        self.assertEqual(server.paths(), [SEARCH])

    def test_none_matching_is_nothing_after_one_request(self) -> None:
        other = ("/other-invented/other-song", "Other Invented - Other Song")
        server = self.serve(_found(other))
        self.assertIsNone(self.provider(QUERY))
        self.assertEqual(len(server.requests), 1)


class PageCheckTest(_ProviderCase):
    """The song page's own JSON-LD must name the track too."""

    def check(self, page: str) -> None:
        """A listed original whose page is *page* is nothing found, with no stop."""
        routes = _found()
        routes[(HOST, tp.HREF)] = page
        server = self.serve(routes)
        self.assertIsNone(self.provider(QUERY))
        self.assertEqual(len(server.requests), 2)
        self.assertIsNone(self.provider.provider_stop())

    def test_a_page_naming_another_artist(self) -> None:
        self.check(tp.song_page(artist="Other Invented"))

    def test_a_page_naming_another_title(self) -> None:
        self.check(tp.song_page(name="Another Invented Song"))

    def test_a_page_with_no_json_ld(self) -> None:
        self.check(tp.song_page(json_ld=False))

    def test_a_blank_inner_text(self) -> None:
        self.check(tp.song_page(lines=()))


class StopTest(_ProviderCase):
    """The last page of a run not recognized stops the step for the run, not asked again."""

    def check_stopped(
        self, routes: Mapping[tuple[str, str], object], what: str
    ) -> None:
        """Calling over *routes* stops with *what*; the provider then reports the stop.

        The client has already met one page fewer than a stop's run of them.
        """
        self.serve(routes)
        self.provider.client.unrecognized_runs[what] = lt.UNRECOGNIZED_STOP_RUN - 1
        with self.assertRaises(lt.TekstowoStopped) as raised:
            self.provider(QUERY)
        self.assertEqual(raised.exception.detail, what)
        self.assertFalse(self.provider.asks(QUERY))
        stop = self.provider.provider_stop()
        self.assertIsNotNone(stop)
        self.assertIs(stop, self.provider.client.stop)
        assert stop is not None
        self.assertEqual(stop.what, what)
        self.assertEqual(stop.until, tf.NOW + lyc.COOL_DOWN)

    def test_a_search_page_without_the_form(self) -> None:
        routes = {(HOST, SEARCH): tp.search_page(ORIGINAL, form=False)}
        self.check_stopped(routes, lt.SEARCH_NOT_RECOGNIZED)

    def test_a_song_page_without_song_text(self) -> None:
        routes = _found()
        routes[(HOST, tp.HREF)] = tp.song_page(song_text=False)
        self.check_stopped(routes, lt.SONG_NOT_RECOGNIZED)

    def test_a_running_step_reports_no_stop_and_is_asked(self) -> None:
        self.serve({})
        self.assertTrue(self.provider.asks(QUERY))
        self.assertIsNone(self.provider.provider_stop())


class NoArtistTest(_ProviderCase):
    """A query with no artist is not asked, and calling it anyway sends nothing."""

    def test_no_artist_tag_sends_nothing(self) -> None:
        server = self.serve(_found())
        query = lm.TrackQuery("", QUERY.title, from_filename=True)
        self.assertFalse(self.provider.asks(query))
        self.assertIsNone(self.provider(query))
        self.assertEqual(server.requests, [])


class ChainTest(fixtures.IsolatedCase):
    """The step asked last, after Genius, from the real table with Tekstowo turned on."""

    def setUp(self) -> None:
        """A fake clock, and the cool-down file's path under the test's directory."""
        super().setUp()
        self.clock = FakeClock()
        self.state = tf.state_path(self.tmp)

    def serve(self, routes: Mapping[tuple[str, str], object]) -> tf.TekstowoServer:
        """Install a server answering *routes*; build the real table, every other step empty."""
        server = tf.TekstowoServer(routes, self.clock)
        self.enterContext(http_fakes.transport(server))
        settings = ltable.ProviderSettings(tekstowo=lyc.CoolingSetup(self.state))
        built = ltable.build_provider_table(
            None, clock=self.clock, sleeper=self.clock.sleep, settings=settings
        )
        step = built[KEY]
        assert isinstance(step, lt.TekstowoProvider)
        self.step = step
        self.table: lm.ProviderTable = {
            key: (built[key] if key == KEY else fixtures.empty()) for key in lf.CHAIN
        }
        return server

    def fetch(self, query: lm.TrackQuery = QUERY) -> lm.FetchResult:
        """Run the chain for *query* over the table."""
        return lf.fetch_lyrics(query, NO_SIDECAR, self.table, deadline_s=30.0)

    def test_asked_last_after_genius(self) -> None:
        self.serve(_found())
        result = self.fetch()
        self.assertEqual(lf.CHAIN[-1], KEY)
        self.assertEqual(lf.CHAIN[-2], GENIUS_KEY)
        self.assertEqual([a.key for a in result.attempts], list(lf.CHAIN))
        self.assertEqual(result.lyrics, lm.PlainLyrics(LYRICS))
        self.assertIs(result.source, TEKSTOWO)
        self.assertEqual(
            (result.attempts[-1].outcome, result.attempts[-1].detail),
            (lm.AttemptOutcome.ANSWERED, lf.DETAIL_ACCEPTED),
        )

    def test_the_stripped_title_retry_searches_again_paced(self) -> None:
        title = f"{QUERY.title} (Live)"
        live = tf.search_path(QUERY.artist, title)
        routes = _found()
        routes[(HOST, live)] = tp.search_page()
        server = self.serve(routes)
        result = self.fetch(lm.TrackQuery(QUERY.artist, title))
        self.assertEqual(server.paths(), [live, SEARCH, tp.HREF])
        gaps = [b - a for a, b in zip(server.times, server.times[1:])]
        self.assertEqual(gaps, [lt.TEKSTOWO_MIN_INTERVAL_S] * 2)
        self.assertTrue(result.query.stripped)
        self.assertIs(result.source, TEKSTOWO)

    def test_a_stop_is_one_error_and_the_next_track_sends_nothing(self) -> None:
        routes: dict[tuple[str, str], object] = {(HOST, SEARCH): 403}
        server = self.serve(routes)
        stopped = self.fetch()
        errors = [a for a in stopped.attempts if a.outcome is lm.AttemptOutcome.ERROR]
        self.assertEqual(
            errors,
            [lm.ProviderAttempt(KEY, False, lm.AttemptOutcome.ERROR, "HTTP 403")],
        )
        following = self.fetch(OTHER)
        self.assertNotIn(KEY, [a.key for a in following.attempts])
        self.assertEqual(len(server.requests), 1)
        self.assertIs(lyc.table_stop(self.table, TEKSTOWO), self.step.provider_stop())

    def test_a_stop_on_the_first_pass_skips_the_steps_stripped_retry(self) -> None:
        # The stop lands mid-track: the same track's stripped retry must not re-ask it.
        title = f"{QUERY.title} (Live)"
        live = tf.search_path(QUERY.artist, title)
        routes = _found()
        routes[(HOST, live)] = 403
        server = self.serve(routes)
        result = self.fetch(lm.TrackQuery(QUERY.artist, title))
        self.assertEqual(server.paths(), [live])
        asked = [(a.key, a.query_stripped) for a in result.attempts]
        self.assertEqual(asked.count((KEY, False)), 1)
        self.assertNotIn((KEY, True), asked)
        self.assertIn((GENIUS_KEY, True), asked)
        self.assertIsNone(result.source)


class TableTest(fixtures.IsolatedCase):
    """The table holds the step only when its settings name a cool-down file."""

    def test_the_default_table_holds_no_tekstowo_step(self) -> None:
        table = ltable.build_provider_table(None)
        self.assertNotIn(TEKSTOWO, {key.source for key in table})

    def test_built_last_without_touching_the_state(self) -> None:
        state = tf.state_path(self.tmp)
        clock = FakeClock()
        settings = ltable.ProviderSettings(tekstowo=lyc.CoolingSetup(state))
        table = ltable.build_provider_table(
            None, clock=clock, sleeper=clock.sleep, settings=settings
        )
        step = table[KEY]
        assert isinstance(step, lt.TekstowoProvider)
        self.assertEqual(
            (step.client.state_path, step.client.clock, step.client.now),
            (state, clock, lyc.utc_now),
        )
        self.assertEqual(list(table)[-1], KEY)
        allowed = (*ltable.LRC_STEP_SOURCES, TEKSTOWO)
        self.assertEqual(list(table), [k for k in lf.CHAIN if k.source in allowed])
        self.assertFalse(state.exists())
        self.assertFalse(state.parent.exists())
