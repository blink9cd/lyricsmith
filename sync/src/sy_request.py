"""The request a track's child process reads and the reply it writes: JSON files, checked.

The parent writes the request: the track, its token ids, the model folder. The child
writes the reply: whether the text fits, the frame count, each frame's length in
seconds, and one span per token. Every field of a reply is checked before the parent
trusts it, so a child that dies half-way or writes nonsense is that track's error.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sy_lines import Span

STATUS_OK: Final = "ok"
#: The text needs more frames than the track has: CTC cannot place it.
STATUS_TOO_LONG: Final = "too_long"


class ReplyError(ValueError):
    """A request or reply file that does not hold what it must."""


@dataclass(frozen=True)
class Request:
    """What the child times: the track, its token ids, and where the models are."""

    audio: Path
    ids: tuple[int, ...]
    model_dir: Path


@dataclass(frozen=True)
class Reply:
    """What the child found: the status, the frames, and one span per token when ok."""

    status: str
    frames: int
    frame_seconds: float
    spans: tuple[Span, ...]


def _object(text: str) -> dict[str, object]:
    """*text* as a JSON object; refused when it is anything else or too deeply nested."""
    try:
        value = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise ReplyError(f"not JSON: {error}") from error
    if not isinstance(value, dict):
        raise ReplyError("not a JSON object")
    return value


def _integer(value: object, name: str) -> int:
    """*value* as an int that is not a bool."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReplyError(f"{name} is not an integer")
    return value


def dump_request(request: Request) -> str:
    """*request* as JSON."""
    return json.dumps(
        {
            "audio": str(request.audio),
            "ids": list(request.ids),
            "model_dir": str(request.model_dir),
        }
    )


def load_request(text: str) -> Request:
    """The request in *text*; raises `ReplyError` when a field is missing or mistyped."""
    data = _object(text)
    audio, ids, model_dir = data.get("audio"), data.get("ids"), data.get("model_dir")
    if (
        not isinstance(audio, str)
        or not isinstance(model_dir, str)
        or not isinstance(ids, list)
    ):
        raise ReplyError("a request needs audio, ids and model_dir")
    return Request(
        Path(audio), tuple(_integer(i, "an id") for i in ids), Path(model_dir)
    )


def dump_reply(reply: Reply) -> str:
    """*reply* as JSON."""
    return json.dumps(
        {
            "status": reply.status,
            "frames": reply.frames,
            "frame_seconds": reply.frame_seconds,
            "spans": [[span.start, span.end, span.score] for span in reply.spans],
        }
    )


def _span(value: object, frames: int, after: int) -> Span:
    """One checked span: inside the track's frames, starting at or after *after*."""
    if not isinstance(value, list) or len(value) != 3:
        raise ReplyError("a span is not [start, end, score]")
    start, end = _integer(value[0], "a span start"), _integer(value[1], "a span end")
    score = value[2]
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not 0.0 <= score <= 1.0
    ):
        raise ReplyError("a span score is not a probability")
    if not after <= start < end <= frames:
        raise ReplyError("a span is out of order or outside the track")
    return Span(start, end, float(score))


def _spans(value: object, frames: int) -> tuple[Span, ...]:
    """The reply's spans, each checked against the last."""
    if not isinstance(value, list):
        raise ReplyError("spans is not a list")
    spans: list[Span] = []
    for item in value:
        spans.append(_span(item, frames, spans[-1].end if spans else 0))
    return tuple(spans)


# fuzz-target
def load_reply(text: str, token_count: int) -> Reply:
    """The reply in *text* to a request of *token_count* tokens; raises `ReplyError`."""
    data = _object(text)
    status, frame_seconds = data.get("status"), data.get("frame_seconds")
    if status not in (STATUS_OK, STATUS_TOO_LONG):
        raise ReplyError("unknown status")
    frames = _integer(data.get("frames"), "frames")
    if frames < 0:
        raise ReplyError("frames is negative")
    if isinstance(frame_seconds, bool) or not isinstance(frame_seconds, (int, float)):
        raise ReplyError("frame_seconds is not a number")
    if not math.isfinite(frame_seconds) or frame_seconds <= 0:
        raise ReplyError("frame_seconds is not a positive number")
    spans = _spans(data.get("spans"), frames)
    expected = token_count if status == STATUS_OK else 0
    if len(spans) != expected:
        raise ReplyError(f"{len(spans)} spans for {expected} tokens")
    return Reply(str(status), frames, float(frame_seconds), spans)
