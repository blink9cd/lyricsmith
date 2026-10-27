"""Finishing a step through Ctrl-C: the action runs to its end, and the caller learns of it."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import sweep_ctrl_c  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above


class FinishedDespiteCtrlCTest(sf.IsolatedCase):
    """An action cut short by Ctrl-C is started again until it ends."""

    def test_an_action_left_alone_says_no_ctrl_c(self) -> None:
        self.assertEqual(sweep_ctrl_c.finished_despite_ctrl_c(lambda: 7), (7, False))

    def test_each_ctrl_c_starts_it_again_and_is_reported(self) -> None:
        tries: list[int] = []

        def action() -> int:
            tries.append(1)
            if len(tries) < 3:
                raise KeyboardInterrupt
            return len(tries)

        self.assertEqual(sweep_ctrl_c.finished_despite_ctrl_c(action), (3, True))
