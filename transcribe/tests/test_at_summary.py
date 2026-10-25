"""Rows, the count line and the exit code."""

from __future__ import annotations

import unittest
from pathlib import Path

import at_summary
from at_types import EXIT_FILE_FAILED, EXIT_OK, Action, FileResult, Outcome, Planned

ROOT = Path("lib")


class SummaryTest(unittest.TestCase):
    def test_plan_line(self) -> None:
        planned = Planned(
            ROOT / "a" / "one.mp3", Path("x.lrc"), Path("x.txt"), Action.SKIPPED
        )
        self.assertEqual(
            at_summary.plan_line(planned, ROOT),
            f"skipped (has sidecar)  {Path('a') / 'one.mp3'}",
        )

    def test_result_lines_indent_the_detail(self) -> None:
        result = FileResult(
            ROOT / "one.mp3", Outcome.FAILED, ("whisper-cli exited 3", "error: x")
        )
        self.assertEqual(
            at_summary.result_lines(result, ROOT),
            [
                "failed                 one.mp3",
                "    whisper-cli exited 3",
                "    error: x",
            ],
        )

    def test_a_path_outside_the_root_is_shown_whole(self) -> None:
        result = FileResult(Path("elsewhere") / "one.mp3", Outcome.TRANSCRIBED)
        self.assertEqual(
            at_summary.result_lines(result, ROOT),
            [f"transcribed            {Path('elsewhere') / 'one.mp3'}"],
        )

    def test_count_line_lists_every_outcome_in_order(self) -> None:
        results = [
            FileResult(ROOT / "a.mp3", Outcome.TRANSCRIBED),
            FileResult(ROOT / "b.mp3", Outcome.SKIPPED),
            FileResult(ROOT / "c.mp3", Outcome.FAILED),
            FileResult(ROOT / "d.mp3", Outcome.LINKED),
        ]
        self.assertEqual(
            at_summary.count_line(results),
            "transcribed 1, overwritten 0, skipped 1, refused 0, failed 1, linked 1",
        )

    def test_exit_code(self) -> None:
        ok = FileResult(ROOT / "a.mp3", Outcome.REFUSED)
        bad = FileResult(ROOT / "b.mp3", Outcome.FAILED)
        self.assertEqual(at_summary.exit_code([]), EXIT_OK)
        self.assertEqual(at_summary.exit_code([ok]), EXIT_OK)
        self.assertEqual(at_summary.exit_code([ok, bad]), EXIT_FILE_FAILED)

    def test_the_label_column_fits_the_longest_label_and_its_gap(self) -> None:
        labels = [label.value for label in (*Action, *Outcome)]
        self.assertEqual(
            at_summary.LABEL_WIDTH, max(map(len, labels)) + at_summary.LABEL_GAP
        )
        self.assertEqual(
            at_summary.label_width(["ab", "abcd"]), 4 + at_summary.LABEL_GAP
        )
