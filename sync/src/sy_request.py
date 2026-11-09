"""The request a track's child process reads and the reply it writes: JSON files, checked.

The parent writes the request: the track, its token ids, the model folder,
whisper-cli with its models' folder, and the transcript kept from an earlier run, if
any, which the child judges instead of running whisper-cli. The child writes the reply:
what whisper-cli heard of the text, then whether the text fits, the frame count, each
frame's length in seconds, and one span per token; or, for a text it did not hear sung,
what it heard and the transcript, which the parent may keep, or null when the
transcript is longer than the store keeps. A transcript sent is refused unless it is
text the store can keep (`lyrics_heard_store.transcript_fits`). Every field of a reply
is checked before the parent trusts it, so a child that dies half-way or writes nonsense
is that track's error, and a reply timing a text the verdict calls not sung is refused,
as is a not-sung reply whose verdict says otherwise.

**A hearing.** A request with *align* false asks the child to stop once it has heard:
it replies `HeardOnly`, the verdict, what was heard and the transcript, for a text sung,
not sung or too little heard alike, read by `load_heard`, which refuses a status the
verdict does not reach, as `load_reply` does. A request also names the whisper model it
hears with, one of the transcribe tool's pinned two.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

import sy_located  # noqa: F401 - puts album's modules on sys.path
from at_models import WHISPER_MODEL_NAME, WHISPER_MODEL_NAMES
from lyrics_heard import Heard, little_heard, not_sung
from lyrics_hearing_log import HeardVerdict
from lyrics_heard_store import MAX_TRANSCRIPT_BYTES, transcript_fits
from sy_lines import Span
from sy_text import NO_LINE

STATUS_OK: Final = "ok"
#: The text needs more frames than the track has: CTC cannot place it.
STATUS_TOO_LONG: Final = "too_long"
#: whisper-cli heard the vocals, and too little of it was the text: nothing is aligned.
STATUS_NOT_SUNG: Final = "not_sung"
#: A hearing's statuses, one a verdict: it aligned nothing, whatever it found.
HEARD_STATUSES: Final = MappingProxyType(
    {
        "heard_sung": HeardVerdict.SUNG,
        "heard_not_sung": HeardVerdict.NOT_SUNG,
        "heard_little": HeardVerdict.LITTLE_HEARD,
    }
)
#: Each verdict's status, the other way round.
_STATUS_OF: Final = MappingProxyType({v: k for k, v in HEARD_STATUSES.items()})


class ReplyError(ValueError):
    """A request or reply file that does not hold what it must."""


@dataclass(frozen=True)
class Request:
    """What the child times: the track, its token ids, the models, each token's line.

    A separator's line is `NO_LINE`. The child reads the second line's start from the
    lines before it looks for a break after it. It hears the vocals with *whisper_cli*,
    absolute, and the whisper model *whisper_model* in *whisper_model_dir*, unless
    *transcript* holds what whisper-cli heard of the track before: then that is
    judged, and nothing heard. With *align* false it stops once it has heard.
    """

    audio: Path
    ids: tuple[int, ...]
    model_dir: Path
    line_of: tuple[int, ...]
    whisper_cli: Path
    whisper_model_dir: Path
    transcript: str | None = None
    align: bool = True
    whisper_model: str = WHISPER_MODEL_NAME


@dataclass(frozen=True)
class Reply:
    """What the child found: the status, the frames, one span per token when ok.

    *heard* is what whisper-cli heard of the text, which did not find it not sung.
    """

    status: str
    frames: int
    frame_seconds: float
    spans: tuple[Span, ...]
    heard: Heard


@dataclass(frozen=True)
class NotSung:
    """The reply for a text whisper-cli did not hear sung: nothing was aligned.

    *transcript* is what whisper-cli heard, which the parent may keep; None when it is
    longer than the store keeps, which leaves the verdict standing.
    """

    heard: Heard
    transcript: str | None


@dataclass(frozen=True)
class HeardOnly:
    """The reply to a hearing: the verdict, what was heard, and the transcript.

    *transcript* is None when it is longer than the store keeps, as a `NotSung`'s is.
    """

    verdict: HeardVerdict
    heard: Heard
    transcript: str | None


def verdict_of(heard: Heard) -> HeardVerdict:
    """What *heard* found, by `lyrics_heard`'s rule: too little to judge, not sung, sung."""
    if little_heard(heard):
        return HeardVerdict.LITTLE_HEARD
    return HeardVerdict.NOT_SUNG if not_sung(heard) else HeardVerdict.SUNG


def heard_only(heard: Heard, transcript: str | None) -> HeardOnly:
    """The reply to a hearing that heard *heard*, its verdict reached by the rule."""
    return HeardOnly(verdict_of(heard), heard, transcript)


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
            "line_of": list(request.line_of),
            "whisper_cli": str(request.whisper_cli),
            "whisper_model_dir": str(request.whisper_model_dir),
            "transcript": request.transcript,
            "align": request.align,
            "whisper_model": request.whisper_model,
        }
    )


# fuzz-target
def load_request(text: str) -> Request:
    """The request in *text*; raises `ReplyError` when a field is missing or mistyped."""
    data = _object(text)
    ids = data.get("ids")
    audio, model_dir, cli, whisper = (
        data.get(name)
        for name in ("audio", "model_dir", "whisper_cli", "whisper_model_dir")
    )
    if not all(isinstance(path, str) for path in (audio, model_dir, cli, whisper)):
        raise ReplyError("a request needs audio, model_dir, whisper_cli and its models")
    if not isinstance(ids, list):
        raise ReplyError("a request needs ids")
    checked = tuple(_integer(i, "an id") for i in ids)
    line_of = _line_of(data.get("line_of"), checked)
    kept = data.get("transcript")
    return Request(
        Path(str(audio)),
        checked,
        Path(str(model_dir)),
        line_of,
        Path(str(cli)),
        Path(str(whisper)),
        None if kept is None else _transcript(kept),
        _align(data.get("align")),
        _whisper_model(data.get("whisper_model")),
    )


def _align(value: object) -> bool:
    """*value* as the request's align flag: a bool, never a stand-in for one."""
    if not isinstance(value, bool):
        raise ReplyError("a request needs align, true or false")
    return value


def _whisper_model(value: object) -> str:
    """*value* as one of the pinned whisper models' file names, never another name."""
    if not isinstance(value, str) or value not in WHISPER_MODEL_NAMES:
        raise ReplyError("a request's whisper_model is not a pinned whisper model")
    return value


def _transcript(value: object) -> str:
    """*value* as a transcript the store can keep; `ReplyError` when it is not one."""
    if not isinstance(value, str):
        raise ReplyError("the transcript is not text")
    if not transcript_fits(value):
        raise ReplyError(f"the transcript is over {MAX_TRANSCRIPT_BYTES} bytes")
    return value


def _sent_transcript(data: dict[str, object]) -> str | None:
    """A not-sung reply's transcript: null when too long to keep, else one it can keep."""
    if "transcript" not in data:
        raise ReplyError("a not-sung reply needs a transcript, or null")
    sent = data["transcript"]
    return None if sent is None else _transcript(sent)


def _line_of(value: object, ids: tuple[int, ...]) -> tuple[int, ...]:
    """The request's token-to-line map: one line index or `NO_LINE` a token."""
    if not isinstance(value, list):
        raise ReplyError("a request needs line_of")
    if len(value) != len(ids):
        raise ReplyError(f"{len(value)} lines for {len(ids)} tokens")
    lines = tuple(_integer(line, "a line") for line in value)
    if any(line < NO_LINE for line in lines):
        raise ReplyError("a line is negative")
    return lines


def _heard_fields(heard: Heard) -> dict[str, object]:
    """*heard* as the reply's two fields."""
    return {"heard_pairs": heard.pairs, "heard_share": heard.share}


def dump_reply(reply: Reply | NotSung | HeardOnly) -> str:
    """*reply* as JSON."""
    if not isinstance(reply, Reply):
        return json.dumps(
            {
                "status": (
                    _STATUS_OF[reply.verdict]
                    if isinstance(reply, HeardOnly)
                    else STATUS_NOT_SUNG
                ),
                **_heard_fields(reply.heard),
                "transcript": reply.transcript,
            }
        )
    return json.dumps(
        {
            "status": reply.status,
            "frames": reply.frames,
            "frame_seconds": reply.frame_seconds,
            "spans": [[span.start, span.end, span.score] for span in reply.spans],
            **_heard_fields(reply.heard),
        }
    )


def _heard(data: dict[str, object]) -> Heard:
    """The reply's verdict: a count of pairs heard, and a share of them in the text."""
    pairs, share = (
        _integer(data.get("heard_pairs"), "heard_pairs"),
        data.get("heard_share"),
    )
    if pairs < 0:
        raise ReplyError("heard_pairs is negative")
    if (
        isinstance(share, bool)
        or not isinstance(share, (int, float))
        or not 0.0 <= share <= 1.0
    ):
        raise ReplyError("heard_share is not a share")
    return Heard(pairs, float(share))


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
def load_reply(text: str, token_count: int) -> Reply | NotSung:
    """The reply in *text* to a request of *token_count* tokens; raises `ReplyError`."""
    data = _object(text)
    status = data.get("status")
    if status not in (STATUS_OK, STATUS_TOO_LONG, STATUS_NOT_SUNG):
        raise ReplyError("unknown status")
    heard = _heard(data)
    if (status == STATUS_NOT_SUNG) is not not_sung(heard):
        raise ReplyError(
            "the status and the verdict disagree on whether it is not sung"
        )
    if status == STATUS_NOT_SUNG:
        return NotSung(heard, _sent_transcript(data))
    return _timed(data, str(status), token_count, heard)


def _timed(
    data: dict[str, object], status: str, token_count: int, heard: Heard
) -> Reply:
    """The ok or too-long reply in *data*, its frames and spans checked."""
    frame_seconds = data.get("frame_seconds")
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
    return Reply(status, frames, float(frame_seconds), spans, heard)


# fuzz-target
def load_heard(text: str) -> HeardOnly:
    """The reply to a hearing in *text*; raises `ReplyError` when it is not one.

    Its status must be a hearing's, and the verdict the rule reaches on what was heard.
    """
    data = _object(text)
    verdict = HEARD_STATUSES.get(str(data.get("status")))
    if verdict is None:
        raise ReplyError("unknown status for a hearing")
    heard = _heard(data)
    if verdict_of(heard) is not verdict:
        raise ReplyError("the status and the verdict disagree on what was heard")
    return HeardOnly(verdict, heard, _sent_transcript(data))
