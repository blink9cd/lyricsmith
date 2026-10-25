"""Whatever the label writes, the planner reads as ours."""

from __future__ import annotations

import unittest

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import at_label
import at_plan
from at_types import MACHINE_BY_LINE, TIMESTAMP_LINE, Label, split_lines

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: One header line as whisper.cpp might print it: any text but a line break or a timestamp.
HEADER_LINE = st.text().filter(
    lambda line: "\n" not in line and not TIMESTAMP_LINE.match(line)
)
#: A label value as read at run time: the tool's version is any TOML string, and a
#: model's stem is any file name, so either may hold a line break or a control.
#: Plain st.text() almost never draws a line break followed by a timestamp, the
#: shape that breaks the header, so pieces of that shape are drawn on purpose.
LABEL_VALUE = st.lists(
    st.sampled_from(["0.1.0", "\n", "\r", "\r\n", "[00:00.00] ", "\x1b", " "])
    | st.text(max_size=3),
    min_size=1,
    max_size=6,
).map("".join)


class RoundTripTest(unittest.TestCase):
    @given(
        st.lists(HEADER_LINE, max_size=4),
        st.text(),
        LABEL_VALUE,
        st.from_regex(r"[0-9][0-9A-Za-z.+-]{0,8}", fullmatch=True),
        LABEL_VALUE,
    )
    def test_relabeled_files_are_ours(
        self,
        header: list[str],
        tail: str,
        tool_version: str,
        engine_version: str,
        model_name: str,
    ) -> None:
        label = Label(tool_version, engine_version, model_name)
        body = "[00:00.10] x"
        text = at_label.relabel("\n".join([*header, body, tail]), label)
        self.assertTrue(at_plan.is_ours(text))
        # Exactly two header lines, whatever the values, then the body as it was.
        lines = split_lines(text)
        self.assertEqual(lines[1], MACHINE_BY_LINE)
        self.assertEqual(lines[2], body)
