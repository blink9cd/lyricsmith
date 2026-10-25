"""One album the sweep cannot read is that album's failure, never the library's.

An album whose run folder cannot be listed, or whose album tool cannot be started, is
recorded as could not run, with the error named, and the sweep goes on to the next
album and prints its report. Only the library's own listing ends a sweep as a library
that cannot be listed. The album tool is played by `sweep_fixtures.FakeAlbumTool`, so
nothing here runs a process or reaches a provider.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_changed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_survey  # noqa: E402 - import follows the sys.path bootstrap above

# os.geteuid exists only off Windows, so it is looked up; root can list a mode-000
# folder, so the unreadable-folder tests skip under it.
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0
_LIBRARY_MESSAGE = "the library cannot be listed"


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


class AlbumErrorCase(sf.IsolatedCase):
    """Two needy albums, the first one sorted first, and a fake album tool."""

    def setUp(self) -> None:
        """Build ``Artist A/First`` and ``Artist B/Second``."""
        super().setUp()
        self.first = sf.needy_album(self.library, "Artist A", "First")
        self.second = sf.needy_album(self.library, "Artist B", "Second")
        self.fake = sf.FakeAlbumTool()
        self.runner: sweep_child.ChildRunner = self.fake

    def main(self, *args: str) -> Result:
        """Run `main` with *args* on this library; the code and the output."""
        argv = ["--mode", "run", "--library", str(self.library), *args]
        wiring = sweep.Wiring(self.runner, sf.FakeSleep(), sf.fixed_now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sweep.main(argv, wiring=wiring)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def entries(self) -> list[sl.LedgerEntry]:
        """Every album entry in this library's ledger."""
        path = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        records = sl.read_ledger(path).records
        return [r for r in records if isinstance(r, sl.LedgerEntry)]

    def lock_run_folder(self) -> None:
        """Make the first album's run folder unlistable until the test ends."""
        locked = self.first / lr.RUN_FOLDER_NAME
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)

    def assert_first_could_not_run(self, result: Result, error: str) -> None:
        """The first album could not run, named with *error*; the second still ran."""
        self.assertEqual(result.code, sweep_run.EXIT_ALBUM_ERRORS)
        self.assertNotIn(_LIBRARY_MESSAGE, result.stderr)
        self.assertEqual(self.fake.albums(), ["Second"])
        self.assertRegex(result.stdout, r"Artist A/First\s+-\s+-\s+could not run")
        self.assertIn(f"Artist A/First  could not run: {error}", result.stdout)
        self.assertIn("finished; albums left: 0", result.stdout)
        first, second = self.entries()
        self.assertEqual(first.album, "Artist A/First")
        self.assertIs(first.outcome, sl.Outcome.COULD_NOT_RUN)
        self.assertIsNone(first.exit_code)
        self.assertIsNone(first.run_id)
        self.assertIs(second.outcome, sl.Outcome.DONE)


class UnreadableRunFolderTest(AlbumErrorCase):
    """An album whose run folder cannot be listed could not run; the sweep goes on."""

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_the_album_could_not_run_and_the_next_one_runs(self) -> None:
        self.lock_run_folder()
        result = self.main()
        self.assert_first_could_not_run(result, "[Errno 13] Permission denied")

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_a_later_sweep_retries_the_album_once_it_can_be_read(self) -> None:
        self.lock_run_folder()
        self.main()
        (self.first / lr.RUN_FOLDER_NAME).chmod(0o755)
        result = self.main()
        self.assertEqual(result.code, sweep_run.EXIT_OK)
        self.assertEqual(self.fake.albums(), ["Second", "First"])
        self.assertIs(self.entries()[-1].outcome, sl.Outcome.DONE)


class ChildLaunchTest(AlbumErrorCase):
    """An album tool that cannot be started could not run; the sweep goes on."""

    def test_a_child_that_cannot_start_could_not_run(self) -> None:
        def runner(argv: Sequence[str], timeout_s: float) -> sweep_child.ChildResult:
            if sf.option_value(argv, "--album").endswith("First"):
                raise FileNotFoundError(2, "No such file or directory", "python")
            return self.fake(argv, timeout_s)

        self.runner = runner
        result = self.main()
        self.assert_first_could_not_run(result, "[Errno 2] No such file or directory")


class PostRunListingTest(AlbumErrorCase):
    """A run folder unlistable only after the album tool ran is no record, not could not run.

    The album tool already ran and may have changed tags, so its exit code is kept and
    the album is never said to have not run at all.
    """

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_the_album_has_no_record_and_keeps_its_exit_code(self) -> None:
        def runner(argv: Sequence[str], timeout_s: float) -> sweep_child.ChildResult:
            result = self.fake(argv, timeout_s)
            if sf.option_value(argv, "--album").endswith("First"):
                locked = self.first / lr.RUN_FOLDER_NAME
                locked.chmod(0)
                self.addCleanup(locked.chmod, 0o755)
            return result

        self.runner = runner
        result = self.main()
        self.assertEqual(result.code, sweep_run.EXIT_ALBUM_ERRORS)
        self.assertNotIn("could not run:", result.stdout)
        self.assertNotIn("could not run:", result.stderr)
        self.assertIn(
            "Artist A/First: the run folder cannot be listed after the album tool "
            "ran: [Errno 13] Permission denied",
            result.stderr,
        )
        first, second = self.entries()
        self.assertIs(first.outcome, sl.Outcome.NO_RECORD)
        self.assertEqual(first.exit_code, 0)
        self.assertIsNone(first.run_id)
        self.assertIs(second.outcome, sl.Outcome.DONE)


class LaunchFailedChangedListTest(AlbumErrorCase):
    """An album tool that could not start lists its album at once, saying it never started."""

    def test_the_notice_comes_before_the_next_album_and_says_so(self) -> None:
        def runner(argv: Sequence[str], timeout_s: float) -> sweep_child.ChildResult:
            if sf.option_value(argv, "--album").endswith("First"):
                raise FileNotFoundError(2, "No such file or directory", "python")
            return self.fake(argv, timeout_s)

        self.runner = runner
        listed = self.tmp / "changed.txt"
        result = self.main("--changed-list", str(listed))
        notice = (
            "Artist A/First: every MP3 of the album is listed in --changed-list, as "
            "the album tool could not start"
        )
        self.assertIn(notice, result.stderr)
        self.assertNotIn(
            "Artist A/First: every MP3 of the album is listed in "
            "--changed-list, as the album tool left",
            result.stderr,
        )
        self.assertLess(
            result.stderr.index(notice), result.stderr.index("[2/2] Artist B/Second")
        )
        lines = listed.read_text(encoding="utf-8").splitlines()
        firsts = [line for line in lines if "First" in line]
        self.assertEqual(len(firsts), 3)
        self.assertEqual(lines[: len(firsts)], firsts)

    def test_a_ctrl_c_during_the_immediate_absorb_stops_the_sweep(self) -> None:
        def runner(argv: Sequence[str], timeout_s: float) -> sweep_child.ChildResult:
            if sf.option_value(argv, "--album").endswith("First"):
                raise FileNotFoundError(2, "No such file or directory", "python")
            return self.fake(argv, timeout_s)

        real = sweep_changed.ChangedUnion._read_child
        calls: list[Path] = []

        def read_child(
            union: sweep_changed.ChangedUnion,
            album: Path,
            pending: sweep_changed._Pending,
            *,
            started: bool,
        ) -> Iterable[str]:
            calls.append(album)
            if len(calls) == 1:
                raise KeyboardInterrupt
            return real(union, album, pending, started=started)

        self.runner = runner
        listed = self.tmp / "changed.txt"
        with mock.patch.object(sweep_changed.ChangedUnion, "_read_child", read_child):
            result = self.main("--changed-list", str(listed))
        self.assertEqual(result.code, sweep_run.EXIT_INTERRUPTED)
        self.assertEqual(self.fake.albums(), [])
        (first,) = self.entries()
        self.assertEqual(first.album, "Artist A/First")
        self.assertIs(first.outcome, sl.Outcome.COULD_NOT_RUN)
        lines = listed.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all("First" in line for line in lines))


class LibraryListingTest(AlbumErrorCase):
    """Only the library's own listing ends a sweep as a library that cannot be listed."""

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_an_unlistable_library_still_exits_two_naming_it(self) -> None:
        self.library.chmod(0)
        self.addCleanup(self.library.chmod, 0o755)
        result = self.main()
        self.assertEqual(result.code, sweep_run.EXIT_USAGE)
        self.assertIn(f"{_LIBRARY_MESSAGE}: {self.library}", result.stderr)
        self.assertEqual(self.fake.calls, [])

    def test_a_permission_error_elsewhere_is_not_the_library(self) -> None:
        denied = PermissionError(13, "Permission denied", "elsewhere")
        with (
            mock.patch.object(sweep_run, "sweep", side_effect=denied),
            self.assertRaises(PermissionError),
        ):
            self.main()

    def test_the_survey_names_a_root_it_cannot_list(self) -> None:
        denied = PermissionError(13, "Permission denied")
        with (
            mock.patch.object(sweep_survey, "album_dirs", side_effect=denied),
            self.assertRaises(sweep_survey.LibraryUnlistableError),
        ):
            sweep_survey.contained_albums(self.library)


class ChangedScratchTest(AlbumErrorCase):
    """The --changed-list scratch folder not made is said as itself, not the library."""

    def test_a_scratch_folder_not_made_exits_two_naming_it(self) -> None:
        denied = PermissionError(13, "Permission denied", "scratch")
        listed = self.tmp / "changed.txt"
        with mock.patch.object(sweep_changed.tempfile, "mkdtemp", side_effect=denied):
            result = self.main("--changed-list", str(listed))
        self.assertEqual(result.code, sweep_run.EXIT_USAGE)
        self.assertNotIn(_LIBRARY_MESSAGE, result.stderr)
        self.assertIn("--changed-list", result.stderr)
        self.assertIn("scratch folder", result.stderr)
        self.assertEqual(self.fake.calls, [])
