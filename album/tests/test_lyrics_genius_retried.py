"""The Genius step's own challenge retries are counted, as `lyrics_http`'s are.

Each retry the step makes after a challenge tells its hook the provider and the
challenge's class; a retry the deadline left no room for is not made, so it is not
counted. The fake client moves the fake clock on as it answers, so no test sleeps.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_retried as lrt  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    DEADLINE_S,
    QUERY,
    FakeClock,
    FakeGeniusClient,
    FakeSong,
    challenge,
    ticker,
)


class CountedTest(fixtures.IsolatedCase):
    """A retry made counts once; one not made counts nothing."""

    def setUp(self) -> None:
        """A challenge then a song, on a step counting into a fresh tally."""
        super().setUp()
        self.clock = FakeClock()
        self.tally = lrt.RetryTally()
        self.client = FakeGeniusClient([challenge(429), FakeSong("words")], self.clock)
        self.genius = lg.GeniusProvider(
            self.client,
            clock=self.clock,
            sleeper=self.clock.sleep,
            deadline_s=DEADLINE_S,
            on_retry=self.tally.count,
        )

    def test_a_challenge_then_an_answer_counts_one_retry(self) -> None:
        self.assertEqual(self.genius(QUERY), "words")
        expected = {lm.Source.GENIUS: {lrf.Refusal.RATE_LIMITED: 1}}
        self.assertEqual(self.tally.counts(), expected)

    def test_a_challenge_the_deadline_cuts_off_counts_nothing(self) -> None:
        bound = self.genius.step_deadline_s()
        self.client.clock = ticker(self.clock, [bound - 1.0])
        with self.assertRaises(lp.HttpStatusError):
            self.genius(QUERY)
        self.assertEqual(self.tally.counts(), {})

    def test_a_retry_whose_turn_times_out_is_not_counted(self) -> None:
        # The retry's wait is slept, but its turn finds the deadline reached, so no
        # request goes out: nothing was retried, and the step's error is the challenge
        # the retry was for, not the timeout of a turn never taken.
        give_up_at = self.clock() + self.genius.step_deadline_s()

        def sleep_to_the_deadline(seconds: float) -> None:
            self.clock.sleeps.append(seconds)
            self.clock.now = give_up_at

        genius = lg.GeniusProvider(
            self.client,
            clock=self.clock,
            sleeper=sleep_to_the_deadline,
            deadline_s=DEADLINE_S,
            on_retry=self.tally.count,
        )
        with self.assertRaises(lp.HttpStatusError) as raised:
            genius(QUERY)
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(len(self.clock.sleeps), 1)
        self.assertEqual(self.tally.counts(), {})

    def test_each_retry_made_counts(self) -> None:
        self.client.script[:0] = [challenge(429), challenge(429)]
        self.assertEqual(self.genius(QUERY), "words")
        expected = {lm.Source.GENIUS: {lrf.Refusal.RATE_LIMITED: 3}}
        self.assertEqual(self.tally.counts(), expected)

    def test_a_403_challenge_counts_as_blocked(self) -> None:
        self.client.script[0] = challenge(403)
        self.assertEqual(self.genius(QUERY), "words")
        expected = {lm.Source.GENIUS: {lrf.Refusal.BLOCKED: 1}}
        self.assertEqual(self.tally.counts(), expected)

    def test_a_later_retry_s_timed_out_turn_ends_on_the_challenges_so_far(self) -> None:
        # Two challenges answered, then the second retry's turn finds the deadline
        # reached: the step's error counts the one retry made, as a wait not begun does.
        self.client.script[1:1] = [challenge(403)]
        give_up_at = self.clock() + self.genius.step_deadline_s()

        def sleep_then_reach_the_deadline(seconds: float) -> None:
            self.clock.sleep(seconds)
            if len(self.clock.sleeps) > 1:
                self.clock.now = give_up_at

        self.genius.sleeper = sleep_then_reach_the_deadline
        with self.assertRaises(lg.GeniusChallengeError) as raised:
            self.genius(QUERY)
        self.assertEqual((raised.exception.status, raised.exception.retries), (403, 1))
        self.assertEqual(lrf.classify(raised.exception).reason, lrf.Refusal.BLOCKED)

    def test_with_no_hook_the_retry_still_runs(self) -> None:
        client = FakeGeniusClient([challenge(403), FakeSong("words")], self.clock)
        genius = lg.GeniusProvider(
            client, clock=self.clock, sleeper=self.clock.sleep, deadline_s=DEADLINE_S
        )
        self.assertEqual(genius(QUERY), "words")
        self.assertEqual(len(client.calls), 2)


if __name__ == "__main__":
    unittest.main()
