"""The YouTube Music step: timed after NetEase, untimed after Bandcamp, one set of requests.

Every request is answered by `youtube_fakes.ScriptedServer` through
`http_fakes.transport`, so the real client, session and adapter run and only the socket
is never reached. The clock is a `FakeClock`, so the 5-second pace takes no time, and
each cool-down file is written under the test's own temporary directory. Every title,
artist and lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_music as lym  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_provider as lyp  # noqa: E402 - import follows the sys.path bootstrap above
import youtube_fakes as yf  # noqa: E402 - import follows the sys.path bootstrap above
import youtube_replies as yr  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import FakeClock  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_answer import LrcAnswer  # noqa: E402 - import follows the sys.path bootstrap above

YTM = lm.Source.YOUTUBE_MUSIC
SYNCED_KEY = lm.ProviderKey(YTM, lm.LyricsKind.SYNCED)
PLAIN_KEY = lm.ProviderKey(YTM, lm.LyricsKind.UNSYNCED)
QUERY = lm.TrackQuery("Invented Artist", "The Keeper Counts", duration_s=201.0)
#: Another track of the same album, asked after the first.
OTHER = lm.TrackQuery("Invented Artist", "A Second Invented Song", duration_s=201.0)
NO_SIDECAR = lm.SidecarState(None)
#: The video the search's audio track plays, and the lyrics tab the next reply names.
VIDEO, TAB = "vidkeeper01", "MPLYt_invented01"
#: Two timed lines, their starts in milliseconds, and the LRC the provider makes of them.
TIMED = [("An invented first line", 9000), ("An invented second line", 12345)]
TIMED_LRC = "[00:09.000]An invented first line\n[00:12.345]An invented second line\n"
PLAIN = "An invented verse\nAnother invented verse"
#: The deadline the watchdog abandons a held call at.
SHORT_S = 0.05


def _search(title: str = QUERY.title, video_type: str = yr.AUDIO_TRACK) -> yf.Answer:
    """A search answering with one result for *title* by the query's artist."""
    item = yr.song_item(title, [QUERY.artist], video_id=VIDEO, video_type=video_type)
    return yf.ok(yr.search_reply(item))


def _found(lyrics: object, title: str = QUERY.title) -> list[yf.Answer]:
    """The three answers of a found song: its search, its lyrics tab, then *lyrics*."""
    return [_search(title), yf.ok(yr.next_reply(TAB)), yf.ok(lyrics)]


def _paths(server: yf.ScriptedServer) -> list[str]:
    """The endpoint each request asked, in order."""
    return [
        (r.path_url or "").split("?")[0].rsplit("/", 1)[-1] for r in server.requests
    ]


class _ProviderCase(fixtures.IsolatedCase):
    """A fake clock, and the provider under both of its keys asking a scripted server."""

    def setUp(self) -> None:
        """Start a fresh fake clock."""
        super().setUp()
        self.clock = FakeClock()

    def serve(self, script: list[yf.Answer]) -> yf.ScriptedServer:
        """Install a server answering *script*; build the provider and its table on it."""
        server = yf.ScriptedServer(script, self.clock)
        self.enterContext(http_fakes.transport(server))
        self.provider = lyp.YouTubeMusicProvider(
            yf.client(self.tmp, server, self.clock)
        )
        self.table: lm.ProviderTable = {
            SYNCED_KEY: self.provider,
            PLAIN_KEY: self.provider,
        }
        return server

    def fetch(
        self, query: lm.TrackQuery = QUERY, sidecar: lm.SidecarState = NO_SIDECAR
    ) -> lm.FetchResult:
        """Run the chain for *query* over the table."""
        return lf.fetch_lyrics(query, sidecar, self.table, deadline_s=30.0)


class OneSetOfRequestsTest(_ProviderCase):
    """Three requests serve both steps: the timed one first, the plain one from them."""

    def test_a_timed_answer_is_accepted_at_the_synced_step(self) -> None:
        server = self.serve(_found(yr.timed_browse_reply(TIMED)))
        result = self.fetch()
        self.assertEqual(_paths(server), [lym.SEARCH, lym.NEXT, lym.BROWSE])
        self.assertIsInstance(result.lyrics, lm.SyncedLyrics)
        self.assertIs(result.source, YTM)
        self.assertEqual(
            [(a.key, a.outcome) for a in result.attempts],
            [(SYNCED_KEY, lm.AttemptOutcome.ANSWERED)],
        )

    def test_a_plain_answer_is_accepted_at_step_seven_with_no_fourth_request(
        self,
    ) -> None:
        server = self.serve(_found(yr.plain_browse_reply(PLAIN)))
        result = self.fetch()
        self.assertEqual(len(server.requests), 3)
        self.assertEqual(result.lyrics, lm.PlainLyrics(PLAIN))
        self.assertIs(result.source, YTM)
        self.assertEqual(lf.CHAIN.index(PLAIN_KEY) + 1, 7)
        self.assertEqual(
            [(a.key, a.outcome, a.detail) for a in result.attempts],
            [
                (SYNCED_KEY, lm.AttemptOutcome.EMPTY, lf.DETAIL_UNTIMED),
                (PLAIN_KEY, lm.AttemptOutcome.ANSWERED, lf.DETAIL_ACCEPTED),
            ],
        )

    def test_each_request_asks_for_what_the_last_named(self) -> None:
        server = self.serve(_found(yr.timed_browse_reply(TIMED)))
        self.fetch()
        today = yf.NOW.date()
        self.assertEqual(
            server.bodies,
            [
                lym.search_body("The Keeper Counts Invented Artist", today),
                lym.next_body(VIDEO, today),
                lym.browse_body(TAB),
            ],
        )

    def test_calling_it_gives_the_plain_steps_text(self) -> None:
        self.serve(_found(yr.plain_browse_reply(PLAIN)))
        self.assertEqual(self.provider(QUERY), PLAIN)


class TimedLinesTest(_ProviderCase):
    """Timed lines become millisecond stamps; one untimed line makes the answer untimed."""

    def test_timed_lines_become_millisecond_stamps(self) -> None:
        self.serve(_found(yr.timed_browse_reply(TIMED)))
        self.assertEqual(
            self.provider.answer_all_kinds(QUERY), LrcAnswer.same(TIMED_LRC)
        )

    def test_a_line_without_a_time_is_accepted_only_at_the_plain_step(self) -> None:
        lines = [("An invented first line", 9000), ("An invented second line", None)]
        self.serve(_found(yr.timed_browse_reply(lines)))
        result = self.fetch()
        self.assertEqual(
            result.lyrics,
            lm.PlainLyrics("An invented first line\nAn invented second line"),
        )
        self.assertEqual(
            [a.outcome for a in result.attempts],
            [lm.AttemptOutcome.EMPTY, lm.AttemptOutcome.ANSWERED],
        )

    def test_untimed_text_opening_with_a_stamp_is_not_taken_as_timed(self) -> None:
        text = "[00:09.00]An invented opening\nAn invented close"
        self.serve(_found(yr.plain_browse_reply(text)))
        answer = self.provider.answer_all_kinds(QUERY)
        self.assertEqual(answer, LrcAnswer(synced=None, plain=text))

    def test_an_oversized_untimed_answer_reaches_the_chain_unread(self) -> None:
        text = "[00:09.00]" + "invented " * (lr.MAX_LYRICS_CHARS // 9 + 1)
        self.serve(_found(yr.plain_browse_reply(text)))
        self.assertEqual(self.provider.answer_all_kinds(QUERY), LrcAnswer.same(text))


class NothingFoundTest(_ProviderCase):
    """No audio track is nothing after the search; no lyrics tab, after two requests."""

    def test_only_a_music_video_is_nothing_found_after_the_search(self) -> None:
        server = self.serve([_search(video_type=yr.MUSIC_VIDEO)])
        result = self.fetch()
        self.assertEqual(_paths(server), [lym.SEARCH])
        self.assertIsNone(result.lyrics)
        self.assertEqual(
            [(a.key, a.detail) for a in result.attempts],
            [(SYNCED_KEY, lf.DETAIL_NOTHING), (PLAIN_KEY, lf.DETAIL_NOTHING)],
        )

    def test_no_lyrics_tab_is_nothing_found_after_two_requests(self) -> None:
        server = self.serve([_search(), yf.ok(yr.next_reply(None))])
        result = self.fetch()
        self.assertEqual(_paths(server), [lym.SEARCH, lym.NEXT])
        self.assertIsNone(result.lyrics)

    def test_a_tab_without_words_is_nothing_found(self) -> None:
        server = self.serve(_found(yr.plain_browse_reply(" ")))
        self.assertEqual(self.provider.answer_all_kinds(QUERY), LrcAnswer.same(None))
        self.assertEqual(len(server.requests), 3)


class NoArtistTest(_ProviderCase):
    """A query with no artist is not asked: no attempt, no request."""

    def test_no_artist_tag_is_skipped_unrecorded(self) -> None:
        server = self.serve([])
        query = lm.TrackQuery("", "The Keeper Counts", from_filename=True)
        self.assertFalse(self.provider.asks(query))
        self.assertEqual(self.fetch(query).attempts, ())
        self.assertEqual(self.provider.answer_all_kinds(query), LrcAnswer.same(None))
        self.assertEqual(server.requests, [])


class StrippedRetryTest(_ProviderCase):
    """The stripped-title retry asks three more requests, each paced 5 s after the last."""

    def test_the_retry_sends_three_more_paced_requests(self) -> None:
        title = "The Keeper Counts (Live)"
        first = _found(yr.plain_browse_reply(""), title)
        server = self.serve([*first, *_found(yr.timed_browse_reply(TIMED))])
        result = self.fetch(lm.TrackQuery(QUERY.artist, title, duration_s=201.0))
        self.assertEqual(len(server.requests), 6)
        gaps = [b - a for a, b in zip(server.times, server.times[1:])]
        self.assertEqual(gaps, [lym.YOUTUBE_MUSIC_MIN_INTERVAL_S] * 5)
        self.assertTrue(result.query.stripped)
        self.assertIsInstance(result.lyrics, lm.SyncedLyrics)


class StopTest(_ProviderCase):
    """After a stop the step is not asked again, by this track or the next."""

    def test_a_refused_search_stops_the_step_for_the_run(self) -> None:
        server = self.serve([(429, b"", {})])
        stopped = self.fetch()
        self.assertEqual(
            stopped.attempts,
            (
                lm.ProviderAttempt(
                    SYNCED_KEY,
                    False,
                    lm.AttemptOutcome.ERROR,
                    "HTTP 429",
                    lrf.RefusalFacts(lrf.Refusal.RATE_LIMITED, 429),
                ),
            ),
        )
        self.assertFalse(self.provider.asks(OTHER))
        self.assertEqual(self.fetch(OTHER).attempts, ())
        self.assertEqual(len(server.requests), 1)
        stop = lyc.table_stop(self.table, YTM)
        self.assertIsNotNone(stop)
        self.assertIs(stop, self.provider.provider_stop())
        assert stop is not None
        self.assertEqual(stop.what, "HTTP 429")
        self.assertEqual(stop.until, yf.NOW + lyc.COOL_DOWN)

    def test_a_table_without_the_step_reports_no_stop(self) -> None:
        self.assertIsNone(lyc.table_stop({}, YTM))
        self.assertIsNone(lyc.table_stop({SYNCED_KEY: fixtures.empty()}, YTM))

    def test_a_running_step_reports_no_stop(self) -> None:
        self.serve([])
        self.assertIsNone(lyc.table_stop(self.table, YTM))


class HeldUntimedTest(_ProviderCase):
    """A track holding untimed lyrics still asks the timed step, and only it."""

    def test_a_txt_sidecar_still_asks_the_timed_step(self) -> None:
        server = self.serve(_found(yr.timed_browse_reply(TIMED)))
        held = lm.SidecarState(lm.PlainLyrics(PLAIN))
        result = self.fetch(sidecar=held)
        self.assertEqual(len(server.requests), 3)
        self.assertIs(result.source, YTM)
        self.assertIsInstance(result.lyrics, lm.SyncedLyrics)

    def test_an_untimed_answer_keeps_the_held_lyrics(self) -> None:
        server = self.serve(_found(yr.plain_browse_reply("Invented other words")))
        held = lm.SidecarState(lm.PlainLyrics(PLAIN))
        result = self.fetch(sidecar=held)
        self.assertEqual(len(server.requests), 3)
        self.assertIs(result.source, lm.Source.SIDECAR)
        self.assertEqual([a.key for a in result.attempts], [SYNCED_KEY])


class AbandonedCallTest(_ProviderCase):
    """A call the watchdog abandoned holds the client's lock; the next sends nothing."""

    def test_a_held_search_keeps_the_next_call_from_sending(self) -> None:
        held = http_fakes.HeldTransport(fixtures.HANG_LIMIT_S)
        # Only a backstop: the abandoned calls are finished inside the transport.
        self.addCleanup(held.released.set)
        provider = lyp.YouTubeMusicProvider(yf.client(self.tmp, held, self.clock))

        def search() -> object:
            return provider.answer_all_kinds(QUERY)

        with http_fakes.transport(held):
            with self.assertRaises(lp.ProviderTimeout):
                lf.run_with_deadline(search, SHORT_S)
            # The first search must hold the lock before the next is tried; under load
            # the watchdog can abandon it before it has, so wait for its request.
            self.assertTrue(held.wait_sent(1))
            # The second sends nothing, so `finish` has no thread of its to join: once
            # released, the first's 404 stops the client and the second ends unsent.
            with self.assertRaises(lp.ProviderTimeout):
                lf.run_with_deadline(search, SHORT_S)
            sent = len(held.sent)
            finished = held.finish()
        self.assertEqual(sent, 1)
        self.assertTrue(finished)


class ChainAndTableTest(fixtures.IsolatedCase):
    """The two steps' places in the chain, and one provider under both keys in the table."""

    def test_the_chain_is_the_nine_steps(self) -> None:
        S, U, src = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED, lm.Source
        self.assertEqual(
            lf.CHAIN,
            (
                lm.ProviderKey(src.LRCLIB, S),
                lm.ProviderKey(src.NETEASE, S),
                lm.ProviderKey(src.YOUTUBE_MUSIC, S),
                lm.ProviderKey(src.LRCLIB, U),
                lm.ProviderKey(src.NETEASE, U),
                lm.ProviderKey(src.BANDCAMP, U),
                lm.ProviderKey(src.YOUTUBE_MUSIC, U),
                lm.ProviderKey(src.GENIUS, U),
                lm.ProviderKey(src.TEKSTOWO, U),
            ),
        )

    def test_the_default_table_holds_no_youtube_music_step(self) -> None:
        table = ltable.build_provider_table(None)
        self.assertNotIn(YTM, {key.source for key in table})

    def test_one_provider_under_both_keys_built_without_touching_the_state(
        self,
    ) -> None:
        state = self.tmp / "s.json"
        clock = FakeClock()
        setup = lyc.CoolingSetup(state)
        settings = ltable.ProviderSettings(youtube_music=setup)
        table = ltable.build_provider_table(
            None, clock=clock, sleeper=clock.sleep, settings=settings
        )
        step = table[SYNCED_KEY]
        self.assertIs(table[PLAIN_KEY], step)
        assert isinstance(step, lyp.YouTubeMusicProvider)
        self.assertEqual(
            (step.client.state_path, step.client.clock, step.client.now),
            (state, clock, lyc.utc_now),
        )
        self.assertEqual(step.step_deadline_s(), lyp.YOUTUBE_MUSIC_STEP_DEADLINE_S)
        self.assertEqual(lyp.YOUTUBE_MUSIC_STEP_DEADLINE_S, 75.0)
        allowed = (*ltable.LRC_STEP_SOURCES, YTM)
        self.assertEqual(list(table), [k for k in lf.CHAIN if k.source in allowed])
        self.assertFalse(state.exists())
