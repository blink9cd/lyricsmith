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
from sy_request import Reply, ReplyError, Request, dump_request, load_reply
from sy_types import MAX_TRACK_S

CHILD: Final = Path(__file__).resolve().with_name("sy_child.py")
REQUEST_NAME: Final = "request.json"
REPLY_NAME: Final = "reply.json"
#: Loading both models takes most of a minute on a cold start.
CHILD_BASE_S: Final = 60.0
#: Timing ran at about 0.2 times the track's length; twice its length leaves ten times that.
CHILD_PER_SECOND: Final = 2.0


@dataclass(frozen=True)
class Aligned:
    """The child's checked reply, or None with what went wrong."""

    reply: Reply | None
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


def child_timeout(seconds: float | None) -> float:
    """The child's bound: a minute, plus twice the track's length.

    The length is the tags' claim, so it counts for no more than `MAX_TRACK_S`, which
    is also the length assumed when there is none to read: a crafted header cannot
    stretch the watchdog past the longest track's bound.
    """
    length = MAX_TRACK_S if seconds is None else min(seconds, MAX_TRACK_S)
    return CHILD_BASE_S + CHILD_PER_SECOND * length


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
