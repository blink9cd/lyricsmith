"""`--mode run --refetch-implausible`: a track serving implausible lyrics is asked again.

A track whose serving lyrics run past the audio's end, or hold more words a second
than any song, is asked as though it held nothing; a plausible answer replaces them,
and with none the implausible side is removed, backed up, and undo restores it. The
same wrong answer cannot come back, since every run declines an answer the track's
duration rules out. A plausible track runs as in any run. The option is refused beside
``--fresh`` and ``--offline`` and by every other mode, but combines with
``--refetch-untimed``; a dry run previews the removal. Each test drives the entry
point with its real wiring; only the clock and the provider table are faked, and
every lyric is invented.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_refetch import (  # noqa: E402 - import follows the sys.path bootstrap above
    REFETCH,
    SYLT,
    SYNCED_KEY,
    RefetchCase,
)
from test_lyrics_run_refetch_remove import (  # noqa: E402 - import follows the sys.path bootstrap above
    REMOVED,
    RemoveCase,
)

OPTION = "--refetch-implausible"
#: A minute of lines on the keeper's 30-second track: another, longer song's.
LATE_LRC = "[00:01.00]A lantern swings\n[01:00.00]over the quay\n"
LATE_SYLT = (tag_builders.sylt((1000, "A lantern swings"), (60000, "over the quay")),)
#: Why, as the check row words it over the length the tag reader measures.
LATE = "last line 60.000 s, track 29.989 s"


class ImplausibleCase(RemoveCase):
    """The keeper and the mill, run with ``--refetch-implausible``."""

    def implausible(self, *extra: str) -> tuple[str, lr.TrackOutcome, lr.TrackOutcome]:
        """Run with the option: the keeper's summary row and both outcomes."""
        result = self.run_album(OPTION, *extra)
        self.assertIn(
            result.code, (album.EXIT_OK, album.EXIT_TRACK_ERRORS), result.stderr
        )
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        keeper, mill = self.afters()
        assert keeper is not None and mill is not None
        return row, keeper, mill

    def assert_late_removed(self, row: str, keeper: lr.TrackOutcome) -> None:
        """The keeper's late lyrics removed, said so in its row and its record."""
        self.assertEqual(keeper.status, lr.OutcomeStatus.REMOVED)
        self.assertEqual(keeper.implausible, LATE)
        self.assertTrue(keeper.refetch_implausible)
        self.assertTrue(row.endswith(f"{REMOVED} ({LATE})"), row)


class ImplausibleRunTest(ImplausibleCase):
    """An implausible track is asked again: replaced, or removed and undone."""

    def test_a_plausible_answer_replaces_late_frames(self) -> None:
        self.answer_both(synced={"The Keeper": fixtures.LIGHTHOUSE_LRC}, plain={})
        paths = self.keeper(LATE_SYLT)
        _row, keeper, _mill = self.implausible()
        self.assertEqual(
            (keeper.status, keeper.source, keeper.kind),
            (lr.OutcomeStatus.WRITTEN, lm.Source.LRCLIB, lm.LyricsKind.SYNCED),
        )
        self.assertTrue(keeper.refetch_implausible)
        self.assertFalse(keeper.refetch_untimed)
        self.assertNotIn(LATE_SYLT[0], rb.lyrics_frames(paths[0]))
        self.assertTrue(paths[0].with_suffix(".lrc").is_file())

    def test_no_answer_removes_late_frames_and_undo_restores_them(self) -> None:
        paths = self.keeper(LATE_SYLT)
        self.keep_before(paths)
        row, keeper, mill = self.implausible()
        self.assert_late_removed(row, keeper)
        self.assertEqual((keeper.frames_written, keeper.sidecars_removed), (True, ()))
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        self.assertFalse(mill.refetch_implausible)
        self.assert_undone(paths)

    def test_the_same_wrong_answer_cannot_come_back(self) -> None:
        self.answer_both(synced={"The Keeper": LATE_LRC}, plain={})
        paths = self.keeper(LATE_SYLT)
        row, keeper, _mill = self.implausible()
        self.assert_late_removed(row, keeper)
        self.assertFalse(paths[0].with_suffix(".lrc").exists())

    def test_a_late_lrc_goes_and_plausible_frames_stay(self) -> None:
        paths = self.keeper(SYLT, lrc=LATE_LRC)
        self.keep_before(paths)
        row, keeper, _mill = self.implausible()
        self.assert_late_removed(row, keeper)
        self.assertEqual(
            (keeper.frames_written, keeper.sidecars_removed),
            (False, ("01 Keeper.lrc",)),
        )
        self.assertEqual(rb.lyrics_frames(paths[0]), self.frames)
        self.assert_undone(paths)

    def test_a_plausible_synced_track_is_not_asked_again(self) -> None:
        self.answer_both(synced={"The Keeper": fixtures.LIGHTHOUSE_LRC}, plain={})
        self.keeper(SYLT)
        _row, keeper, _mill = self.implausible()
        self.assertEqual(keeper.source, lm.Source.FRAMES)
        self.assertFalse(keeper.refetch_implausible)
        self.assertEqual([q.title for q in self.titles[SYNCED_KEY].calls], ["The Mill"])

    def test_without_the_option_late_frames_still_serve(self) -> None:
        self.keeper(LATE_SYLT)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        keeper, _mill = self.afters()
        assert keeper is not None
        self.assertEqual(keeper.source, lm.Source.FRAMES)
        self.assertFalse(keeper.refetch_implausible)

    def test_it_combines_with_the_untimed_refetch(self) -> None:
        paths = self.keeper(LATE_SYLT)
        self.keep_before(paths)
        row, keeper, _mill = self.implausible(REFETCH)
        self.assert_late_removed(row, keeper)
        self.assert_undone(paths)


class ImplausibleModesTest(RefetchCase):
    """Refused where it could not mean what it says; a dry run previews it."""

    def assert_refused(self, message: str, *extra: str) -> None:
        """The run with the option and *extra* exits 2 with *message*, writing nothing."""
        paths = self.keeper(LATE_SYLT)
        before = raw(paths)
        result = self.run_album(OPTION, *extra)
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertIn(message, result.stderr)
        self.assertEqual(raw(paths), before)
        self.assertTrue(self.no_run_folder())

    def test_fresh_and_offline_refuse_it(self) -> None:
        for other in ("--fresh", "--offline"):
            with self.subTest(other=other):
                self.assert_refused(f"{OPTION} cannot be used with {other}", other)

    def test_every_other_mode_refuses_it(self) -> None:
        modes = (album.MODE_CHECK, album.MODE_UNDO, "strip", "compare")
        for mode in modes:
            with self.subTest(mode=mode):
                self.assert_refused(
                    f"{OPTION} cannot be used with --mode {mode}", "--mode", mode
                )

    def test_a_dry_run_plans_the_removal(self) -> None:
        paths = self.keeper(LATE_SYLT)
        before = rb.snapshots(paths)
        result = self.run_album(OPTION, "--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertEqual(rb.snapshots(paths), before)
        (keeper,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        expected = f"{lyrics_modes.PLAN_REMOVE} (implausible: {LATE})"
        self.assertTrue(keeper.endswith(expected), keeper)
