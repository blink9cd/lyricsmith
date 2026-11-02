"""check_track: the timing rows, the word-rate and similarity rows, the normalization.

Each threshold is tested at its boundary, one value just inside and one just outside,
so a changed operator or constant fails a test rather than passing at 100% coverage.
Every lyric here is invented (see fixtures.py). The property test at the bottom explores
the normalization's input space: it reads lyrics that came from providers, sidecars and
hand edits, so it is a parser of untrusted text.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_check as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages as ls  # noqa: E402 - import follows the sys.path bootstrap above

#: A track length used where the timing rows should all pass, in seconds.
_TRACK_S = 180.0
#: The SKIP's detail for untimed lyrics, and for synced, when the duration is unreadable.
_WORDS_NOT_CHECKED = "too-many-words row not checked"
_BOTH_NOT_CHECKED = "timing and too-many-words rows not checked"


def _synced(*seconds: float, text: str = "an invented line") -> lm.SyncedLyrics:
    """Synced lyrics with one line at each of *seconds*, in the order given."""
    lines = tuple(lm.SyncedLine(round(s * 1000), text) for s in seconds)
    return lm.SyncedLyrics(lines)


def _rules(reasons: tuple[lr.CheckReason, ...]) -> list[lr.CheckRule]:
    """The rules of *reasons*, in order."""
    return [reason.rule for reason in reasons]


class ProtocolTest(fixtures.IsolatedCase):
    """check_track is the stage the orchestrator calls."""

    def test_check_track_binds_to_the_protocol(self) -> None:
        checker: ls.CheckTrack = lc.check_track
        result = checker(lm.PlainLyrics(fixtures.LIGHTHOUSE_PLAIN), None, _TRACK_S)
        self.assertIs(result.verdict, lr.CheckVerdict.PASS)


class NoLyricsTest(fixtures.IsolatedCase):
    """A written track that now holds no lyrics is a FLAG, and nothing else is judged."""

    def test_missing_embedded_lyrics_flag(self) -> None:
        backup = lm.PlainLyrics(fixtures.ORCHARD_PLAIN)
        result = lc.check_track(None, backup, None)
        self.assertEqual(_rules(result.flags), [lr.CheckRule.NO_LYRICS])
        self.assertEqual(result.skips, ())
        self.assertIs(result.verdict, lr.CheckVerdict.FLAG)


class PlaceholderTest(fixtures.IsolatedCase):
    """Embedded lyrics that are a placeholder flag, and the FLAG says what to do."""

    def test_a_header_tag_alone_flags_first(self) -> None:
        backup = lm.PlainLyrics(fixtures.ORCHARD_PLAIN)
        result = lc.check_track(lm.PlainLyrics("[au:comedy]"), backup, _TRACK_S)
        self.assertEqual(result.flags[0].rule, lr.CheckRule.PLACEHOLDER)
        self.assertIn("run the album again", result.flags[0].detail)
        self.assertEqual(str(lr.CheckRule.PLACEHOLDER), "placeholder lyrics")

    def test_wordless_synced_lyrics_flag(self) -> None:
        result = lc.check_track(lm.parse_lrc("[00:01.00]\n"), None, _TRACK_S)
        self.assertIn(lr.CheckRule.PLACEHOLDER, _rules(result.flags))

    def test_lyrics_beside_a_header_tag_pass(self) -> None:
        text = "[au:comedy]\n" + fixtures.LIGHTHOUSE_PLAIN
        result = lc.check_track(lm.PlainLyrics(text), None, _TRACK_S)
        self.assertIs(result.verdict, lr.CheckVerdict.PASS)


class PastEndTest(fixtures.IsolatedCase):
    """A line timed after the audio ends, beyond the tolerance, flags."""

    def test_last_line_at_the_tolerance_passes(self) -> None:
        lyrics = _synced(1.0, _TRACK_S + lc.PAST_END_TOLERANCE_S)
        result = lc.check_track(lyrics, None, _TRACK_S)
        self.assertIs(result.verdict, lr.CheckVerdict.PASS)

    def test_last_line_just_past_the_tolerance_flags(self) -> None:
        lyrics = _synced(1.0, _TRACK_S + lc.PAST_END_TOLERANCE_S + 0.001)
        result = lc.check_track(lyrics, None, _TRACK_S)
        self.assertEqual(_rules(result.flags), [lr.CheckRule.PAST_END])
        self.assertIn("182.001", result.summary())
        self.assertIn("180.0", result.summary())

    def test_an_early_line_stored_last_does_not_hide_a_late_one(self) -> None:
        lyrics = _synced(1.0, 200.0, 50.0)
        result = lc.check_track(lyrics, None, _TRACK_S)
        self.assertIn(lr.CheckRule.PAST_END, _rules(result.flags))


class EndsShortTest(fixtures.IsolatedCase):
    """Lyrics ending before half the track AND over a minute early flag."""

    def test_last_line_just_before_half_flags(self) -> None:
        result = lc.check_track(_synced(1.0, 99.999), None, 200.0)
        self.assertEqual(_rules(result.flags), [lr.CheckRule.ENDS_SHORT])
        self.assertIn("99.999", result.summary())

    def test_last_line_at_half_passes(self) -> None:
        result = lc.check_track(_synced(1.0, 100.0), None, 200.0)
        self.assertIs(result.verdict, lr.CheckVerdict.PASS)

    def test_a_gap_just_over_the_minimum_flags(self) -> None:
        result = lc.check_track(_synced(1.0, 39.999), None, 100.0)
        self.assertEqual(_rules(result.flags), [lr.CheckRule.ENDS_SHORT])

    def test_a_gap_at_the_minimum_passes(self) -> None:
        result = lc.check_track(_synced(1.0, 40.0), None, 100.0)
        self.assertIs(result.verdict, lr.CheckVerdict.PASS)

    def test_a_long_instrumental_outro_does_not_flag(self) -> None:
        # The last line at 60% of a seven-minute track: nearly three minutes of outro,
        # but past half the track, so only one of the two conditions holds.
        seven_minutes = 420.0
        result = lc.check_track(_synced(5.0, 252.0), None, seven_minutes)
        self.assertIs(result.verdict, lr.CheckVerdict.PASS)


class OutOfOrderTest(fixtures.IsolatedCase):
    """A timestamp lower than the one before it flags."""

    def test_a_backward_step_flags(self) -> None:
        result = lc.check_track(_synced(1.0, 10.0, 9.999, 170.0), None, _TRACK_S)
        self.assertEqual(_rules(result.flags), [lr.CheckRule.OUT_OF_ORDER])
        self.assertIn("9.999", result.summary())

    def test_equal_timestamps_pass(self) -> None:
        result = lc.check_track(_synced(1.0, 10.0, 10.0, 170.0), None, _TRACK_S)
        self.assertIs(result.verdict, lr.CheckVerdict.PASS)

    def test_order_is_judged_without_a_duration(self) -> None:
        result = lc.check_track(_synced(10.0, 5.0), None, None)
        self.assertEqual(_rules(result.flags), [lr.CheckRule.OUT_OF_ORDER])
        self.assertEqual(_rules(result.skips), [lr.CheckRule.DURATION_UNREADABLE])
        self.assertIs(result.verdict, lr.CheckVerdict.FLAG)


class DurationUnreadableTest(fixtures.IsolatedCase):
    """With no usable duration the timing rows SKIP; nothing flags for it."""

    def test_no_duration_skips_the_timing_rows(self) -> None:
        result = lc.check_track(_synced(1.0, 999.0), None, None)
        self.assertEqual(result.flags, ())
        self.assertEqual(_rules(result.skips), [lr.CheckRule.DURATION_UNREADABLE])
        self.assertIs(result.verdict, lr.CheckVerdict.SKIP)

    def test_a_zero_or_non_finite_duration_counts_as_unreadable(self) -> None:
        for duration in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(duration=duration):
                result = lc.check_track(_synced(1.0, 2.0), None, duration)
                self.assertEqual(result.flags, ())
                self.assertEqual(
                    _rules(result.skips), [lr.CheckRule.DURATION_UNREADABLE]
                )

    def test_unsynced_lyrics_skip_the_word_rate_row(self) -> None:
        result = lc.check_track(lm.PlainLyrics(fixtures.ORCHARD_PLAIN), None, None)
        self.assertEqual(result.flags, ())
        self.assertEqual(
            result.skips,
            (lr.CheckReason(lr.CheckRule.DURATION_UNREADABLE, _WORDS_NOT_CHECKED),),
        )
        self.assertIs(result.verdict, lr.CheckVerdict.SKIP)

    def test_synced_lyrics_skip_the_timing_and_word_rate_rows_in_one(self) -> None:
        result = lc.check_track(_synced(1.0, 2.0), None, None)
        self.assertEqual(
            result.skips,
            (lr.CheckReason(lr.CheckRule.DURATION_UNREADABLE, _BOTH_NOT_CHECKED),),
        )


class UnlikeBackupTest(fixtures.IsolatedCase):
    """Embedded lyrics must resemble the lyrics the backup held."""

    def test_a_punctuation_edited_copy_passes(self) -> None:
        embedded = lm.PlainLyrics(fixtures.LIGHTHOUSE_EDITED)
        backup = lm.PlainLyrics(fixtures.LIGHTHOUSE_PLAIN)
        self.assertIs(
            lc.check_track(embedded, backup, _TRACK_S).verdict,
            lr.CheckVerdict.PASS,
        )

    def test_synced_lyrics_compare_against_a_plain_backup_by_their_words(self) -> None:
        embedded = lm.parse_lrc(fixtures.LIGHTHOUSE_LRC)
        backup = lm.PlainLyrics(fixtures.LIGHTHOUSE_EDITED)
        # The invented song's last line is at 25.75 s, so a 30-second track fits it.
        result = lc.check_track(embedded, backup, 30.0)
        self.assertIs(result.verdict, lr.CheckVerdict.PASS)

    def test_a_different_song_flags(self) -> None:
        embedded = lm.PlainLyrics(fixtures.ORCHARD_PLAIN)
        backup = lm.PlainLyrics(fixtures.LIGHTHOUSE_PLAIN)
        result = lc.check_track(embedded, backup, None)
        self.assertEqual(_rules(result.flags), [lr.CheckRule.UNLIKE_BACKUP])
        self.assertIn("similarity", result.summary())

    def test_similarity_at_the_minimum_passes(self) -> None:
        # Three of five words shared in place: 2 * 3 / 10 = 0.6 exactly.
        embedded = lm.PlainLyrics("amber kite falls over hills")
        backup = lm.PlainLyrics("amber kite falls under rivers")
        self.assertIs(
            lc.check_track(embedded, backup, _TRACK_S).verdict,
            lr.CheckVerdict.PASS,
        )

    def test_similarity_just_under_the_minimum_flags(self) -> None:
        # Three shared of five and six words: 2 * 3 / 11, about 0.545.
        embedded = lm.PlainLyrics("amber kite falls over hills")
        backup = lm.PlainLyrics("amber kite falls under rivers slowly")
        result = lc.check_track(embedded, backup, None)
        self.assertEqual(_rules(result.flags), [lr.CheckRule.UNLIKE_BACKUP])
        self.assertIn("similarity 0.545 <", result.summary())

    def test_a_similarity_just_under_the_minimum_never_reads_as_the_minimum(
        self,
    ) -> None:
        # 301 words shared in place of 502 each: 2 * 301 / 1004, about 0.59960, which
        # two or three rounded decimals would both show as 0.60, contradicting the "<".
        shared = [f"word{index}" for index in range(301)]
        embedded = shared + [f"tail{index}" for index in range(201)]
        backup = shared + [f"other{index}" for index in range(201)]
        result = lc.check_track(
            lm.PlainLyrics(" ".join(embedded)), lm.PlainLyrics(" ".join(backup)), None
        )
        self.assertEqual(_rules(result.flags), [lr.CheckRule.UNLIKE_BACKUP])
        shown = result.flags[0].detail.split()[1]
        self.assertEqual(shown, "0.599")
        self.assertLess(float(shown), lc.MIN_SIMILARITY)

    def test_a_backup_with_no_words_is_not_compared(self) -> None:
        embedded = lm.PlainLyrics(fixtures.ORCHARD_PLAIN)
        backup = lm.PlainLyrics(" [Chorus] ...\n")
        self.assertIs(
            lc.check_track(embedded, backup, _TRACK_S).verdict,
            lr.CheckVerdict.PASS,
        )

    def test_every_finding_is_reported_together(self) -> None:
        embedded = _synced(10.0, 5.0, 300.0, text="a wholly different line")
        backup = lm.PlainLyrics(fixtures.LIGHTHOUSE_PLAIN)
        result = lc.check_track(embedded, backup, _TRACK_S)
        self.assertEqual(
            _rules(result.flags),
            [
                lr.CheckRule.PAST_END,
                lr.CheckRule.OUT_OF_ORDER,
                lr.CheckRule.UNLIKE_BACKUP,
            ],
        )


class TooManyWordsTest(fixtures.IsolatedCase):
    """More words a second than any song is sung at flags, timed or untimed."""

    def test_the_threshold_is_six_words_a_second(self) -> None:
        self.assertEqual(lc.MAX_WORDS_PER_SECOND, 6.0)

    def test_words_at_the_threshold_pass(self) -> None:
        # Sixty words over ten seconds: 6.0 a second exactly.
        lyrics = lm.PlainLyrics(" ".join(["lantern"] * 60))
        self.assertIs(lc.check_track(lyrics, None, 10.0).verdict, lr.CheckVerdict.PASS)

    def test_words_just_over_the_threshold_flag(self) -> None:
        lyrics = lm.PlainLyrics(" ".join(["lantern"] * 61))
        result = lc.check_track(lyrics, None, 10.0)
        self.assertEqual(_rules(result.flags), [lr.CheckRule.TOO_MANY_WORDS])
        self.assertEqual(
            result.flags[0].detail, "6.100 words per second: 61 words in 10.000 s"
        )

    def test_the_rate_is_truncated_never_rounded(self) -> None:
        # Twenty words over three seconds is 6.6667 a second, shown as 6.666.
        lyrics = lm.PlainLyrics(" ".join(["lantern"] * 20))
        result = lc.check_track(lyrics, None, 3.0)
        self.assertTrue(result.flags[0].detail.startswith("6.666 words per second"))

    def test_a_page_of_words_flags(self) -> None:
        page = "\n".join(["home about contact search the archive menu login"] * 400)
        result = lc.check_track(lm.PlainLyrics(page), None, _TRACK_S)
        self.assertEqual(_rules(result.flags), [lr.CheckRule.TOO_MANY_WORDS])

    def test_words_are_counted_as_the_similarity_row_counts_them(self) -> None:
        # Section headers and punctuation are no words, so only "go" counts, once.
        lyrics = lm.PlainLyrics("[Chorus] -- ... !? " * 100 + "go")
        self.assertIs(lc.check_track(lyrics, None, 1.0).verdict, lr.CheckVerdict.PASS)

    def test_synced_lyrics_with_too_many_words_flag(self) -> None:
        # Three lines of 25 words in a ten-second track, every line inside it.
        line = " ".join(["lantern"] * 25)
        result = lc.check_track(_synced(1.0, 2.0, 3.0, text=line), None, 10.0)
        self.assertEqual(_rules(result.flags), [lr.CheckRule.TOO_MANY_WORDS])
        self.assertIn("7.500 words per second", result.summary())

    def test_the_row_alone_is_the_checks_own_finding(self) -> None:
        # A refetch judges old lyrics by this row alone, so it must say what the
        # check says, and nothing when the duration cannot be read.
        page = lm.PlainLyrics(" ".join(["lantern"] * 61))
        (flag,) = lc.check_track(page, None, 10.0).flags
        self.assertEqual(lc.too_many_words(page, 10.0), flag)
        self.assertIsNone(lc.too_many_words(page, None))
        self.assertIsNone(lc.too_many_words(lm.PlainLyrics("lantern"), 10.0))

    def test_the_row_sits_between_the_timing_rows_and_the_similarity_row(
        self,
    ) -> None:
        line = " ".join(["lantern"] * 25)
        embedded = _synced(10.0, 5.0, 300.0, text=line)
        backup = lm.PlainLyrics(fixtures.LIGHTHOUSE_PLAIN)
        result = lc.check_track(embedded, backup, 10.0)
        self.assertEqual(
            _rules(result.flags),
            [
                lr.CheckRule.PAST_END,
                lr.CheckRule.OUT_OF_ORDER,
                lr.CheckRule.TOO_MANY_WORDS,
                lr.CheckRule.UNLIKE_BACKUP,
            ],
        )


class NormalizeWordsTest(fixtures.IsolatedCase):
    """The normalization both sides of the similarity row go through."""

    def test_case_punctuation_stamps_and_headers_go(self) -> None:
        text = "[Verse 1]\n[00:01.50]Don't  WAIT, the tide's\tturning!\n[ar:x]"
        self.assertEqual(
            lc.normalize_words(text), ["dont", "wait", "the", "tides", "turning"]
        )

    def test_curly_and_straight_apostrophes_are_deleted(self) -> None:
        self.assertEqual(
            lc.normalize_words("Don\u2019t say it's late"),
            ["dont", "say", "its", "late"],
        )

    def test_dashes_and_other_punctuation_separate_words(self) -> None:
        for joined in ("night\u2014day", "night-day", "night/day", "night,day"):
            with self.subTest(joined=joined):
                self.assertEqual(lc.normalize_words(joined), ["night", "day"])

    def test_a_dashed_pair_matches_its_two_word_transcription(self) -> None:
        self.assertEqual(
            lc.normalize_words("from night\u2014day on"),
            lc.normalize_words("from night day on"),
        )

    def test_enhanced_lrc_word_stamps_go(self) -> None:
        text = "[00:01.00]<00:01.00>Lanterns <00:01.80>drift <00:02.40>home"
        self.assertEqual(lc.normalize_words(text), ["lanterns", "drift", "home"])

    def test_the_shared_pattern_decides_what_a_word_stamp_is(self) -> None:
        # The check and the LRC reader read one pattern: a stamp the reader removes is
        # dropped here, and one it keeps as text (seconds past 59) is read as words.
        for stamp in ("<00:01>", "<00:01.5>", "<00:01.50>", "<00:01.500>", "<0:01:50>"):
            with self.subTest(stamp=stamp):
                self.assertIsNotNone(lm.WORD_STAMP.fullmatch(stamp))
                self.assertEqual(lc.normalize_words(f"one{stamp}two"), ["one", "two"])
        self.assertIsNone(lm.WORD_STAMP.fullmatch("<00:60.00>"))
        self.assertEqual(
            lc.normalize_words("one<00:60.00>two"), ["one", "00", "60", "00", "two"]
        )

    def test_a_combining_mark_stays_inside_its_word(self) -> None:
        self.assertEqual(lc.normalize_words("nai\u0308ve"), ["naive"])

    def test_a_text_of_only_punctuation_has_no_words(self) -> None:
        self.assertEqual(lc.normalize_words(" -- ... !? "), [])

    def test_similarity_of_identical_word_lists_is_one(self) -> None:
        self.assertEqual(lc.word_similarity(["a", "b"], ["a", "b"]), 1.0)


class NormalizePropertyTest(fixtures.IsolatedCase):
    """normalize_words never crashes, and what it returns is already normal."""

    @given(st.text(max_size=300))
    def test_fuzz_normalize_words(self, text: str) -> None:
        words = lc.normalize_words(text)
        self.assertTrue(all(words))
        self.assertEqual(lc.normalize_words(" ".join(words)), words)


if __name__ == "__main__":
    unittest.main()
