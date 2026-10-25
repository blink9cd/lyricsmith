"""The album tool's exit codes, as the sweep reads them: the album tool's own constants.

The sweep tells a child that could not run, one whose record was not written, one
interrupted and one that finished apart by the album tool's exit code. Each code is
read from the module that defines it, never restated, so these cases rebind each one
to a value the album tool has never used and expect the sweep to follow it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_exit  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_outcome import outcome_of  # noqa: E402 - follows the sys.path bootstrap above

#: A code the album tool has never used, put in place of one of its own.
_UNUSED = 77
#: A tally of an album whose providers answered.
_ANSWERED = sweep_tally.tally_manifest(sf.manifest_of(*sf.ANSWERED))


def _outcome(code: int) -> sl.Outcome:
    """How a child that exited *code*, its run read, is recorded."""
    return outcome_of(sweep_child.ChildResult(code), _ANSWERED, offline=False)


class OwnCodesTest(sf.SweepCase):
    """Each of the album tool's codes is read from where the album tool defines it."""

    def test_the_entry_point_re_exports_the_codes_the_sweep_reads(self) -> None:
        for name in (
            "EXIT_OK",
            "EXIT_TRACK_ERRORS",
            "EXIT_CANNOT_RUN",
            "EXIT_RECORD_NOT_WRITTEN",
            "EXIT_INTERRUPTED",
        ):
            with self.subTest(name=name):
                self.assertEqual(getattr(album, name), getattr(lyrics_exit, name))

    def test_could_not_run_follows_the_album_tools_code(self) -> None:
        with mock.patch.object(lyrics_exit, "EXIT_CANNOT_RUN", _UNUSED):
            self.assertIs(_outcome(_UNUSED), sl.Outcome.COULD_NOT_RUN)

    def test_record_not_written_follows_the_album_tools_code(self) -> None:
        with mock.patch.object(lyrics_exit, "EXIT_RECORD_NOT_WRITTEN", _UNUSED):
            self.assertIs(_outcome(_UNUSED), sl.Outcome.RECORD_NOT_WRITTEN)

    def test_interrupted_follows_the_album_tools_code(self) -> None:
        with mock.patch.object(lyrics_exit, "EXIT_INTERRUPTED", _UNUSED):
            self.assertIs(_outcome(_UNUSED), sl.Outcome.INTERRUPTED)

    def test_finished_follows_the_album_tools_codes(self) -> None:
        for name in ("EXIT_OK", "EXIT_TRACK_ERRORS"):
            with (
                self.subTest(name=name),
                mock.patch.object(lyrics_exit, name, _UNUSED),
            ):
                self.assertIs(_outcome(_UNUSED), sl.Outcome.DONE)

    def test_a_code_the_album_tool_never_gives_is_a_failure(self) -> None:
        self.assertIs(_outcome(_UNUSED), sl.Outcome.FAILED)

    def test_a_child_that_could_not_run_has_no_run_read(self) -> None:
        # A run that appears beside a child that could not run is another's.
        self.fake.scripts["A1"] = sf.Script(exit_code=_UNUSED)
        with mock.patch.object(lyrics_exit, "EXIT_CANNOT_RUN", _UNUSED):
            entry = self._sweep(limit=1).visited[0].entry
        self.assertIs(entry.outcome, sl.Outcome.COULD_NOT_RUN)
        self.assertIsNone(entry.run_id)
        self.assertIsNone(entry.counts)
