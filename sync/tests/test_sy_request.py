"""The child's request and reply files: round trips, and every malformed reply refused."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import Heard  # noqa: E402 - import follows the sys.path bootstrap above
from sy_lines import Span  # noqa: E402 - import follows the sys.path bootstrap above
from sy_text import NO_LINE  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard_store import (  # noqa: E402 - import follows the sys.path bootstrap above
    MAX_TRANSCRIPT_BYTES,
)

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")
#: What whisper-cli heard of a text it can judge, and of a text it found not sung.
SUNG = Heard(30, 0.6)
UNSUNG = Heard(30, 0.1)
#: What whisper-cli heard of a not-sung text: invented words.
HEARD_TEXT = "a kettle sings in the corner of the room"
#: A transcript one byte over the store's cap.
TOO_LONG_TEXT = "a" * (MAX_TRANSCRIPT_BYTES + 1)
GOOD = rq.Reply(rq.STATUS_OK, 10, 0.02, (Span(0, 2, 0.5), Span(3, 4, 1.0)), SUNG)
#: A request for *ids* naming every file it needs.
REQUEST = rq.Request(
    Path("a.mp3"),
    (2, 1, 3),
    Path("models"),
    (0, NO_LINE, 1),
    Path("bin/whisper-cli"),
    Path("whisper"),
)
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
    {"heard_pairs": -1},
    {"heard_pairs": True},
    {"heard_pairs": 2.0},
    {"heard_share": 1.5},
    {"heard_share": -0.1},
    {"heard_share": True},
    {"heard_share": "0.5"},
    {"heard_share": float("nan")},
    # A text whisper found not sung is never timed.
    {"heard_share": UNSUNG.share},
)
#: The fields every request names, as the parent writes them.
FIELDS = '"audio": "a", "model_dir": "m", "whisper_cli": "w", "whisper_model_dir": "d"'

#: Requests each missing or mistyping a field.
BAD_REQUESTS = (
    "[]",
    '{"audio": 1, "ids": [], "model_dir": "m", "line_of": []}',
    "{" + FIELDS + ', "ids": [true], "line_of": [0]}',
    "{" + FIELDS + ', "ids": [2]}',
    "{" + FIELDS + ', "ids": 2, "line_of": [0]}',
    "{" + FIELDS + ', "ids": [2], "line_of": {"a": 0}}',
    "{" + FIELDS + ', "ids": [2], "line_of": [true]}',
    "{" + FIELDS + ', "ids": [2], "line_of": [0.5]}',
    "{" + FIELDS + ', "ids": [2, 3], "line_of": [0]}',
    "{" + FIELDS + ', "ids": [2], "line_of": [-2]}',
    '{"audio": "a", "model_dir": "m", "whisper_cli": "w", "ids": [2], "line_of": [0]}',
    '{"audio": "a", "model_dir": "m", "whisper_model_dir": "d", "ids": [], "line_of": []}',
    "{" + FIELDS.replace('"w"', "7") + ', "ids": [], "line_of": []}',
)
#: Any token sequence's line map: a line index, or a separator's `NO_LINE`.
LINE_OF = hs.lists(hs.integers(NO_LINE, 50), max_size=20)


class RequestTest(sy_cases.IsolatedCase):
    def test_a_request_round_trips(self) -> None:
        self.assertEqual(rq.load_request(rq.dump_request(REQUEST)), REQUEST)

    def test_a_request_carries_each_tokens_line(self) -> None:
        self.assertEqual(json.loads(rq.dump_request(REQUEST))["line_of"], [0, -1, 1])

    def test_a_request_names_whisper_cli_and_its_models(self) -> None:
        dumped = json.loads(rq.dump_request(REQUEST))
        named = (dumped["whisper_cli"], dumped["whisper_model_dir"])
        self.assertEqual(named, (str(Path("bin/whisper-cli")), "whisper"))

    @given(LINE_OF)
    def test_fuzz_load_request(self, line_of: list[int]) -> None:
        # Every request with any token-to-line map loads back as it was dumped.
        ids = tuple(2 for _ in line_of)
        request = dataclasses.replace(REQUEST, ids=ids, line_of=tuple(line_of))
        self.assertEqual(rq.load_request(rq.dump_request(request)), request)

    def test_a_reply_round_trips(self) -> None:
        self.assertEqual(rq.load_reply(rq.dump_reply(GOOD), 2), GOOD)

    def test_too_long_carries_no_spans(self) -> None:
        reply = rq.Reply(rq.STATUS_TOO_LONG, 3, 0.02, (), SUNG)
        self.assertEqual(rq.load_reply(rq.dump_reply(reply), 9), reply)

    def test_a_reply_of_no_frames_is_not_negative(self) -> None:
        reply = rq.Reply(rq.STATUS_TOO_LONG, 0, 0.02, (), SUNG)
        self.assertEqual(rq.load_reply(rq.dump_reply(reply), 9), reply)

    def test_a_reply_that_could_not_judge_the_text_still_times_it(self) -> None:
        little = rq.Reply(rq.STATUS_OK, 10, 0.02, GOOD.spans, Heard(3, 0.0))
        self.assertEqual(rq.load_reply(rq.dump_reply(little), 2), little)

    def test_a_not_sung_reply_round_trips_with_what_was_heard(self) -> None:
        reply = rq.NotSung(UNSUNG, HEARD_TEXT)
        self.assertEqual(rq.load_reply(rq.dump_reply(reply), 9), reply)

    def test_a_not_sung_reply_carries_its_transcript(self) -> None:
        dumped = json.loads(rq.dump_reply(rq.NotSung(UNSUNG, HEARD_TEXT)))
        self.assertEqual(dumped["transcript"], HEARD_TEXT)

    def test_a_not_sung_reply_with_a_transcript_it_cannot_keep_is_refused(
        self,
    ) -> None:
        good = json.loads(rq.dump_reply(rq.NotSung(UNSUNG, HEARD_TEXT)))
        for transcript in (7, ["a"], TOO_LONG_TEXT):
            with self.subTest(transcript=str(transcript)[:10]):
                with self.assertRaisesRegex(rq.ReplyError, "transcript"):
                    rq.load_reply(json.dumps(good | {"transcript": transcript}), 9)
        del good["transcript"]
        with self.assertRaisesRegex(rq.ReplyError, "transcript"):
            rq.load_reply(json.dumps(good), 9)

    def test_a_not_sung_reply_too_long_to_keep_round_trips_as_null(self) -> None:
        # A child that heard more than the store keeps says so with null: the text
        # is still not sung, so the reply is no error.
        reply = rq.NotSung(UNSUNG, None)
        self.assertIsNone(json.loads(rq.dump_reply(reply))["transcript"])
        self.assertEqual(rq.load_reply(rq.dump_reply(reply), 9), reply)

    def test_a_transcript_at_the_cap_is_kept(self) -> None:
        reply = rq.NotSung(UNSUNG, "a" * MAX_TRANSCRIPT_BYTES)
        self.assertEqual(rq.load_reply(rq.dump_reply(reply), 9), reply)

    def test_a_request_carries_a_kept_transcript_or_none(self) -> None:
        self.assertIsNone(json.loads(rq.dump_request(REQUEST))["transcript"])
        kept = dataclasses.replace(REQUEST, transcript=HEARD_TEXT)
        self.assertEqual(rq.load_request(rq.dump_request(kept)), kept)

    def test_a_request_whose_transcript_it_cannot_keep_is_refused(self) -> None:
        good = json.loads(rq.dump_request(REQUEST))
        for transcript in (7, ["a"], TOO_LONG_TEXT):
            with self.subTest(transcript=str(transcript)[:10]):
                with self.assertRaisesRegex(rq.ReplyError, "transcript"):
                    rq.load_request(json.dumps(good | {"transcript": transcript}))

    def test_a_not_sung_reply_whose_text_was_heard_is_refused(self) -> None:
        for heard in (SUNG, Heard(3, 0.0)):
            with self.subTest(heard=heard):
                dumped = rq.dump_reply(rq.NotSung(heard, HEARD_TEXT))
                with self.assertRaisesRegex(rq.ReplyError, "not sung"):
                    rq.load_reply(dumped, 9)

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
        # The request's loader is held to the same bar: refused, never a crash.
        for load in (lambda: rq.load_reply(text, 2), lambda: rq.load_request(text)):
            try:
                load()
            except rq.ReplyError:
                pass

    def test_deep_nesting_is_refused_not_a_crash(self) -> None:
        with self.assertRaises(rq.ReplyError):
            rq.load_reply("[" * 100_000, 2)
