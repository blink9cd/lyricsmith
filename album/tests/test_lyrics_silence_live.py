"""The live boundary test for lyrics_silence: a real ffmpeg measures two local files.

Skipped when ffmpeg is not on PATH. It reads ``shutil.which`` itself, never
``find_ffmpeg``, which the isolated base may replace. Nothing touches the network:
the silent input is the fixtures' generated MP3, and the tone is a WAV ffmpeg
synthesizes from its own lavfi ``sine`` source, so no MP3 encoder is needed. Both
files are written only under the test's temporary directory.
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_silence as ls  # noqa: E402 - import follows the sys.path bootstrap above

# On sys.path once lyrics_silence's own bootstrap has run.
import bounded_run  # noqa: E402 - import follows the sys.path bootstrap above

#: ffmpeg on PATH at import, or None; read here, not through ls.find_ffmpeg.
_FFMPEG = shutil.which("ffmpeg")


@unittest.skipIf(_FFMPEG is None, "ffmpeg not on PATH")
class LiveMeasureTest(fixtures.IsolatedCase):
    """A silent MP3 measures silent; a 440 Hz tone does not."""

    def _tone(self, ffmpeg: str) -> Path:
        """A one-second 440 Hz WAV under the test's temporary directory."""
        tone = self.tmp / "tone.wav"
        argv = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "sine=f=440:d=1", str(tone),
        ]  # fmt: skip
        done = bounded_run.run_bounded(argv, timeout=ls.SILENCE_TIMEOUT_S)
        self.assertEqual(done.returncode, 0, done.stderr)
        return tone

    def test_silence_and_a_tone(self) -> None:
        ffmpeg = _FFMPEG
        if ffmpeg is None:  # pragma: no cover - the class skip already guarantees it
            self.skipTest("ffmpeg not on PATH")
        silent = fixtures.build_silent_mp3(self.tmp / "silent.mp3", 1.0)
        quiet = ls.measure_level(silent, ffmpeg)
        loud = ls.measure_level(self._tone(ffmpeg), ffmpeg)
        self.assertTrue(isinstance(quiet, ls.Peak) and ls.is_silent(quiet), quiet)
        self.assertTrue(isinstance(loud, ls.Peak) and not ls.is_silent(loud), loud)
