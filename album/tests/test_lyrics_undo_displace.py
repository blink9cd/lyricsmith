"""Undo never destroys a sidecar it cannot prove is the run's: it displaces it.

Before undo removes a sidecar name the backup did not hold, or replaces a sidecar whose
bytes differ from the backed-up copy, it copies the file into the run folder under
``undo/``, mirroring the album, and names the copy in the track's result. A sidecar that
already matches its backup, or that the run recorded writing and nobody changed since,
is left without a copy. Every lyric here is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above

_TXT = "Lanterns on the jetty\nburn until the morning\n"
_HAND = b"A verse typed in by hand\nafter the run was over\n"
_EDITED = b"The run's verse, edited\n"


class DisplaceTest(rb.UndoCase):
    """A sidecar undo would remove or overwrite is kept in the run folder first."""

    def displaced(self, run_id: str, relpath: str) -> Path:
        """Where run *run_id*'s undo keeps a displaced file *relpath*."""
        return lr.run_folder(self.album, run_id) / "undo" / relpath

    def copy(self, run_id: str, relpath: str) -> str:
        """The album path the result names run *run_id*'s displaced *relpath* by."""
        return self.displaced(run_id, relpath).relative_to(self.album).as_posix()

    def test_a_hand_made_sidecar_on_an_untouched_track_is_kept(self) -> None:
        mp3 = self.track("CD1/01.mp3")
        manifest = self.back_up(mp3)
        mp3.with_suffix(".txt").write_bytes(_HAND)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.UNDONE)
        self.assertEqual(rb.sidecars(mp3), {})
        kept = self.displaced(manifest.run_id, "CD1/01.txt")
        self.assertEqual(kept.read_bytes(), _HAND)
        self.assertEqual(
            report.tracks[0].displaced, (self.copy(manifest.run_id, "CD1/01.txt"),)
        )
        self.assertIsNone(report.tracks[0].error)

    def test_a_sidecar_edited_after_the_run_is_displaced_not_lost(self) -> None:
        mp3 = self.track("01.mp3", txt=_TXT)
        manifest = rb.simulate_run(self.album, self.back_up(mp3), rb.RUN_PLAIN)
        mp3.with_suffix(".txt").write_bytes(_EDITED)
        report = self.undo()
        self.assertEqual(rb.sidecars(mp3), {"01.txt": _TXT.encode()})
        self.assertEqual(
            self.displaced(manifest.run_id, "01.txt").read_bytes(), _EDITED
        )
        self.assertEqual(
            report.tracks[0].displaced, (self.copy(manifest.run_id, "01.txt"),)
        )

    def test_a_sidecar_that_matches_its_backup_is_not_displaced(self) -> None:
        mp3 = self.track("01.mp3", txt=_TXT)
        manifest = self.back_up(mp3)
        report = self.undo()
        self.assertEqual(
            (report.tracks[0].error, report.tracks[0].displaced), (None, ())
        )
        self.assertFalse(self.displaced(manifest.run_id, "01.txt").exists())

    def test_a_re_undo_never_overwrites_an_earlier_displaced_copy(self) -> None:
        mp3 = self.track("01.mp3")
        manifest = self.back_up(mp3)
        rb.simulate_run(self.album, manifest)
        mp3.with_suffix(".lrc").write_bytes(_HAND)
        self.undo()
        rb.simulate_run(self.album, manifest)
        mp3.with_suffix(".lrc").write_bytes(_EDITED)
        report = self.undo()
        self.assertEqual(self.displaced(manifest.run_id, "01.lrc").read_bytes(), _HAND)
        self.assertEqual(
            self.displaced(manifest.run_id, "01-2.lrc").read_bytes(), _EDITED
        )
        self.assertEqual(
            report.tracks[0].displaced, (self.copy(manifest.run_id, "01-2.lrc"),)
        )
        self.assertEqual(rb.sidecars(mp3), {})

    def test_a_displacement_that_fails_leaves_the_sidecar_in_place(self) -> None:
        mp3 = self.track("01.mp3")
        self.back_up(mp3)
        mp3.with_suffix(".txt").write_bytes(_HAND)
        real = lyrics_files.write_confirmed

        def refuse_undo(target: Path, data: bytes) -> None:
            if "undo" in target.parts:
                raise OSError("no space left")
            real(target, data)

        with mock.patch.object(lyrics_files, "write_confirmed", refuse_undo):
            report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ERROR)
        self.assertEqual(report.tracks[0].displaced, ())
        self.assertEqual(rb.sidecars(mp3), {"01.txt": _HAND})


class LinkedTrackTest(rb.UndoCase):
    """A track turned into a link since the run is refused, and nothing outside changes."""

    def test_a_track_replaced_by_a_link_is_that_tracks_error(self) -> None:
        mp3 = self.track("01.mp3")
        rb.simulate_run(self.album, self.back_up(mp3))
        outside = self.tmp / "outside.mp3"
        mp3.rename(outside)
        before = outside.read_bytes()
        mp3.symlink_to(outside)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ERROR)
        self.assertIn("link", rb.undo_error(report.tracks[0]))
        self.assertEqual(outside.read_bytes(), before)

    def test_a_track_under_a_folder_linked_outside_is_that_tracks_error(self) -> None:
        mp3 = self.track("disc/01.mp3")
        rb.simulate_run(self.album, self.back_up(mp3))
        outside = self.tmp / "outside"
        mp3.parent.rename(outside)
        before = rb.snapshots(outside.iterdir())
        mp3.parent.symlink_to(outside, target_is_directory=True)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ERROR)
        self.assertIn("outside", rb.undo_error(report.tracks[0]))
        self.assertEqual(rb.snapshots(outside.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
