"""The Genius step's challenge retries never wait past the call's own deadline.

A retry wait that would end past it is not begun, as a pace wait is not: the step's
error is then the last challenge, raised as it came when no retry was made, else as
`lyrics_genius.GeniusChallengeError` counting the retries made. The search's fake client
moves the fake clock on as it answers, so a call spends its deadline without sleeping.
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    QUERY,
    FakeClock,
    FakeGeniusClient,
    FakeSong,
    challenge,
    paced_genius,
)

#: The time, short of the next retry's wait, left once a slow answer has come.
_SHORT_S = 10.0


def _ticker(clock: FakeClock, spends: list[float]) -> Callable[[], float]:
    """A call-time reader moving *clock* on by each of *spends* in turn, then reading it."""
    pending = list(spends)

    def tick() -> float:
        """Spend the next answer's time, then read the clock."""
        clock.now += pending.pop(0) if pending else 0.0
        return clock.now

    return tick


class RetryWaitDeadlineTest(fixtures.IsolatedCase):
    """A retry wait that would end past the deadline is not begun."""

    def setUp(self) -> None:
        """Two challenges, then a song; a step on the fake clock and sleeper."""
        super().setUp()
        self.clock = FakeClock()
        script: list[FakeSong | Exception | None] = [challenge(429), challenge(429)]
        self.client = FakeGeniusClient([*script, FakeSong("words")])
        self.genius = paced_genius(self.client, self.clock)

    def test_a_first_retry_past_the_deadline_raises_the_challenge_unretried(
        self,
    ) -> None:
        bound = self.genius.step_deadline_s()
        self.client.clock = _ticker(self.clock, [bound - _SHORT_S])
        with self.assertRaises(lp.HttpStatusError) as raised:
            self.genius(QUERY)
        self.assertEqual(lp.error_detail(raised.exception), "HTTP 429")
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(len(self.client.calls), 1)

    def test_a_retry_wait_ending_at_the_deadline_is_not_begun(self) -> None:
        # The next attempt could not start once the wait ends, so the wait is useless.
        first_wait = lg.GENIUS_RETRY_WAITS_S[0]
        bound = self.genius.step_deadline_s()
        self.client.clock = _ticker(self.clock, [bound - first_wait])
        with self.assertRaises(lp.HttpStatusError):
            self.genius(QUERY)
        self.assertEqual(self.clock.sleeps, [])

    def test_a_later_retry_past_the_deadline_counts_the_retries_made(self) -> None:
        first_wait = lg.GENIUS_RETRY_WAITS_S[0]
        late = self.genius.step_deadline_s() - first_wait - _SHORT_S
        self.client.clock = _ticker(self.clock, [0.0, late])
        with self.assertRaises(lg.GeniusChallengeError) as raised:
            self.genius(QUERY)
        self.assertEqual(raised.exception.detail, "HTTP 429 after 1 retry")
        self.assertEqual(self.clock.sleeps, [first_wait])
        self.assertEqual(len(self.client.calls), 2)

    def test_every_retry_made_still_counts_them_all(self) -> None:
        error = lg.GeniusChallengeError(403, len(lg.GENIUS_RETRY_WAITS_S))
        self.assertEqual(error.detail, "HTTP 403 after 3 retries")
        self.assertEqual(error.status, 403)


if __name__ == "__main__":
    unittest.main()
