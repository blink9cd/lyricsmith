"""The shared names: outcomes for each skip, and the sets the summary reads."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_types as st  # noqa: E402 - import follows the sys.path bootstrap above


class TypesTest(sy_cases.IsolatedCase):
    def test_every_action_but_timing_has_an_outcome(self) -> None:
        untimed = set(st.Action) - {st.Action.TIME, st.Action.RETIME}
        self.assertEqual(set(st.UNTIMED_OUTCOMES), untimed)

    def test_a_skip_reads_the_same_planned_and_done(self) -> None:
        for action, outcome in st.UNTIMED_OUTCOMES.items():
            self.assertEqual(action.value, outcome.value)

    def test_written_and_failed_do_not_overlap(self) -> None:
        self.assertEqual(st.WRITTEN & st.FAILED, frozenset())
        self.assertIn(st.Outcome.ERROR_TEXT, st.FAILED)
        self.assertNotIn(st.Outcome.LOW_CONFIDENCE, st.FAILED)
