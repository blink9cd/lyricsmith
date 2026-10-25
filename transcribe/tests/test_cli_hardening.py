"""What the walk takes and what reaches whisper-cli: regular files, links, absolute paths.

A FIFO, socket or device named like audio would block whisper-cli for its whole bound, so
only regular files are transcribed and anything else is a reported row; a linked file the
walk finds is skipped, as lyricsmith album skips a linked MP3, while a file named directly
by --path is the operator's choice. Every path handed to whisper-cli is absolute, so a
name starting with "-" is never read as an option and a relative --whisper-cli runs the
very file the setup check looked at.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import at_engine
import at_models
import at_types
import transcribe
from at_types import EXIT_FILE_FAILED, EXIT_OK, EXIT_SETUP
from tests._cli import CliCase
from tests._isolation import write_fake_whisper

#: The row label of a linked file the walk skipped.
LINKED_ROW = "skipped (linked)"
#: The detail of a row whose entry is not a regular file.
NOT_REGULAR = "not a regular file"
#: Why a FIFO case skips on Windows.
_NO_FIFO = "Windows has no named pipes in its file system"


def _chdir(case: unittest.TestCase, folder: Path) -> Path:
    """Move into *folder* for the rest of the test; the working folder as the OS names it.

    That name may differ from *folder*'s (macOS's /var is a link to /private/var),
    and it is the one a relative path is made absolute against.
    """
    previous = Path.cwd()
    os.chdir(folder)
    case.addCleanup(os.chdir, previous)
    return Path.cwd()


@unittest.skipIf(sys.platform == "win32", "FIFOs and symlinks are POSIX here")
class NotRegularTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.fifo = self.lib / "a" / "pipe.mp3"
        # Skipped by platform rather than by probing for os.mkfifo, so a win32 type
        # check of the rest is narrowed too; the class decorator skips there first.
        if sys.platform == "win32":
            self.skipTest(_NO_FIFO)  # pragma: only-on win32
        os.mkfifo(self.fifo)

    def test_a_fifo_in_the_walk_is_a_failed_row_never_transcribed(self) -> None:
        code, out, _ = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn(f"failed{' ' * 2}", out)
        self.assertIn("a/pipe.mp3", out)
        self.assertIn(NOT_REGULAR, out)
        sent = [call[call.index("-f") + 1] for call in self.transcriptions()]
        self.assertEqual(len(sent), 2)
        self.assertNotIn(str(self.fifo), sent)

    def test_a_dry_run_shows_the_fifo_as_failed(self) -> None:
        code, out, _ = self.run_main("--path", str(self.lib), "--dry-run")
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn(NOT_REGULAR, out)
        self.assertNotIn("transcribe  a/pipe.mp3", out)

    def test_a_fifo_named_by_path_is_a_setup_error(self) -> None:
        code, _, err = self.run_main("--path", str(self.fifo))
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn(NOT_REGULAR, err)
        self.assertEqual(self.calls(), [])


class VanishedEntryTest(CliCase):
    def test_a_file_that_vanishes_before_its_stat_is_a_failed_row(self) -> None:
        # A file removed between the walk and its stat: its row says why, the run goes on.
        real = transcribe.at_modes.at_discover.classify
        gone = self.lib / "b" / "two.mp3"

        def classify(path: Path, *, named: bool) -> object:
            if path == gone:
                raise FileNotFoundError(2, "No such file or directory", str(path))
            return real(path, named=named)

        with mock.patch.object(transcribe.at_modes.at_discover, "classify", classify):
            code, out, _ = self.run_main("--path", str(self.lib), "--dry-run")
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn("could not read it: ", out)
        self.assertIn("transcribe             a/one.mp3", out)


@unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
class LinkedFileTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        outside = self.root / "outside"
        outside.mkdir()
        self.far = outside / "far.mp3"
        self.far.write_bytes(b"")
        self.link = self.lib / "a" / "link.mp3"
        os.symlink(self.far, self.link)

    def test_a_linked_file_in_the_walk_is_a_reported_skip(self) -> None:
        code, out, _ = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_OK)
        self.assertIn(LINKED_ROW, out)
        self.assertIn("a/link.mp3", out)
        self.assertIn("linked 1", out)
        sent = [call[call.index("-f") + 1] for call in self.transcriptions()]
        self.assertEqual(len(sent), 2)
        self.assertNotIn(str(self.link), sent)
        self.assertFalse((self.lib / "a" / "link.lrc").exists())

    def test_a_dry_run_shows_the_linked_skip(self) -> None:
        code, out, _ = self.run_main("--path", str(self.lib), "--dry-run")
        self.assertEqual(code, EXIT_OK)
        self.assertIn(f"{LINKED_ROW}", out)

    def test_a_linked_file_named_by_path_is_transcribed(self) -> None:
        code, _, _ = self.run_main("--path", str(self.link))
        self.assertEqual(code, EXIT_OK)
        [call] = self.transcriptions()
        self.assertEqual(call[call.index("-f") + 1], str(self.link))
        self.assertTrue((self.lib / "a" / "link.lrc").is_file())

    def test_a_dangling_link_in_the_walk_is_a_linked_skip(self) -> None:
        os.symlink(self.root / "gone.mp3", self.lib / "a" / "gone.mp3")
        code, out, _ = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("a/gone.mp3", out)
        self.assertIn("linked 2", out)


@unittest.skipIf(sys.platform == "win32", "the fake whisper-cli is a shebang script")
class AbsolutePathTest(CliCase):
    def test_a_relative_whisper_cli_runs_the_file_the_check_found(self) -> None:
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        write_fake_whisper(elsewhere)
        (self.bin / "whisper-cli").unlink()
        _chdir(self, elsewhere)
        code, _, err = self.run_main(
            "--path", str(self.lib), "--whisper-cli", "whisper-cli"
        )
        self.assertEqual(code, EXIT_OK, err)
        self.assertEqual(len(self.transcriptions()), 2)

    def test_the_setup_check_answers_an_absolute_program(self) -> None:
        here = _chdir(self, self.bin)
        options = self.options(whisper_cli=Path("whisper-cli"))
        setup = transcribe.at_modes.check_setup(
            options, at_engine.run_bounded, read_version=False
        )
        self.assertEqual(setup.cli, here / "whisper-cli")

    def test_a_program_found_through_a_relative_path_entry_is_absolute(self) -> None:
        here = _chdir(self, self.root)
        os.environ["PATH"] = self.bin.name
        setup = transcribe.at_modes.check_setup(
            self.options(), at_engine.run_bounded, read_version=False
        )
        self.assertEqual(setup.cli, here / self.bin.name / "whisper-cli")

    def test_a_name_starting_with_a_dash_reaches_whisper_cli_absolute(self) -> None:
        (self.lib / "-x.mp3").write_bytes(b"")
        here = _chdir(self, self.lib)
        code, _, err = self.run_main("--path", "./-x.mp3")
        self.assertEqual(code, EXIT_OK, err)
        [call] = self.transcriptions()
        self.assertEqual(call[call.index("-f") + 1], str(here / "-x.mp3"))

    def test_paths_and_models_are_resolved_absolute(self) -> None:
        here = _chdir(self, self.root)
        parser = transcribe.build_parser(Path("models"))
        options = transcribe.resolve_options(
            parser.parse_args(
                ["--path", "lib", "--output-dir", "out", "--vad-model", "v.bin"]
            )
        )
        self.assertEqual(options.path, here / "lib")
        self.assertEqual(options.output_dir, here / "out")
        self.assertEqual(options.model, here / "models" / at_models.WHISPER_MODEL_NAME)
        self.assertEqual(options.vad_model, here / "v.bin")

    def options(self, whisper_cli: Path | None = None) -> at_types.Options:
        """Transcribe options over the library, with *whisper_cli* as given."""
        return at_types.Options(
            mode="transcribe",
            path=self.lib,
            extensions=frozenset({"mp3"}),
            output_dir=None,
            overwrite=False,
            dry_run=True,
            language="en",
            model=self.models / at_models.WHISPER_MODEL_NAME,
            vad_model=self.models / at_models.VAD_MODEL_NAME,
            model_dir=self.models,
            whisper_cli=whisper_cli,
        )
