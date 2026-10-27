"""The command line: survey and run output, the resume command, exit codes, refusals.

`main` runs with a fake album tool, a recording sleep and a fixed clock, with stdout
and stderr captured, so nothing here waits, reaches a provider, or touches a real library
or the real ledger.
"""

from __future__ import annotations

import contextlib
import errno
import io
import os
import shlex
import shutil
import sys
import unittest
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import album  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_made_run  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_lock  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run  # noqa: E402 - import follows the sys.path bootstrap above

_REFUSED = sf.Script(outcomes=sf.REFUSED, exit_code=album.EXIT_TRACK_ERRORS)
# os.geteuid exists only off Windows, so it is looked up; root can list a mode-000
# folder, so the unreadable-folder test skips under it.
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


class CliCase(sf.IsolatedCase):
    """Runs `sweep.main` against this test's library."""

    def setUp(self) -> None:
        """A fake album tool and a recording sleep."""
        super().setUp()
        self.fake = sf.FakeAlbumTool()
        self.runner: sweep_child.ChildRunner = self.fake
        self.sleep = sf.FakeSleep()
        self.now: Callable[[], datetime] = sf.fixed_now

    def main(self, *args: str, library: Path | None = None) -> Result:
        """Run `main` with *args* and ``--library``; return the code and the output."""
        argv = [*args, "--library", str(library or self.library)]
        wiring = sweep.Wiring(self.runner, self.sleep, self.now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = sweep.main(argv, wiring=wiring)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def ledger_entries(self) -> list[sl.LedgerEntry]:
        """Every entry in this library's ledger."""
        path = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        records = sl.read_ledger(path).records
        return [r for r in records if isinstance(r, sl.LedgerEntry)]


class SurveyTest(CliCase):
    """--mode survey: counts per album and in total, the visit list, nothing written."""

    def test_the_survey_counts_lists_and_writes_nothing(self) -> None:
        sf.needy_album(self.library, "Artist", "Needs")
        sf.synced_album(self.library, "Artist", "Done")
        result = self.main("--mode", "survey")
        self.assertEqual(result.code, 0)
        self.assertIn("Artist/Needs", result.stdout)
        self.assertRegex(result.stdout, r"total \(2 albums\)\s+2\s+1\s+1\s+0")
        visit = result.stdout.split("albums a run would visit: 1\n")[1]
        self.assertIn("Artist/Needs", visit)
        self.assertNotIn("Artist/Done", visit)
        self.assertIn("saved once", result.stdout)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.state.exists())

    def test_one_album_is_counted_as_one_album(self) -> None:
        sf.needy_album(self.library, "Artist", "Only")
        result = self.main("--mode", "survey")
        self.assertRegex(result.stdout, r"total \(1 album\)\s")

    def test_an_unreadable_track_is_counted_and_named(self) -> None:
        album = self.library / "Artist" / "Broken"
        sf.add_track(album, "01.mp3", txt=sf.NOT_UTF8)
        result = self.main("--mode", "survey")
        self.assertEqual(result.code, 0)
        self.assertIn("unreadable: Artist/Broken/01.mp3", result.stdout)

    def test_a_folder_with_no_mp3_is_counted_apart(self) -> None:
        (self.library / "Artist" / "Empty").mkdir(parents=True)
        result = self.main("--mode", "survey")
        self.assertIn("folders with no MP3: 1", result.stdout)

    def test_survey_is_the_default_mode(self) -> None:
        sf.needy_album(self.library, "Artist", "Needs")
        result = self.main()
        self.assertIn("albums a run would visit: 1", result.stdout)
        self.assertEqual(self.fake.calls, [])

    def test_run_only_options_are_refused_by_the_survey(self) -> None:
        options = (
            ["--offline"],
            ["--limit", "1"],
            ["--restart"],
            ["--pause-seconds", "99"],
            ["--stop-after", "1"],
        )
        for option in options:
            with self.subTest(option=option):
                result = self.main("--mode", "survey", *option)
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"{option[0]} cannot be used with --mode survey", result.stderr
                )


class LibraryTest(CliCase):
    """--library: its default under HOME, and a missing one."""

    def test_a_missing_library_exits_two_naming_it(self) -> None:
        for mode in ("survey", "run"):
            with self.subTest(mode=mode):
                result = self.main("--mode", mode, library=self.tmp / "nowhere")
                self.assertEqual(result.code, 2)
                self.assertIn("nowhere", result.stderr)

    def test_the_default_library_is_music_albums_under_home(self) -> None:
        default = self.home / "Music" / "Albums"
        sf.needy_album(default, "Home Artist", "Album")
        stdout = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = sweep.main(["--mode", "survey"])
        self.assertEqual(code, 0)
        self.assertIn("Home Artist/Album", stdout.getvalue())

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_a_library_that_cannot_be_listed_exits_two(self) -> None:
        self.library.chmod(0)
        self.addCleanup(self.library.chmod, 0o755)
        result = self.main("--mode", "survey")
        self.assertEqual(result.code, 2)
        self.assertIn("cannot be listed", result.stderr)

    def test_ctrl_c_during_a_survey_exits_130_with_no_resume(self) -> None:
        with mock.patch.object(sweep, "survey_library", side_effect=KeyboardInterrupt):
            result = self.main("--mode", "survey")
        self.assertEqual(result.code, 130)
        self.assertNotIn("resume:", result.stdout)

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_an_unreadable_artist_folder_is_noticed_by_name(self) -> None:
        locked = self.library / "Locked"
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        result = self.main("--mode", "survey")
        self.assertIn("Locked", result.stderr)


class LinkedFolderTest(CliCase):
    """A linked Artist or Album folder is named on stderr and never visited."""

    def setUp(self) -> None:
        """One album at home, and two reached through links that lead outside."""
        super().setUp()
        sf.needy_album(self.library, "Artist", "Home")
        outside = self.tmp / "outside"
        sf.needy_album(outside, "Far Artist", "Far Album")
        (self.library / "Linked Artist").symlink_to(outside / "Far Artist")
        (self.library / "Artist" / "Linked").symlink_to(
            outside / "Far Artist" / "Far Album"
        )

    def test_the_survey_names_each_dropped_album(self) -> None:
        result = self.main("--mode", "survey")
        self.assertEqual(result.code, 0)
        self.assertIn("albums a run would visit: 1\n", result.stdout)
        self.assertIn("Linked Artist/Far Album", result.stderr)
        self.assertIn("Artist/Linked", result.stderr)

    def test_a_run_never_visits_a_dropped_album(self) -> None:
        result = self.main("--mode", "run")
        self.assertEqual(result.code, 0)
        self.assertEqual(self.fake.albums(), ["Home"])
        self.assertIn("Linked Artist/Far Album", result.stderr)
        self.assertIn("Artist/Linked", result.stderr)


class RunTest(CliCase):
    """--mode run: the final report, each album's undo, and the resume command."""

    def setUp(self) -> None:
        """Three albums to visit and one already synced."""
        super().setUp()
        self.albums = [
            sf.needy_album(self.library, "Artist", name) for name in ("A1", "A2", "A3")
        ]
        sf.synced_album(self.library, "Artist", "Synced")

    def test_the_report_lists_each_album_with_its_undo(self) -> None:
        result = self.main("--mode", "run")
        self.assertEqual(result.code, 0)
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A3"])
        for entry, folder in zip(self.ledger_entries(), self.albums, strict=True):
            undo = (
                f"album.py --album {shlex.quote(str(folder))} --mode undo "
                f"--run-id {entry.run_id}"
            )
            self.assertIn(undo, result.stdout)
        self.assertNotIn("resume:", result.stdout)

    def test_lang_and_offline_reach_the_album_tool(self) -> None:
        self.main("--mode", "run", "--lang", "deu", "--offline", "--limit", "1")
        self.assertEqual(self.fake.calls[0][-3:], ["--lang", "deu", "--offline"])
        self.assertEqual(self.fake.timeouts, [sweep_child.ALBUM_TIMEOUT_S])

    def test_the_pause_and_the_stop_take_their_defaults(self) -> None:
        # Every album refused: the default stop is what ends the run, and where.
        self.fake.scripts.update(A1=_REFUSED, A2=_REFUSED, A3=_REFUSED)
        result = self.main("--mode", "run")
        self.assertEqual(result.code, sweep_run.EXIT_REFUSED)
        self.assertEqual(len(self.fake.albums()), sweep.DEFAULT_STOP_AFTER)
        self.assertEqual(self.sleep.pauses, [sweep.DEFAULT_PAUSE_S] * 2)

    def test_a_refusal_stop_exits_three_with_the_resume_command(self) -> None:
        self.fake.scripts.update(A1=_REFUSED, A2=_REFUSED)
        result = self.main("--mode", "run", "--stop-after", "2", "--pause-seconds", "4")
        self.assertEqual(result.code, 3)
        resume = (
            f"sweep.py --mode run --library {shlex.quote(str(self.library))} "
            "--pause-seconds 4.0 --stop-after 2"
        )
        self.assertIn(f"resume: {resume}", result.stdout)
        self.assertIn("refused", result.stdout)

    def test_the_resume_command_carries_the_options_but_never_restart(self) -> None:
        result = self.main(
            "--mode", "run", "--limit", "1", "--restart", "--offline", "--lang", "deu"
        )
        resume = result.stdout.split("resume: ")[1].splitlines()[0]
        self.assertIn("--limit 1", resume)
        self.assertIn("--offline", resume)
        self.assertIn("--lang deu", resume)
        self.assertNotIn("--restart", resume)

    def test_ctrl_c_in_an_album_exits_130_recorded_with_the_resume(self) -> None:
        self.fake.scripts["A2"] = sf.Script(
            exit_code=album.EXIT_INTERRUPTED, interrupted=True
        )
        result = self.main("--mode", "run")
        self.assertEqual(result.code, 130)
        self.assertIn("resume: ", result.stdout)
        self.assertEqual(
            [e.outcome for e in self.ledger_entries()],
            [sl.Outcome.DONE, sl.Outcome.INTERRUPTED],
        )

    def test_ctrl_c_anywhere_else_exits_130_with_the_resume(self) -> None:
        def interrupting(_argv: object, _timeout: float) -> sweep_child.ChildResult:
            raise KeyboardInterrupt

        self.runner = interrupting
        result = self.main("--mode", "run")
        self.assertEqual(result.code, 130)
        self.assertIn("interrupted", result.stderr)
        self.assertIn("resume: ", result.stdout)

    def test_ctrl_c_while_a_run_is_read_exits_130_recorded_with_the_resume(
        self,
    ) -> None:
        # The album tool finished A1, and Ctrl-C lands while its manifest is read.
        with mock.patch.object(
            sweep_made_run, "tally_manifest", side_effect=KeyboardInterrupt
        ):
            result = self.main("--mode", "run")
        self.assertEqual(result.code, 130)
        self.assertEqual(self.fake.albums(), ["A1"])
        self.assertIn("Artist/A1", result.stdout)
        self.assertIn("resume: ", result.stdout)
        (entry,) = self.ledger_entries()
        self.assertEqual((entry.outcome, entry.exit_code), (sl.Outcome.INTERRUPTED, 0))

    def assert_stopped_with_a1_recorded(self, result: Result) -> None:
        """Only A1 was visited, recorded once with its undo, then the sweep stopped."""
        self.assertEqual(result.code, 130)
        self.assertEqual(self.fake.albums(), ["A1"])
        (entry,) = self.ledger_entries()
        self.assertEqual(entry.outcome, sl.Outcome.DONE)
        self.assertIn(f"--mode undo --run-id {entry.run_id}", result.stdout)
        self.assertIn("resume: ", result.stdout)

    def test_ctrl_c_during_the_ledger_write_finishes_it_then_stops(self) -> None:
        # Ctrl-C lands once A1's entry is already in the ledger, before the call returns.
        real_append = sweep_run.append_unless_last
        calls: list[object] = []

        def interrupted_once(path: Path, record: sl.LedgerRecord) -> None:
            calls.append(record)
            real_append(path, record)
            if len(calls) == 1:
                raise KeyboardInterrupt

        with mock.patch.object(sweep_run, "append_unless_last", interrupted_once):
            result = self.main("--mode", "run")
        self.assert_stopped_with_a1_recorded(result)

    def test_ctrl_c_just_after_the_tally_still_records_the_album(self) -> None:
        # Ctrl-C lands as A1's entry is made, after its run was read and tallied.
        stamps: list[datetime] = []

        def interrupted_once() -> datetime:
            stamps.append(sf.fixed_now())
            if len(stamps) == 1:
                raise KeyboardInterrupt
            return stamps[-1]

        self.now = interrupted_once
        self.assert_stopped_with_a1_recorded(self.main("--mode", "run"))

    def test_an_album_swapped_for_a_link_after_the_survey_is_never_run(self) -> None:
        outside = sf.needy_album(self.tmp / "outside", "Far Artist", "Far Album")

        def swapping(argv: Sequence[str], timeout_s: float) -> sweep_child.ChildResult:
            # While the first album runs, A2 is replaced by a link leading out of the
            # library; the survey had found it inside.
            if not self.fake.calls:
                shutil.rmtree(self.albums[1])
                self.albums[1].symlink_to(outside)
            return self.fake(argv, timeout_s)

        self.runner = swapping
        result = self.main("--mode", "run")
        self.assertEqual(self.fake.albums(), ["A1", "A3"])
        self.assertFalse((outside / ".lyricsmith").exists())
        self.assertIn("Artist/A2", result.stderr)
        self.assertIn("Artist/A2", result.stdout)
        self.assertEqual(
            [e.album for e in self.ledger_entries()], ["Artist/A1", "Artist/A3"]
        )
        self.assertEqual(result.code, 0)

    def test_an_album_with_no_run_shows_dashes_and_no_undo(self) -> None:
        self.fake.scripts["A1"] = sf.Script(
            exit_code=album.EXIT_CANNOT_RUN, manifest=False
        )
        result = self.main("--mode", "run", "--limit", "1")
        self.assertEqual(result.code, 1)
        self.assertRegex(result.stdout, r"Artist/A1\s+-\s+2\s+could not run(\s+-){7}")
        self.assertNotIn("undo:", result.stdout)

    def test_an_abandoned_child_shows_no_exit_code_and_its_pid(self) -> None:
        self.fake.scripts["A1"] = sf.Script(exit_code=None, timed_out=True, pid=4242)
        result = self.main("--mode", "run", "--limit", "1")
        self.assertEqual(result.code, 1)
        self.assertRegex(result.stdout, r"Artist/A1\s+\S+\s+-\s+abandoned")
        self.assertIn("4242", result.stderr)

    @unittest.skipIf(_IS_ROOT, "root can write a read-only folder")
    def test_a_ledger_that_cannot_be_written_exits_two_naming_it(self) -> None:
        ledger = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        ledger.parent.mkdir(parents=True)
        # The lock is made first, so only the ledger's own write meets the folder.
        sweep_lock.lock_path(ledger).touch()
        ledger.parent.chmod(0o500)
        self.addCleanup(ledger.parent.chmod, 0o755)
        result = self.main("--mode", "run")
        self.assertEqual(result.code, 2)
        self.assertIn(str(ledger), result.stderr)
        self.assertNotIn("cannot be listed", result.stderr)
        # The report so far still prints: the album in hand, and why it stopped.
        self.assertIn("Artist/A1", result.stdout)
        self.assertIn("ledger", result.stdout)

    def test_an_album_that_errored_exits_one(self) -> None:
        self.fake.scripts["A3"] = sf.Script(
            outcomes=(sf.fetch_error(),), exit_code=album.EXIT_TRACK_ERRORS
        )
        self.assertEqual(self.main("--mode", "run").code, 1)

    def test_a_run_with_every_album_done_prints_no_table(self) -> None:
        self.main("--mode", "run")
        result = self.main("--mode", "run")
        self.assertEqual(result.code, 0)
        self.assertEqual(result.stdout, "already done: 3\nfinished; albums left: 0\n")

    def test_a_second_run_skips_what_the_first_finished(self) -> None:
        self.main("--mode", "run", "--limit", "2")
        result = self.main("--mode", "run")
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A3"])
        self.assertIn("already done: 2", result.stdout)


class OneSweepAtATimeTest(CliCase):
    """A second run on a library whose sweep is running exits 2 before visiting."""

    def setUp(self) -> None:
        """One album to visit, and the path of this library's lock."""
        super().setUp()
        sf.needy_album(self.library, "Artist", "A1")
        ledger = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        self.lock = sweep_lock.lock_path(ledger)

    def test_a_run_while_another_holds_the_lock_exits_two(self) -> None:
        with sweep_lock.held(self.lock):
            result = self.main("--mode", "run")
        self.assertEqual(result.code, 2)
        self.assertIn("another sweep", result.stderr)
        self.assertIn(str(self.lock), result.stderr)
        self.assertEqual(self.fake.calls, [])
        self.assertNotIn("resume:", result.stdout)

    def test_a_lock_flock_refuses_for_another_reason_exits_two(self) -> None:
        no_locks = OSError(errno.ENOLCK, "No locks available")
        with mock.patch("fcntl.flock", side_effect=no_locks):
            result = self.main("--mode", "run")
        self.assertEqual(result.code, 2)
        self.assertIn(str(self.lock), result.stderr)
        self.assertEqual(self.fake.calls, [])

    def test_a_survey_needs_no_lock(self) -> None:
        with sweep_lock.held(self.lock):
            self.assertEqual(self.main("--mode", "survey").code, 0)

    def test_the_lock_is_let_go_after_a_run(self) -> None:
        self.main("--mode", "run")
        with sweep_lock.held(self.lock):
            pass


class PosixOnlyTest(CliCase):
    """A run signals the album tool with SIGINT, so it is refused off POSIX."""

    def test_a_run_on_windows_exits_two_before_anything_runs(self) -> None:
        sf.needy_album(self.library, "Artist", "A1")
        windows = SimpleNamespace(name="nt", environ=os.environ)
        with mock.patch.object(sweep, "os", windows):
            result = self.main("--mode", "run")
        self.assertEqual(result.code, 2)
        self.assertIn("POSIX", result.stderr)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.state.exists())

    def test_a_survey_on_windows_still_runs(self) -> None:
        windows = SimpleNamespace(name="nt", environ=os.environ)
        with mock.patch.object(sweep, "os", windows):
            self.assertEqual(self.main("--mode", "survey").code, 0)


class OptionTest(CliCase):
    """Values a run cannot use are refused before anything runs."""

    def test_unusable_values_exit_two(self) -> None:
        for option in (
            ["--limit", "0"],
            ["--stop-after", "0"],
            ["--pause-seconds", "-1"],
            ["--pause-seconds", "nan"],
            ["--pause-seconds", "soon"],
            ["--limit", "some"],
            ["--lang", "English"],
        ):
            with self.subTest(option=option):
                self.assertEqual(self.main("--mode", "run", *option).code, 2)
        self.assertEqual(self.fake.calls, [])

    def test_help_shows_the_defaults_and_the_exit_codes(self) -> None:
        result = self.main("--help")
        self.assertEqual(result.code, 0)
        self.assertIn(f"default: {sweep.DEFAULT_STOP_AFTER}", result.stdout)
        self.assertIn("130", result.stdout)
        self.assertIn("stopped", result.stdout)
