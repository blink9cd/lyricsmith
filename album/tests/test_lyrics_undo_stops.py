"""How an undo stops on Ctrl-C: the track being restored finishes, then undo reports.

A Ctrl-C that lands while a track's frames or sidecars are being restored, or while
its undo is being recorded, waits for that track and its record, so no MP3 is left
half-saved and no displaced copy goes unrecorded. Undo then stops, reporting the tracks
it reached and the rest as not reached, and the entry point prints that report and
exits 130. A record that fails behind the held interrupt is still reported. Every lyric
here is invented.
"""

from __future__ import annotations

import signal
import sys
from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_summary  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_undo  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: What the entry point prints for an undo interrupted after its first track.
INTERRUPTED_UNDO = f"""\
album undo {run_fakes.RUN_ID}: 2 tracks under album

 #  track     undo
 1  01 A.mp3  undone
 2  02 B.mp3  not reached
undone 1 · already undone 0 · errors 0
"""


def _interrupting_restore() -> AbstractContextManager[object]:
    """Patch the frame restore to finish, then receive a real Ctrl-C, on its first call."""
    real, calls = lyrics_undo.restore_frames, []

    def restore(
        mp3: Path, frames: Sequence[lm.FrameRecord], version: tuple[int, int]
    ) -> None:
        real(mp3, frames, version)
        calls.append(mp3)
        if len(calls) == 1:
            signal.raise_signal(signal.SIGINT)

    return mock.patch.object(lyrics_undo, "restore_frames", restore)


class UndoInterruptTest(rb.UndoCase):
    """A Ctrl-C mid-restore lets the track finish and be recorded, then stops undo."""

    def setUp(self) -> None:
        """Three tracks, run, so each has frames to restore."""
        super().setUp()
        self.paths = [self.track(f"0{n}.mp3") for n in (1, 2, 3)]
        self.run_id = rb.simulate_run(self.album, self.back_up(*self.paths)).run_id

    def test_the_restored_track_is_recorded_and_the_rest_not_reached(self) -> None:
        with _interrupting_restore():
            report = self.undo()
        self.assertTrue(report.interrupted)
        self.assertEqual(
            [(t.path, t.status) for t in report.tracks],
            [("01.mp3", lr.UndoStatus.UNDONE)],
        )
        self.assertEqual(report.not_reached, ("02.mp3", "03.mp3"))
        self.assertEqual(rb.lyrics_frames(self.paths[0]), {})
        self.assertTrue(rb.lyrics_frames(self.paths[1]))
        undone = [t.undone for t in self.manifest(self.run_id).tracks]
        self.assertEqual(undone, [True, False, False])

    def test_a_record_failing_behind_the_interrupt_is_still_reported(self) -> None:
        refused = mock.patch.object(
            lmf, "write_manifest", side_effect=OSError("disk full")
        )
        with _interrupting_restore(), refused:
            report = self.undo()
        self.assertTrue(report.interrupted)
        self.assertIn("disk full", report.record_error or "")
        self.assertEqual([t.path for t in report.tracks], ["01.mp3"])

    def test_an_uninterrupted_undo_reaches_every_track(self) -> None:
        report = self.undo()
        self.assertFalse(report.interrupted)
        self.assertEqual(report.not_reached, ())


class UndoInterruptCliTest(cli_fakes.CliCase):
    """The entry point prints an interrupted undo's report and exits 130."""

    def setUp(self) -> None:
        """Run a two-track album through the faked wiring."""
        super().setUp()
        fixtures.build_album(
            self.album, (TrackSpec("01 A.mp3", title="A"), TrackSpec("02 B.mp3"))
        )
        self.run_album()

    def test_the_report_shows_the_tracks_not_reached_and_exits_130(self) -> None:
        self.folder.undo_report = lr.UndoReport(
            run_fakes.RUN_ID,
            True,
            (lr.UndoTrackResult("01 A.mp3", lr.UndoStatus.UNDONE),),
            interrupted=True,
            not_reached=("02 B.mp3",),
        )
        result = self.run_album("--mode", "undo")
        self.assertEqual(
            (result.code, result.stdout),
            (album.EXIT_INTERRUPTED, INTERRUPTED_UNDO),
        )
        self.assertIn("interrupted", result.stderr)

    def test_a_record_error_behind_the_interrupt_exits_3(self) -> None:
        self.folder.undo_report = lr.UndoReport(
            run_fakes.RUN_ID,
            True,
            (lr.UndoTrackResult("01 A.mp3", lr.UndoStatus.UNDONE),),
            record_error="could not record the undo: disk full",
            interrupted=True,
            not_reached=("02 B.mp3",),
        )
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertIn(f"02 B.mp3  {lyrics_summary.NOT_REACHED}", result.stdout)
