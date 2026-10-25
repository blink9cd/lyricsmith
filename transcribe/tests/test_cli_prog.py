"""The program name: run as `lyricsmith transcribe`, the help and notices name it."""

from __future__ import annotations

import contextlib
import io

import transcribe
from tests._isolation import IsolatedCase

#: The program name the lyricsmith dispatcher hands the transcribe tool.
DISPATCHED = "lyricsmith transcribe"


class DispatchedProgTest(IsolatedCase):
    """`main` given the dispatcher's program name shows it wherever it names itself."""

    def dispatched(self, *args: str) -> tuple[int, str, str]:
        """main() with *args* under the dispatcher's name; its code, stdout and stderr."""
        out, err = io.StringIO(), io.StringIO()
        models = str(self.root / "models")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = transcribe.main(["--model-dir", models, *args], prog=DISPATCHED)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_help_names_the_transcribe_command(self) -> None:
        code, out, _ = self.dispatched("--help")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(f"usage: {DISPATCHED} "), out)

    def test_a_setup_error_names_the_transcribe_command(self) -> None:
        code, _, err = self.dispatched()
        self.assertEqual(code, transcribe.EXIT_SETUP)
        self.assertTrue(err.startswith(f"{DISPATCHED}: "), err)
