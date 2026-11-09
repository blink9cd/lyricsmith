"""The transcribe mode end to end, through main(), against the fake whisper-cli."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import at_models
import at_place
import at_plan
import transcribe

# Importable once transcribe has put the project's folders on sys.path.
import lyricsmith_project
from at_types import (
    EXIT_FILE_FAILED,
    EXIT_OK,
    EXIT_SETUP,
    MACHINE_BY_LINE,
    TEMP_PREFIX,
    Planned,
)
from tests._cli import CliCase
from tests._isolation import write_fake_whisper

#: The label line, naming the project version the label reads from pyproject.toml, so
#: a release's version bump needs no edit here.
RE_LINE = (
    f"[re:lyricsmith transcribe {lyricsmith_project.project_version()}; "
    "whisper.cpp 1.9.5; ggml-large-v3-turbo]"
)
HAND = "[ar:someone]\n[00:01.00] An invented hand-made line.\n"
#: A name that would clear the screen if it reached the terminal raw.
HOSTILE = "bad\x1b[2Jname.mp3"


class TranscribeTest(CliCase):
    def test_a_folder_is_transcribed_and_labeled(self) -> None:
        code, out, _ = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_OK)
        lrc = (self.lib / "a" / "one.lrc").read_text(encoding="utf-8")
        self.assertEqual(
            lrc, f"{RE_LINE}\n{MACHINE_BY_LINE}\n[00:00.26] An invented example line.\n"
        )
        self.assertTrue((self.lib / "b" / "two.txt").is_file())
        self.assertIn(
            "transcribed 2, overwritten 0, skipped 0, refused 0, failed 0", out
        )
        [first, _second] = self.transcriptions()
        for flag in (["--vad"], ["-mc", "0"], ["-l", "en"], ["-np"]):
            self.assertTrue(
                any(first[i : i + len(flag)] == flag for i in range(len(first))), flag
            )
        stem = Path(first[first.index("-of") + 1])
        self.assertEqual(stem.parent.parent, self.lib / "a")
        self.assertTrue(stem.parent.name.startswith(TEMP_PREFIX))
        self.assertEqual(list(self.lib.rglob(f"{TEMP_PREFIX}*")), [])

    def test_a_second_run_skips_and_never_transcribes(self) -> None:
        self.run_main("--path", str(self.lib))
        self.log.unlink()
        code, out, _ = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("skipped 2", out)
        self.assertEqual(self.transcriptions(), [])

    def test_overwrite_replaces_ours_and_refuses_the_rest(self) -> None:
        self.run_main("--path", str(self.lib / "a"))
        (self.lib / "b" / "two.lrc").write_text(HAND, encoding="utf-8", newline="\n")
        (self.lib / "c").mkdir()
        (self.lib / "c" / "three.mp3").write_bytes(b"")
        (self.lib / "c" / "three.txt").write_text(
            "hand words\n", encoding="utf-8", newline="\n"
        )
        code, out, _ = self.run_main("--path", str(self.lib), "--overwrite")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("overwritten 1, skipped 0, refused 2", out)
        self.assertEqual((self.lib / "b" / "two.lrc").read_text(encoding="utf-8"), HAND)
        self.assertEqual(
            (self.lib / "c" / "three.txt").read_text(encoding="utf-8"), "hand words\n"
        )

    def test_output_dir_mirrors_the_tree_and_writes_nothing_beside(self) -> None:
        out_dir = self.root / "out"
        code, _, _ = self.run_main(
            "--path", str(self.lib), "--output-dir", str(out_dir)
        )
        self.assertEqual(code, EXIT_OK)
        self.assertTrue((out_dir / "a" / "one.lrc").is_file())
        self.assertTrue((out_dir / "b" / "two.txt").is_file())
        self.assertEqual(
            sorted(p.suffix for p in self.lib.rglob("*") if p.is_file()),
            [".mp3", ".mp3"],
        )

    def test_dry_run_prints_the_check_and_the_plan_and_runs_nothing(self) -> None:
        code, out, _ = self.run_main("--path", str(self.lib), "--dry-run")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("version not read in a dry run", out)
        self.assertIn("transcribe             a/one.mp3", out)
        self.assertEqual(self.calls(), [])
        self.assertEqual(
            sorted(p.suffix for p in self.lib.rglob("*") if p.is_file()),
            [".mp3", ".mp3"],
        )

    def test_dry_run_with_a_missing_model_prints_the_plan_and_exits_2(self) -> None:
        (self.models / at_models.VAD_MODEL_NAME).unlink()
        code, out, _ = self.run_main("--path", str(self.lib), "--dry-run")
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("missing", out)
        self.assertIn("one.mp3", out)
        self.assertEqual(self.calls(), [])

    def test_one_failure_is_its_row_and_the_run_goes_on(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_WHISPER_FAIL": "one.mp3"}):
            code, out, _ = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn("    whisper-cli exited 3", out)
        self.assertIn("    error: failed to read audio", out)
        self.assertFalse((self.lib / "a" / "one.lrc").exists())
        self.assertTrue((self.lib / "b" / "two.lrc").is_file())
        self.assertEqual(list(self.lib.rglob(f"{TEMP_PREFIX}*")), [])

    def test_two_audio_files_never_share_one_output_pair(self) -> None:
        # Regression: one.mp3 and one.m4a both map to one.lrc; the second replaced
        # the first's pair without --overwrite while the count said "transcribed 2".
        (self.lib / "a" / "one.m4a").write_bytes(b"")
        claimed = "another audio file, a/one.m4a, already writes this .lrc"
        code, out, _ = self.run_main(
            "--path", str(self.lib), "--extensions", "mp3,m4a", "--dry-run"
        )
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn("failed                 a/one.mp3", out)
        self.assertIn(f"    {claimed}", out)
        self.assertEqual(self.calls(), [])
        code, out, _ = self.run_main("--path", str(self.lib), "--extensions", "mp3,m4a")
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn(f"    {claimed}", out)
        self.assertIn(
            "transcribed 2, overwritten 0, skipped 0, refused 0, failed 1", out
        )
        audio = [call[call.index("-f") + 1] for call in self.transcriptions()]
        self.assertEqual([Path(path).name for path in audio], ["one.m4a", "two.mp3"])
        self.assertTrue(
            at_plan.is_ours((self.lib / "a" / "one.lrc").read_text(encoding="utf-8"))
        )
        self.assertTrue((self.lib / "a" / "one.txt").is_file())

    def test_an_untimed_lrc_fails_that_file(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_WHISPER_UNTIMED": "two.mp3"}):
            code, out, _ = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn("the .lrc has no timestamped line", out)
        self.assertFalse((self.lib / "b" / "two.txt").exists())

    def test_setup_errors_touch_no_file(self) -> None:
        cases = {
            "brew install whisper.cpp": lambda: self.fake.unlink(),
            "--mode fetch-models": lambda: (
                self.models / at_models.WHISPER_MODEL_NAME
            ).unlink(),
        }
        for needle, breakage in cases.items():
            with self.subTest(needle=needle):
                self.setUp()
                breakage()
                code, _, err = self.run_main("--path", str(self.lib))
                self.assertEqual(code, EXIT_SETUP)
                self.assertIn(needle, err)
                self.assertEqual(self.transcriptions(), [])
                self.assertEqual(
                    sorted(p.suffix for p in self.lib.rglob("*") if p.is_file()),
                    [".mp3", ".mp3"],
                )

    def test_an_unreadable_engine_version_is_a_setup_error(self) -> None:
        with mock.patch.dict(
            os.environ, {"FAKE_WHISPER_VERSION": "usage: whisper-cli"}
        ):
            code, _, err = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("--version", err)

    def test_a_non_executable_whisper_cli_is_a_setup_error(self) -> None:
        plain = self.root / "plain-file"
        plain.write_text("not a program\n", encoding="utf-8", newline="\n")
        code, _, err = self.run_main(
            "--path", str(self.lib), "--whisper-cli", str(plain)
        )
        self.assertEqual(code, EXIT_SETUP)
        if sys.platform == "win32":
            # Windows has no execute bit: os.access passes any file for X_OK, so the
            # refusal comes from the --version read, which cannot start the file.
            self.assertIn(f"{plain} --version printed no version", err)
        else:
            self.assertIn("is not an executable file", err)

    def test_a_failed_placement_is_that_files_row(self) -> None:
        with mock.patch.object(
            at_place, "place_pair", side_effect=OSError("disk full")
        ):
            code, out, _ = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn("    could not write the output: disk full", out)
        self.assertIn("failed 2", out)
        self.assertEqual(list(self.lib.rglob(f"{TEMP_PREFIX}*")), [])

    def test_a_placement_failing_after_the_lrc_landed_is_that_files_row(self) -> None:
        # The .lrc is renamed into place, then the .txt rename fails.
        def lrc_then_fail(workdir: Path, lrc_text: str, planned: Planned) -> None:
            planned.lrc.write_text(lrc_text, encoding="utf-8", newline="\n")
            raise FileNotFoundError(
                2, "No such file or directory", str(workdir / "out.txt")
            )

        with mock.patch.object(at_place, "place_pair", side_effect=lrc_then_fail):
            code, out, _ = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn(
            "    could not write the output: [Errno 2] No such file or directory", out
        )
        self.assertIn("failed 2", out)
        self.assertEqual(list(self.lib.rglob(f"{TEMP_PREFIX}*")), [])
        self.assertFalse((self.lib / "a" / "one.txt").exists())
        # The .lrc left behind is ours, so --overwrite repairs the pair.
        self.assertTrue(
            at_plan.is_ours((self.lib / "a" / "one.lrc").read_text(encoding="utf-8"))
        )
        code, out, _ = self.run_main("--path", str(self.lib), "--overwrite")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("overwritten 2", out)
        self.assertTrue((self.lib / "a" / "one.txt").is_file())

    def test_a_sidecar_named_as_the_path_is_a_setup_error(self) -> None:
        # output_paths would map the input onto itself.
        self.run_main("--path", str(self.lib / "a"))
        before = (self.lib / "a" / "one.lrc").read_text(encoding="utf-8")
        self.log.unlink()
        upper = self.lib / "a" / "ONE.TXT"
        upper.write_text("hand words\n", encoding="utf-8", newline="\n")
        for named in (self.lib / "a" / "one.lrc", self.lib / "a" / "one.txt", upper):
            with self.subTest(named=named.name):
                code, _, err = self.run_main("--path", str(named), "--overwrite")
                self.assertEqual(code, EXIT_SETUP)
                self.assertIn("is a .lrc or .txt", err)
        self.assertEqual(self.calls(), [])
        self.assertEqual(
            (self.lib / "a" / "one.lrc").read_text(encoding="utf-8"), before
        )
        self.assertEqual(upper.read_text(encoding="utf-8"), "hand words\n")

    @unittest.skipIf(
        sys.platform == "win32", "Windows forbids control characters in file names"
    )
    def test_a_control_character_in_a_name_is_escaped(self) -> None:
        # A hostile file name cannot write a raw control character to the terminal.
        (self.lib / "a" / HOSTILE).write_bytes(b"")
        with mock.patch.dict(os.environ, {"FAKE_WHISPER_FAIL": "name.mp3"}):
            code, out, _ = self.run_main("--path", str(self.lib))
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn("bad\\x1b[2Jname.mp3", out)
        _, dry, _ = self.run_main("--path", str(self.lib), "--dry-run")
        self.assertIn("bad\\x1b[2Jname.mp3", dry)
        code, _, err = self.run_main("--path", str(self.root / "gone\x07.mp3"))
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("gone\\x07.mp3", err)
        for text in (out, dry, err):
            self.assertNotIn("\x1b", text)
            self.assertNotIn("\x07", text)

    def test_an_output_dir_that_is_a_file_fails_each_file_and_the_run_goes_on(
        self,
    ) -> None:
        blocker = self.root / "blocker"
        blocker.write_text(
            "a file where the folder should be\n", encoding="utf-8", newline="\n"
        )
        code, out, _ = self.run_main(
            "--path", str(self.lib), "--output-dir", str(blocker)
        )
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn(
            "transcribed 0, overwritten 0, skipped 0, refused 0, failed 2", out
        )
        self.assertEqual(self.transcriptions(), [])

    def test_a_failed_file_leaves_no_empty_mirror_folder(self) -> None:
        # Regression: the mirror folder was created before the engine ran and kept
        # when it failed, so a failed run left an empty tree under --output-dir.
        out_dir = self.root / "out"
        with mock.patch.dict(os.environ, {"FAKE_WHISPER_FAIL": "one.mp3"}):
            code, _, _ = self.run_main(
                "--path", str(self.lib), "--output-dir", str(out_dir)
            )
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertFalse((out_dir / "a").exists())
        self.assertTrue((out_dir / "b" / "two.lrc").is_file())
        with mock.patch.dict(os.environ, {"FAKE_WHISPER_FAIL": "two.mp3"}):
            self.run_main(
                "--path", str(self.lib / "b"), "--output-dir", str(self.root / "x")
            )
        self.assertFalse((self.root / "x").exists())

    def test_an_explicit_whisper_cli_outside_path(self) -> None:
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        fake = write_fake_whisper(elsewhere)
        self.fake.unlink()
        code, _, _ = self.run_main("--path", str(self.lib), "--whisper-cli", str(fake))
        self.assertEqual(code, EXIT_OK)

    def test_no_matching_files(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        code, out, _ = self.run_main("--path", str(empty))
        self.assertEqual(code, EXIT_OK)
        self.assertIn(f"no audio files found under {empty}", out)
        code, out, _ = self.run_main("--path", str(empty), "--dry-run")
        self.assertEqual(code, EXIT_OK)
        self.assertIn(f"no audio files found under {empty}", out)

    def test_the_entry_point_runs_as_a_program(self) -> None:
        script = Path(transcribe.__file__)
        # reliability: ignore[RL005] --help starts no child, so no grandchild holds a pipe
        done = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            encoding="utf-8",
            timeout=60,
            check=False,
            env=dict(os.environ),
        )
        self.assertEqual(done.returncode, 0)
        self.assertIn("--mode", done.stdout)
