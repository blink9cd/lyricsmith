"""The real whisper-cli on its own bundled sample, with the real models.

Skips, with the reason, when whisper-cli, its bundled jfk.wav or either model is absent,
so a machine without whisper.cpp still gates. Under a logging stand-in for whisper-cli
no jfk.wav sits beside it, so the test skips without calling it.
Reads the real per-user model folder; writes only into a temporary folder.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import transcribe
import at_models
from at_types import MACHINE_BY_LINE, TIMESTAMP_LINE


def bundled_sample() -> Path | None:
    """whisper.cpp's jfk.wav beside the installed whisper-cli, if there is one."""
    found = shutil.which("whisper-cli")
    if found is None:
        return None
    sample = (
        Path(os.path.realpath(found)).parent.parent
        / "share"
        / "whisper.cpp"
        / "jfk.wav"
    )
    return sample if sample.is_file() else None


SAMPLE = bundled_sample()
MODEL_DIR = at_models.default_model_dir(sys.platform, os.environ, Path.home())
MODELS_PRESENT = all(
    (MODEL_DIR / model_file.name).is_file() for model_file in at_models.MANIFEST
)


@unittest.skipUnless(
    SAMPLE is not None and MODELS_PRESENT,
    "needs whisper-cli with its bundled jfk.wav and both models in the per-user model "
    "folder (transcribe.py --mode fetch-models)",
)
class LiveWhisperTest(unittest.TestCase):
    def test_the_bundled_sample_transcribes_and_is_labeled(self) -> None:
        assert SAMPLE is not None
        with tempfile.TemporaryDirectory() as out:
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                code = transcribe.main(["--path", str(SAMPLE), "--output-dir", out])
            self.assertEqual(code, 0)
            lines = (Path(out) / "jfk.lrc").read_text(encoding="utf-8").split("\n")
            self.assertTrue(lines[0].startswith("[re:lyricsmith transcribe "))
            self.assertEqual(lines[1], MACHINE_BY_LINE)
            self.assertRegex(lines[2], TIMESTAMP_LINE)
            text = (Path(out) / "jfk.txt").read_text(encoding="utf-8")
            # The bundled clip's best-known phrase: a nonempty but wrong transcript
            # (a silent model, a garbled decode) fails here rather than passing.
            self.assertIn("ask not what your country", text.lower())
