"""Rules compare shares with the chain and a run, each defined once.

Compare asks the chain's untimed steps, and an album whose every provider failed is the
same fetch error in a compare row as in a run's outcome; these tests hold each to the
one definition it is derived from.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_compare as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes as lo  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above


class ComparedStepsTest(fixtures.IsolatedCase):
    """Compare asks every untimed step of the chain, in the chain's order."""

    def test_the_compared_providers_are_the_chains_untimed_steps(self) -> None:
        untimed = tuple(
            key.source for key in lf.CHAIN if key.kind is lm.LyricsKind.UNSYNCED
        )
        self.assertEqual(lc.COMPARED, untimed)


class EveryProviderFailedTest(fixtures.IsolatedCase):
    """The fetch error for an album of failures, built in one place."""

    def test_the_error_is_a_fetch_stage_error_counting_each_failure(self) -> None:
        outcomes = (lm.AttemptOutcome.TIMEOUT, lm.AttemptOutcome.ERROR)
        error = lo.every_provider_failed(outcomes)
        message = lo.describe_failures(outcomes)
        self.assertEqual(
            error, lr.StageError(lr.ErrorStage.FETCH, "FetchError", message)
        )


if __name__ == "__main__":
    unittest.main()
