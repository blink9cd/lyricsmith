"""The machine-made label."""

from __future__ import annotations

import unittest
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import at_label
import at_plan
from at_types import (
    MACHINE_BY_LINE,
    TOOL_LABEL,
    Label,
    split_lines,
)

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

LABEL = Label("0.1.0", "1.9.5", "ggml-large-v3-turbo")


class LabelTest(unittest.TestCase):
    def test_the_shipped_sample_opens_with_the_real_label(self) -> None:
        """samples/jfk.lrc is compared by hand against real output, so its label must match."""
        sample = Path(__file__).resolve().parent.parent / "samples" / "jfk.lrc"
        first = sample.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first, at_label.re_line(LABEL))

    def test_ownership_is_the_by_line_not_the_label(self) -> None:
        """An .lrc carrying the by-line is ours whatever its [re:] line names."""
        tail = "; whisper.cpp 1.7.6; ggml-large-v3-turbo]\n[by:machine transcript]\n[00:00.00]x\n"
        self.assertTrue(at_plan.is_ours("[re:an invented program 0.1.0" + tail))
        self.assertTrue(at_plan.is_ours("[re:lyricsmith transcribe 0.1.0" + tail))
        self.assertFalse(at_plan.is_ours("[re:someone else]\n[00:00.00]x\n"))

    def test_the_label_names_the_command(self) -> None:
        self.assertEqual(TOOL_LABEL, "lyricsmith transcribe")

    def test_relabel_swaps_the_engine_header_for_ours(self) -> None:
        engine = "[by:whisper.cpp]\n\n[00:00.26] An invented example line.\n"
        self.assertEqual(
            at_label.relabel(engine, LABEL),
            "[re:lyricsmith transcribe 0.1.0; whisper.cpp 1.9.5; ggml-large-v3-turbo]\n"
            "[by:machine transcript]\n"
            "[00:00.26] An invented example line.\n",
        )

    def test_the_body_is_kept_verbatim_with_lf_endings(self) -> None:
        engine = "[by:whisper.cpp]\r\n[00:00.26] One.\r\nuntimed\r\n[00:01.00] Two.\r\n"
        self.assertEqual(
            split_lines(at_label.relabel(engine, LABEL))[2:],
            ["[00:00.26] One.", "untimed", "[00:01.00] Two."],
        )

    def test_no_timed_line_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            at_label.relabel("[by:whisper.cpp]\nuntimed\n", LABEL)

    def test_model_name_is_the_file_stem(self) -> None:
        self.assertEqual(
            at_label.model_name(Path("m") / "ggml-large-v3-turbo.bin"),
            "ggml-large-v3-turbo",
        )

    @given(st.text())
    def test_fuzz_relabel(self, tail: str) -> None:
        lines = split_lines(
            at_label.relabel(f"[by:whisper.cpp]\n[00:01.00] x\n{tail}", LABEL)
        )
        self.assertEqual(lines[0], at_label.re_line(LABEL))
        self.assertEqual(lines[1], MACHINE_BY_LINE)
        self.assertEqual(lines[2], "[00:01.00] x")
