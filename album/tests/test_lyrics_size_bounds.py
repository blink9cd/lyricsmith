"""The comparison's word bound, and the lyrics cap on a track's own frames.

`lyrics_check.word_similarity` compares only the opening `MAX_COMPARED_WORDS` words of
each side, so no caller's comparison grows past a fixed cost. A lyrics frame over
`MAX_LYRICS_CHARS` is reported, as a sidecar over it is, and never used: no lyrics are
taken from it and no sidecar is written from it. Every lyric is invented, and no test
measures time.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_check as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory as linv  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_embedded import EmbeddedCase  # noqa: E402 - import follows the sys.path bootstrap above

#: Words past the bound, so a list this long is longer than the comparison reads.
_LONG = 2_500
#: The language every frame here is in.
_LANG = "eng"
#: The dry run's two tracks: one with an over-cap frame, one with none.
_KEEPER = "01 Keeper.mp3"
_MILL = "02 Mill.mp3"


def _words(count: int, word: str = "lamp") -> list[str]:
    """*count* copies of *word*."""
    return [word] * count


class WordBoundTest(fixtures.IsolatedCase):
    """Only the opening words of each side are compared."""

    def test_the_bound_is_two_thousand_words(self) -> None:
        self.assertEqual(lc.MAX_COMPARED_WORDS, 2_000)

    def test_lists_differing_only_past_the_bound_are_alike(self) -> None:
        opening = _words(lc.MAX_COMPARED_WORDS)
        first = opening + _words(_LONG - lc.MAX_COMPARED_WORDS, "gull")
        second = opening + _words(_LONG - lc.MAX_COMPARED_WORDS, "rope")
        self.assertEqual(lc.word_similarity(first, second), 1.0)

    def test_a_list_longer_than_the_bound_is_compared_by_its_opening(self) -> None:
        self.assertEqual(lc.word_similarity(_words(_LONG), _words(2_000)), 1.0)

    def test_a_difference_inside_the_bound_still_counts(self) -> None:
        first = _words(lc.MAX_COMPARED_WORDS)
        second = _words(lc.MAX_COMPARED_WORDS - 1) + ["gull"]
        self.assertLess(lc.word_similarity(first, second), 1.0)

    def test_the_check_passes_lyrics_alike_in_their_opening(self) -> None:
        opening = " ".join(_words(lc.MAX_COMPARED_WORDS))
        embedded = lm.PlainLyrics(opening + " gull" * 3_000)
        backup = lm.PlainLyrics(opening + " rope" * 3_000)
        result = lc.check_track(embedded, backup, None)
        rules = [flag.rule for flag in result.flags]
        self.assertNotIn(lr.CheckRule.UNLIKE_BACKUP, rules)


class FrameCapTest(fixtures.IsolatedCase):
    """A frame over the lyrics cap raises; one at the cap converts as before."""

    def test_a_uslt_over_the_cap_is_refused(self) -> None:
        frame = tag_builders.uslt("w" * (lr.MAX_LYRICS_CHARS + 1))
        with self.assertRaises(lm.FrameTooLargeError) as raised:
            lm.lyrics_from_frames((frame,), _LANG)
        self.assertEqual(
            str(raised.exception),
            f"a USLT frame is too large: over {lr.MAX_LYRICS_CHARS} characters",
        )

    def test_a_uslt_at_the_cap_is_read(self) -> None:
        text = "w" * lr.MAX_LYRICS_CHARS
        frame = tag_builders.uslt(text)
        self.assertEqual(lm.lyrics_from_frames((frame,), _LANG), lm.PlainLyrics(text))

    def test_a_sylt_counts_a_line_break_per_entry(self) -> None:
        # Each entry becomes a line, so its text and its line break both count.
        half = lr.MAX_LYRICS_CHARS // 2
        at_cap = tag_builders.sylt((1000, "w" * (half - 1)), (2000, "w" * (half - 1)))
        self.assertIsNotNone(lm.lyrics_from_frames((at_cap,), _LANG))
        over = tag_builders.sylt((1000, "w" * half), (2000, "w" * half))
        with self.assertRaises(lm.FrameTooLargeError):
            lm.lyrics_from_frames((over,), _LANG)

    def test_a_sylt_of_empty_entries_past_the_cap_is_refused(self) -> None:
        entries = ((ms, "") for ms in range(lr.MAX_LYRICS_CHARS + 1))
        frame = tag_builders.sylt(*entries)
        with self.assertRaises(lm.FrameTooLargeError):
            lm.lyrics_from_frames((frame,), _LANG)

    def test_an_over_cap_frame_is_refused_beside_a_usable_one(self) -> None:
        small = tag_builders.sylt((1000, "A lamp swings"))
        large = tag_builders.uslt("w" * (lr.MAX_LYRICS_CHARS + 1), lang="deu")
        with self.assertRaises(lm.FrameTooLargeError):
            linv.embedded_lyrics((small, large), _LANG)

    def test_it_is_a_lyrics_error(self) -> None:
        self.assertTrue(issubclass(lm.FrameTooLargeError, lm.LyricsError))


class OverCapFrameRunTest(EmbeddedCase):
    """A run reports a track whose frame is over the cap, and writes nothing from it."""

    def test_the_track_is_an_error_and_no_sidecar_is_written(self) -> None:
        paths = self.keeper(tag_builders.uslt("w " * (lr.MAX_LYRICS_CHARS // 2 + 1)))
        self.run_album()
        keeper, mill = self.afters()
        assert keeper is not None and keeper.error is not None
        self.assertEqual(keeper.status, lr.OutcomeStatus.ERROR)
        self.assertEqual(keeper.error.exc_type, "FrameTooLargeError")
        # The stage that first reads a track's lyrics, as for a sidecar over the cap.
        self.assertEqual(
            keeper.error.summary(),
            f"ERROR sidecar: a USLT frame is too large: over {lr.MAX_LYRICS_CHARS} "
            "characters",
        )
        self.assertEqual(rb.sidecars(paths[0]), {})
        self.assertEqual(self.asked_for_keeper(), [])
        assert mill is not None
        self.assertNotEqual(mill.status, lr.OutcomeStatus.ERROR)

    def test_a_fresh_run_reports_it_too(self) -> None:
        paths = self.keeper(tag_builders.uslt("w " * (lr.MAX_LYRICS_CHARS // 2 + 1)))
        self.run_album("--fresh")
        keeper, _mill = self.afters()
        assert keeper is not None and keeper.error is not None
        self.assertEqual(keeper.error.exc_type, "FrameTooLargeError")
        self.assertEqual(rb.sidecars(paths[0]), {})

    def test_a_strip_removes_it(self) -> None:
        paths = self.keeper(tag_builders.uslt("w " * (lr.MAX_LYRICS_CHARS // 2 + 1)))
        self.run_album("--mode", "strip")
        self.assertEqual(lyrics_tags.read_tags(paths[0]).frames, ())


class OverCapFramePlanTest(fixtures.IsolatedCase):
    """The dry run plans a track whose frame is over the cap as that track's error."""

    def plan(self) -> dict[str, lyrics_modes.PlannedTrack]:
        """An over-cap keeper beside a plain mill, planned; each track by path."""
        large = tag_builders.uslt("w " * (lr.MAX_LYRICS_CHARS // 2 + 1))
        tracks = (fixtures.TrackSpec(_KEEPER), fixtures.TrackSpec(_MILL))
        paths = fixtures.build_album(self.tmp, tracks)
        tag_builders.plant_frames(paths[0], (large,))
        planned = lyrics_modes.plan_album(
            self.tmp,
            paths,
            run_fakes.make_stages(run_fakes.FakeRunFolder()),
            offline=False,
            lang=run_fakes.LANG,
        )
        return {track.path: track for track in planned}

    def assert_planned_as_its_error(self) -> None:
        """The keeper's plan is its frame's error; the mill is planned as usual."""
        plans = self.plan()
        keeper = plans[_KEEPER]
        assert keeper.error is not None
        self.assertEqual(keeper.plan, lyrics_modes.PLAN_UNKNOWN)
        self.assertEqual(keeper.error.exc_type, "FrameTooLargeError")
        self.assertEqual(plans[_MILL].plan, lyrics_modes.PLAN_FETCH)

    def test_the_track_is_planned_as_its_error(self) -> None:
        self.assert_planned_as_its_error()

    def test_the_frames_own_read_is_inside_the_track_boundary(self) -> None:
        # The placeholder check reads the frames first today; without it, the read of
        # the frames themselves must still end that track alone, not the dry run.
        with mock.patch.object(lyrics_modes, "afresh_target", return_value=None):
            self.assert_planned_as_its_error()
