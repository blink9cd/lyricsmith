"""The program name: run alone or as `lyricsmith sweep`, each printed command names it.

`main` runs with a fake album tool, a recording sleep and a fixed clock, as the
command-line suite runs it, but with *prog* given the way the lyricsmith dispatcher
gives it. Nothing here waits, reaches a provider, or touches a real library.
"""

from __future__ import annotations

import contextlib
import io
import shlex
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import album  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_lock  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above

#: The program name the lyricsmith dispatcher hands the sweep.
_DISPATCHED = "lyricsmith sweep"
_REFUSED = sf.Script(outcomes=sf.REFUSED, exit_code=album.EXIT_TRACK_ERRORS)


class CommandWordsTest(sf.IsolatedCase):
    """The words a printed command starts with follow the way the sweep was run."""

    def test_run_alone_the_name_is_one_word_whatever_it_holds(self) -> None:
        self.assertEqual(sweep_report.command_words("my sweep", None), ("my sweep",))

    def test_dispatched_the_name_is_the_dispatchers_words(self) -> None:
        self.assertEqual(
            sweep_report.command_words("ignored", _DISPATCHED), ("lyricsmith", "sweep")
        )

    def test_through_the_dispatcher_it_is_the_album_command(self) -> None:
        self.assertEqual(
            sweep.album_prog(("lyricsmith", "sweep")), ("lyricsmith", "album")
        )

    def test_run_alone_it_is_the_album_entry_file(self) -> None:
        self.assertEqual(sweep.album_prog(("sweep.py",)), (sweep.ALBUM_ENTRY.name,))

    def test_run_alone_a_spaced_name_is_still_the_album_entry_file(self) -> None:
        self.assertEqual(sweep.album_prog(("my sweep",)), (sweep.ALBUM_ENTRY.name,))

    def test_run_alone_a_spaced_album_name_stays_one_word_in_the_undo(self) -> None:
        album = self.library / "Artist" / "An Album"
        line = sweep_report.undo_command(("my album.py",), album, "20261022T000000Z")
        self.assertEqual(
            shlex.split(line),
            ["my album.py", "--album", str(album), "--mode", "undo",
             "--run-id", "20261022T000000Z"],
        )  # fmt: skip

    def test_resume_is_the_dispatchers_words_or_the_entry_file(self) -> None:
        self.assertEqual(
            sweep_report.resume_prog(("lyricsmith", "sweep"), "sweep.py"),
            ("lyricsmith", "sweep"),
        )
        self.assertEqual(
            sweep_report.resume_prog(("my sweep",), "sweep.py"), ("sweep.py",)
        )


class DispatchedRunTest(sf.IsolatedCase):
    """A run given the dispatcher's program name prints the dispatcher's commands."""

    def setUp(self) -> None:
        """A fake album tool, a recording sleep, and two albums to visit."""
        super().setUp()
        self.fake = sf.FakeAlbumTool()
        self.sleep = sf.FakeSleep()
        self.albums = [sf.needy_album(self.library, "Artist", n) for n in ("A1", "A2")]

    def main(self, *args: str) -> tuple[int, str, str]:
        """Run `main` as the dispatcher would; return the code, stdout and stderr."""
        argv = [*args, "--library", str(self.library)]
        wiring = sweep.Wiring(self.fake, self.sleep, sf.fixed_now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = sweep.main(argv, wiring=wiring, prog=_DISPATCHED)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_each_undo_names_the_album_command(self) -> None:
        code, stdout, _ = self.main("--mode", "run")
        self.assertEqual(code, 0)
        path = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        entries = [
            r for r in sl.read_ledger(path).records if isinstance(r, sl.LedgerEntry)
        ]
        undos = [
            line.split("undo: ", 1)[1]
            for line in stdout.splitlines()
            if "undo: " in line
        ]
        for entry, folder, undo in zip(entries, self.albums, undos, strict=True):
            self.assertEqual(
                shlex.split(undo),
                ["lyricsmith", "album", "--album", str(folder), "--mode", "undo",
                 "--run-id", entry.run_id],
            )  # fmt: skip

    def test_the_resume_names_the_sweep_command(self) -> None:
        self.fake.scripts.update(A1=_REFUSED, A2=_REFUSED)
        code, stdout, _ = self.main("--mode", "run", "--stop-after", "2")
        self.assertEqual(code, 3)
        resume = stdout.split("resume: ", 1)[1].splitlines()[0]
        self.assertEqual(
            shlex.split(resume)[:5],
            ["lyricsmith", "sweep", "--mode", "run", "--library"],
        )

    def test_a_held_lock_is_noticed_under_the_sweep_command(self) -> None:
        path = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        with sweep_lock.held(sweep_lock.lock_path(path)):
            code, _, stderr = self.main("--mode", "run")
        self.assertEqual(code, 2)
        self.assertTrue(stderr.startswith(f"{_DISPATCHED}: "), stderr)

    def test_help_names_the_sweep_command(self) -> None:
        code, stdout, _ = self.main("--help")
        self.assertEqual(code, 0)
        self.assertTrue(stdout.startswith(f"usage: {_DISPATCHED} "), stdout)
