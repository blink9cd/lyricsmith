"""What undo records and reports: displaced copies, the run's own sidecars, a failed record.

Each displaced copy is named in the track's result, an error's included, and kept in
the manifest track's record, so its origin can be found later. A sidecar whose bytes
still hash to what the run recorded is the run's own and goes without a copy; one a
run killed before recording left behind is displaced as any unproven file is. When the
manifest cannot be written after a track, undo stops and reports what it did so far.
Every lyric here is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above

_TXT = "Lanterns on the jetty\nburn until the morning\n"
_HAND = b"A verse typed in by hand\nafter the run was over\n"


def _copy(run_id: str, relpath: str) -> str:
    """The album path of run *run_id*'s displaced copy *relpath*."""
    return f"{lr.RUN_FOLDER_NAME}/{run_id}/undo/{relpath}"


class DisplacedRecordTest(rb.UndoCase):
    """Displaced copies are named in the result and kept in the manifest."""

    def test_an_error_after_a_displacement_still_names_the_copy(self) -> None:
        mp3 = self.track("01.mp3")
        manifest = self.back_up(mp3)
        mp3.with_suffix(".lrc").write_bytes(_HAND)
        mp3.with_suffix(".txt").write_bytes(_HAND)
        real, copies = lyrics_files.write_confirmed, []

        def refuse_second_copy(target: Path, data: bytes) -> None:
            if "undo" in target.parts:
                copies.append(target)
                if len(copies) == 2:
                    raise OSError("no space left")
            real(target, data)

        with mock.patch.object(lyrics_files, "write_confirmed", refuse_second_copy):
            report = self.undo()
        lrc_copy = _copy(manifest.run_id, "01.lrc")
        (result,) = report.tracks
        self.assertEqual(
            (result.status, result.displaced), (lr.UndoStatus.ERROR, (lrc_copy,))
        )
        recorded = self.manifest(manifest.run_id).tracks[0]
        self.assertEqual((recorded.undone, recorded.displaced), (False, (lrc_copy,)))

        report = self.undo()
        txt_copy = _copy(manifest.run_id, "01.txt")
        self.assertEqual(report.tracks[0].displaced, (txt_copy,))
        self.assertIsNone(report.tracks[0].error)
        recorded = self.manifest(manifest.run_id).tracks[0]
        self.assertEqual(
            (recorded.undone, recorded.displaced), (True, (lrc_copy, txt_copy))
        )


class RunSidecarTest(rb.UndoCase):
    """A sidecar the run recorded, unchanged since, is removed or replaced outright."""

    def test_an_ordinary_run_is_undone_displacing_nothing(self) -> None:
        tracks = [self.track("01.mp3", txt=_TXT), self.track("02.mp3", lrc="untimed\n")]
        before = rb.snapshots(tracks)
        manifest = rb.simulate_run(self.album, self.back_up(*tracks))
        report = self.undo()
        self.assertEqual([t.displaced for t in report.tracks], [(), ()])
        self.assertEqual(rb.snapshots(tracks), before)
        self.assertFalse((lr.run_folder(self.album, manifest.run_id) / "undo").exists())

    def test_a_run_sidecar_replaced_since_is_displaced(self) -> None:
        mp3 = self.track("01.mp3", txt=_TXT)
        manifest = rb.simulate_run(self.album, self.back_up(mp3), rb.RUN_PLAIN)
        mp3.with_suffix(".txt").write_bytes(_HAND)
        report = self.undo()
        self.assertEqual(
            report.tracks[0].displaced, (_copy(manifest.run_id, "01.txt"),)
        )

    def test_a_run_killed_before_recording_displaces_what_it_wrote(self) -> None:
        mp3 = self.track("01.mp3")
        manifest = rb.simulate_run(self.album, self.back_up(mp3), record=False)
        report = self.undo()
        self.assertEqual(
            report.tracks[0].displaced, (_copy(manifest.run_id, "01.lrc"),)
        )


class RecordFailureTest(rb.UndoCase):
    """A manifest write that fails after a track stops undo with a partial report."""

    def test_the_tracks_undone_so_far_are_reported_and_undo_stops(self) -> None:
        for error in (OSError("disk full"), lm.ReadBackError("did not read back")):
            with self.subTest(error=type(error).__name__):
                self.album = self.tmp / type(error).__name__
                self.album.mkdir()
                first, second = self.track("01.mp3"), self.track("02.mp3")
                rb.simulate_run(self.album, self.back_up(first, second))
                with mock.patch.object(lmf, "write_manifest", side_effect=error):
                    report = self.undo()
                self.assertEqual(
                    [(t.path, t.status) for t in report.tracks],
                    [("01.mp3", lr.UndoStatus.UNDONE)],
                )
                self.assertIn(str(error), report.record_error or "")
                self.assertEqual(rb.lyrics_frames(first), {})
                self.assertTrue(rb.lyrics_frames(second))

    def test_a_complete_undo_has_no_record_error(self) -> None:
        rb.simulate_run(self.album, self.back_up(self.track("01.mp3")))
        self.assertIsNone(self.undo().record_error)


if __name__ == "__main__":
    unittest.main()
