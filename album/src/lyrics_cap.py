"""The lyrics size cap, and the refusal of a track's own frame past it.

`MAX_LYRICS_CHARS` bounds every lyrics text a run takes: a provider's answer, a sidecar
and, here, a lyrics frame the track already holds. A frame over it is no lyrics to use:
`refuse_oversized_frames` raises `FrameTooLargeError` naming its kind, as reading a
sidecar over the cap raises, so a run records the track's error and takes nothing from
it, and no sidecar is written from it. The frame is still read and backed up verbatim,
so a strip or an undo handles it as any other.

A leaf below `lyrics_model`, which calls it from its one frame conversion, so it imports
nothing of the tool's at run time but the error. Pure: no I/O, no printing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from lyrics_errors import FrameTooLargeError

if TYPE_CHECKING:
    from lyrics_model import FrameRecord

#: The most characters of lyrics text a run accepts, from a provider, a sidecar or a
#: frame. A song's lyrics run to a few thousand; larger text is refused before any
#: parse, since the cleanup, LRC parse and check grow faster than the text does.
MAX_LYRICS_CHARS = 65_536


def frame_chars(frame: FrameRecord) -> int:
    """The characters *frame*'s lyrics take: a USLT's text, or each SYLT entry's line.

    A SYLT entry counts its text and the line break it becomes, so a frame of many
    empty entries is not counted as nothing.
    """
    payload = frame.payload
    if isinstance(payload, str):
        return len(payload)
    return sum(len(entry.text) + 1 for entry in payload)


def refuse_oversized_frames(frames: Iterable[FrameRecord]) -> None:
    """Raise `FrameTooLargeError` when any of *frames* is over `MAX_LYRICS_CHARS`."""
    for frame in frames:
        if frame_chars(frame) > MAX_LYRICS_CHARS:
            raise FrameTooLargeError(
                f"a {frame.kind} frame is too large: over {MAX_LYRICS_CHARS} characters"
            )
