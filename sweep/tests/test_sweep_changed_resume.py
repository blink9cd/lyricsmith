"""--changed-list across the legs of one pass: a resume keeps the list, a restart empties it.

A resumed sweep is the same pass as the leg it resumes, so its list must still name what
that leg changed, or those tracks are never refreshed. The album
tool is played by `sweep_fixtures.FakeAlbumTool`, so nothing here runs a process or
reaches a provider.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import lyrics_changed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run  # noqa: E402 - import follows the sys.path bootstrap above

#: A path an earlier leg listed, outside the library, so no album lists it again.
_EARLIER = "/earlier/leg/01 Song.mp3"


class LegsCase(sf.IsolatedCase):
    """Two needy albums, a fake album tool, and the sweep's list file."""

    def setUp(self) -> None:
        """Build albums A1 and A2, each changing its first track."""
        super().setUp()
        self.a1 = sf.needy_album(self.library, "Artist", "A1")
        self.a2 = sf.needy_album(self.library, "Artist", "A2")
        self.fake = sf.FakeAlbumTool()
        self.fake.scripts["A1"] = sf.Script(changed=("01 Timed.mp3",))
        self.fake.scripts["A2"] = sf.Script(changed=("01 Timed.mp3",))
        self.listed = self.tmp / "changed.txt"

    def sweep(self, *args: str) -> tuple[int, str, str]:
        """A sweep writing its list to this test's file: exit code, stdout, stderr."""
        argv = ["--mode", "run", "--library", str(self.library)]
        argv += ["--changed-list", str(self.listed), *args]
        wiring = sweep.Wiring(self.fake, sf.FakeSleep(), sf.fixed_now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sweep.main(argv, wiring=wiring)
        return code, stdout.getvalue(), stderr.getvalue()

    def changed(self) -> list[str]:
        """The sweep's list, in order."""
        read = lyrics_changed.parse_changed_list(self.listed.read_bytes())
        assert read is not None
        return list(read)


class ResumeKeepsTheListTest(LegsCase):
    """A sweep merges into a list that exists; only --restart starts it empty."""

    def test_a_resume_keeps_what_the_stopped_leg_listed(self) -> None:
        code, _, _ = self.sweep("--limit", "1")
        self.assertEqual(code, sweep_run.EXIT_OK)
        self.assertEqual(self.changed(), [str(self.a1 / "01 Timed.mp3")])
        self.sweep()
        self.assertEqual(self.fake.albums(), ["A1", "A2"])
        self.assertEqual(
            self.changed(),
            [str(self.a1 / "01 Timed.mp3"), str(self.a2 / "01 Timed.mp3")],
        )

    def test_an_existing_list_comes_first_and_each_path_stays_once(self) -> None:
        listed_again = str(self.a2 / "01 Timed.mp3")
        self.listed.write_bytes(f"{_EARLIER}\n{listed_again}\n".encode())
        self.sweep()
        self.assertEqual(
            self.changed(), [_EARLIER, listed_again, str(self.a1 / "01 Timed.mp3")]
        )

    def test_a_restart_starts_the_list_empty(self) -> None:
        self.listed.write_bytes(f"{_EARLIER}\n".encode())
        self.sweep("--restart")
        self.assertEqual(
            self.changed(),
            [str(self.a1 / "01 Timed.mp3"), str(self.a2 / "01 Timed.mp3")],
        )

    def test_a_list_the_contract_refuses_stops_before_the_first_album(self) -> None:
        held = f"{_EARLIER}\n/half/written".encode()
        self.listed.write_bytes(held)
        code, stdout, stderr = self.sweep()
        self.assertEqual(code, sweep_run.EXIT_USAGE)
        self.assertEqual(self.fake.albums(), [])
        self.assertEqual(self.listed.read_bytes(), held)
        self.assertEqual(stdout, "")
        self.assertIn(f"--changed-list {self.listed} is not a readable list", stderr)

    def test_a_list_that_cannot_be_read_stops_before_the_first_album(self) -> None:
        self.listed.mkdir()
        code, _, stderr = self.sweep()
        self.assertEqual(code, sweep_run.EXIT_USAGE)
        self.assertEqual(self.fake.albums(), [])
        self.assertIn(f"could not read --changed-list {self.listed}", stderr)

    def test_a_restart_never_reads_the_list_it_replaces(self) -> None:
        self.listed.write_bytes(b"/half/written")
        code, _, _ = self.sweep("--restart")
        self.assertEqual(code, sweep_run.EXIT_OK)
        self.assertEqual(len(self.changed()), 2)


class ResumeLineTest(LegsCase):
    """The printed resume command carries the list, so the next leg keeps adding to it."""

    def test_the_resume_line_carries_the_changed_list(self) -> None:
        _, stdout, _ = self.sweep("--limit", "1")
        resume = stdout.split("resume: ")[1].splitlines()[0]
        self.assertTrue(resume.endswith(f"--changed-list {self.listed}"))

    def test_no_list_named_puts_none_in_the_resume_line(self) -> None:
        options = sweep_run.SweepOptions(
            lang=None, offline=False, pause_s=1.0, stop_after=3, limit=1, restart=False
        )
        command = sweep_report.resume_command(("sweep.py",), self.library, options)
        self.assertNotIn("--changed-list", command)
