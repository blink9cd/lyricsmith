"""The reads sync shares with transcribe and album refuse a FIFO pipe at once.

transcribe keeps its own short open for a ``.lrc`` rather than taking album's library,
so its flags are pinned here against album's: a change to one that the other missed
fails this suite. sync is where both tools are on the path. Each read of a pipe runs
under album's `fifo_fakes.within_deadline`, so a reader that blocks fails its test
rather than hanging the suite.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import at_plan  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import sy_engine  # noqa: E402 - import follows the sys.path bootstrap above
from fifo_fakes import make_pipe, within_deadline  # noqa: E402 - album's test helpers

#: Windows' O_BINARY value, stood in where the host has none.
_FAKE_BINARY = 0x8000


class ReadFlagsTest(sy_cases.IsolatedCase):
    """transcribe opens a ``.lrc`` with the very flags album reads a file with."""

    def test_transcribe_reads_with_albums_flags(self) -> None:
        self.assertEqual(at_plan.read_flags(), lyrics_files.read_flags())
        # Windows' O_BINARY, stood in on any host: both must pick it up.
        with mock.patch.object(os, "O_BINARY", _FAKE_BINARY, create=True):
            self.assertEqual(at_plan.read_flags(), lyrics_files.read_flags())
            self.assertTrue(lyrics_files.read_flags() & _FAKE_BINARY)


class FifoReadTest(sy_cases.IsolatedCase):
    """A FIFO swapped in after the name was looked at is refused, never waited on."""

    def setUp(self) -> None:
        """A FIFO pipe in the test's folder."""
        super().setUp()
        self.pipe = self.tmp / "pipe"
        make_pipe(self.pipe)

    def test_a_fifo_lrc_that_passed_the_file_check_is_present_not_ours(self) -> None:
        with mock.patch.object(Path, "is_file", return_value=True):
            text = within_deadline(self, self.pipe, lambda: at_plan.read_lrc(self.pipe))
        self.assertEqual(text, "")

    def test_a_fifo_track_has_no_length(self) -> None:
        seconds = within_deadline(
            self, self.pipe, lambda: sy_engine.track_seconds(self.pipe)
        )
        self.assertIsNone(seconds)
