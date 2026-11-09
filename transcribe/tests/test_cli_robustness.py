"""Unreadable paths, unreadable folders and hostile arguments, through main()."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import at_discover
import at_models
import transcribe
from at_types import EXIT_FILE_FAILED, EXIT_SETUP
from tests._cli import CliCase
from tests._isolation import IsolatedCase

#: A folder name that would ring the terminal's bell if it reached it raw.
LOCKED = "locked\x07"


@contextlib.contextmanager
def raising_for(method: str, target: Path) -> Iterator[None]:
    """Make Path.<method> raise PermissionError for target only, as 3.12 does.

    On 3.14 a path under an unsearchable folder answers False instead, so a real
    unsearchable folder would not exercise the 3.12 behavior these tests pin.
    """
    real = getattr(Path, method)

    def answer(self: Path, *args: Any, **kwargs: Any) -> bool:
        if self == target:
            raise PermissionError(13, "Permission denied", str(self))
        return bool(real(self, *args, **kwargs))

    with mock.patch.object(Path, method, autospec=True, side_effect=answer):
        yield


class UnreadableTest(CliCase):
    def test_an_unreadable_path_is_a_setup_error(self) -> None:
        for method in ("exists", "is_dir"):
            with self.subTest(method=method), raising_for(method, self.lib):
                code, _, err = self.run_main("--path", str(self.lib))
                self.assertEqual(code, EXIT_SETUP)
                self.assertIn("cannot be read", err)
        self.assertEqual(self.calls(), [])

    def test_an_unreadable_model_is_a_setup_error(self) -> None:
        model = self.models / at_models.WHISPER_MODEL_NAME
        with raising_for("is_file", model):
            code, _, err = self.run_main("--path", str(self.lib))
            self.assertEqual(code, EXIT_SETUP)
            self.assertIn(f"{model} cannot be read", err)
            code, out, _ = self.run_main("--path", str(self.lib), "--dry-run")
            self.assertEqual(code, EXIT_SETUP)
            self.assertIn("(unreadable)", out)
        self.assertEqual(self.transcriptions(), [])

    def test_a_model_without_read_access_is_a_setup_error(self) -> None:
        model = self.models / at_models.VAD_MODEL_NAME
        real = os.access

        def access(path: Any, mode: int, *args: Any, **kwargs: Any) -> bool:
            if Path(path) == model and mode == os.R_OK:
                return False
            return real(path, mode, *args, **kwargs)

        with mock.patch.object(os, "access", side_effect=access):
            code, _, err = self.run_main("--path", str(self.lib))
            self.assertEqual(code, EXIT_SETUP)
            self.assertIn(f"{model} cannot be read", err)
            code, out, _ = self.run_main("--path", str(self.lib), "--dry-run")
            self.assertEqual(code, EXIT_SETUP)
            self.assertIn(f"{model} (unreadable)", out)
        self.assertEqual(self.transcriptions(), [])

    def test_an_unreadable_whisper_cli_is_a_setup_error(self) -> None:
        with raising_for("is_file", self.fake):
            code, _, err = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn(f"{self.fake.name} cannot be read", err)
        self.assertEqual(self.calls(), [])

    def test_an_output_that_cannot_be_checked_fails_only_its_file(self) -> None:
        for method, name in (("is_file", "one.lrc"), ("exists", "one.txt")):
            with self.subTest(method=method):
                self.setUp()
                with raising_for(method, self.lib / "a" / name):
                    code, out, _ = self.run_main("--path", str(self.lib))
                self.assertEqual(code, EXIT_FILE_FAILED)
                self.assertIn("failed                 a/one.mp3", out)
                self.assertIn("    could not check the output: [Errno 13]", out)
                self.assertIn(
                    "transcribed 1, overwritten 0, skipped 0, refused 0, failed 1", out
                )
                self.assertTrue((self.lib / "b" / "two.lrc").is_file())
                self.assertEqual(len(self.transcriptions()), 1)


# WalkErrorTest reaches the same rows on every host, through the walk's error list.
@unittest.skipIf(
    sys.platform == "win32",
    "Windows forbids a control character in a folder name and ignores mode 0 on one",
)
@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads any folder")
class UnreadableFolderTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        locked = self.lib / LOCKED
        locked.mkdir()
        (locked / "hidden-from-us.mp3").write_bytes(b"")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)

    def test_an_unreadable_folder_is_a_failed_row(self) -> None:
        code, out, _ = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn("failed                 locked\\x07", out)
        self.assertIn("    could not read the folder: Permission denied", out)
        self.assertIn(
            "transcribed 2, overwritten 0, skipped 0, refused 0, failed 1", out
        )
        self.assertNotIn("\x07", out)

    def test_a_dry_run_shows_it_and_exits_1(self) -> None:
        code, out, _ = self.run_main("--path", str(self.lib), "--dry-run")
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn("failed                 locked\\x07", out)
        self.assertIn("transcribe             a/one.mp3", out)
        self.assertEqual(self.calls(), [])


class HostileArgumentTest(IsolatedCase):
    def test_a_stray_argument_is_escaped_and_the_usage_keeps_its_lines(self) -> None:
        err = io.StringIO()
        with (
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            transcribe.main(["--mode", "fetch-models", "evil\x1b[2Jname.mp3"])
        self.assertEqual(caught.exception.code, EXIT_SETUP)
        text = err.getvalue()
        self.assertNotIn("\x1b", text)
        self.assertIn("unrecognized arguments: evil\\x1b[2Jname.mp3", text)
        self.assertTrue(text.startswith("usage:"))
        self.assertGreaterEqual(text.count("\n"), 3)


@contextlib.contextmanager
def walk_error(filename: str | None) -> Iterator[None]:
    """Make the walk report one unreadable folder, as os.walk's onerror would."""
    real = at_discover.find_audio

    def find(
        path: Path, extensions: frozenset[str], errors: list[OSError] | None = None
    ) -> list[Path]:
        found = real(path, extensions, errors)
        if errors is not None:
            errors.append(PermissionError(13, "Permission denied", filename))
        return found

    with mock.patch.object(at_discover, "find_audio", side_effect=find):
        yield


class WalkErrorTest(CliCase):
    """The unreadable-folder rows, reached without a real locked folder (root too)."""

    def test_a_walk_error_is_a_failed_row_in_a_run_and_a_dry_run(self) -> None:
        with walk_error(str(self.lib / LOCKED)):
            code, out, _ = self.run_main("--path", str(self.lib))
            self.assertEqual(code, EXIT_FILE_FAILED)
            self.assertIn("failed                 locked\\x07", out)
            self.assertIn("    could not read the folder: Permission denied", out)
            self.assertIn(
                "transcribed 2, overwritten 0, skipped 0, refused 0, failed 1", out
            )
            code, out, _ = self.run_main(
                "--path", str(self.lib), "--overwrite", "--dry-run"
            )
            self.assertEqual(code, EXIT_FILE_FAILED)
            self.assertIn("failed                 locked\\x07", out)
            self.assertIn("overwrite (ours)       a/one.mp3", out)

    def test_a_walk_error_without_a_filename_names_the_walk_root(self) -> None:
        # OSError.filename may be None; the row then names the --path folder.
        with walk_error(None):
            code, out, _ = self.run_main("--path", str(self.lib), "--dry-run")
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn("failed                 .\n", out)
        self.assertNotIn("None", out)


class UnreadablePathFolderTest(CliCase):
    """A --path folder that cannot be listed is a setup error, not a failed row."""

    def test_a_path_folder_without_read_access_is_a_setup_error(self) -> None:
        real = os.access

        def access(path: Any, mode: int, **kwargs: Any) -> bool:
            if Path(path) == self.lib:
                return False
            return real(path, mode, **kwargs)

        with mock.patch("os.access", side_effect=access):
            code, _, err = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn(f"--path {self.lib} cannot be read", err)
        self.assertEqual(self.calls(), [])

    @unittest.skipIf(sys.platform == "win32", "Windows ignores mode 0 on a folder")
    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0, "root reads any folder"
    )
    def test_a_real_mode_0_path_folder_is_a_setup_error(self) -> None:
        self.lib.chmod(0)
        self.addCleanup(self.lib.chmod, 0o755)
        code, _, err = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("cannot be read", err)


class ColorTest(IsolatedCase):
    """Argparse's own color codes must not reach the output as escaped text.

    Meaningful on Python 3.14 and later only: 3.12's argparse has no color, so there
    the test passes either way.
    """

    def capture(self, *argv: str) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit),
        ):
            for name in ("NO_COLOR", "PYTHON_COLORS"):
                os.environ.pop(name, None)
            transcribe.main(list(argv))
        return out.getvalue(), err.getvalue()

    def test_help_and_errors_carry_no_color_codes(self) -> None:
        help_text, _ = self.capture("--help")
        _, error_text = self.capture("--no-such-option")
        self.assertIn("usage:", help_text)
        self.assertIn("unrecognized arguments", error_text)
        for text in (help_text, error_text):
            self.assertNotIn("\x1b", text)
            self.assertNotIn("\\x1b", text)


class TerminalSafeTest(CliCase):
    def test_direction_and_separator_characters_are_spelled(self) -> None:
        # Regression: only C0, DEL and C1 were spelled, so a right-to-left override
        # made "one\u202egpj.mp3" print as an image's name.
        for char in ("\u202e", "\u200f", "\u2066", "\u2028", "\u2029"):
            with self.subTest(char=f"U+{ord(char):04X}"):
                folder = self.root / f"lib-{ord(char):04x}"
                folder.mkdir()
                (folder / f"one{char}gpj.mp3").write_bytes(b"")
                _, out, _ = self.run_main("--path", str(folder), "--dry-run")
                self.assertIn(f"one\\u{ord(char):04x}gpj.mp3", out)
                self.assertNotIn(char, out)

    def test_accents_and_cjk_print_as_they_are(self) -> None:
        (self.lib / "café 日本.mp3").write_bytes(b"")
        _, out, _ = self.run_main("--path", str(self.lib), "--dry-run")
        self.assertIn("café 日本.mp3", out)
