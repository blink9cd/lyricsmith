"""The lyricsmith dispatcher: forward to the tool, return its exit code.

The in-process cases replace the tool loader with a fake, so no tool runs. The help
cases run each entry file with ``--help`` only, alone and through the dispatcher.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

import lyricsmith

_HERE = Path(__file__).resolve().parent
# bounded_run, the shared library the help cases run each entry file through, sits
# directly under the project root, this file's own folder.
# See stdio_utf8/stdio_utf8.md, "How this project's tools import it", for why
# each is ONE fixed candidate rather than a search up the ancestors, and why a
# missing copy fails its import below by name.
sys.path.append(str(_HERE / "bounded_run"))

import bounded_run  # noqa: E402 - import follows the sys.path bootstrap above

#: How long one ``--help`` subprocess may take before the test fails.
_HELP_TIMEOUT_S = 60


class DispatchTest(unittest.TestCase):
    """The command picks the tool; everything after it reaches that tool untouched."""

    def test_arguments_and_the_exit_code_pass_through(self) -> None:
        seen: list[tuple[list[str], str | None]] = []

        def fake_main(
            argv: Sequence[str] | None = None, *, prog: str | None = None
        ) -> int:
            seen.append((list(argv or ()), prog))
            return 3

        with mock.patch.object(lyricsmith, "_load_main", return_value=fake_main):
            code = lyricsmith.main(["album", "--album", "x", "--mode", "check"])
        self.assertEqual(code, 3)
        self.assertEqual(
            seen, [(["--album", "x", "--mode", "check"], "lyricsmith album")]
        )

    def test_a_tools_help_reaches_the_tool_not_the_dispatcher(self) -> None:
        seen: list[list[str]] = []

        def fake_main(
            argv: Sequence[str] | None = None, *, prog: str | None = None
        ) -> int:
            seen.append(list(argv or ()))
            return 0

        with mock.patch.object(lyricsmith, "_load_main", return_value=fake_main):
            self.assertEqual(lyricsmith.main(["sweep", "--help", "--version"]), 0)
        self.assertEqual(seen, [["--help", "--version"]])

    def test_no_argv_reads_the_command_line(self) -> None:
        loaded: list[str] = []

        def load(command: str) -> lyricsmith.ToolMain:
            loaded.append(command)
            return lambda argv=None, *, prog=None: 0

        argv = ["lyricsmith.py", "transcribe", "--dry-run"]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(lyricsmith, "_load_main", side_effect=load),
        ):
            self.assertEqual(lyricsmith.main(), 0)
        self.assertEqual(loaded, ["transcribe"])

    def test_an_unknown_command_exits_2_listing_the_commands(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            lyricsmith.main(["lyric"])
        self.assertEqual(stop.exception.code, 2)
        for name in lyricsmith.COMMANDS:
            self.assertIn(name, err.getvalue())

    def test_no_command_exits_2(self) -> None:
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as stop,
        ):
            lyricsmith.main([])
        self.assertEqual(stop.exception.code, 2)

    def test_an_option_before_the_command_exits_2(self) -> None:
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as stop,
        ):
            lyricsmith.main(["--mode", "album"])
        self.assertEqual(stop.exception.code, 2)

    def test_version_prints_the_project_version(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stop:
            lyricsmith.main(["--version"])
        self.assertEqual(stop.exception.code, 0)
        self.assertIn("0.1.0", out.getvalue())

    def test_the_dispatchers_own_name_is_argv0s_and_its_epilog_follows(self) -> None:
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["/somewhere/renamed.py", "--help"]),
            contextlib.redirect_stdout(out),
            self.assertRaises(SystemExit),
        ):
            lyricsmith.main()
        self.assertTrue(out.getvalue().startswith("usage: renamed.py "), out.getvalue())
        self.assertIn(
            "Run `renamed.py COMMAND --help`", " ".join(out.getvalue().split())
        )

    def test_the_dispatchers_help_names_each_command(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stop:
            lyricsmith.main(["--help"])
        self.assertEqual(stop.exception.code, 0)
        for name in lyricsmith.COMMANDS:
            self.assertIn(name, out.getvalue())


class LoadTest(unittest.TestCase):
    """The loader imports the chosen tool's entry module and hands back its main."""

    def test_each_command_loads_its_tools_main(self) -> None:
        for name, module in lyricsmith.COMMANDS.items():
            with self.subTest(command=name):
                loaded = lyricsmith._load_main(name)
                self.assertIs(sys.modules[module].main, loaded)

    def test_a_second_load_adds_no_second_path_entry(self) -> None:
        folder = str(_HERE / lyricsmith.COMMANDS["album"])
        lyricsmith._load_main("album")
        lyricsmith._load_main("album")
        self.assertEqual(sys.path.count(folder), 1)


class VersionTest(unittest.TestCase):
    """The version is the pyproject's, and 'unknown' whenever it cannot be read."""

    def setUp(self) -> None:
        """A scratch folder for the stand-in pyproject files."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.config = Path(scratch.name) / "pyproject.toml"

    def test_the_project_files_version(self) -> None:
        self.config.write_text(
            '[project]\nversion = "9.8.7"\n', encoding="utf-8", newline="\n"
        )
        self.assertEqual(lyricsmith.project_version(self.config), "9.8.7")

    def test_a_missing_file_is_unknown(self) -> None:
        self.assertEqual(lyricsmith.project_version(self.config), "unknown")

    def test_a_malformed_file_is_unknown(self) -> None:
        self.config.write_text("[project\n", encoding="utf-8", newline="\n")
        self.assertEqual(lyricsmith.project_version(self.config), "unknown")

    def test_a_version_that_is_not_text_is_unknown(self) -> None:
        self.config.write_text(
            "[project]\nversion = 1\n", encoding="utf-8", newline="\n"
        )
        self.assertEqual(lyricsmith.project_version(self.config), "unknown")

    def test_a_project_that_is_not_a_table_is_unknown(self) -> None:
        self.config.write_text('project = "x"\n', encoding="utf-8", newline="\n")
        self.assertEqual(lyricsmith.project_version(self.config), "unknown")

    def test_a_file_that_is_not_utf8_is_unknown(self) -> None:
        self.config.write_bytes(b'[project]\nversion = "\xff"\n')
        self.assertEqual(lyricsmith.project_version(self.config), "unknown")


class HelpTest(unittest.TestCase):
    """Each command's help is its tool's own, the program name aside."""

    def test_each_commands_help_matches_the_tools_own(self) -> None:
        for name in lyricsmith.COMMANDS:
            with self.subTest(command=name):
                via = _run([str(_HERE / "lyricsmith.py"), name, "--help"])
                alone = _run([str(_HERE / name / f"{name}.py"), "--help"])
                self.assertIn(f"usage: lyricsmith {name}", via)
                # argparse wraps the usage line by the program name's length, so the
                # layout differs; the words must not. Only the usage line's name is
                # masked: the text after it may name the tool (transcribe's label is
                # "lyricsmith transcribe"), and that must read the same both ways.
                self.assertEqual(
                    _words(
                        via.replace(f"usage: lyricsmith {name}", "usage: <prog>", 1)
                    ),
                    _words(alone.replace(f"usage: {name}.py", "usage: <prog>", 1)),
                )


#: Every entry file that finds the shared libraries by the bounded upward search.
_ENTRY_FILES = (
    "lyricsmith.py",
    "album/album.py",
    "album/samples/build_sample_album.py",
    "sweep/sweep.py",
    "transcribe/transcribe.py",
)
#: Every shared library an entry file finds that way, the project's own tools aside.
_HELPERS = ("stdio_utf8", "help_formatter", "run_lock", "library_walk")
#: The library album's private modules find by a search of their own (lyrics_silence),
#: bounded the same way; planted above the tree like the others.
_ALBUM_OWN = "bounded_run"
#: The entry files that load album's private modules, so fail without that library.
_ALBUM_LOADERS = (
    "album/album.py",
    "album/samples/build_sample_album.py",
    "sweep/sweep.py",
)
#: A planted library's whole body: importing it announces itself and stops the run.
_PLANTED = "raise SystemExit('PLANTED ' + __file__)\n"


class BoundedSearchTest(unittest.TestCase):
    """Each tool finds its libraries at one fixed place, directly under the project root.

    A tree missing its copy of a library must fail by that library's name, and never
    import a same-named folder from anywhere else: planted above it (in a shared temporary
    folder, or the home folder), or elsewhere in a repository that encloses it.
    """

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.top = Path(scratch.name).resolve()
        if any((folder / ".git").exists() for folder in self.top.parents):
            self.skipTest("the temporary folder sits inside a repository")
        self.home = self.top / "home"
        self.home.mkdir()
        for name in (*_HELPERS, _ALBUM_OWN):
            (self.top / name).mkdir()
            (self.top / name / f"{name}.py").write_text(
                _PLANTED, encoding="utf-8", newline="\n"
            )

    def copy_project(self, parent: Path) -> Path:
        """This project's files, its shared libraries left out, copied into *parent*."""
        copy = parent / "lyricsmith"
        shutil.copytree(
            _HERE,
            copy,
            ignore=shutil.ignore_patterns("__pycache__", ".*", *_HELPERS, _ALBUM_OWN),
        )
        return copy

    def vendor(self, folder: Path, names: Sequence[str]) -> None:
        """The real shared libraries *names*, copied into *folder* as this tree ships them."""
        for name in names:
            shutil.copytree(
                _HERE / name,
                folder / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )

    def help_of(self, project: Path) -> dict[str, subprocess.CompletedProcess[str]]:
        """Each entry file's --help, run isolated with HOME in the scratch folder."""
        env = {**os.environ, "HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}
        return {
            entry: bounded_run.run_bounded(
                [sys.executable, "-I", str(project / entry), "--help"],
                timeout=_HELP_TIMEOUT_S,
                env=env,
            )
            for entry in _ENTRY_FILES
        }

    def assert_refused_by_name(self, project: Path) -> None:
        """Every entry file fails naming a missing library, and nothing planted ran."""
        for entry, done in self.help_of(project).items():
            with self.subTest(entry=entry):
                self.assertNotIn("PLANTED", done.stderr)
                self.assertNotEqual(done.returncode, 0)
                self.assertRegex(
                    done.stderr,
                    r"No module named '(" + "|".join((*_HELPERS, _ALBUM_OWN)) + ")'",
                )

    def assert_runs(self, project: Path) -> None:
        """Every entry file's --help succeeds, and nothing planted ran."""
        for entry, done in self.help_of(project).items():
            with self.subTest(entry=entry):
                self.assertNotIn("PLANTED", done.stderr)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_album_modules_own_library_is_fixed_too(self) -> None:
        project = self.copy_project(self.top / "unpacked")
        self.vendor(project, _HELPERS)
        for entry, done in self.help_of(project).items():
            with self.subTest(entry=entry):
                self.assertNotIn("PLANTED", done.stderr)
                if entry in _ALBUM_LOADERS:
                    self.assertNotEqual(done.returncode, 0)
                    self.assertIn(f"No module named '{_ALBUM_OWN}'", done.stderr)
                else:
                    self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_copy_elsewhere_in_an_enclosing_repository_is_never_used(self) -> None:
        repo = self.top / "repo"
        repo.mkdir()
        (repo / ".git").write_text(
            "gitdir: <placeholder>\n", encoding="utf-8", newline="\n"
        )
        project = self.copy_project(repo / "projects")
        self.vendor(repo / "utilities", [*_HELPERS, _ALBUM_OWN])
        self.assert_refused_by_name(project)
        self.vendor(project, [*_HELPERS, _ALBUM_OWN])
        self.assert_runs(project)

    def test_a_missing_library_fails_by_name(self) -> None:
        project = self.copy_project(self.top / "unpacked")
        self.vendor(project, [_ALBUM_OWN])
        self.assert_refused_by_name(project)
        self.vendor(project, _HELPERS)
        self.assert_runs(project)


def _words(text: str) -> str:
    """*text* with every run of whitespace collapsed to one space."""
    return " ".join(text.split())


def _run(args: list[str]) -> str:
    """Run a Python entry file with *args*; return its stdout, failing on a nonzero exit.

    Bounded by a process-tree kill, so a child that leaves a grandchild holding the
    captured pipes cannot stall the test past its timeout.
    """
    done = bounded_run.run_bounded([sys.executable, *args], timeout=_HELP_TIMEOUT_S)
    if done.returncode != 0:
        raise AssertionError(f"{args} exited {done.returncode}: {done.stderr}")
    return done.stdout


if __name__ == "__main__":  # pragma: no cover - the suite's own entry point
    unittest.main()
