"""Each line's start, the track's confidence and the first-line check, from frame spans.

The child process returns one span per token: the frames it covers and its mean
probability. A line starts at its own first letter's first frame. Pure.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sy_text import NO_LINE

#: Below this median line confidence a track is left for timing by hand. Tracks that
#: aligned well scored medians of 0.27 to 0.68; one whose vocals the model could not
#: follow scored 0.08, with most of its lines below this floor.
CONFIDENCE_FLOOR: Final = 0.15
#: A first line spanning more than this many times the track's median line span is
#: flagged. A first line has no gap before it, so it can stretch back over the intro;
#: this flagged the one such line on the reference tracks and one more, and no others.
FIRST_LINE_FACTOR: Final = 2.0


@dataclass(frozen=True)
class Span:
    """One token's frames, [start, end), and its mean probability over them."""

    start: int
    end: int
    score: float


@dataclass(frozen=True)
class Timing:
    """A track's line starts in seconds, and what the checks read."""

    starts: tuple[float, ...]
    confidence: float
    first_span: float
    median_span: float
    unaligned: int


def frames_needed(ids: Sequence[int]) -> int:
    """The fewest frames CTC needs for *ids*: one a token, and a blank between repeats."""
    return len(ids) + sum(1 for left, right in zip(ids, ids[1:]) if left == right)


def _letters(
    line_of: Sequence[int], spans: Sequence[Span], line_count: int
) -> list[list[Span]]:
    """Each line's letter spans, in order; separators belong to no line."""
    letters: list[list[Span]] = [[] for _ in range(line_count)]
    for line, span in zip(line_of, spans, strict=True):
        if line != NO_LINE:
            letters[line].append(span)
    return letters


def _fill(firsts: Sequence[int | None]) -> list[int]:
    """*firsts* with each gap given the next line's frame, or the last one's at the end."""
    following: int | None = None
    nexts: list[int | None] = []
    for first in reversed(firsts):
        following = first if first is not None else following
        nexts.append(following)
    nexts.reverse()
    last = next((first for first in reversed(firsts) if first is not None), 0)
    return [last if frame is None else frame for frame in nexts]


# fuzz-target
def line_starts(
    line_of: Sequence[int], spans: Sequence[Span], line_count: int, frame_seconds: float
) -> tuple[float, ...]:
    """Each line's start in seconds: its own first letter's first frame.

    A line with no letter takes the next aligned line's start, or the last one's when
    no line after it has a letter; with no letter at all, every line starts at 0.
    """
    letters = _letters(line_of, spans, line_count)
    firsts = [line[0].start if line else None for line in letters]
    return tuple(frame * frame_seconds for frame in _fill(firsts))


def measure(
    line_of: Sequence[int], spans: Sequence[Span], line_count: int, frame_seconds: float
) -> Timing:
    """The starts, the median line confidence and the line spans the checks read."""
    aligned = [line for line in _letters(line_of, spans, line_count) if line]
    scores = [statistics.fmean(span.score for span in line) for line in aligned]
    widths = [(line[-1].end - line[0].start) * frame_seconds for line in aligned]
    return Timing(
        starts=line_starts(line_of, spans, line_count, frame_seconds),
        confidence=statistics.median(scores) if scores else 0.0,
        first_span=widths[0] if widths else 0.0,
        median_span=statistics.median(widths) if widths else 0.0,
        unaligned=line_count - len(aligned),
    )


def low_confidence(timing: Timing) -> bool:
    """True when the track's median line confidence is under the floor."""
    return timing.confidence < CONFIDENCE_FLOOR


def first_line_stretched(timing: Timing) -> bool:
    """True when the first aligned line spans over twice the median line span."""
    return timing.first_span > FIRST_LINE_FACTOR * timing.median_span
