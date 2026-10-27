"""`changed_lyrics`: which recorded outcomes changed what a track's lyrics are.

Each outcome is built as a dataclass; every file name is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes as lo  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above

S = lr.OutcomeStatus


class StatusTest(fixtures.IsolatedCase):
    """A status alone says whether the run changed the lyrics, except an error's."""

    def test_the_writing_statuses_changed_the_lyrics(self) -> None:
        for status in (S.WRITTEN, S.REMOVED, S.STRIPPED):
            with self.subTest(status=status):
                self.assertTrue(lo.changed_lyrics(lr.TrackOutcome(status)))

    def test_the_statuses_that_leave_the_lyrics_did_not(self) -> None:
        left = (S.UNCHANGED, S.NOT_FOUND, S.NO_LYRICS_EXPECTED, S.SILENT)
        for status in (*left, S.KEPT_EXISTING):
            with self.subTest(status=status):
                self.assertFalse(lo.changed_lyrics(lr.TrackOutcome(status)))


class ErrorTest(fixtures.IsolatedCase):
    """An error changed the lyrics only when it recorded a write first."""

    def test_an_error_that_recorded_nothing_did_not(self) -> None:
        self.assertFalse(lo.changed_lyrics(lr.TrackOutcome(S.ERROR)))

    def test_an_error_after_any_recorded_write_did(self) -> None:
        rename = lm.SidecarRename("01.TXT", "01.txt")
        writes = (
            lr.TrackOutcome(S.ERROR, frames_written=True),
            lr.TrackOutcome(S.ERROR, sidecar_written="01.lrc"),
            lr.TrackOutcome(S.ERROR, sidecars_removed=("01.txt",)),
            lr.TrackOutcome(S.ERROR, sidecars_renamed=(rename,)),
        )
        for outcome in writes:
            with self.subTest(outcome=outcome):
                self.assertTrue(lo.changed_lyrics(outcome))


if __name__ == "__main__":
    unittest.main()
