"""`--fresh` declines an answer the check's unlike-backup row would flag.

A fresh run replaces a track's lyrics only with an answer the check would call like the
lyrics the backup holds: an unlike answer is not accepted, so the track keeps its old
frames and sidecars, byte for byte, and reports `kept existing` with the reason. A run
without `--fresh` still writes such an answer and flags it, and a track with no old
lyrics frames has nothing to be unlike. Each test drives the entry point with its real
wiring; only the clock and the provider table are faked, and every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh import FreshCliCase  # noqa: E402 - import follows the sys.path bootstrap above

#: Old sidecars beside the track, which a declined answer must leave exactly as they are.
_OLD_LRC = "[00:01.00]Under the orchard wall we lay\n[00:04.00]and named the clouds\n"
_OLD_TXT = "Under the orchard wall we lay\nand named the clouds\n"
#: Old lyrics frames holding a different song from the answer the providers give.
_ORCHARD = (tag_builders.uslt(fixtures.ORCHARD_PLAIN),)
#: Old lyrics frames holding another transcription of the answer's own song.
_EDITED = (tag_builders.uslt(fixtures.LIGHTHOUSE_EDITED),)
#: Old lyrics frames in a language other than the run's alone, holding a different song.
_FOREIGN_ORCHARD = (tag_builders.uslt(fixtures.ORCHARD_PLAIN, lang="fra"),)
#: What a declined track's row and progress line say, before the measured value.
_DECLINED = "kept existing: new answer unlike backup (similarity "
KEEPER = TrackSpec("01 Keeper.mp3", title="The Keeper", lrc=_OLD_LRC, txt=_OLD_TXT)
MILL = TrackSpec("02 Mill.mp3", title="The Mill")


def raw(paths: list[Path]) -> dict[str, bytes]:
    """Every file beside and including *paths*, by name, as its bytes."""
    folder = paths[0].parent
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()}


class FreshUnlikeTest(FreshCliCase):
    """An unlike answer is declined under --fresh, and written without it."""

    def setUp(self) -> None:
        """Two tracks the providers both answer with the lighthouse song."""
        super().setUp()
        self.answer(
            {"The Keeper": fixtures.LIGHTHOUSE_LRC, "The Mill": fixtures.LIGHTHOUSE_LRC}
        )

    def planted(self, frames: tuple[lm.FrameRecord, ...]) -> list[Path]:
        """The keeper with *frames* and both sidecars, then the mill with nothing."""
        paths = fixtures.build_album(self.album, (KEEPER, MILL))
        tag_builders.plant_frames(paths[0], frames)
        return paths

    @staticmethod
    def providers_line(stdout: str) -> str:
        """The run summary's providers tally line."""
        (line,) = [row for row in stdout.splitlines() if row.startswith("providers:")]
        return line

    def test_an_unlike_answer_leaves_the_track_byte_identical(self) -> None:
        paths = self.planted(_ORCHARD)
        before = raw(paths)
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        after = raw(paths)
        for name in ("01 Keeper.mp3", "01 Keeper.lrc", "01 Keeper.txt"):
            self.assertEqual(after[name], before[name], name)
        self.assertTrue(rb.lyrics_frames(paths[1]))

    def test_the_reason_is_reported_and_recorded(self) -> None:
        self.planted(_ORCHARD)
        result = self.fresh()
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        self.assertIn(_DECLINED, row)
        self.assertIn(f"01 Keeper.mp3: {_DECLINED}", result.stderr)
        self.assertIn("kept existing 1", result.stdout)
        self.assertIn("flagged 0", result.stdout)
        keeper, mill = self.afters()
        # LRCLIB answered both tracks; the declined answer is not counted as kept.
        self.assertIn("LRCLIB 1/2", self.providers_line(result.stdout))
        assert keeper is not None and keeper.check is not None and mill is not None
        self.assertEqual(keeper.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertEqual(
            [flag.rule for flag in keeper.check.flags], [lr.CheckRule.UNLIKE_BACKUP]
        )
        self.assertEqual(keeper.check.skips, ())
        self.assertEqual(
            (keeper.frames_written, keeper.sidecars_removed, keeper.sidecar_written),
            (False, (), None),
        )
        self.assertEqual(mill.status, lr.OutcomeStatus.WRITTEN)

    def test_a_like_answer_is_still_written(self) -> None:
        paths = self.planted(_EDITED)
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        keeper, _mill = self.afters()
        assert keeper is not None and keeper.check is not None
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertEqual(keeper.check.verdict, lr.CheckVerdict.PASS)
        self.assertEqual(list(rb.sidecars(paths[0])), ["01 Keeper.lrc"])

    def test_without_fresh_an_unlike_answer_is_written_and_flagged(self) -> None:
        # No sidecar, so the run without --fresh asks the providers too.
        paths = fixtures.build_album(
            self.album, (TrackSpec("01 Keeper.mp3", title="The Keeper"),)
        )
        tag_builders.plant_frames(paths[0], _ORCHARD)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (keeper,) = self.afters()
        assert keeper is not None and keeper.check is not None
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertEqual(
            [flag.rule for flag in keeper.check.flags], [lr.CheckRule.UNLIKE_BACKUP]
        )
        self.assertIn("flagged 1", result.stdout)
        self.assertNotIn(_DECLINED, result.stdout)

    def test_a_track_with_no_old_frames_has_nothing_to_be_unlike(self) -> None:
        # Sidecars alone are not what the check compares with: the backup's frames are.
        paths = fixtures.build_album(self.album, (KEEPER, MILL))
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(
            [after.status for after in self.afters() if after is not None],
            [lr.OutcomeStatus.WRITTEN, lr.OutcomeStatus.WRITTEN],
        )
        self.assertEqual(list(rb.sidecars(paths[0])), ["01 Keeper.lrc"])

    def test_old_frames_in_another_language_alone_still_decline(self) -> None:
        # With no frame in the run's language, the check compares the answer with
        # the other language's lyrics, so an answer unlike them keeps the old lyrics.
        paths = self.planted(_FOREIGN_ORCHARD)
        before = raw(paths)
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        keeper, _mill = self.afters()
        assert keeper is not None and keeper.check is not None
        self.assertEqual(keeper.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertEqual(
            [flag.rule for flag in keeper.check.flags], [lr.CheckRule.UNLIKE_BACKUP]
        )
        self.assertEqual(raw(paths)["01 Keeper.mp3"], before["01 Keeper.mp3"])

    def test_undo_of_a_run_that_declined_an_answer_restores_the_album(self) -> None:
        paths = self.planted(_ORCHARD)
        before = rb.snapshots(paths)
        self.fresh()
        self.assertNotEqual(rb.snapshots(paths), before)
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertNotIn("displaced", result.stdout)
        self.assertEqual(rb.snapshots(paths), before)
