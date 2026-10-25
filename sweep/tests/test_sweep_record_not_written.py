"""A child that made its run but could not write its record: the album tool's exit 3.

The album tool exits 3 when the lyrics work finished, or stopped part-way, but the run's
manifest or its ``--changed-list`` could not be written. The run was made, so the sweep
reads what manifest there is, tallies it and prints its undo command; the album is
recorded as ``record not written``, which is not done, so a resumed sweep retries it.
The album tool is played by `sweep_fixtures.FakeAlbumTool`; every name is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import album  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run as sr  # noqa: E402 - import follows the sys.path bootstrap above

#: The album tool's exit code for a run made whose record or list could not be written.
_RECORD_NOT_WRITTEN = album.EXIT_RECORD_NOT_WRITTEN
#: A made run whose record could not be written, its tracks answered.
_UNRECORDED = sf.Script(exit_code=_RECORD_NOT_WRITTEN)


class RecordNotWrittenTest(sf.SweepCase):
    """An album whose child exited 3 is a made run, tallied, with its undo printed."""

    def _only(self, script: sf.Script, *, offline: bool = False) -> sr.SweepReport:
        self.fake.scripts["A1"] = script
        return self._sweep(limit=1, offline=offline)

    def test_the_run_is_read_and_tallied(self) -> None:
        entry = self._only(_UNRECORDED).visited[0].entry
        self.assertIs(entry.outcome, sl.Outcome.RECORD_NOT_WRITTEN)
        self.assertEqual(entry.exit_code, _RECORD_NOT_WRITTEN)
        self.assertIsNotNone(entry.run_id)
        self.assertIsNotNone(entry.counts)
        self.assertEqual(self._ledger(), [entry])

    def test_its_undo_command_is_printed(self) -> None:
        report = self._only(_UNRECORDED)
        entry = report.visited[0].entry
        assert entry.run_id is not None
        text = sweep_report.format_sweep(
            report, library=self.library, options=sf.OPTIONS, undo_prog=("album.py",)
        )
        self.assertIn(f"--mode undo --run-id {entry.run_id}", text)
        self.assertIn(sl.Outcome.RECORD_NOT_WRITTEN.value, text)

    def test_with_no_manifest_to_read_it_is_still_record_not_written(self) -> None:
        # The child said its record failed: more specific than "no record".
        report = self._only(sf.Script(exit_code=_RECORD_NOT_WRITTEN, manifest=False))
        entry = report.visited[0].entry
        self.assertIs(entry.outcome, sl.Outcome.RECORD_NOT_WRITTEN)
        self.assertIsNone(entry.run_id)
        self.assertIsNone(entry.counts)

    def test_offline_it_is_record_not_written_not_offline(self) -> None:
        entry = self._only(_UNRECORDED, offline=True).visited[0].entry
        self.assertIs(entry.outcome, sl.Outcome.RECORD_NOT_WRITTEN)

    def test_a_resumed_sweep_retries_it(self) -> None:
        self._only(_UNRECORDED)
        self.fake.scripts.pop("A1")
        report = self._sweep(limit=1)
        self.assertEqual(report.skipped_done, 0)
        self.assertEqual(report.visited[0].entry.album, "Artist/A1")
        self.assertIs(report.visited[0].entry.outcome, sl.Outcome.DONE)

    def test_the_sweep_exits_1(self) -> None:
        self.assertEqual(
            sr.sweep_exit_code(self._only(_UNRECORDED)), sr.EXIT_ALBUM_ERRORS
        )

    def test_refusals_behind_it_neither_count_nor_reset_the_stop_rule(self) -> None:
        refused = sf.Script(outcomes=sf.REFUSED, exit_code=album.EXIT_TRACK_ERRORS)
        self.fake.scripts.update(
            {
                "A1": refused,
                "A2": sf.Script(outcomes=sf.REFUSED, exit_code=_RECORD_NOT_WRITTEN),
                "A3": refused,
            }
        )
        report = self._sweep()
        self.assertIs(report.stop, sr.StopReason.REFUSALS)
        self.assertEqual(len(report.visited), 3)
        self.assertIs(report.visited[1].entry.outcome, sl.Outcome.RECORD_NOT_WRITTEN)

    def test_a_child_that_could_not_run_is_unchanged(self) -> None:
        entry = self._only(sf.Script(exit_code=album.EXIT_CANNOT_RUN)).visited[0].entry
        self.assertIs(entry.outcome, sl.Outcome.COULD_NOT_RUN)
        self.assertIsNone(entry.run_id)
