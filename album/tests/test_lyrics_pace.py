"""The shared pacer and the deadline-bounded lock guard, alone.

The clock is a `FakeClock`, so every wait takes no time and each sleep is recorded.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_pace as lpace  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import FakeClock  # noqa: E402 - import follows the sys.path bootstrap above

#: The interval every pacer here keeps.
INTERVAL_S = 5.0
#: How long past now a deadline lies when it is not the point of the test.
ROOM_S = 100.0


class _PacerCase(fixtures.IsolatedCase):
    """A fake clock, and a pacer on it."""

    def setUp(self) -> None:
        """A fresh clock and pacer for each test."""
        super().setUp()
        self.clock = FakeClock()
        self.pacer = lpace.Pacer(
            INTERVAL_S, "Invented", clock=self.clock, sleeper=self.clock.sleep
        )


class PacerWaitTest(_PacerCase):
    """`wait` sleeps out what is left of the interval, or refuses past the deadline."""

    def test_the_first_turn_does_not_wait(self) -> None:
        self.pacer.take_turn(self.clock() + ROOM_S)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.pacer.last, 1000.0)

    def test_a_later_turn_waits_for_the_remainder(self) -> None:
        self.pacer.take_turn(self.clock() + ROOM_S)
        self.clock.now += 2.0
        self.pacer.take_turn(self.clock() + ROOM_S)
        self.assertEqual(self.clock.sleeps, [3.0])
        self.assertEqual(self.pacer.last, 1000.0 + INTERVAL_S)

    def test_a_wait_already_served_sleeps_not_at_all(self) -> None:
        self.pacer.take_turn(self.clock() + ROOM_S)
        self.clock.now += INTERVAL_S + 1.0
        self.pacer.wait(self.clock() + ROOM_S)
        self.assertEqual(self.clock.sleeps, [])

    def test_a_wait_ending_exactly_at_the_deadline_is_allowed(self) -> None:
        self.pacer.take_turn(self.clock() + ROOM_S)
        self.pacer.wait(self.clock() + INTERVAL_S)
        self.assertEqual(self.clock.sleeps, [INTERVAL_S])

    def test_a_wait_past_the_deadline_is_refused_unslept_and_unmarked(self) -> None:
        self.pacer.take_turn(self.clock() + ROOM_S)
        with self.assertRaises(lp.ProviderTimeout) as raised:
            self.pacer.take_turn(self.clock() + INTERVAL_S - 0.5)
        self.assertEqual(
            str(raised.exception), "the Invented pace outlasts the deadline"
        )
        self.assertEqual((self.clock.sleeps, self.pacer.last), ([], 1000.0))

    def test_a_first_turn_past_the_deadline_is_refused(self) -> None:
        with self.assertRaises(lp.ProviderTimeout):
            self.pacer.wait(self.clock() - 1.0)
        self.assertIsNone(self.pacer.last)


class PacerMarkTest(_PacerCase):
    """`wait` never marks; `mark` reads the clock; `take_turn` is both."""

    def test_wait_alone_does_not_mark(self) -> None:
        self.pacer.wait(self.clock() + ROOM_S)
        self.assertIsNone(self.pacer.last)

    def test_mark_counts_the_interval_from_when_it_is_called(self) -> None:
        self.pacer.wait(self.clock() + ROOM_S)
        self.clock.now += 4.0  # a request that took four seconds
        self.pacer.mark()
        self.assertEqual(self.pacer.last, 1004.0)
        self.pacer.wait(self.clock() + ROOM_S)
        self.assertEqual(self.clock.sleeps, [INTERVAL_S])

    def test_take_turn_marks_after_its_wait(self) -> None:
        self.pacer.take_turn(self.clock() + ROOM_S)
        self.pacer.take_turn(self.clock() + ROOM_S)
        self.assertEqual(self.pacer.last, 1000.0 + INTERVAL_S)


class HeldUntilTest(fixtures.IsolatedCase):
    """`held_until` gets the lock by the deadline, or raises with nothing held."""

    def setUp(self) -> None:
        """A fresh clock and lock for each test."""
        super().setUp()
        self.clock = FakeClock()
        self.lock = threading.Lock()

    def test_a_free_lock_is_held_inside_and_released_after(self) -> None:
        with lpace.held_until(self.lock, self.clock, self.clock() + ROOM_S, "Invented"):
            self.assertTrue(self.lock.locked())
        self.assertFalse(self.lock.locked())

    def test_the_lock_is_released_when_the_body_raises(self) -> None:
        with self.assertRaises(ValueError):
            with lpace.held_until(self.lock, self.clock, self.clock() + 1.0, "X"):
                raise ValueError("inside")
        self.assertFalse(self.lock.locked())

    def test_a_held_lock_is_a_timeout_at_the_deadline(self) -> None:
        self.assertTrue(self.lock.acquire(timeout=fixtures.HANG_LIMIT_S))
        self.addCleanup(self.lock.release)
        with self.assertRaises(lp.ProviderTimeout) as raised:
            with lpace.held_until(self.lock, self.clock, self.clock() + 0.05, "Inv"):
                self.fail("the body ran without the lock")
        self.assertEqual(str(raised.exception), "the Inv step's deadline passed")

    def test_a_deadline_reached_asks_for_no_lock(self) -> None:
        for give_up_at in (self.clock(), self.clock() - 1.0):
            with self.subTest(give_up_at=give_up_at):
                with self.assertRaises(lp.ProviderTimeout):
                    with lpace.held_until(self.lock, self.clock, give_up_at, "X"):
                        self.fail("the body ran past the deadline")
                self.assertFalse(self.lock.locked())


class EndsBeforeTest(fixtures.IsolatedCase):
    """`ends_before`: a deadline-bounded wait is begun only if it ends before the deadline."""

    def test_a_wait_ending_before_the_deadline_may_begin(self) -> None:
        self.assertTrue(lpace.ends_before(1000.0, INTERVAL_S, 1000.0 + ROOM_S))

    def test_a_wait_ending_at_or_past_the_deadline_may_not(self) -> None:
        for give_up_at in (1000.0 + INTERVAL_S, 1000.0 + INTERVAL_S - 0.5):
            with self.subTest(give_up_at=give_up_at):
                self.assertFalse(lpace.ends_before(1000.0, INTERVAL_S, give_up_at))


class SleepWithinTest(fixtures.IsolatedCase):
    """`sleep_within` sleeps a wait only when it ends by the deadline."""

    def setUp(self) -> None:
        """A fresh fake clock."""
        super().setUp()
        self.clock = FakeClock()

    def _sleep(self, wait_s: float, left_s: float) -> bool:
        """Whether *wait_s* is slept with *left_s* before the deadline."""
        give_up_at = self.clock() + left_s
        return lpace.sleep_within(self.clock, self.clock.sleep, wait_s, give_up_at)

    def test_a_wait_ending_before_the_deadline_is_slept(self) -> None:
        self.assertTrue(self._sleep(INTERVAL_S, ROOM_S))
        self.assertEqual(self.clock.sleeps, [INTERVAL_S])

    def test_a_wait_ending_at_or_past_the_deadline_is_not_begun(self) -> None:
        for left_s in (INTERVAL_S, INTERVAL_S - 0.5):
            with self.subTest(left_s=left_s):
                self.assertFalse(self._sleep(INTERVAL_S, left_s))
                self.assertEqual(self.clock.sleeps, [])
