"""The hearing request and its reply: a child asked not to align replies what it heard."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import Heard  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_hearing_log import HeardVerdict  # noqa: E402 - follows the bootstrap above
from lyrics_heard_store import MAX_TRANSCRIPT_BYTES  # noqa: E402 - follows the bootstrap
from sy_text import NO_LINE  # noqa: E402 - import follows the sys.path bootstrap above

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

REQUEST = rq.Request(
    Path("a.mp3"),
    (2, 1, 3),
    Path("models"),
    (0, NO_LINE, 1),
    Path("bin/whisper-cli"),
    Path("whisper"),
)
#: A hearing request: no alignment, and full large-v3.
HEARING = dataclasses.replace(
    REQUEST, align=False, whisper_model=at_models.LARGE_MODEL_NAME
)
#: What whisper-cli heard, invented: a text it found sung, one not, one too little.
SUNG = Heard(30, 0.6)
UNSUNG = Heard(30, 0.1)
LITTLE = Heard(12, 0.0)
TRANSCRIPT = "a kettle sings in the corner of the room"
#: Each verdict, a hearing that reached it, and the status its reply carries.
VERDICTS = (
    (HeardVerdict.SUNG, SUNG, "heard_sung"),
    (HeardVerdict.NOT_SUNG, UNSUNG, "heard_not_sung"),
    (HeardVerdict.LITTLE_HEARD, LITTLE, "heard_little"),
)


class HearingRequestTest(sy_cases.IsolatedCase):
    def test_a_request_aligns_with_the_turbo_unless_told_otherwise(self) -> None:
        self.assertTrue(REQUEST.align)
        self.assertEqual(REQUEST.whisper_model, at_models.WHISPER_MODEL_NAME)
        dumped = json.loads(rq.dump_request(REQUEST))
        self.assertEqual(
            (dumped["align"], dumped["whisper_model"]),
            (True, at_models.WHISPER_MODEL_NAME),
        )

    def test_a_hearing_request_round_trips(self) -> None:
        self.assertEqual(rq.load_request(rq.dump_request(HEARING)), HEARING)

    def test_a_request_without_its_align_or_model_is_refused(self) -> None:
        good = json.loads(rq.dump_request(HEARING))
        for name in ("align", "whisper_model"):
            with self.subTest(name=name):
                text = json.dumps({k: v for k, v in good.items() if k != name})
                with self.assertRaisesRegex(rq.ReplyError, name):
                    rq.load_request(text)

    def test_a_request_naming_no_pinned_whisper_model_is_refused(self) -> None:
        good = json.loads(rq.dump_request(HEARING))
        for model in ("ggml-tiny.bin", "../ggml-large-v3.bin", 3, None):
            with self.subTest(model=model):
                text = json.dumps(good | {"whisper_model": model})
                with self.assertRaisesRegex(rq.ReplyError, "whisper_model"):
                    rq.load_request(text)

    def test_an_align_that_is_not_a_bool_is_refused(self) -> None:
        good = json.loads(rq.dump_request(HEARING))
        for align in (0, 1, "false", None):
            with self.subTest(align=align):
                with self.assertRaisesRegex(rq.ReplyError, "align"):
                    rq.load_request(json.dumps(good | {"align": align}))


class HeardReplyTest(sy_cases.IsolatedCase):
    def test_each_verdict_round_trips_with_its_transcript(self) -> None:
        for verdict, heard, status in VERDICTS:
            with self.subTest(verdict=verdict):
                reply = rq.heard_only(heard, TRANSCRIPT)
                self.assertEqual(reply, rq.HeardOnly(verdict, heard, TRANSCRIPT))
                dumped = rq.dump_reply(reply)
                self.assertEqual(json.loads(dumped)["status"], status)
                self.assertEqual(rq.load_heard(dumped), reply)

    def test_a_transcript_too_long_to_keep_is_sent_as_null(self) -> None:
        reply = rq.HeardOnly(HeardVerdict.SUNG, SUNG, None)
        self.assertIsNone(json.loads(rq.dump_reply(reply))["transcript"])
        self.assertEqual(rq.load_heard(rq.dump_reply(reply)), reply)

    def test_a_status_the_verdict_does_not_reach_is_refused(self) -> None:
        for _, heard, _ in VERDICTS:
            for _, _, status in VERDICTS:
                reached = rq.dump_reply(rq.heard_only(heard, TRANSCRIPT))
                if json.loads(reached)["status"] == status:
                    continue
                with self.subTest(heard=heard, status=status):
                    text = json.dumps(json.loads(reached) | {"status": status})
                    with self.assertRaisesRegex(rq.ReplyError, "disagree"):
                        rq.load_heard(text)

    def test_an_aligning_reply_is_no_hearing_and_a_hearing_no_alignment(self) -> None:
        aligned = rq.dump_reply(rq.NotSung(UNSUNG, TRANSCRIPT))
        with self.assertRaisesRegex(rq.ReplyError, "status"):
            rq.load_heard(aligned)
        heard = rq.dump_reply(rq.heard_only(UNSUNG, TRANSCRIPT))
        with self.assertRaisesRegex(rq.ReplyError, "status"):
            rq.load_reply(heard, 3)

    def test_a_malformed_hearing_is_refused(self) -> None:
        good = json.loads(rq.dump_reply(rq.heard_only(SUNG, TRANSCRIPT)))
        changes: tuple[dict[str, object], ...] = (
            {"heard_pairs": -1},
            {"heard_pairs": True},
            {"heard_share": 1.5},
            {"transcript": 7},
            {"transcript": "a" * (MAX_TRANSCRIPT_BYTES + 1)},
        )
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(rq.ReplyError):
                    rq.load_heard(json.dumps(good | change))
        del good["transcript"]
        with self.assertRaisesRegex(rq.ReplyError, "transcript"):
            rq.load_heard(json.dumps(good))
        for text in ("[]", "not json"):
            with self.subTest(text=text), self.assertRaises(rq.ReplyError):
                rq.load_heard(text)

    @given(hs.text(max_size=200))
    def test_fuzz_load_heard(self, text: str) -> None:
        # Any text is a hearing or a ReplyError, never another exception.
        try:
            reply = rq.load_heard(text)
        except rq.ReplyError:
            return
        self.assertEqual(rq.load_heard(rq.dump_reply(reply)), reply)
