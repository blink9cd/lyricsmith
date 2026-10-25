"""Answers judged against the track's duration: an implausible one is declined.

Every run, the fetch declines a provider's answer that fails the check's `past end`
row (synced answers only) or its `too many words` row over the track's readable
duration, as it declines a placeholder, and the chain moves on. The attempt's detail
names the rule. A duration that cannot be read declines nothing, and what the track
already holds is never judged here. Every provider is a fake keyed into a table the
way the real one is; every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_check as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import step_key  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB, NETEASE = lm.Source.LRCLIB, lm.Source.NETEASE
DEADLINE_S = 5.0
QUERY = lm.TrackQuery("Invented Artist", "The Keeper Counts")
NO_SIDECAR = lm.SidecarState(None)
#: A track shorter than the lighthouse's latest line (25.75 s) by more than the
#: tolerance: its lyrics run past this track's end.
SHORT_S = 20.0
PAST_END = "last line 25.750 s, track 20.000 s"
#: Timed lines that fit the short track.
SHORT_LRC = "[00:01.00]A gull on the railing\n[00:04.00]counts the boats coming in\n"
#: A saved page: far more words a second than any song over a 30-second track.
SECONDS = 30.0
PAGE = "la " * 400
PAGE_RATE = "13.333 words per second: 400 words in 30.000 s"


def _fetch(
    table: dict[lm.ProviderKey, fixtures.FakeProvider],
    duration_s: float | None,
    *,
    sidecar: lm.SidecarState = NO_SIDECAR,
    query: lm.TrackQuery = QUERY,
) -> lm.FetchResult:
    """The chain over *table* for a track of *duration_s*."""
    return lf.fetch_lyrics(
        query, sidecar, table, deadline_s=DEADLINE_S, duration_s=duration_s
    )


class PastEndRuleTest(fixtures.IsolatedCase):
    """The check's `past end` row, exposed as `too_many_words` is."""

    def test_lines_past_the_end_are_flagged(self) -> None:
        lyrics = lm.parse_lrc(fixtures.LIGHTHOUSE_LRC)
        assert isinstance(lyrics, lm.SyncedLyrics)
        self.assertEqual(
            lc.past_end(lyrics, SHORT_S),
            lr.CheckReason(lr.CheckRule.PAST_END, PAST_END),
        )

    def test_lines_inside_the_track_or_an_unreadable_one_pass(self) -> None:
        lyrics = lm.parse_lrc(fixtures.LIGHTHOUSE_LRC)
        assert isinstance(lyrics, lm.SyncedLyrics)
        for duration_s in (SECONDS, None, 0.0, float("nan")):
            with self.subTest(duration_s=duration_s):
                self.assertIsNone(lc.past_end(lyrics, duration_s))

    def test_the_latest_line_is_judged_not_the_last_stored(self) -> None:
        lyrics = lm.parse_lrc("[00:25.75]late\n[00:01.00]early\n")
        assert isinstance(lyrics, lm.SyncedLyrics)
        flag = lc.past_end(lyrics, SHORT_S)
        assert flag is not None
        self.assertEqual(flag.detail, PAST_END)

    def test_the_implausible_flag_is_past_end_first_then_the_word_rate(self) -> None:
        synced = lm.parse_lrc(fixtures.LIGHTHOUSE_LRC)
        flag = lc.implausible_flag(synced, SHORT_S)
        assert flag is not None
        self.assertIs(flag.rule, lr.CheckRule.PAST_END)
        wordy = lc.implausible_flag(lm.PlainLyrics(PAGE), SECONDS)
        assert wordy is not None
        self.assertIs(wordy.rule, lr.CheckRule.TOO_MANY_WORDS)
        self.assertIsNone(lc.implausible_flag(synced, SECONDS))
        # Ends short is no reason: a short song's lyrics on a long track pass.
        self.assertIsNone(lc.implausible_flag(lm.parse_lrc(SHORT_LRC), 600.0))


class DeclinedAnswerTest(fixtures.IsolatedCase):
    """An implausible answer is declined, and the next step's plausible one taken."""

    def test_a_synced_answer_past_the_end_gives_way_to_the_next(self) -> None:
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC),
                step_key(NETEASE, S): fixtures.answering(SHORT_LRC),
            }
        )
        result = _fetch(table, SHORT_S)
        self.assertIs(result.source, NETEASE)
        self.assertEqual(result.lyrics, lm.parse_lrc(SHORT_LRC))
        first = result.attempts[0]
        self.assertIs(first.outcome, lm.AttemptOutcome.EMPTY)
        self.assertEqual(first.detail, f"implausible: past end ({PAST_END})")

    def test_a_too_wordy_plain_answer_gives_way_to_the_next(self) -> None:
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, U): fixtures.answering(PAGE),
                step_key(NETEASE, U): fixtures.answering(fixtures.LIGHTHOUSE_PLAIN),
            }
        )
        result = _fetch(table, SECONDS)
        self.assertIs(result.source, NETEASE)
        declined = [a for a in result.attempts if a.key == step_key(LRCLIB, U)]
        self.assertEqual(
            [a.detail for a in declined], [f"implausible: too many words ({PAGE_RATE})"]
        )

    def test_nothing_plausible_is_nothing_found(self) -> None:
        table = fixtures.chain_table({step_key(LRCLIB, U): fixtures.answering(PAGE)})
        result = _fetch(table, SECONDS)
        self.assertIsNone(result.lyrics)
        self.assertFalse(result.every_provider_failed)

    def test_an_unreadable_duration_declines_nothing(self) -> None:
        for duration_s in (None, 0.0, float("nan")):
            with self.subTest(duration_s=duration_s):
                answer = fixtures.answering(fixtures.LIGHTHOUSE_LRC)
                table = fixtures.chain_table({step_key(LRCLIB, S): answer})
                result = _fetch(table, duration_s)
                self.assertIs(result.source, LRCLIB)

    def test_without_a_duration_nothing_is_judged(self) -> None:
        answer = fixtures.answering(PAGE)
        table = fixtures.chain_table({step_key(LRCLIB, U): answer})
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertIs(result.source, LRCLIB)

    def test_a_declined_answer_still_lets_the_stripped_title_retry(self) -> None:
        live = lm.TrackQuery("Invented Artist", "The Keeper Counts (Live)")
        titles = run_fakes.TitleProvider(
            {live.title: fixtures.LIGHTHOUSE_LRC, QUERY.title: SHORT_LRC}
        )
        table = {step_key(LRCLIB, S): titles}
        result = lf.fetch_lyrics(
            live, NO_SIDECAR, table, deadline_s=DEADLINE_S, duration_s=SHORT_S
        )
        self.assertEqual(result.lyrics, lm.parse_lrc(SHORT_LRC))
        self.assertTrue(result.query.stripped)

    def test_held_untimed_lyrics_serve_when_the_timed_answer_is_implausible(
        self,
    ) -> None:
        held = lm.SidecarState(lm.PlainLyrics(fixtures.ORCHARD_PLAIN))
        table = fixtures.chain_table(
            {step_key(LRCLIB, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC)}
        )
        result = _fetch(table, SHORT_S, sidecar=held)
        self.assertIs(result.source, lm.Source.SIDECAR)
        self.assertEqual(
            result.attempts[0].detail, f"implausible: past end ({PAST_END})"
        )

    def test_a_labeled_transcript_serves_when_the_timed_answer_is_implausible(
        self,
    ) -> None:
        transcript = fixtures.synced_lrc(fixtures.MACHINE_LRC)
        held = lm.SidecarState(transcript, label=lm.Source.MACHINE_TRANSCRIPT)
        table = fixtures.chain_table(
            {step_key(LRCLIB, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC)}
        )
        result = _fetch(table, SHORT_S, sidecar=held)
        self.assertIs(result.source, lm.Source.MACHINE_TRANSCRIPT)

    def test_what_the_track_holds_is_never_judged_here(self) -> None:
        held = lm.SidecarState(lm.parse_lrc(fixtures.LIGHTHOUSE_LRC))
        result = _fetch(fixtures.chain_table(), SHORT_S, sidecar=held)
        self.assertIs(result.source, lm.Source.SIDECAR)
        self.assertEqual(result.attempts, ())


#: A track the lighthouse's latest line (25.75 s) overruns by more than half its
#: length: its timed answer is another, longer song, so its plain words are too.
FAR_S = 10.0
#: The detail of a plain answer declined as the far-past timed answer's song.
SAME_SONG = "implausible: same song as a declined timed answer (1.000)"


class SameSongTest(fixtures.IsolatedCase):
    """A plain answer of the song a far-past timed answer was is declined too."""

    def test_far_past_then_the_same_songs_plain_text_is_declined(self) -> None:
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC),
                step_key(NETEASE, U): fixtures.answering(fixtures.LIGHTHOUSE_EDITED),
            }
        )
        result = _fetch(table, FAR_S)
        self.assertIsNone(result.lyrics)
        declined = [a for a in result.attempts if a.key == step_key(NETEASE, U)]
        self.assertEqual([a.detail for a in declined], [SAME_SONG])
        self.assertIs(declined[0].outcome, lm.AttemptOutcome.EMPTY)

    def test_far_past_then_another_songs_plain_text_is_accepted(self) -> None:
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC),
                step_key(NETEASE, U): fixtures.answering(fixtures.ORCHARD_PLAIN),
            }
        )
        result = _fetch(table, FAR_S)
        self.assertIs(result.source, NETEASE)
        self.assertEqual(result.lyrics, lm.PlainLyrics(fixtures.ORCHARD_PLAIN))

    def test_slightly_past_then_the_same_songs_plain_text_is_accepted(self) -> None:
        # 25.75 s on a 20 s track is past the end, yet within half its length again:
        # a longer version of the right song, whose words are still the track's.
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC),
                step_key(NETEASE, U): fixtures.answering(fixtures.LIGHTHOUSE_EDITED),
            }
        )
        result = _fetch(table, SHORT_S)
        self.assertIs(result.source, NETEASE)
        self.assertEqual(
            result.attempts[0].detail, f"implausible: past end ({PAST_END})"
        )

    def test_the_stripped_title_retrys_plain_text_is_declined_too(self) -> None:
        live = lm.TrackQuery("Invented Artist", "The Keeper Counts (Live)")
        timed = run_fakes.TitleProvider({live.title: fixtures.LIGHTHOUSE_LRC})
        plain = run_fakes.TitleProvider({QUERY.title: fixtures.LIGHTHOUSE_PLAIN})
        table = {step_key(LRCLIB, S): timed, step_key(LRCLIB, U): plain}
        result = lf.fetch_lyrics(
            live, NO_SIDECAR, table, deadline_s=DEADLINE_S, duration_s=FAR_S
        )
        self.assertIsNone(result.lyrics)
        self.assertEqual(len(plain.calls), 2)
        self.assertEqual(result.attempts[-1].detail, SAME_SONG)

    def test_an_unreadable_duration_triggers_nothing(self) -> None:
        # The timed answer that runs far past FAR_S is accepted when the duration
        # cannot be read: nothing is declined, so nothing is recorded as a far-past song.
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC),
                step_key(NETEASE, U): fixtures.answering(fixtures.LIGHTHOUSE_EDITED),
            }
        )
        result = _fetch(table, None)
        self.assertIs(result.source, LRCLIB)
        self.assertEqual(result.attempts[0].detail, lf.DETAIL_ACCEPTED)

    def test_far_past_needs_the_past_end_decline(self) -> None:
        lyrics = lm.parse_lrc(fixtures.LIGHTHOUSE_LRC)
        self.assertTrue(lf.far_past_end(lyrics, FAR_S))
        self.assertFalse(lf.far_past_end(lyrics, SHORT_S))
        self.assertFalse(lf.far_past_end(lyrics, None))
        self.assertFalse(
            lf.far_past_end(lm.PlainLyrics(fixtures.LIGHTHOUSE_PLAIN), FAR_S)
        )
        # On a 2 s track a line at 3.5 s is beyond one and a half times its length,
        # yet within the tolerance, so it is not declined as past end, nor far past.
        within = lm.parse_lrc("[00:01.00]A gull\n[00:03.50]on the railing\n")
        self.assertFalse(lf.far_past_end(within, 2.0))
        self.assertEqual(lf.FAR_PAST_END_FACTOR, 1.5)
