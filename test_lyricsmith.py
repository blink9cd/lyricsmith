"""The lyricsmith dispatcher: forward to the tool, return its exit code.

The in-process cases replace the tool loader with a fake, so no tool runs. The help
cases run each entry file with ``--help`` only, alone and through the dispatcher.
"""

from __future__ import annotations

import ast
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
# The project's manifest reader, in its own folder beside this file: it puts the shared
# libraries the project's pyproject.toml names on sys.path, bounded_run among them.
_READER_DIR = str(_HERE / "lyricsmith_project")
sys.path[:] = [_READER_DIR, *(entry for entry in sys.path if entry != _READER_DIR)]
import lyricsmith_project  # noqa: E402 - import follows the sys.path bootstrap above

lyricsmith_project.add_shared_libraries()

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

    def test_no_command_an_option_first_or_a_tool_left_unlocated_exits_2(self) -> None:
        failure = lyricsmith_project.SharedLibraryError("not here")
        for argv in ([], ["--mode", "album"], ["sweep"]):
            with (
                mock.patch.object(lyricsmith, "_load_main", side_effect=failure),
                contextlib.redirect_stderr(io.StringIO()) as err,
                self.assertRaises(SystemExit) as stop,
            ):
                lyricsmith.main(argv)
            with self.subTest(argv=argv):
                self.assertEqual(stop.exception.code, 2)
                said, loaded = err.getvalue(), argv == ["sweep"]
                self.assertEqual("SharedLibraryError: not here" in said, loaded, said)
                self.assertEqual("usage:" in said, not loaded, said)

    def test_version_prints_the_project_version(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stop:
            lyricsmith.main(["--version"])
        self.assertEqual(stop.exception.code, 0)
        version = lyricsmith_project.project_version()
        self.assertNotEqual(version, lyricsmith_project.UNKNOWN_VERSION)
        self.assertIn(version, out.getvalue())

    def test_the_dispatchers_own_name_is_argv0s_and_its_epilog_follows(self) -> None:
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["/somewhere/renamed.py", "--help"]),
            contextlib.redirect_stdout(out),
            self.assertRaises(SystemExit),
        ):
            lyricsmith.main()
        self.assertTrue(out.getvalue().startswith("usage: renamed.py "), out.getvalue())
        self.assertIn("Run `renamed.py COMMAND --help`", _words(out.getvalue()))

    def test_the_dispatchers_help_names_each_command(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stop:
            lyricsmith.main(["--help"])
        self.assertEqual(stop.exception.code, 0)
        for name in lyricsmith.COMMANDS:
            self.assertIn(name, out.getvalue())
        self.assertIn("Prerequisites:", out.getvalue())


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

    def test_loading_album_loads_no_other_tools_code(self) -> None:
        done = bounded_run.run_bounded(
            [sys.executable, "-I", "-c", _ALBUM_ONLY_CHILD, str(_HERE)],
            timeout=_HELP_TIMEOUT_S,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), [])


#: A child that loads the album tool through the dispatcher, then prints each module of
#: the other tools that came with it: their entry modules, and their src/ prefixes.
_ALBUM_ONLY_CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
import lyricsmith
lyricsmith._load_main("album")
others = ("transcribe", "sync", "sweep")
print(*(name for name in sys.modules
        if name in others or name.startswith(("at_", "sy_", "sweep_"))))
"""


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


#: The locator's calls every file that runs it makes.
_LOCATOR_CALLS = frozenset({"add_shared_libraries", "confirm_from"})
#: Call sites the located cases leave out: this suite, which locates bounded_run for
#: itself and runs every other call site.
_NOT_LOCATED = frozenset({"test_lyricsmith.py"})
#: Folders whose files the scan skips: the locator and its own suite, which call it on
#: invented manifests, and the export's policy files.
_UNSCANNED = frozenset({"lyricsmith_project", "export", "__pycache__"})
#: The folders whose files are imported, never run: each must import alone.
_IMPORTED_FOLDERS = frozenset({"src", "tests"})


def _calls_the_locator(file: Path) -> bool:
    """Whether *file* calls the locator anywhere in it."""
    tree = ast.parse(file.read_text(encoding="utf-8"), str(file))
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _LOCATOR_CALLS
        for node in ast.walk(tree)
    )


def _locator_call_sites() -> list[tuple[str, str]]:
    """Each file that runs the locator, as ("entry" | "module", its path), sorted.

    Found by reading every file, so a new call site is covered without being listed.
    """
    sites = []
    for file in sorted(_HERE.rglob("*.py")):
        relative = file.relative_to(_HERE)
        if _UNSCANNED & set(relative.parts) or relative.as_posix() in _NOT_LOCATED:
            continue
        if _calls_the_locator(file):
            imported = _IMPORTED_FOLDERS & set(relative.parts[:-1])
            sites.append(("module" if imported else "entry", relative.as_posix()))
    return sites


#: Every file the located cases run or import.
_SITES = _locator_call_sites()


#: Where the copy's manifest names each library: a folder no upward search looks in
#: first, below the copy's root, where a decoy of each library sits instead.
_VENDORED = "vendored"
#: A planted library's whole body: importing it announces itself and stops the run.
_PLANTED = "raise SystemExit('PLANTED ' + __file__)\n"
#: What prefixes each line the child below prints for a shared library it loaded.
_LOADED = "LOADED"
#: The child each case runs, isolated: it runs an entry file with --help as its own
#: script would run (after the dispatcher's command, for a kind of "entry:<command>"),
#: or imports a private module alone with its src/ folder on the path, then prints the
#: file each shared library it loaded came from. Any decoy folders go just after the
#: script's own, in order, where PYTHONPATH would put them.
_LOCATED_CHILD = f"""
import importlib, os, runpy, sys
kind, target, decoys, names = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
kind, _, command = kind.partition(":")
folder, file_name = os.path.split(target)
sys.path[:0] = [folder, *(decoys.split(os.pathsep) if decoys else [])]
if kind == "entry":
    sys.argv = [target, *command.split(), "--help"]
    try:
        runpy.run_path(target, run_name="__main__")
    except SystemExit as stop:
        if stop.code:
            raise
else:
    importlib.import_module(os.path.splitext(file_name)[0])
for name in names:
    if name in sys.modules:
        print("{_LOADED}", name, sys.modules[name].__file__)
"""


class LocatedLibrariesTest(unittest.TestCase):
    """Each entry file takes its libraries from the folders the project's manifest names.

    The copy's manifest names each library in a folder below the copy's root, so a
    search up the ancestors would meet a decoy first: one at the copy's root, and one in
    each folder above the copy (a shared temporary folder, or the home folder). A copy
    lacking any one library fails naming it; a copy holding all of them loads each from
    its named folder and never a decoy.
    """

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.top = Path(scratch.name).resolve()
        self.home = self.top / "home"
        self.home.mkdir()
        self.real = lyricsmith_project.shared_library_folders()
        self.names = [folder.name for folder in self.real]
        self.project = self.top / "unpacked" / "lyricsmith"
        # The copy leaves out any library folder this tree carries beside the tools.
        shutil.copytree(
            _HERE,
            self.project,
            ignore=shutil.ignore_patterns("__pycache__", ".*", *self.names),
        )
        for name in self.names:
            self.plant_decoys(name)
        self.vendored = self.project / _VENDORED
        for folder in self.real:
            shutil.copytree(
                folder,
                self.vendored / folder.name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        listed = ", ".join(f'"{_VENDORED}/{name}"' for name in self.names)
        (self.project / "pyproject.toml").write_text(
            f"[tool.lyricsmith]\nshared_libraries = [{listed}]\n",
            encoding="utf-8",
            newline="\n",
        )

    def plant_decoys(self, name: str) -> None:
        """A decoy *name* library at the copy's root and in each folder above it."""
        for parent in (self.project, self.project.parent, self.top):
            decoy = parent / name
            decoy.mkdir()
            (decoy / f"{name}.py").write_text(_PLANTED, encoding="utf-8", newline="\n")

    def plant_shadows(self, names: Sequence[str]) -> Path:
        """A folder of planted modules, one per name in *names*; the folder."""
        shadows = self.top / "shadows"
        shadows.mkdir(exist_ok=True)
        for name in names:
            planted = shadows / f"{name}.py"
            planted.write_text(_PLANTED, encoding="utf-8", newline="\n")
        return shadows

    def located(
        self, *decoys: Path, only: Sequence[tuple[str, str]] = ()
    ) -> dict[str, subprocess.CompletedProcess[str]]:
        """Each entry file's --help, and each private module imported alone, isolated.

        Run with HOME and the working folder in the scratch folder, *decoys* just after
        the script's folder, over the (kind, target) runs in *only* when not empty.
        """
        env = {**os.environ, "HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}
        return {
            f"{kind} {target}": bounded_run.run_bounded(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    _LOCATED_CHILD,
                    kind,
                    str(self.project / target),
                    os.pathsep.join(map(str, decoys)),
                    *self.names,
                ],
                timeout=_HELP_TIMEOUT_S,
                env=env,
                cwd=self.home,
            )
            for kind, target in only or _SITES
        }

    def assert_refused(
        self, done: subprocess.CompletedProcess[str], *names: str
    ) -> None:
        """*done* failed naming the error and *names*, and never ran a planted module.

        An entry file exits 2; a private module raises the error, as an import does.
        """
        self.assertNotIn("PLANTED", done.stdout + done.stderr)
        if done.args[4].startswith("entry"):
            self.assertEqual(done.returncode, 2, done.stderr)
            self.assertNotIn("Traceback", done.stderr)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("SharedLibraryError", done.stderr)
        for name in names:
            self.assertIn(name, done.stderr)

    def test_the_call_sites_are_found_by_reading_the_files(self) -> None:
        self.assertIn(("entry", "lyricsmith.py"), _SITES)
        self.assertIn(("module", "transcribe/tests/__init__.py"), _SITES)

    def test_each_named_folder_that_is_missing_fails_by_name(self) -> None:
        for name in self.names:
            away = self.vendored / f"{name}.away"
            (self.vendored / name).rename(away)
            try:
                runs = self.located()
            finally:
                away.rename(self.vendored / name)
            for target, done in runs.items():
                with self.subTest(missing=name, target=target):
                    self.assert_refused(
                        done, f"'{_VENDORED}/{name}'", lyricsmith_project.CONFIG_KEY
                    )

    def test_a_same_named_library_earlier_on_the_path_is_refused(self) -> None:
        shadows = self.plant_shadows([*self.names, "lyricsmith_project", "album"])
        for target, done in self.located(shadows).items():
            with self.subTest(target=target):
                self.assert_refused(done, str(shadows), self.names[0])

    def test_a_reader_earlier_on_the_path_is_never_imported(self) -> None:
        shadows = self.plant_shadows(["lyricsmith_project"])
        # The real reader is on the path too, behind the decoy: it must still win.
        reader = self.project / "lyricsmith_project"
        for target, done in self.located(shadows, reader).items():
            with self.subTest(target=target):
                self.assertNotIn("PLANTED", done.stdout + done.stderr)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_sibling_tool_earlier_on_the_path_is_refused(self) -> None:
        shadows = self.plant_shadows(["album"])
        # Each tool that checks a sibling, run alone and through the dispatcher.
        alone = [("entry", f"{name}/{name}.py") for name in ("sweep", "sync")]
        via = [(f"entry:{name}", "lyricsmith.py") for name in ("sweep", "sync")]
        runs = self.located(shadows, only=alone + via)
        self.assertEqual(len(runs), 4)
        for target, done in runs.items():
            with self.subTest(target=target):
                self.assert_refused(done, "'album'", str(shadows / "album.py"))

    def test_each_library_loads_from_its_named_folder(self) -> None:
        loaded: set[str] = set()
        for target, done in self.located().items():
            with self.subTest(target=target):
                self.assertNotIn("PLANTED", done.stderr)
                self.assertEqual(done.returncode, 0, done.stderr)
                found = _loaded_from(done.stdout)
                # Every entry file imports stdio_utf8 or bounded_run, so none passes
                # empty; a private module that only locates them may import none.
                if done.args[4] == "entry":
                    self.assertTrue(found.keys() & {"stdio_utf8", "bounded_run"})
                for name, file in found.items():
                    self.assertEqual(file.parent, self.vendored / name)
                # bounded_run puts its sibling stdio_utf8 first on sys.path: that is
                # the located one only while the two share a parent folder.
                if "bounded_run" in found:
                    self.assertEqual(
                        found["stdio_utf8"].parent, self.vendored / "stdio_utf8"
                    )
                loaded |= found.keys()
        # live_pid_guard is imported only by bounded_run's own tests.
        self.assertEqual(loaded, set(self.names) - {"live_pid_guard"})


#: The sibling tool folders an entry file puts on sys.path for itself.
_SIBLING_TOOLS = ("album", "transcribe", "sweep", "sync")
#: A child that starts with every tool folder on sys.path, imports the tools that add
#: their siblings, then prints how often each tool folder is on it.
_SIBLINGS_CHILD = """
import sys
folders = sys.argv[1:]
sys.path[:0] = folders
import sweep, sync
print(" ".join(str(sys.path.count(folder)) for folder in folders))
"""


class SiblingFoldersTest(unittest.TestCase):
    """A tool that adds its sibling tools' folders adds none already on sys.path."""

    def test_a_sibling_folder_already_on_the_path_is_not_added_again(self) -> None:
        folders = [str(_HERE / name) for name in _SIBLING_TOOLS]
        done = bounded_run.run_bounded(
            [sys.executable, "-I", "-c", _SIBLINGS_CHILD, *folders],
            timeout=_HELP_TIMEOUT_S,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ["1"] * len(folders))


def _loaded_from(stdout: str) -> dict[str, Path]:
    """Each shared library the located child reported loading, and its file."""
    return {
        name: Path(file)
        for _, name, file in (
            line.split(" ", 2)
            for line in stdout.splitlines()
            if line.startswith(_LOADED + " ")
        )
    }


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
