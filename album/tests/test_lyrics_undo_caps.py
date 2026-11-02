"""Undo reads every sidecar and backup copy under a cap, never a huge file whole.

A sidecar beside a track is read under the lyrics cap the run itself reads one under,
and so is a sidecar copy in the run folder; a frame's export or exact copy is read under
the manifest's cap, so the backup refuses to write one past it: a frame undo could not
read back is that track's backup error, and the track is never written. A file past
its cap is that track's undo error, refused unread past the cap: nothing over it is
copied or written back, and the other tracks are still undone. Each oversized file here is sparse (`os.truncate`), so it costs no disk. Every
lyric here is invented.
"""

from __future__ import annotations

import codecs
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above

_TXT = "Salt on the window\nand the radio low\n"
#: One byte past each cap: the sidecar reader's, and the manifest's. The sidecar
#: reader's is the most bytes the lyrics cap allows, at four UTF-8 bytes a character
#: and a byte-order mark; one past it is past any smaller bound the reader could hold.
_OVER_SIDECAR = 4 * lr.MAX_LYRICS_CHARS + len(codecs.BOM_UTF8) + 1
_OVER_MANIFEST = lyrics_manifest.MAX_MANIFEST_BYTES + 1
_TOO_LARGE = "too large"


def _swell(path: Path, size: int) -> None:
    """Make *path* a sparse file of *size* bytes, replacing what it held."""
    path.write_bytes(b"")
    os.truncate(path, size)


class OversizedUndoTest(rb.UndoCase):
    """An oversized sidecar or backup copy is the track's error; the rest undo."""

    def run_two(self, first: Path) -> tuple[lr.Manifest, Path, Path]:
        """Back up and run *first* and an ordinary second track.

        Return the backup's manifest, the second track and the run folder.
        """
        second = self.track("02.mp3", txt=_TXT)
        manifest = self.back_up(first, second)
        rb.simulate_run(self.album, manifest)
        return manifest, second, lr.run_folder(self.album, manifest.run_id)

    def assert_refused(self, report: lr.UndoReport, second: Path) -> None:
        """The first track errored as too large, keeping nothing; the second undid."""
        self.assert_statuses(report, lr.UndoStatus.ERROR, lr.UndoStatus.UNDONE)
        self.assertIn(_TOO_LARGE, rb.undo_error(report.tracks[0]))
        self.assertEqual(report.tracks[0].displaced, ())
        self.assertEqual(rb.sidecars(second), {"02.txt": _TXT.encode()})

    def before(self, manifest: lr.Manifest) -> lr.TrackBefore:
        """The first track's backup record in *manifest*."""
        before = manifest.tracks[0].before
        assert before is not None
        return before

    def test_an_oversized_sidecar_undo_would_remove_is_refused_uncopied(
        self,
    ) -> None:
        first = self.track("01.mp3")
        _, second, run_dir = self.run_two(first)
        _swell(first.with_suffix(".txt"), _OVER_SIDECAR)
        report = self.undo()
        self.assert_refused(report, second)
        self.assertEqual(first.with_suffix(".txt").stat().st_size, _OVER_SIDECAR)
        self.assertFalse((run_dir / "undo").exists())

    def test_an_oversized_sidecar_undo_would_replace_is_refused_uncopied(
        self,
    ) -> None:
        first = self.track("01.mp3", txt=_TXT)
        _, second, run_dir = self.run_two(first)
        _swell(first.with_suffix(".txt"), _OVER_SIDECAR)
        report = self.undo()
        self.assert_refused(report, second)
        self.assertEqual(first.with_suffix(".txt").stat().st_size, _OVER_SIDECAR)
        self.assertFalse((run_dir / "undo").exists())

    def test_an_oversized_sidecar_copy_is_never_written_back(self) -> None:
        first = self.track("01.mp3", txt=_TXT)
        manifest, second, run_dir = self.run_two(first)
        copy = self.before(manifest).sidecars[0].file
        assert copy is not None
        _swell(run_dir / copy, _OVER_SIDECAR)
        report = self.undo()
        self.assert_refused(report, second)
        self.assertFalse(first.with_suffix(".txt").exists())

    def test_an_oversized_frame_export_is_refused(self) -> None:
        first = self.track("01.mp3", (tb.uslt("a lamp in the boathouse"),))
        manifest, second, run_dir = self.run_two(first)
        _swell(run_dir / self.before(manifest).frames[0].file, _OVER_MANIFEST)
        untouched = rb.snapshot(first)
        report = self.undo()
        self.assert_refused(report, second)
        self.assertEqual(rb.snapshot(first), untouched)

    def test_an_oversized_exact_frame_copy_is_refused(self) -> None:
        frames = (tb.sylt((40, "counted"), time_format=tb.MPEG_FRAMES),)
        first = self.track("01.mp3", frames)
        manifest, second, run_dir = self.run_two(first)
        exact = self.before(manifest).frames[0].exact
        assert exact is not None
        _swell(run_dir / exact, _OVER_MANIFEST)
        untouched = rb.snapshot(first)
        report = self.undo()
        self.assert_refused(report, second)
        self.assertEqual(rb.snapshot(first), untouched)


class BackupFrameCapTest(rb.UndoCase):
    """A frame whose export or exact copy undo could not read back is not backed up."""

    def assert_backup_error(self, frames: tuple[lm.FrameRecord, ...], cap: int) -> None:
        """Back up a track of *frames* under *cap*: its backup error, the other's kept."""
        first = self.track("01.mp3", frames)
        second = self.track("02.mp3", txt=_TXT)
        with mock.patch.object(lyrics_manifest, "MAX_MANIFEST_BYTES", cap):
            manifest = self.back_up(first, second)
        refused, kept = manifest.tracks
        assert refused.after is not None and refused.after.error is not None
        self.assertIsNone(refused.before)
        self.assertEqual(refused.after.error.exc_type, "FrameTooLargeError")
        self.assertIn(
            f"too large to back up: over {cap} bytes", refused.after.error.message
        )
        self.assertIsNotNone(kept.before)

    def test_a_frame_export_past_the_cap_is_the_tracks_backup_error(self) -> None:
        self.assert_backup_error((tb.uslt("a lamp in the boathouse " * 8),), 64)

    def test_an_exact_copy_past_the_cap_is_the_tracks_backup_error(self) -> None:
        # Timed in MPEG frames, so its LRC export cannot carry it and an exact copy is
        # written; the cap passes the export and refuses the far larger JSON copy.
        frames = (tb.sylt((40, "counted"), time_format=tb.MPEG_FRAMES),)
        self.assert_backup_error(frames, 64)


if __name__ == "__main__":
    unittest.main()
