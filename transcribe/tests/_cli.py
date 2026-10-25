"""The shared fixture of the black-box CLI suites: a fake whisper-cli, models, a library.

Not a suite itself (discovery loads only test_*.py): each CLI suite subclasses CliCase.
"""

from __future__ import annotations

import contextlib
import io
import json

import at_models
import transcribe
from tests._isolation import IsolatedCase, write_fake_whisper

#: The two model files the setup check looks for, written empty.
MODEL_NAMES = (at_models.WHISPER_MODEL_NAME, at_models.VAD_MODEL_NAME)
#: The invented library: two audio files in two folders.
LIBRARY = ("a/one.mp3", "b/two.mp3")


class CliCase(IsolatedCase):
    """An isolated case with the fake whisper-cli on PATH, both models and a library."""

    def setUp(self) -> None:
        """Write the fake, the empty models and the empty audio files."""
        super().setUp()
        write_fake_whisper(self.bin)
        self.models = self.root / "models"
        self.models.mkdir()
        for name in MODEL_NAMES:
            (self.models / name).write_bytes(b"")
        self.lib = self.root / "lib"
        for relative in LIBRARY:
            (self.lib / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.lib / relative).write_bytes(b"")

    def run_main(self, *args: str) -> tuple[int, str, str]:
        """main() with --model-dir set; its exit code, stdout and stderr."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = transcribe.main(["--model-dir", str(self.models), *args])
        return code, out.getvalue(), err.getvalue()

    def calls(self) -> list[list[str]]:
        """Every argv the fake was called with, in order."""
        if not self.log.exists():
            return []
        text = self.log.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]

    def transcriptions(self) -> list[list[str]]:
        """The calls that were not --version."""
        return [call for call in self.calls() if call != ["--version"]]
