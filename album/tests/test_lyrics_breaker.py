"""A provider's breaker: its policy, and its state file beside the cool-down files.

The policy is pure: each test hands it a state, a refusal's class or an answer, and a
moment, and reads the state it gives back. The state file is written inside the
test's own temporary directory, and the isolation points XDG_STATE_HOME under the
temporary HOME, so no test reaches the real state folder.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breaker as lbk  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above

R = lrf.Refusal
NOW = datetime(2026, 10, 25, 9, 30, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
#: JSON-like values keyed by the file's own fields, for the property test.
_JSON = http_fakes.json_values(
    ("run", "reason", "window_s", "open_until"), max_size=4, max_leaves=8
)


def refused(state: lbk.BreakerState, reason: R, times: int = 1) -> lbk.BreakerState:
    """*state* after *times* refusals of *reason*, all at `NOW`."""
    for _ in range(times):
        state = lbk.after_refusal(state, reason, NOW)
    return state


def _as_read(state: lbk.BreakerState, now: datetime) -> lbk.BreakerState:
    """*state* as its file reads at *now*: closed once its window ended too long ago.

    Too long is over `lbk.MAX_WINDOW` past the window's end; otherwise *state* is kept.
    """
    if state.open_until is None:
        return state
    decays_at = state.open_until + lbk.MAX_WINDOW
    return lbk.CLOSED if now > decays_at else state


def record(**fields: object) -> bytes:
    """A breaker file's bytes holding *fields* as given."""
    return json.dumps(fields).encode()


class OpeningTest(fixtures.IsolatedCase):
    """Three overloaded, unreachable or rate-limited refusals in a row open it."""

    def test_the_constants_are_the_rulings(self) -> None:
        self.assertEqual(lbk.OPEN_AFTER, 3)
        self.assertEqual(lbk.FIRST_WINDOW, timedelta(minutes=5))
        self.assertEqual(lbk.MAX_WINDOW, timedelta(minutes=60))

    def test_two_refusals_count_but_do_not_open(self) -> None:
        state = refused(lbk.CLOSED, R.OVERLOADED, 2)
        self.assertEqual(state, lbk.BreakerState(run=2, reason=R.OVERLOADED))
        self.assertFalse(state.is_open(NOW))

    def test_the_third_counted_refusal_opens_it_for_five_minutes(self) -> None:
        for reason in (R.OVERLOADED, R.UNREACHABLE, R.RATE_LIMITED):
            with self.subTest(reason=reason):
                state = refused(lbk.CLOSED, reason, 3)
                self.assertEqual(state.window, lbk.FIRST_WINDOW)
                self.assertEqual(state.open_until, NOW + lbk.FIRST_WINDOW)
                self.assertEqual(state.reason, reason)
                self.assertTrue(state.is_open(NOW + 4 * MINUTE))

    def test_mixed_counted_reasons_count_together_and_keep_the_last(self) -> None:
        state = refused(lbk.CLOSED, R.OVERLOADED, 2)
        state = refused(state, R.UNREACHABLE)
        self.assertTrue(state.is_open(NOW))
        self.assertEqual(state.reason, R.UNREACHABLE)

    def test_an_answer_resets_the_count(self) -> None:
        state = lbk.after_answer(refused(lbk.CLOSED, R.OVERLOADED, 2))
        self.assertEqual(state, lbk.CLOSED)
        self.assertFalse(refused(state, R.OVERLOADED, 2).is_open(NOW))

    def test_an_unrecognized_refusal_neither_counts_nor_resets(self) -> None:
        state = refused(lbk.CLOSED, R.OVERLOADED, 2)
        self.assertEqual(refused(state, R.UNRECOGNIZED), state)
        self.assertTrue(
            refused(refused(state, R.UNRECOGNIZED), R.OVERLOADED).is_open(NOW)
        )

    def test_a_moment_is_kept_to_the_second_as_the_file_keeps_it(self) -> None:
        moment = NOW + timedelta(microseconds=600_000)
        state = lbk.after_refusal(
            refused(lbk.CLOSED, R.OVERLOADED, 2), R.OVERLOADED, moment
        )
        self.assertEqual(state.open_until, NOW + lbk.FIRST_WINDOW)


class HalfOpenTest(fixtures.IsolatedCase):
    """Past its window one track may ask: an answer closes it, a refusal doubles it."""

    def setUp(self) -> None:
        """A breaker opened at `NOW`, and the moment its window ends."""
        super().setUp()
        self.opened = refused(lbk.CLOSED, R.OVERLOADED, 3)
        self.ended = NOW + lbk.FIRST_WINDOW

    def test_it_is_open_until_its_window_ends_and_not_at_the_end(self) -> None:
        self.assertTrue(self.opened.is_open(self.ended - timedelta(seconds=1)))
        self.assertFalse(self.opened.is_open(self.ended))

    def test_an_answer_after_the_window_closes_it(self) -> None:
        self.assertEqual(lbk.after_answer(self.opened), lbk.CLOSED)

    def test_a_refusal_after_the_window_reopens_it_at_double(self) -> None:
        state = lbk.after_refusal(self.opened, R.UNREACHABLE, self.ended)
        self.assertEqual(state.window, 2 * lbk.FIRST_WINDOW)
        self.assertEqual(state.open_until, self.ended + 2 * lbk.FIRST_WINDOW)
        self.assertEqual(state.reason, R.UNREACHABLE)

    def test_the_window_doubles_up_to_the_cap_and_stays_there(self) -> None:
        state, moment, windows = self.opened, self.ended, []
        for _ in range(6):
            state = lbk.after_refusal(state, R.OVERLOADED, moment)
            assert state.window is not None and state.open_until is not None
            windows.append(state.window // MINUTE)
            moment = state.open_until
        self.assertEqual(windows, [10, 20, 40, 60, 60, 60])

    def test_a_counted_refusal_while_still_open_changes_nothing(self) -> None:
        moment = self.ended - timedelta(seconds=1)
        self.assertEqual(
            lbk.after_refusal(self.opened, R.OVERLOADED, moment), self.opened
        )

    def test_a_block_while_still_open_reopens_it_at_the_cap(self) -> None:
        state = lbk.after_refusal(self.opened, R.BLOCKED, NOW)
        self.assertEqual(state.window, lbk.MAX_WINDOW)

    def test_an_unrecognized_probe_leaves_it_half_open(self) -> None:
        state = lbk.after_refusal(self.opened, R.UNRECOGNIZED, self.ended)
        self.assertEqual(state, self.opened)
        self.assertFalse(state.is_open(self.ended))


class DecayTest(fixtures.IsolatedCase):
    """A half-open breaker left alone past the longest window reads back closed."""

    def setUp(self) -> None:
        """A breaker opened at `NOW` and written, and the moment its window ends."""
        super().setUp()
        self.opened = refused(lbk.CLOSED, R.OVERLOADED, 3)
        self.ended = NOW + lbk.FIRST_WINDOW
        self.path = self.tmp / "state" / "lrclib.json"
        lbk.write_breaker(self.path, self.opened)

    def test_up_to_the_longest_window_past_its_end_it_stays_half_open(self) -> None:
        read = lbk.read_breaker(self.path, self.ended + lbk.MAX_WINDOW)
        self.assertEqual(read, lbk.BreakerRead(self.opened))

    def test_further_past_its_end_it_reads_as_closed(self) -> None:
        later = self.ended + lbk.MAX_WINDOW + timedelta(seconds=1)
        self.assertEqual(lbk.read_breaker(self.path, later), lbk.BreakerRead())

    def test_a_refusal_after_the_decay_counts_from_one(self) -> None:
        later = self.ended + 7 * 24 * 60 * MINUTE
        state = lbk.read_breaker(self.path, later).state
        self.assertEqual(
            lbk.after_refusal(state, R.OVERLOADED, later),
            lbk.BreakerState(1, R.OVERLOADED),
        )


class BlockedTest(fixtures.IsolatedCase):
    """One blocked refusal opens it at once at the cap."""

    def test_one_block_opens_a_closed_breaker_for_an_hour(self) -> None:
        state = refused(lbk.CLOSED, R.BLOCKED)
        self.assertEqual(state.window, lbk.MAX_WINDOW)
        self.assertEqual(state.open_until, NOW + lbk.MAX_WINDOW)
        self.assertEqual(state.reason, R.BLOCKED)

    def test_a_block_on_a_probe_reopens_at_the_cap(self) -> None:
        opened = refused(lbk.CLOSED, R.OVERLOADED, 3)
        state = lbk.after_refusal(opened, R.BLOCKED, NOW + lbk.FIRST_WINDOW)
        self.assertEqual(state.window, lbk.MAX_WINDOW)


class DetailTest(fixtures.IsolatedCase):
    """A skipped step's detail names the window's end, to the second, in UTC."""

    def test_the_detail_names_the_end(self) -> None:
        state = refused(lbk.CLOSED, R.OVERLOADED, 3)
        self.assertEqual(lbk.open_detail(state), "breaker open until 09:35:00 UTC")

    def test_a_closed_breaker_has_no_detail(self) -> None:
        with self.assertRaises(ValueError):
            lbk.open_detail(lbk.CLOSED)


class PathTest(fixtures.IsolatedCase):
    """Each guarded provider's file sits beside the cool-down files."""

    def test_the_file_is_in_the_cool_down_folder(self) -> None:
        environ = {"XDG_STATE_HOME": "/srv/state", "HOME": "/srv/someone"}
        for source in lbk.GUARDED_SOURCES:
            with self.subTest(source=source):
                path = lbk.breaker_path(environ, source)
                youtube = lyc.cooldown_path(environ, lm.Source.YOUTUBE_MUSIC)
                self.assertEqual(path.parent, youtube.parent)
                self.assertNotEqual(path.name, youtube.name)

    def test_the_guarded_sources_are_the_four_ruled(self) -> None:
        self.assertEqual(
            set(lbk.GUARDED_SOURCES),
            {lm.Source.LRCLIB, lm.Source.NETEASE, lm.Source.GENIUS, lm.Source.BANDCAMP},
        )


class RoundTripTest(fixtures.IsolatedCase):
    """What is written reads back as the same state."""

    def test_every_shape_the_policy_makes_reads_back(self) -> None:
        opened = refused(lbk.CLOSED, R.OVERLOADED, 3)
        states = {
            "closed": lbk.CLOSED,
            "counting": refused(lbk.CLOSED, R.RATE_LIMITED, 2),
            "open": opened,
            "doubled": lbk.after_refusal(opened, R.UNREACHABLE, NOW),
            "blocked": refused(lbk.CLOSED, R.BLOCKED),
        }
        for name, state in states.items():
            with self.subTest(name=name):
                path = self.tmp / "state" / f"{name}.json"
                lbk.write_breaker(path, state)
                self.assertEqual(lbk.read_breaker(path, NOW), lbk.BreakerRead(state))

    def test_each_doubled_window_reads_back(self) -> None:
        state, moment = refused(lbk.CLOSED, R.OVERLOADED, 3), NOW + lbk.FIRST_WINDOW
        for minutes in (10, 20, 40, 60):
            with self.subTest(minutes=minutes):
                state = lbk.after_refusal(state, R.OVERLOADED, moment)
                self.assertEqual(state.window, minutes * MINUTE)
                self.assertEqual(
                    lbk.parse_breaker(lbk.breaker_bytes(state), moment), state
                )
                assert state.open_until is not None
                moment = state.open_until

    def test_a_read_cannot_be_changed_once_made(self) -> None:
        read = lbk.BreakerRead()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            read.warning = "changed"  # type: ignore[misc]

    def test_a_write_is_atomic_and_confirmed(self) -> None:
        path = self.tmp / "state" / "lrclib.json"
        with mock.patch.object(
            lyrics_files, "write_confirmed", wraps=lyrics_files.write_confirmed
        ) as written:
            lbk.write_breaker(path, lbk.CLOSED)
        written.assert_called_once()

    def test_a_missing_file_is_a_closed_breaker_without_a_warning(self) -> None:
        read = lbk.read_breaker(self.tmp / "absent.json", NOW)
        self.assertEqual(read, lbk.BreakerRead(lbk.CLOSED))


class CorruptionTest(fixtures.IsolatedCase):
    """A broken or edited file is no breaker, with a warning that quotes nothing."""

    GOOD = {
        "run": 3,
        "reason": "overloaded",
        "window_s": 300,
        "open_until": "2026-10-25T09:35:00Z",
    }

    def _ignored(self, data: bytes) -> None:
        """*data* in a file reads as a closed breaker, with a warning."""
        path = self.tmp / "breaker.json"
        path.write_bytes(data)
        read = lbk.read_breaker(path, NOW)
        self.assertEqual(read.state, lbk.CLOSED)
        self.assertIn("breaker file ignored", read.warning or "")
        self.assertNotIn("overloaded", read.warning or "")

    def test_the_good_record_parses(self) -> None:
        state = lbk.parse_breaker(record(**self.GOOD), NOW)
        self.assertEqual(state, refused(lbk.CLOSED, R.OVERLOADED, 3))

    def test_each_malformed_record_is_refused(self) -> None:
        good = self.GOOD
        cases: dict[str, dict[str, object]] = {
            "a missing field": {k: v for k, v in good.items() if k != "run"},
            "an extra field": {**good, "x": 1},
            "a negative run": {**good, "run": -1},
            "a run not a number": {**good, "run": "3"},
            "a boolean run": {**good, "run": True},
            "an unknown reason": {**good, "reason": "tired"},
            "a reason that never counts": {**good, "reason": "unrecognized"},
            "a window not of the schedule": {**good, "window_s": 301},
            "a window not a number": {**good, "window_s": "300"},
            "a window with no end": {**good, "open_until": None},
            "an end with no window": {**good, "window_s": None},
            "an end too far off": {**good, "open_until": "2026-10-25T09:36:00Z"},
            "a loose time": {**good, "open_until": "2026-10-25T9:35:00Z"},
            "a time not text": {**good, "open_until": 1},
            "a count with no reason": {
                "run": 1,
                "reason": None,
                "window_s": None,
                "open_until": None,
            },
            "a reason with no count": {
                "run": 0,
                "reason": "overloaded",
                "window_s": None,
                "open_until": None,
            },
            "a closed count past opening": {
                "run": 3,
                "reason": "overloaded",
                "window_s": None,
                "open_until": None,
            },
            "an open breaker with no reason": {**good, "reason": None},
            "a closed block": {
                "run": 1,
                "reason": "blocked",
                "window_s": None,
                "open_until": None,
            },
        }
        for name, fields in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                lbk.parse_breaker(record(**fields), NOW)

    def test_an_open_breaker_with_no_count_is_a_block(self) -> None:
        fields = {
            **self.GOOD,
            "run": 0,
            "reason": "blocked",
            "window_s": 3600,
            "open_until": "2026-10-25T10:30:00Z",
        }
        self.assertEqual(lbk.parse_breaker(record(**fields), NOW).reason, R.BLOCKED)

    def test_a_window_too_large_for_a_timedelta_is_refused_not_overflowed(
        self,
    ) -> None:
        # Past timedelta's own range, so the window must be refused before one is built.
        for window_s in (10**20, -(10**20)):
            with self.subTest(window_s=window_s):
                data = record(**{**self.GOOD, "window_s": window_s})
                with self.assertRaises(ValueError):
                    lbk.parse_breaker(data, NOW)
                path = self.tmp / "breaker.json"
                path.write_bytes(data)
                read = lbk.read_breaker(path, NOW)
                self.assertEqual(read.state, lbk.CLOSED)
                self.assertIn("breaker file ignored (ValueError)", read.warning or "")

    def test_a_window_outside_zero_and_the_cap_is_refused(self) -> None:
        cap = int(lbk.MAX_WINDOW.total_seconds())
        for window_s in (0, -300, cap + 1):
            with self.subTest(window_s=window_s), self.assertRaises(ValueError):
                lbk.parse_breaker(record(**{**self.GOOD, "window_s": window_s}), NOW)

    def test_garbage_nesting_and_size_are_ignored(self) -> None:
        self._ignored(b"\xff\xfe not json")
        self._ignored(b"[" * 200_000)
        self._ignored(record(**self.GOOD) + b" " * lbk.MAX_BREAKER_BYTES)
        self._ignored(b"[]")

    def test_an_oversized_file_is_read_no_further_than_one_byte_past_the_cap(
        self,
    ) -> None:
        path = self.tmp / "breaker.json"
        path.write_bytes(b" " * (lbk.MAX_BREAKER_BYTES * 4))
        seen: list[int] = []

        def recording(data: bytes, now: datetime) -> lbk.BreakerState:
            del now  # only the size handed over matters
            seen.append(len(data))
            raise ValueError("refused")

        with mock.patch.object(lbk, "parse_breaker", recording):
            read = lbk.read_breaker(path, NOW)
        self.assertEqual(read.state, lbk.CLOSED)
        self.assertEqual(seen, [lbk.MAX_BREAKER_BYTES + 1])

    def test_an_unreadable_file_is_ignored(self) -> None:
        folder = self.tmp / "breaker.json"
        folder.mkdir()
        read = lbk.read_breaker(folder, NOW)
        self.assertEqual(read.state, lbk.CLOSED)
        self.assertIn("breaker file ignored (NotRegularFileError)", read.warning or "")

    def test_a_broken_file_is_replaced_on_the_next_write(self) -> None:
        path = self.tmp / "breaker.json"
        path.write_bytes(b"not json")
        state = refused(lbk.CLOSED, R.OVERLOADED)
        lbk.write_breaker(path, state)
        self.assertEqual(lbk.read_breaker(path, NOW), lbk.BreakerRead(state))


class PropertyTest(fixtures.IsolatedCase):
    """The parser raises nothing but ValueError, whatever bytes it is handed."""

    def _assert_parses_or_refuses(self, data: bytes) -> None:
        """*data* is refused with a ValueError, or is a state the policy could make."""
        try:
            state = lbk.parse_breaker(data, NOW)
        except ValueError:
            return
        self.assertGreaterEqual(state.run, 0)
        if state.open_until is not None:
            assert state.window is not None
            self.assertLessEqual(state.open_until, NOW + state.window)

    @given(st.binary(max_size=200))
    def test_fuzz_parse_breaker(self, data: bytes) -> None:
        self._assert_parses_or_refuses(data)

    @given(_JSON)
    def test_json_values_parse_or_are_refused(self, value: object) -> None:
        self._assert_parses_or_refuses(http_fakes.json_body(value))

    @given(
        st.lists(st.sampled_from(list(R)) | st.none(), max_size=12),
        st.integers(min_value=0, max_value=7200),
    )
    def test_every_state_the_policy_makes_reads_back(
        self, events: list[R | None], step_s: int
    ) -> None:
        state, moment = lbk.CLOSED, NOW
        for event in events:
            moment += timedelta(seconds=step_s)
            if event is None:
                state = lbk.after_answer(state)
            else:
                state = lbk.after_refusal(state, event, moment)
            data = lbk.breaker_bytes(state)
            self.assertEqual(lbk.parse_breaker(data, moment), _as_read(state, moment))
