"""The walk-through in samples/README.md, run for real, and its blocks checked verbatim.

The library is built by running the README's own build block in bash, from the project
root, with `mktemp -d` pointed inside this test's directory and ``python`` this
interpreter. The survey, an offline run with the real child runner and each album's undo
then run in process, with the arguments and the state directory the README's command
lines give, from the folder the library sits in. Every block, commands and outputs,
must appear in the README as written, each output must equal its block with its
placeholders filled in, and the undo must hand back each album as it was built, byte
for byte. The silence pass's preview and its resume run with the host's real ffmpeg,
and are skipped without one. The walk-through is a POSIX shell session over POSIX
paths, so it skips on Windows; the check that every block appears in the README runs
on every host.
"""

from __future__ import annotations

import contextlib
import io
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from collections.abc import Callable, Sequence
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import album  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above

_README = _HERE.parent / "samples" / "README.md"
#: The folder the README's commands are run from, the project's root.
_PROJECT = _HERE.parent.parent
#: How long building the sample album may take.
_BUILD_TIMEOUT_S = 120
#: Why the walk-through skips on Windows. Its build block is a POSIX shell session run
#: through a bare ``bash``, which a Windows process resolves to WSL's ``System32`` alias
#: ahead of Git Bash, and its expected output spells each path the POSIX way, with
#: ``/`` between the library and its albums.
_POSIX_WALK_THROUGH = (
    "the README's walk-through is a POSIX shell session over POSIX paths"
)
#: The width of a run id with no collision suffix, ``YYYYMMDDTHHMMSSZ``.
_RUN_ID_WIDTH = 16

_SAMPLE = "The Invented Quartet/Sample Album"
_SECOND = "The Invented Quartet/Second Album"
_UNTIMED = "The Made-Up Trio/Untimed Album"
#: How each sweep command line starts, as the shell splits it: the state
#: directory set for that command alone, then the tool.
_SWEEP_PREFIX = (
    "XDG_STATE_HOME=$PWD/state",
    "python",
    "$TOOLS/sweep/sweep.py",
)
#: ffmpeg on PATH at import, or None: the silence pass's steps need a real one.
_FFMPEG = shutil.which("ffmpeg")
#: How each undo command line starts, as the shell splits it.
_UNDO_PREFIX = ("python", "$TOOLS/album/album.py")

BUILD = """\
TOOLS="$PWD"  # run from this project's root folder
cd "$(mktemp -d)" || return
python "$TOOLS/album/samples/build_sample_album.py" \\
  --out "library/The Invented Quartet/Sample Album"
mkdir -p "library/The Invented Quartet/Second Album" \\
  "library/The Made-Up Trio/Untimed Album"
cp "library/The Invented Quartet/Sample Album/01 The Keeper Counts."* \\
  "library/The Invented Quartet/Second Album/"
python "$TOOLS/album/album.py" \\
  --album "library/The Invented Quartet/Second Album" --offline
rm -r "library/The Invented Quartet/Second Album/.lyricsmith"
cp "library/The Invented Quartet/Sample Album/02 Orchard Wall.mp3" \\
  "library/The Invented Quartet/Sample Album/03 The Mill Bell.mp3" \\
  "library/The Made-Up Trio/Untimed Album/"
printf 'Over the orchard wall\\nthe ladders lean and wait\\n' \\
  > "library/The Made-Up Trio/Untimed Album/02 Orchard Wall.txt"
printf 'The mill bell rings at noon\\nand nobody comes home\\n' \\
  > "library/The Made-Up Trio/Untimed Album/03 The Mill Bell.txt"
"""

SURVEY_COMMAND = """\
XDG_STATE_HOME="$PWD/state" python "$TOOLS/sweep/sweep.py" \\
  --library library --mode survey
"""

RUN_COMMAND = """\
XDG_STATE_HOME="$PWD/state" python "$TOOLS/sweep/sweep.py" \\
  --library library --mode run --offline --pause-seconds 0
"""

SILENCE_COMMAND = """\
XDG_STATE_HOME="$PWD/state" python "$TOOLS/sweep/sweep.py" \\
  --library library --mode silence --limit 1
"""

SILENCE_RESUME_COMMAND = """\
XDG_STATE_HOME="$PWD/state" python "$TOOLS/sweep/sweep.py" \\
  --mode silence --library "<library>" --resume "<list>"
"""

UNDO_COMMANDS = """\
python "$TOOLS/album/album.py" \\
  --album "library/The Invented Quartet/Sample Album" --mode undo --run-id "<run-id-1>"
python "$TOOLS/album/album.py" \\
  --album "library/The Made-Up Trio/Untimed Album" --mode undo --run-id "<run-id-2>"
"""

SURVEY = """\
album                              synced  untimed  none  unreadable
The Invented Quartet/Sample Album       1        2     3           0
The Invented Quartet/Second Album       1        0     0           0
The Made-Up Trio/Untimed Album          0        2     0           0
total (3 albums)                        2        4     3           0
unembedded: The Invented Quartet/Sample Album/01 The Keeper Counts.mp3 (unlabeled)
albums a run would visit: 2
  The Invented Quartet/Sample Album
  The Made-Up Trio/Untimed Album
note: an album whose lyrics were embedded by other tools may have each track
  saved once by its first run, even where the words stay the same: the frames
  written from a timed sidecar keep blank lines those tools left out. In a
  library kept in a synced cloud folder, that is one upload per track.
"""

RUN = """\
already done: 0
album                                           run  exit  outcome  written  unchanged  not found  removed  no lyrics expected  silent  kept  errors  flagged  refusing
The Invented Quartet/Sample Album        <run-id-1>     0  offline        2          0          2        1                   1       0     0       0        1         0
The Made-Up Trio/Untimed Album           <run-id-2>     0  offline        2          0          0        0                   0       0     0       0        0         0
written: embedded 4
removed: placeholder 1
The Invented Quartet/Sample Album  undo: album.py --album '<library>/The Invented Quartet/Sample Album' --mode undo --run-id <run-id-1>
The Made-Up Trio/Untimed Album  undo: album.py --album '<library>/The Made-Up Trio/Untimed Album' --mode undo --run-id <run-id-2>
finished; albums left: 0
"""

SILENCE_PREVIEW = """\
already done: 0
albums measured: 1
tracks measured: 6
silent: 6
  likely meant: 0
  likely a ripping mistake: 6
  unsure: 0
not measured: 0
limit reached; albums left: 2
list: <list>
page: <page>
resume: sweep.py --mode silence --library <library> --resume <list>
"""

SILENCE_RESUMED = """\
already done: 1
albums measured: 2
tracks measured: 3
silent: 3
  likely meant: 0
  likely a ripping mistake: 3
  unsure: 0
not measured: 0
finished; albums left: 0
list: <list>
page: <page>
"""

UNDO_SAMPLE = """\
album undo <run-id-1>: 6 tracks under Sample Album

 #  track                     undo
 1  01 The Keeper Counts.mp3  undone
 2  02 Orchard Wall.mp3       undone
 3  03 The Mill Bell.mp3      undone
 4  04 The Long Bit.mp3       undone
 5  05 The Short Bit.mp3      undone
 6  06 The Quiet Reel.mp3     undone
undone 6 · already undone 0 · errors 0
"""

UNDO_UNTIMED = """\
album undo <run-id-2>: 2 tracks under Untimed Album

 #  track                 undo
 1  02 Orchard Wall.mp3   undone
 2  03 The Mill Bell.mp3  undone
undone 2 · already undone 0 · errors 0
"""


def _write_script(path: Path, body: str) -> None:
    """Make *path* an executable sh script running *body*."""
    path.write_bytes(f"#!/bin/sh\n{body}\n".encode())
    path.chmod(0o755)


def _command_lines(block: str) -> list[list[str]]:
    """Each command line of *block*, continuations joined, split as the shell splits it."""
    return [shlex.split(line) for line in block.replace("\\\n", " ").splitlines()]


def _snapshot(folder: Path) -> dict[str, bytes]:
    """Every file under *folder* outside the run folders, by relative path, with bytes."""
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and ".lyricsmith" not in path.parts
    }


def _filled(block: str, run_ids: Sequence[str], library: Path) -> str:
    """*block* with each ``<run-id-N>`` and ``<library>`` replaced by its real value.

    In the report's table a placeholder stands right-aligned in a run id's width, so
    that padded form is replaced first.
    """
    for number, run_id in enumerate(run_ids, start=1):
        placeholder = f"<run-id-{number}>"
        block = block.replace(placeholder.rjust(len(run_id)), run_id)
        block = block.replace(placeholder, run_id)
    return block.replace("<library>", str(library))


def _placed(block: str, values: dict[str, str]) -> str:
    """*block* with each placeholder of *values* replaced by its value."""
    for placeholder, value in values.items():
        block = block.replace(placeholder, value)
    return block


class ReadmeBlocksTest(sf.IsolatedCase):
    """Every block the walk-through below runs appears in the README as written.

    It reads the README alone, so it runs on every host, Windows included.
    """

    def test_the_readme_shows_every_block_verbatim(self) -> None:
        readme = _README.read_text(encoding="utf-8")
        commands = (BUILD, SURVEY_COMMAND, RUN_COMMAND, UNDO_COMMANDS)
        silence = (SILENCE_COMMAND, SILENCE_RESUME_COMMAND, SILENCE_PREVIEW)
        for block in (*commands, *silence, SILENCE_RESUMED, SURVEY, RUN):
            self.assertIn(block, readme)
        for block in (UNDO_SAMPLE, UNDO_UNTIMED):
            self.assertIn(block, readme)


@unittest.skipIf(os.name == "nt", _POSIX_WALK_THROUGH)
class SampleSweepTest(sf.IsolatedCase):
    """The README's walk-through, step by step, on a freshly built library."""

    maxDiff = None
    work: Path

    def setUp(self) -> None:
        """Run the README's build block, then work from the folder it made."""
        super().setUp()
        self.work, tools = self.tmp / "work", self.tmp / "bin"
        tools.mkdir()
        # The block's bare `python` must be this interpreter, which has the lyrics
        # extra, and its `mktemp -d` must make a folder inside this test's own: not
        # every mktemp heeds TMPDIR.
        _write_script(tools / "python", f'exec {shlex.quote(sys.executable)} "$@"')
        work = shlex.quote(str(self.work))
        _write_script(tools / "mktemp", f"mkdir {work} && echo {work}")
        path = os.pathsep.join([str(tools), *os.get_exec_path()])
        subprocess.run(  # reliability: ignore[RL005] DEVNULL sinks, no pipe to hold
            ["bash", "-e", "-c", BUILD],
            cwd=_PROJECT,
            env={**os.environ, "PATH": path},
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_BUILD_TIMEOUT_S,
        )
        self.library = self.work / "library"
        cwd = Path.cwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, cwd)

    def _call(
        self, main: Callable[[list[str]], int], prog: str, argv: list[str]
    ) -> tuple[int, str]:
        """Run *main* on *argv* as *prog*; its exit code and stdout, stderr dropped."""
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", [prog]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = main(argv)
        return code, out.getvalue()

    def _arguments(self, words: Sequence[str], prefix: Sequence[str]) -> list[str]:
        """*words* after *prefix*, which they must start with."""
        self.assertEqual(list(words[: len(prefix)]), list(prefix))
        return list(words[len(prefix) :])

    def _sweep(self, command: str) -> tuple[int, str]:
        """Run the README's sweep *command*, its state directory set as it sets it."""
        (words,) = _command_lines(command)
        argv = self._arguments(words, _SWEEP_PREFIX)
        os.environ["XDG_STATE_HOME"] = str(Path.cwd() / "state")
        # The child inherits this environment: keep the host's ffmpeg out of it.
        os.environ["PATH"] = sf.env_without_ffmpeg(self.tmp)["PATH"]
        return self._call(sweep.main, "sweep.py", argv)

    def _silence(self, command: str) -> tuple[int, str]:
        """Run the README's silence *command* with the host's PATH, ffmpeg on it."""
        (words,) = _command_lines(command)
        argv = self._arguments(words, _SWEEP_PREFIX)
        os.environ["XDG_STATE_HOME"] = str(Path.cwd() / "state")
        return self._call(sweep.main, "sweep.py", argv)

    def _undo(self, words: Sequence[str]) -> tuple[int, str]:
        """Run one of the README's album undo command lines."""
        argv = self._arguments(words, _UNDO_PREFIX)
        return self._call(album.main, "album.py", argv)

    def _run_id(self, album: str) -> str:
        """The id of the one run made on *album*."""
        (run_id,) = sweep_child.run_ids(self.library / album)
        self.assertEqual(len(run_id), _RUN_ID_WIDTH)
        return run_id

    def test_the_survey_prints_what_the_readme_shows_and_writes_nothing(self) -> None:
        self.assertEqual(self._sweep(SURVEY_COMMAND), (0, SURVEY))
        self.assertEqual(list(self.library.rglob(".lyricsmith")), [])
        self.assertFalse((self.work / "state").exists())

    def test_the_run_and_each_undo_print_what_the_readme_shows(self) -> None:
        built = _snapshot(self.library)
        code, report = self._sweep(RUN_COMMAND)
        ids = (self._run_id(_SAMPLE), self._run_id(_UNTIMED))
        self.assertEqual((code, report), (0, _filled(RUN, ids, self.library)))
        self.assertEqual(sweep_child.run_ids(self.library / _SECOND), frozenset())
        (ledger,) = (self.work / "state" / "lyricsmith" / "sweep").glob(
            "ledger-*.jsonl"
        )
        lines = ledger.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), len(ids))
        self.assertNotEqual(_snapshot(self.library), built)
        undo_lines = _command_lines(_filled(UNDO_COMMANDS, ids, self.library))
        sample = self._undo(undo_lines[0])
        self.assertEqual(sample, (0, _filled(UNDO_SAMPLE, ids, self.library)))
        untimed = self._undo(undo_lines[1])
        self.assertEqual(untimed, (0, _filled(UNDO_UNTIMED, ids, self.library)))
        self.assertEqual(_snapshot(self.library), built)

    @unittest.skipIf(_FFMPEG is None, "ffmpeg not on PATH")
    def test_the_silence_preview_and_its_resume_print_what_the_readme_shows(
        self,
    ) -> None:
        code, preview = self._silence(SILENCE_COMMAND)
        (listed,) = (self.work / "state" / "lyricsmith" / "silence").glob("*.tsv")
        names = {
            "<list>": str(listed),
            "<page>": str(listed.with_suffix(".html")),
            "<library>": str(self.library),
        }
        self.assertEqual((code, preview), (0, _placed(SILENCE_PREVIEW, names)))
        code, resumed = self._silence(_placed(SILENCE_RESUME_COMMAND, names))
        self.assertEqual((code, resumed), (0, _placed(SILENCE_RESUMED, names)))
        lines = listed.read_text(encoding="utf-8").splitlines()[1:]
        rows = [line for line in lines if not line.startswith("#")]
        self.assertEqual(len(rows), 9)
        self.assertTrue(
            all(row.startswith("likely a ripping mistake\t") for row in rows)
        )
        self.assertEqual(
            [line.split("\t")[1] for line in lines if line.startswith("#")],
            [_SAMPLE, _SECOND, _UNTIMED],
        )
        page = listed.with_suffix(".html").read_text(encoding="utf-8")
        for heading, count in (
            ("Likely a ripping mistake", 9),
            ("Unsure", 0),
            ("Not measured", 0),
            ("Likely meant", 0),
        ):
            self.assertIn(f'{heading} <span class="count">{count}</span>', page)
