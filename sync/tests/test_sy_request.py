"""The child's request and reply files: round trips, and every malformed reply refused."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
from sy_lines import Span  # noqa: E402 - import follows the sys.path bootstrap above

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")
GOOD = rq.Reply(rq.STATUS_OK, 10, 0.02, (Span(0, 2, 0.5), Span(3, 4, 1.0)))
#: Each change that makes the good reply malformed.
MALFORMED: tuple[dict[str, object], ...] = (
    {"status": "maybe"},
    {"frames": True},
    {"frames": -1},
    {"frame_seconds": 0},
    {"frame_seconds": True},
    {"frame_seconds": float("nan")},
    {"spans": {"a": 1}},
    {"spans": [[0, 2, 0.5]]},
    {"spans": [[0, 2, 0.5], [1, 4, 1.0]]},
    {"spans": [[0, 2, 0.5], [3, 11, 1.0]]},
    {"spans": [[0, 2, 0.5], [3, 3, 1.0]]},
    {"spans": [[0, 2, 1.5], [3, 4, 1.0]]},
    {"spans": [[0, 2, True], [3, 4, 1.0]]},
    {"spans": [[0, 2], [3, 4, 1.0]]},
)
#: Requests each missing or mistyping a field.
BAD_REQUESTS = (
    "[]",
    '{"audio": 1, "ids": [], "model_dir": "m"}',
    '{"audio": "a", "ids": [true], "model_dir": "m"}',
)


class RequestTest(sy_cases.IsolatedCase):
    def test_a_request_round_trips(self) -> None:
        request = rq.Request(Path("a.mp3"), (2, 1, 3), Path("models"))
        self.assertEqual(rq.load_request(rq.dump_request(request)), request)

    def test_a_reply_round_trips(self) -> None:
        self.assertEqual(rq.load_reply(rq.dump_reply(GOOD), 2), GOOD)

    def test_too_long_carries_no_spans(self) -> None:
        reply = rq.Reply(rq.STATUS_TOO_LONG, 3, 0.02, ())
        self.assertEqual(rq.load_reply(rq.dump_reply(reply), 9), reply)

    def test_a_reply_of_no_frames_is_not_negative(self) -> None:
        reply = rq.Reply(rq.STATUS_TOO_LONG, 0, 0.02, ())
        self.assertEqual(rq.load_reply(rq.dump_reply(reply), 9), reply)

    def test_each_malformed_reply_is_refused(self) -> None:
        good = json.loads(rq.dump_reply(GOOD))
        for change in MALFORMED:
            with self.subTest(change=change):
                with self.assertRaises(rq.ReplyError):
                    rq.load_reply(json.dumps(good | change), 2)

    def test_a_request_with_a_bad_field_is_refused(self) -> None:
        for text in BAD_REQUESTS:
            with self.subTest(text=text):
                with self.assertRaises(rq.ReplyError):
                    rq.load_request(text)

    @given(hs.text())
    def test_fuzz_load_reply(self, text: str) -> None:
        try:
            rq.load_reply(text, 2)
        except rq.ReplyError:
            pass

    def test_deep_nesting_is_refused_not_a_crash(self) -> None:
        with self.assertRaises(rq.ReplyError):
            rq.load_reply("[" * 100_000, 2)
