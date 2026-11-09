"""One track's child process: its arguments, its bounded run, and the reply it leaves.

PyTorch's calls take no timeout, so the child process is the watchdog: the transcribe
tool's bounded runner kills it at the deadline. The request and reply are files in a
work folder the caller owns and removes.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
from lyrics_files import open_regular
import at_engine
import mutagen
from at_models import WHISPER_MODEL_NAME
from sy_request import (
    HeardOnly,
    NotSung,
    Reply,
    ReplyError,
    Request,
    dump_request,
    load_heard,
    load_reply,
)
from sy_types import MAX_TRACK_S
from sy_whisper import whisper_timeout

CHILD: Final = Path(__file__).resolve().with_name("sy_child.py")
REQUEST_NAME: Final = "request.json"
REPLY_NAME: Final = "reply.json"
#: Loading both models takes most of a minute on a cold start.
CHILD_BASE_S: Final = 60.0
#: Timing ran at about 0.2 times the track's length; twice its length leaves ten times that.
CHILD_PER_SECOND: Final = 2.0
#: A hearing child only separates before it hears: no wav2vec2 to load or score with,
#: and nothing aligned. Separation took a median 59 s a track on the test library,
#: whose median track whisper-cli's 7.8 s at 0.036 of its length puts at about 3.6
#: minutes, so about 0.27 of the length: the length again leaves near four times that.
SEPARATE_PER_SECOND: Final = 1.0


@dataclass(frozen=True)
class Aligned:
    """The child's checked reply, or None with what went wrong."""

    reply: Reply | NotSung | None
    detail: tuple[str, ...] = ()


@dataclass(frozen=True)
class HeardBack:
    """A hearing child's checked reply, or None with what went wrong."""

    reply: HeardOnly | None
    detail: tuple[str, ...] = ()


def track_seconds(audio: Path) -> float | None:
    """*audio*'s length in seconds as its tags say, or None when it cannot be read.

    Opened through album's `lyrics_files.open_regular`, and mutagen handed the open
    file, so a FIFO pipe or a device under the name is no length at once, never
    waited on.
    """
    try:
        with open_regular(audio, "rb") as stream:
            found = mutagen.File(stream)
    except (mutagen.MutagenError, OSError):
        return None
    length = getattr(getattr(found, "info", None), "length", None)
    return float(length) if isinstance(length, (int, float)) and length > 0 else None


def child_timeout(
    seconds: float | None, *, align: bool = True, model: str = WHISPER_MODEL_NAME
) -> float:
    """The child's bound: a minute plus twice the track's length, then whisper-cli's.

    whisper-cli's own bound in the child, for its *model*, is added on top, so the
    time left to separate, score and align is what it was before the sung check. A
    child that does not *align* only separates before it hears: its budget is the
    length once (`SEPARATE_PER_SECOND`), not twice. The length is the tags' claim, so
    it counts for no more than `MAX_TRACK_S`, which is also the length assumed when
    there is none to read: a crafted header cannot stretch the watchdog past the
    longest track's bound.
    """
    length = MAX_TRACK_S if seconds is None else min(seconds, MAX_TRACK_S)
    per_second = CHILD_PER_SECOND if align else SEPARATE_PER_SECOND
    timing = CHILD_BASE_S + per_second * length
    return timing + whisper_timeout(length, model)


def child_argv(workdir: Path) -> list[str]:
    """The child's command line, under this same interpreter."""
    return [
        sys.executable,
        str(CHILD),
        "--request",
        str(workdir / REQUEST_NAME),
        "--reply",
        str(workdir / REPLY_NAME),
    ]


def _reply_text(
    request: Request, workdir: Path, run: at_engine.Runner, seconds: float | None
) -> tuple[str | None, tuple[str, ...]]:
    """Run the child on *request* inside *workdir*; its reply's text, or why none.

    Bounded by `child_timeout` for the request's alignment and whisper model.
    """
    (workdir / REQUEST_NAME).write_text(
        dump_request(request), encoding="utf-8", newline="\n"
    )
    bound = child_timeout(seconds, align=request.align, model=request.whisper_model)
    try:
        completed = run(child_argv(workdir), timeout=bound)
    except subprocess.TimeoutExpired:
        return None, ("timed out",)
    except OSError as error:
        return None, (f"could not start: {error}",)
    if completed.returncode != 0:
        tail = at_engine.stderr_tail(completed.stderr)
        return None, (f"exit {completed.returncode}", *tail)
    try:
        return (workdir / REPLY_NAME).read_text(encoding="utf-8"), ()
    except OSError as error:
        return None, (f"unreadable reply: {error}",)


def align(
    request: Request,
    workdir: Path,
    run: at_engine.Runner,
    seconds: float | None,
) -> Aligned:
    """Run the child on *request* inside *workdir*; its reply, or why there is none."""
    text, detail = _reply_text(request, workdir, run, seconds)
    if text is None:
        return Aligned(None, detail)
    try:
        return Aligned(load_reply(text, len(request.ids)))
    except ReplyError as error:
        return Aligned(None, (f"unreadable reply: {error}",))


def hear(
    request: Request,
    workdir: Path,
    run: at_engine.Runner,
    seconds: float | None,
) -> HeardBack:
    """Run the hearing child on *request* inside *workdir*; what it heard, or why not.

    *request* asks the child not to align, so its reply is a hearing's alone.
    """
    text, detail = _reply_text(request, workdir, run, seconds)
    if text is None:
        return HeardBack(None, detail)
    try:
        return HeardBack(load_heard(text))
    except ReplyError as error:
        return HeardBack(None, (f"unreadable reply: {error}",))
