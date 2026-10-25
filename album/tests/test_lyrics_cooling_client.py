"""The cooling client's base: the refusal classes, the one policy point, and `turn`.

A small invented subclass stands in for a real client, so the base is tested alone. The
clock is a `FakeClock` and each cool-down file is written under the test's own
temporary directory.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooling_client as lcc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import FakeClock  # noqa: E402 - import follows the sys.path bootstrap above

#: The fixed moment the invented client reads as now, aware UTC.
NOW = datetime(2026, 10, 21, 9, 30, tzinfo=UTC)
#: How long past now a turn's deadline lies when it is not the point of the test.
ROOM_S = 100.0


class InventedStopped(lp.ProviderError):
    """The invented source's stopped error."""

    def __init__(self, what: str) -> None:
        """Carry *what* as the detail."""
        super().__init__(f"Invented stopped: {what}", detail=what)


class InventedClient(lcc.CoolingClient):
    """A cooling client for an invented source, paced two seconds apart."""

    NAME: ClassVar[str] = "Invented"
    MIN_INTERVAL_S: ClassVar[float] = 2.0
    stopped_error: ClassVar[type[lp.ProviderError]] = InventedStopped


class StatusRefusalTest(fixtures.IsolatedCase):
    """A refusing status's class."""

    def test_each_status_maps_to_its_class(self) -> None:
        cases = {
            429: lcc.Refusal.RATE_LIMITED,
            502: lcc.Refusal.OVERLOADED,
            503: lcc.Refusal.OVERLOADED,
            504: lcc.Refusal.OVERLOADED,
            403: lcc.Refusal.BLOCKED,
            500: lcc.Refusal.BLOCKED,
            302: lcc.Refusal.BLOCKED,
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.assertIs(lcc.status_refusal(status), expected)


class _ClientCase(fixtures.IsolatedCase):
    """A fake clock, and an invented client on it."""

    def setUp(self) -> None:
        """A fresh clock and client for each test."""
        super().setUp()
        self.clock = FakeClock()
        self.state = self.tmp / "state" / "invented.json"
        self.client = InventedClient(
            self.state, clock=self.clock, sleeper=self.clock.sleep, now=lambda: NOW
        )


class RefuseTest(_ClientCase):
    """Every refusal stops the client for the run and writes the cool-down."""

    def test_a_refusal_stops_and_writes_the_cool_down(self) -> None:
        for kind in lcc.Refusal:
            with self.subTest(kind=kind):
                client = InventedClient(
                    self.tmp / kind.name / "s.json", now=lambda: NOW
                )
                error = client.refuse(kind, "HTTP 429")
                self.assertIsInstance(error, InventedStopped)
                self.assertEqual(error.detail, "HTTP 429")
                assert client.stop is not None
                self.assertEqual(client.stop.until, NOW + lyc.COOL_DOWN)
                self.assertIsNone(client.stop.write_error)
                read = lyc.read_cooldown(client.state_path, NOW).cool_down
                assert read is not None
                self.assertEqual(read.what, "HTTP 429")
                self.assertFalse(client.available())

    def test_a_failed_write_still_stops(self) -> None:
        failing = mock.patch.object(lyc, "write_cooldown", side_effect=OSError("full"))
        with failing:
            self.client.refuse(lcc.Refusal.BLOCKED, "challenge page")
        assert self.client.stop is not None
        self.assertIsNotNone(self.client.stop.write_error)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.client.available())

    def test_the_stop_is_kept_to_the_second(self) -> None:
        self.client.now = lambda: NOW.replace(microsecond=123_456)
        self.client.refuse(lcc.Refusal.RATE_LIMITED, "HTTP 429")
        assert self.client.stop is not None
        self.assertEqual(self.client.stop.until, NOW + lyc.COOL_DOWN)

    def test_a_fresh_client_is_available(self) -> None:
        self.assertTrue(self.client.available())
        self.assertEqual(self.client.pacer.interval_s, 2.0)
        self.assertEqual(self.client.pacer.name, "Invented")


class TurnTest(_ClientCase):
    """`turn` checks the deadline, then the stop, then paces, all under the lock."""

    def test_a_turn_is_held_under_the_lock_and_paced(self) -> None:
        with self.client.turn(self.clock() + ROOM_S):
            self.assertTrue(self.client.lock.locked())
        self.assertFalse(self.client.lock.locked())
        with self.client.turn(self.clock() + ROOM_S):
            pass
        self.assertEqual(self.clock.sleeps, [2.0])
        self.assertEqual(self.client.pacer.last, 1002.0)

    def test_the_deadline_is_checked_before_the_stop(self) -> None:
        self.client.refuse(lcc.Refusal.BLOCKED, "challenge page")
        with self.assertRaises(lp.ProviderTimeout) as raised:
            with self.client.turn(self.clock()):
                self.fail("a turn ran past its deadline")
        self.assertEqual(str(raised.exception), "the Invented step's deadline passed")

    def test_the_stop_is_checked_before_the_pace(self) -> None:
        with self.client.turn(self.clock() + ROOM_S):
            pass
        self.client.refuse(lcc.Refusal.BLOCKED, "challenge page")
        with self.assertRaises(InventedStopped) as raised:
            # The pace alone would outlast this deadline: the stop is raised first.
            with self.client.turn(self.clock() + 1.0):
                self.fail("a stopped client took a turn")
        self.assertEqual(raised.exception.detail, "challenge page")
        self.assertEqual((self.clock.sleeps, self.client.pacer.last), ([], 1000.0))
        self.assertFalse(self.client.lock.locked())

    def test_a_pace_past_the_deadline_takes_no_turn(self) -> None:
        with self.client.turn(self.clock() + ROOM_S):
            pass
        with self.assertRaises(lp.ProviderTimeout):
            with self.client.turn(self.clock() + 1.0):
                self.fail("a turn ran past its deadline")
        self.assertFalse(self.client.lock.locked())
