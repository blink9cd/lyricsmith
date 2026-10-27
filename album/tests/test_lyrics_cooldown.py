"""Each source's cool-down file: where it lives, its round trip, a broken one ignored, a stop.

Every test writes inside its own temporary directory, and the isolation points
XDG_STATE_HOME under the temporary HOME, so no test reaches the real state folder.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above

YTM, TEKSTOWO = lm.Source.YOUTUBE_MUSIC, lm.Source.TEKSTOWO
#: The moment the tests read as now, and a refusal an hour before it.
NOW = datetime(2026, 10, 21, 9, 30, tzinfo=UTC)
REFUSED = NOW - timedelta(hours=1)
#: JSON-like values keyed by the file's own fields, for the property test.
_JSON = http_fakes.json_values(
    ("refused_at", "what", "until"), max_size=3, max_leaves=8
)

#: Nesting deep enough that decoding it would exhaust the recursion limit.
NESTED = b"[" * 200_000


def record(refused_at: str, what: str, until: str) -> bytes:
    """A cool-down file's bytes holding the three fields as given."""
    return json.dumps({"refused_at": refused_at, "what": what, "until": until}).encode()


class PathTest(fixtures.IsolatedCase):
    """The file sits in the XDG state folder, or ~/.local/state when that is unusable."""

    def test_an_absolute_state_home_wins(self) -> None:
        environ = {"XDG_STATE_HOME": "/srv/state", "HOME": "/srv/someone"}
        self.assertEqual(
            lyc.cooldown_path(environ, YTM),
            Path("/srv/state/lyricsmith/youtube_music.json"),
        )

    def test_a_relative_state_home_is_ignored(self) -> None:
        environ = {"XDG_STATE_HOME": "state", "HOME": "/srv/someone"}
        self.assertEqual(
            lyc.cooldown_path(environ, YTM),
            Path("/srv/someone/.local/state/lyricsmith/youtube_music.json"),
        )

    def test_with_neither_the_process_home_is_used(self) -> None:
        self.assertEqual(
            lyc.cooldown_path({}, YTM),
            Path.home() / ".local/state/lyricsmith/youtube_music.json",
        )

    def test_the_isolation_keeps_it_under_the_temporary_home(self) -> None:
        state = self.xdg["XDG_STATE_HOME"] / lyc.STATE_DIR
        for source in (YTM, TEKSTOWO):
            with self.subTest(source=source):
                path = lyc.cooldown_path(os.environ, source)
                self.assertTrue(path.is_relative_to(self.home))
                self.assertEqual(path, state / lyc.STATE_FILES[source])

    def test_each_source_has_its_own_file_in_one_folder(self) -> None:
        youtube = lyc.cooldown_path(os.environ, YTM)
        tekstowo = lyc.cooldown_path(os.environ, TEKSTOWO)
        self.assertEqual(youtube.parent, tekstowo.parent)
        self.assertNotEqual(youtube.name, tekstowo.name)
        self.assertEqual(tekstowo.name, "tekstowo.json")

    def test_a_source_with_no_file_is_a_key_error(self) -> None:
        with self.assertRaises(KeyError):
            lyc.cooldown_path(os.environ, lm.Source.GENIUS)


class RoundTripTest(fixtures.IsolatedCase):
    """A written cool-down reads back as written, in the file's fixed form."""

    def test_a_write_then_a_read_round_trips(self) -> None:
        path = self.tmp / "state" / "nested" / "youtube_music.json"
        written = lyc.write_cooldown(path, REFUSED, "HTTP 429")
        self.assertEqual(written.until - written.refused_at, timedelta(hours=24))
        self.assertEqual(lyc.read_cooldown(path, NOW), lyc.CoolDownRead(written))
        self.assertEqual(
            json.loads(path.read_bytes()),
            {
                "refused_at": "2026-10-21T08:30:00Z",
                "what": "HTTP 429",
                "until": "2026-10-22T08:30:00Z",
            },
        )
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [path.name])

    def test_a_refusal_with_microseconds_still_round_trips(self) -> None:
        path = self.tmp / "youtube_music.json"
        written = lyc.write_cooldown(path, REFUSED.replace(microsecond=7), "HTTP 503")
        self.assertEqual(written.refused_at, REFUSED)
        self.assertEqual(lyc.read_cooldown(path, NOW).cool_down, written)

    def test_the_write_is_confirmed(self) -> None:
        calls: list[tuple[Path, bytes]] = []
        real = lyrics_files.write_confirmed

        def recording(target: Path, data: bytes, *, mode: int) -> None:
            calls.append((target, data))
            real(target, data, mode=mode)

        path = self.tmp / "youtube_music.json"
        with mock.patch.object(lyrics_files, "write_confirmed", recording):
            lyc.write_cooldown(path, REFUSED, "redirect to consent")
        self.assertEqual([target for target, _ in calls], [path])
        self.assertEqual(calls[0][1], path.read_bytes())

    def test_the_utc_form(self) -> None:
        moment = datetime(
            2026, 10, 22, 13, 30, 5, 999, tzinfo=timezone(timedelta(hours=2))
        )
        self.assertEqual(lyc.format_utc(moment), "2026-10-22T11:30:05Z")

    def test_utc_now_is_aware_utc(self) -> None:
        self.assertEqual(lyc.utc_now().utcoffset(), timedelta(0))


class CoolingTest(fixtures.IsolatedCase):
    """A cool-down holds until its end and not past it."""

    def test_a_second_before_the_end_it_is_cooling(self) -> None:
        cool = lyc.CoolDown(REFUSED, "HTTP 429", REFUSED + lyc.COOL_DOWN)
        self.assertTrue(lyc.is_cooling(cool, cool.until - timedelta(seconds=1)))
        self.assertFalse(lyc.is_cooling(cool, cool.until))

    def test_none_is_not_cooling(self) -> None:
        self.assertFalse(lyc.is_cooling(None, NOW))


class BrokenFileTest(fixtures.IsolatedCase):
    """A missing file is no cool-down; a broken one is a warning and is ignored."""

    def test_a_missing_file_is_no_cool_down_and_no_warning(self) -> None:
        missing = self.tmp / "absent.json"
        self.assertEqual(lyc.read_cooldown(missing, NOW), lyc.CoolDownRead(None))

    def _assert_ignored(self, data: bytes, error: str) -> None:
        """A file of *data* is ignored with a warning naming the path and *error*."""
        path = self.tmp / "youtube_music.json"
        path.write_bytes(data)
        result = lyc.read_cooldown(path, NOW)
        self.assertIsNone(result.cool_down)
        self.assertEqual(result.warning, f"{path}: cool-down file ignored ({error})")

    def test_each_broken_file_is_ignored_naming_only_its_error(self) -> None:
        later = "2026-10-22T08:30:00Z"
        cases = {
            "not JSON": (b"{", "JSONDecodeError"),
            "until 48 hours on": (
                record("2026-10-21T08:30:00Z", "HTTP 429", "2026-10-23T08:30:00Z"),
                "ValueError",
            ),
            "refused tomorrow": (
                record(later, "HTTP 429", "2026-10-23T08:30:00Z"),
                "ValueError",
            ),
            "not text": (b"\xff", "UnicodeDecodeError"),
        }
        for name, (data, error) in cases.items():
            with self.subTest(name=name):
                self._assert_ignored(data, error)

    #: A well-formed record's three fields.
    GOOD = ("2026-10-21T08:30:00Z", "HTTP 429", "2026-10-22T08:30:00Z")

    def test_each_malformed_shape_is_refused(self) -> None:
        good = self.GOOD
        cases = {
            "a list": b"[]",
            "a missing field": json.dumps({"what": "HTTP 429"}).encode(),
            "an extra field": json.dumps(
                {"refused_at": good[0], "what": good[1], "until": good[2], "x": 1}
            ).encode(),
            "a time not text": json.dumps(
                {"refused_at": 1, "what": good[1], "until": good[2]}
            ).encode(),
            "a loose time": record("2026-10-21T8:30:00Z", good[1], good[2]),
            "an empty what": record(good[0], "", good[2]),
            "a long what": record(good[0], "x" * 101, good[2]),
            "an unprintable what": record(good[0], "HTTP\n429", good[2]),
            "a what not text": json.dumps(
                {"refused_at": good[0], "what": 429, "until": good[2]}
            ).encode(),
        }
        for name, data in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                lyc.parse_cooldown(data, NOW)
        self.assertEqual(lyc.parse_cooldown(record(*good), NOW).what, "HTTP 429")

    def test_a_deeply_nested_file_is_refused_as_malformed(self) -> None:
        with self.assertRaises(ValueError):
            lyc.parse_cooldown(NESTED, NOW)
        self._assert_ignored(NESTED, "ValueError")

    def test_a_file_over_the_size_bar_is_refused_before_decoding(self) -> None:
        padded = record(*self.GOOD) + b" " * lyc.MAX_COOLDOWN_BYTES
        with mock.patch.object(lyc.json, "loads") as loads:
            with self.assertRaises(ValueError):
                lyc.parse_cooldown(padded, NOW)
        loads.assert_not_called()

    def test_an_oversized_file_is_read_no_further_than_one_byte_past_the_cap(
        self,
    ) -> None:
        path = self.tmp / "youtube_music.json"
        path.write_bytes(b" " * (lyc.MAX_COOLDOWN_BYTES * 4))
        seen: list[int] = []

        def recording(data: bytes, now: datetime) -> lyc.CoolDown:
            del now  # only the size handed over matters
            seen.append(len(data))
            raise ValueError("refused")

        with mock.patch.object(lyc, "parse_cooldown", recording):
            result = lyc.read_cooldown(path, NOW)
        self.assertIsNone(result.cool_down)
        self.assertEqual(seen, [lyc.MAX_COOLDOWN_BYTES + 1])

    def test_read_state_reads_one_byte_past_the_cap_it_is_given(self) -> None:
        path = self.tmp / "state.json"
        path.write_bytes(b"x" * 64)
        state, warning = lyc.read_state(path, len, "state", max_bytes=10)
        self.assertEqual((state, warning), (11, None))

    def test_a_recursion_error_while_decoding_is_a_value_error(self) -> None:
        with mock.patch.object(lyc.json, "loads", side_effect=RecursionError):
            with self.assertRaises(ValueError):
                lyc.parse_cooldown(b"[]", NOW)

    def test_an_unreadable_file_is_ignored(self) -> None:
        folder = self.tmp / "youtube_music.json"
        folder.mkdir()
        result = lyc.read_cooldown(folder, NOW)
        self.assertIsNone(result.cool_down)
        self.assertIn("cool-down file ignored", result.warning or "")


class PropertyTest(fixtures.IsolatedCase):
    """The parser raises nothing but ValueError, whatever bytes it is handed."""

    def _assert_parses_or_refuses(self, data: bytes) -> None:
        """*data* is refused with a ValueError, or is a plausible cool-down."""
        try:
            parsed = lyc.parse_cooldown(data, NOW)
        except ValueError:
            return
        self.assertLessEqual(parsed.refused_at, NOW)
        self.assertEqual(parsed.until - parsed.refused_at, lyc.COOL_DOWN)

    @given(st.binary(max_size=200))
    def test_fuzz_parse_cooldown(self, data: bytes) -> None:
        self._assert_parses_or_refuses(data)

    @given(_JSON)
    def test_json_values_parse_or_are_refused(self, value: object) -> None:
        self._assert_parses_or_refuses(http_fakes.json_body(value))


class RecordStopTest(fixtures.IsolatedCase):
    """A stop writes its source's cool-down; a write that fails still stops, and says why."""

    def test_a_stop_writes_a_file_that_reads_back(self) -> None:
        path = self.tmp / "state" / "tekstowo.json"
        stop = lyc.record_stop(path, REFUSED, "HTTP 403")
        self.assertEqual(stop, lyc.Stop("HTTP 403", REFUSED + lyc.COOL_DOWN))
        cool_down = lyc.read_cooldown(path, NOW).cool_down
        assert cool_down is not None
        self.assertEqual((cool_down.what, cool_down.until), ("HTTP 403", stop.until))

    def test_a_failed_write_still_stops_and_keeps_the_error(self) -> None:
        path = self.tmp / "tekstowo.json"
        failing = mock.patch.object(lyc, "write_cooldown", side_effect=OSError("full"))
        with failing:
            stop = lyc.record_stop(path, REFUSED, "HTTP 429")
        self.assertEqual(stop.until, REFUSED + lyc.COOL_DOWN)
        self.assertEqual(stop.write_error, "full")
        self.assertFalse(path.exists())


@dataclass
class _Stopping:
    """A provider that can say what stopped it, standing in for a cooling source."""

    stop: lyc.Stop | None = None

    def __call__(self, query: lm.TrackQuery) -> str | None:  # pragma: no cover
        # Never called: the table is only read for its stop.
        """Nothing found."""
        del query
        return None

    def provider_stop(self) -> lyc.Stop | None:
        """What stopped it, or None."""
        return self.stop


class TableStopTest(fixtures.IsolatedCase):
    """A source's stop is read from its provider in the table, under any of its keys."""

    STOP = lyc.Stop("HTTP 403", NOW + lyc.COOL_DOWN)
    KEY = lm.ProviderKey(TEKSTOWO, lm.LyricsKind.UNSYNCED)

    def test_a_stop_under_an_untimed_key_alone_is_found(self) -> None:
        self.assertIs(
            lyc.table_stop({self.KEY: _Stopping(self.STOP)}, TEKSTOWO), self.STOP
        )

    def test_a_source_absent_from_the_table_is_no_stop(self) -> None:
        self.assertIsNone(lyc.table_stop({self.KEY: _Stopping(self.STOP)}, YTM))

    def test_a_provider_that_cannot_report_is_no_stop(self) -> None:
        self.assertIsNone(lyc.table_stop({self.KEY: fixtures.empty()}, TEKSTOWO))
