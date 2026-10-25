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

import at_engine
import mutagen
from sy_request import Reply, ReplyError, Request, dump_request, load_reply

CHILD: Final = Path(__file__).resolve().with_name("sy_child.py")
REQUEST_NAME: Final = "request.json"
REPLY_NAME: Final = "reply.json"
#: Loading both models takes most of a minute on a cold start.
CHILD_BASE_S: Final = 60.0
#: Timing ran at about 0.2 times the track's length; twice its length leaves ten times that.
CHILD_PER_SECOND: Final = 2.0
#: A track whose length cannot be read is given the bound of an hour-long one.
UNKNOWN_LENGTH_S: Final = 3600.0


@dataclass(frozen=True)
class Aligned:
    """The child's checked reply, or None with what went wrong."""

    reply: Reply | None
    detail: tuple[str, ...] = ()


def track_seconds(audio: Path) -> float | None:
    """*audio*'s length in seconds as its tags say, or None when it cannot be read."""
    try:
        found = mutagen.File(audio)
    except (mutagen.MutagenError, OSError):
        return None
    length = getattr(getattr(found, "info", None), "length", None)
    return float(length) if isinstance(length, (int, float)) and length > 0 else None


def child_timeout(seconds: float | None) -> float:
    """The child's bound: a minute, plus twice the track's length."""
    return CHILD_BASE_S + CHILD_PER_SECOND * (
        UNKNOWN_LENGTH_S if seconds is None else seconds
    )


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


def align(
    request: Request,
    workdir: Path,
    run: at_engine.Runner,
    seconds: float | None,
) -> Aligned:
    """Run the child on *request* inside *workdir*; its reply, or why there is none."""
    (workdir / REQUEST_NAME).write_text(
        dump_request(request), encoding="utf-8", newline="\n"
    )
    try:
        completed = run(child_argv(workdir), timeout=child_timeout(seconds))
    except subprocess.TimeoutExpired:
        return Aligned(None, ("timed out",))
    except OSError as error:
        return Aligned(None, (f"could not start: {error}",))
    if completed.returncode != 0:
        tail = at_engine.stderr_tail(completed.stderr)
        return Aligned(None, (f"exit {completed.returncode}", *tail))
    try:
        text = (workdir / REPLY_NAME).read_text(encoding="utf-8")
        return Aligned(load_reply(text, len(request.ids)))
    except (OSError, ReplyError) as error:
        return Aligned(None, (f"unreadable reply: {error}",))
