"""Ctrl-C just before an album's album tool starts stops the sweep, its report kept.

Reading an album's holds, checking it for a link and setting up its album tool all
happen before the album tool runs, so Ctrl-C there stops the sweep as Ctrl-C during the
pause does: the albums visited so far are reported, and nothing more is run. The album
tool is played by `sweep_fixtures.FakeAlbumTool`, so nothing here runs a process or
reaches a provider.
"""

from __future__ import annotations

import contextlib
import io
import sys
from collections.abc import Callable
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run as sr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_waiting as sw  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_survey import AlbumSurvey  # noqa: E402 - follows the sys.path bootstrap


def _interrupted_holds() -> sw.HoldsRead:
    """A holds read that Ctrl-C cuts short."""
    raise KeyboardInterrupt


class BetweenAlbumsTest(sf.SweepCase):
    """Ctrl-C just before an album's visit stops the sweep, its report kept."""

    def sweep_with(self, holds: Callable[[], sw.HoldsRead]) -> sr.SweepReport:
        """A sweep of A1 to A5, its providers' holds read by *holds*."""
        seams = sr.SweepSeams(
            run_child=self.fake,
            sleep=self.sleep,
            now=sf.fixed_now,
            progress=self.notes.append,
            entry=self.tmp / "album.py",
            holds=holds,
        )
        surveys = [AlbumSurvey(folder, none=1) for folder in self.albums]
        return sr.sweep(self.library, surveys, self.ledger, sf.OPTIONS, seams)

    def test_ctrl_c_while_the_holds_are_read_stops_before_the_album(self) -> None:
        owed = sl.LedgerEntry(
            "Artist/A2",
            None,
            0,
            sl.Outcome.OWED,
            "t",
            owed_providers=(lm.Source.GENIUS,),
        )
        sl.append_record(self.ledger, owed)
        report = self.sweep_with(_interrupted_holds)
        self.assertEqual(self.fake.albums(), ["A1"])
        self.assertIs(report.stop, sr.StopReason.INTERRUPTED)
        self.assertEqual(report.remaining, 4)

    def test_ctrl_c_while_an_album_is_checked_for_a_link_stops_before_it(
        self,
    ) -> None:
        real = sr.linked_now
        checks: list[Path] = []

        def interrupted_second(
            library: Path, album: Path, progress: Callable[[str], None]
        ) -> bool:
            checks.append(album)
            if len(checks) == 2:
                raise KeyboardInterrupt
            return real(library, album, progress)

        with mock.patch.object(sr, "linked_now", interrupted_second):
            report = self.sweep_with(sw.no_holds)
        self.assertEqual(self.fake.albums(), ["A1"])
        self.assertIs(report.stop, sr.StopReason.INTERRUPTED)
        self.assertEqual(report.remaining, 4)


def _run_ids_interrupted_on(album: str) -> Callable[[Path], frozenset[str]]:
    """`sweep_child.run_ids`, cut short by Ctrl-C for the album named *album*."""
    real = sweep_child.run_ids

    def run_ids(folder: Path) -> frozenset[str]:
        if folder.name == album:
            raise KeyboardInterrupt
        return real(folder)

    return run_ids


class BeforeTheAlbumToolTest(sf.SweepCase):
    """Ctrl-C while an album's album tool is set up stops before it, with no entry."""

    def main(self) -> tuple[int, str]:
        """Run a sweep through `sweep.main`; its exit code and stdout."""
        argv = ["--mode", "run", "--library", str(self.library)]
        wiring = sweep.Wiring(self.fake, self.sleep, sf.fixed_now)
        stdout = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = sweep.main(argv, wiring=wiring)
        return code, stdout.getvalue()

    def test_ctrl_c_while_the_album_is_set_up_stops_before_it(self) -> None:
        with mock.patch.object(sweep_child, "run_ids", _run_ids_interrupted_on("A2")):
            report = self._sweep()
        self.assertEqual(self.fake.albums(), ["A1"])
        self.assertIs(report.stop, sr.StopReason.INTERRUPTED)
        self.assertEqual(report.remaining, 4)
        self.assertEqual([e.album for e in self._ledger()], ["Artist/A1"])

    def test_the_report_prints_and_the_exit_is_130(self) -> None:
        with mock.patch.object(sweep_child, "run_ids", _run_ids_interrupted_on("A2")):
            code, stdout = self.main()
        self.assertEqual(code, sr.EXIT_INTERRUPTED)
        self.assertIn("Artist/A1", stdout)
        self.assertIn("resume: ", stdout)
