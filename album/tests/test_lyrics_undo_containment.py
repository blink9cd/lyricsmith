"""Undo keeps every displaced copy inside the run folder, never through a link out.

A run folder can arrive with an album (a shared or copied one), so its ``undo``
folder, or a track's folder under it, may be a link planted to somewhere else. Undo
displaces a sidecar into that folder before replacing or removing it; through such a
link the copy would land outside the album, carrying whatever bytes stood beside the
track. Each test plants one, undoes, and checks that nothing was written outside, that
the track needing the copy is that track's error with its sidecar left in place, and
that the other tracks are still undone. Every lyric here is invented.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above

_HAND = b"A chorus written in after the run\nby someone sharing the album\n"
_REFUSED = "OutsideRunFolderError"


class LinkedUndoFolderTest(rb.UndoCase):
    """A linked ``undo`` folder, or a linked track folder under it, is never written."""

    def run_two(self, folder: str) -> tuple[Path, Path, Path]:
        """Back up and run two tracks in *folder*; edit the first's sidecar since.

        The first track's sidecar no longer holds what the run wrote, so undo must
        displace it; the second's still does, so it goes without a copy. Return both
        tracks and the run folder.
        """
        edited = self.track(f"{folder}01.mp3")
        kept = self.track(f"{folder}02.mp3")
        manifest = rb.simulate_run(self.album, self.back_up(edited, kept))
        edited.with_suffix(".lrc").write_bytes(_HAND)
        return edited, kept, lr.run_folder(self.album, manifest.run_id)

    def assert_refused(self, report: lr.UndoReport, edited: Path) -> None:
        """The edited track errored, keeping its sidecar; the other was undone."""
        self.assert_statuses(report, lr.UndoStatus.ERROR, lr.UndoStatus.UNDONE)
        self.assertIn(_REFUSED, rb.undo_error(report.tracks[0]))
        self.assertEqual(report.tracks[0].displaced, ())
        self.assertEqual(rb.sidecars(edited), {"01.lrc": _HAND})
        self.assertEqual(report.tracks[1].error, None)

    def outside(self) -> Path:
        """An empty folder outside the album, where a planted link leads."""
        folder = self.tmp / "outside"
        folder.mkdir()
        return folder

    def test_a_linked_undo_folder_is_refused_and_nothing_lands_outside(self) -> None:
        edited, kept, run_dir = self.run_two("")
        outside = self.outside()
        (run_dir / "undo").symlink_to(outside, target_is_directory=True)
        report = self.undo()
        self.assert_refused(report, edited)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(rb.sidecars(kept), {})

    def test_a_linked_track_folder_under_undo_is_refused(self) -> None:
        edited, kept, run_dir = self.run_two("CD1/")
        outside = self.outside()
        (run_dir / "undo").mkdir()
        (run_dir / "undo" / "CD1").symlink_to(outside, target_is_directory=True)
        report = self.undo()
        self.assert_refused(report, edited)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(rb.sidecars(kept), {})

    def test_a_dangling_undo_link_is_refused_and_never_created(self) -> None:
        edited, _, run_dir = self.run_two("")
        nowhere = self.tmp / "nowhere"
        (run_dir / "undo").symlink_to(nowhere, target_is_directory=True)
        report = self.undo()
        self.assert_refused(report, edited)
        self.assertFalse(os.path.lexists(nowhere))

    def test_an_undo_folder_linked_out_while_undo_runs_is_refused(self) -> None:
        edited, _, run_dir = self.run_two("")
        outside = self.outside()
        real = lyrics_files.inside_run_folder

        def plant_after_the_check(folder: Path, relative: str) -> Path:
            # The first look at the undo folder passes; a link put there between
            # that look and the write must still be refused.
            found = real(folder, relative)
            link = run_dir / "undo"
            if relative.startswith("undo") and not os.path.lexists(link):
                link.symlink_to(outside, target_is_directory=True)
            return found

        with mock.patch.object(
            lyrics_files, "inside_run_folder", plant_after_the_check
        ):
            report = self.undo()
        self.assert_refused(report, edited)
        self.assertEqual(list(outside.iterdir()), [])

    def test_an_undo_folder_linked_inside_the_run_folder_is_still_used(self) -> None:
        edited, _, run_dir = self.run_two("")
        (run_dir / "kept").mkdir()
        (run_dir / "undo").symlink_to(run_dir / "kept", target_is_directory=True)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.UNDONE, lr.UndoStatus.UNDONE)
        self.assertEqual((run_dir / "kept" / "01.lrc").read_bytes(), _HAND)
        self.assertEqual(rb.sidecars(edited), {})


if __name__ == "__main__":
    unittest.main()
