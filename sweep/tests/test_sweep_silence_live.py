"""The live boundary test for --mode silence: a real ffmpeg measures a generated MP3.

Skipped when ffmpeg is not on PATH. It reads ``shutil.which`` itself rather than the
pass's own lookup. The only input is a silent MP3 the album tool's test builder writes
under the test's temporary directory, in a library there; the list goes to the
isolated state directory. Nothing touches the network or a real library.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import silence_fixtures as sfx  # noqa: E402 - import follows the sys.path bootstrap above

#: ffmpeg on PATH at import, or None; read here, not through the pass's lookup.
_FFMPEG = shutil.which("ffmpeg")


@unittest.skipIf(_FFMPEG is None, "ffmpeg not on PATH")
class LiveSilenceTest(sfx.SilenceCase):
    """A short untitled silent track, measured for real, is listed as a gap track."""

    def test_a_real_measurement_lists_a_gap_track(self) -> None:
        sfx.add_track(
            self.library / "Artist" / "Album", "02 Gap.mp3", seconds=4.0, title=None
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sweep.main(["--mode", "silence", "--library", str(self.library)])
        self.assertEqual(code, 0, stderr.getvalue())
        (row, marker) = self.list_lines()
        self.assertTrue(row.startswith("likely meant\tgap track"), row)
        self.assertTrue(row.endswith("\tArtist/Album/02 Gap.mp3"), row)
        self.assertEqual(marker, "#album-done\tArtist/Album")
