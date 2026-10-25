"""The Genius step's wait for its lock, bounded by the step's own deadline.

A call the watchdog abandoned while it waited for the lock would otherwise get it later,
make a request nobody reads, and move the pacing on. These tests inject a lock that
records each wait asked of it, or hold the real one, with the clock and sleeper faked.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    DEADLINE_S,
    QUERY,
    FakeClock,
    FakeGeniusClient,
    FakeSong,
    challenge,
    paced_genius,
)


class RecordingLock:
    """A lock that records each wait it is asked for; *free* says whether it is got."""

    def __init__(self, free: bool) -> None:
        """Free, or held by another call for good."""
        self.free = free
        self.timeouts: list[float] = []

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Record *timeout*; succeed only when free."""
        self.timeouts.append(timeout)
        return self.free

    def release(self) -> None:
        """Nothing to release in a recording."""


class JumpingClock(FakeClock):
    """A clock that moves past any step deadline between one read and the next."""

    def __call__(self) -> float:
        """Jump far ahead, then read."""
        self.now += 1000.0
        return self.now


class _LateLock(RecordingLock):
    """A free lock got with *left_s* of the wait left, just as another call ends.

    The call that held it marked its own request as just made, so the pace since
    that request has its whole interval still to run.
    """

    def __init__(
        self, genius: lg.GeniusProvider, clock: FakeClock, left_s: float
    ) -> None:
        """Hold *genius*'s lock on *clock* until *left_s* of each wait remains."""
        super().__init__(free=True)
        self.genius, self.clock, self.left_s = genius, clock, left_s

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Spend all but *left_s* of *timeout* behind another call, then succeed."""
        self.clock.now += timeout - self.left_s
        self.genius.pacer.last = self.clock.now
        return super().acquire(blocking, timeout)


class GeniusLockWaitTest(fixtures.IsolatedCase):
    """A call waits for the lock only until its step deadline, then makes no request."""

    def _genius(
        self, client: FakeGeniusClient, lock: RecordingLock, clock: FakeClock
    ) -> lg.GeniusProvider:
        """A GeniusProvider on *client* and *clock*, waiting on *lock*."""
        genius = paced_genius(client, clock)
        genius.lock = lock  # type: ignore[assignment] # record the waits asked for
        return genius

    def test_a_lock_held_past_the_deadline_is_a_timeout_with_no_request(self) -> None:
        client, lock = FakeGeniusClient([FakeSong("words")]), RecordingLock(free=False)
        genius = self._genius(client, lock, FakeClock())
        with self.assertRaises(lp.ProviderTimeout):
            genius(QUERY)
        self.assertEqual(client.calls, [])
        self.assertIsNone(genius.pacer.last)
        bound = genius.step_deadline_s()
        self.assertEqual(lock.timeouts, [bound])

    def test_each_wait_is_what_is_left_of_the_step_deadline(self) -> None:
        client = FakeGeniusClient([challenge(429), FakeSong("words")])
        lock = RecordingLock(free=True)
        genius = self._genius(client, lock, FakeClock())
        self.assertEqual(genius(QUERY), "words")
        bound = genius.step_deadline_s()
        self.assertEqual(lock.timeouts, [bound, bound - lg.GENIUS_RETRY_WAITS_S[0]])

    def test_no_request_is_made_once_the_deadline_has_passed(self) -> None:
        client = FakeGeniusClient([challenge(429), FakeSong("words")])
        lock = RecordingLock(free=True)
        clock = FakeClock()
        genius = self._genius(client, lock, clock)
        # The first retry's wait alone runs past the whole step deadline.
        genius.sleeper = lambda seconds: clock.sleep(seconds + 1000.0)
        with self.assertRaises(lp.ProviderTimeout):
            genius(QUERY)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(lock.timeouts), 1)

    def test_a_call_past_its_deadline_does_not_wait_on_a_held_lock(self) -> None:
        client = FakeGeniusClient([FakeSong("words")])
        clock = JumpingClock()
        genius = lg.GeniusProvider(
            client, clock=clock, sleeper=clock.sleep, deadline_s=DEADLINE_S
        )
        timeouts: list[lp.ProviderTimeout] = []
        self.assertTrue(genius.lock.acquire(timeout=fixtures.HANG_LIMIT_S))
        self.addCleanup(genius.lock.release)

        def call() -> None:
            try:
                genius(QUERY)
            except lp.ProviderTimeout as timeout:
                timeouts.append(timeout)

        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        worker.join(fixtures.HANG_LIMIT_S)
        self.assertFalse(worker.is_alive())
        self.assertEqual((len(timeouts), client.calls), (1, []))

    def test_a_pace_that_would_outlast_the_deadline_is_not_begun(self) -> None:
        # The lock is got with one second of the step deadline left, and the pace
        # since the other call's request has longer than that to run.
        clock = FakeClock()
        client = FakeGeniusClient([FakeSong("words")])
        genius = paced_genius(client, clock)
        genius.lock = _LateLock(genius, clock, left_s=1.0)  # type: ignore[assignment] # spend the wait
        with self.assertRaises(lp.ProviderTimeout):
            genius(QUERY)
        self.assertEqual((client.calls, clock.sleeps), ([], []))
        self.assertEqual(genius.pacer.last, clock.now)

    def test_the_deadline_is_the_providers_own(self) -> None:
        genius = lg.GeniusProvider(FakeGeniusClient([]), deadline_s=2.0)
        lock = RecordingLock(free=False)
        genius.lock = lock  # type: ignore[assignment] # record the waits asked for
        with self.assertRaises(lp.ProviderTimeout):
            genius(QUERY)
        self.assertAlmostEqual(lock.timeouts[0], genius.step_deadline_s(), places=3)


class TableDeadlineTest(fixtures.IsolatedCase):
    """The table's Genius step runs with the deadline the caller fetches with."""

    KEY = lr.Secret("not-a-real-key")

    @staticmethod
    def _client(_secret: lr.Secret) -> FakeGeniusClient:
        """A fake Genius client in place of the real one."""
        return FakeGeniusClient([FakeSong("words")])

    def _genius_of(self, table: lm.ProviderTable) -> lg.GeniusProvider:
        """*table*'s Genius step."""
        genius = table[lm.ProviderKey(lm.Source.GENIUS, lm.LyricsKind.UNSYNCED)]
        assert isinstance(genius, lg.GeniusProvider)
        return genius

    def test_a_table_built_without_one_uses_the_chain_default(self) -> None:
        table = ltable.build_provider_table(self.KEY, genius_factory=self._client)
        self.assertEqual(self._genius_of(table).deadline_s, lf.PROVIDER_DEADLINE_S)

    def test_a_non_default_deadline_bounds_the_genius_lock_wait(self) -> None:
        genius = self._genius_of(
            ltable.build_provider_table(
                self.KEY, genius_factory=self._client, deadline_s=2.0
            )
        )
        lock = RecordingLock(free=False)
        genius.lock = lock  # type: ignore[assignment] # record the waits asked for
        with self.assertRaises(lp.ProviderTimeout):
            genius(QUERY)
        self.assertEqual(genius.deadline_s, 2.0)
        self.assertAlmostEqual(lock.timeouts[0], genius.step_deadline_s(), places=3)
        default = lg.GeniusProvider(FakeGeniusClient([]), deadline_s=DEADLINE_S)
        self.assertLess(lock.timeouts[0], default.step_deadline_s())

    def test_the_watchdog_and_the_step_give_up_at_one_bound(self) -> None:
        genius = self._genius_of(
            ltable.build_provider_table(
                self.KEY, genius_factory=self._client, deadline_s=2.0
            )
        )
        lock = RecordingLock(free=False)
        genius.lock = lock  # type: ignore[assignment] # record the waits asked for
        bounds: list[float] = []
        real = lf.run_with_deadline

        def recording(call: object, deadline_s: float) -> object:
            bounds.append(deadline_s)
            return real(call, deadline_s)  # type: ignore[arg-type] # pass-through

        key = lm.ProviderKey(lm.Source.GENIUS, lm.LyricsKind.UNSYNCED)
        with mock.patch.object(lf, "run_with_deadline", recording):
            # The chain's own deadline differs from the one the table was built with.
            lf.fetch_lyrics(
                QUERY, lm.SidecarState(None), {key: genius}, deadline_s=30.0
            )
        self.assertEqual(len(bounds), 1)
        self.assertAlmostEqual(bounds[0], lock.timeouts[0], places=3)
