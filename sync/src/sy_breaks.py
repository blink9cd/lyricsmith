"""Long instrumental breaks: stretches where the vocal stem sits far under the band. Pure.

CTC spreads lines across a long instrumental break, since nothing in the emissions tells
it a stretch holds no singing. The child reads each emission frame's level of the vocal
stem against the combined level of the other stems, smooths that ratio, and treats a long
stretch far under the band as a break: it forbids every letter there and aligns again.
This module finds those stretches as frame ranges; the child, which has PyTorch, masks
them. Every value below was measured on the reference tracks and on held-out tracks
(`../sync.md`, *Fixed settings*): a new value needs a new measurement.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sy_lines import Span, line_start_frames
from sy_text import NO_LINE

#: A frame is under the band when the smoothed vocal-to-band ratio is below this. With
#: the other three values below, the lines over 3 s off on a reference track with a 48 s
#: break fell from between 6 and 9, across five separation seeds, to 3. Of 60 held-out
#: tracks, 9 improved and 5 got worse; the rest were unchanged, and each worse one
#: already held a line 20 s or more off. -10 dB gave the same reference figures; at -15 dB and lower they stayed
#: the built tool's.
BREAK_RATIO_DB: Final = -12.0
#: The shortest break, measured with the threshold above. Settings that scored better on
#: the reference tracks (-10 to -12 dB at 8 s) pushed one of them under the confidence
#: floor or made more held-out tracks worse.
BREAK_MIN_S: Final = 12.0
#: Trimmed from each edge of a break before its letters are forbidden: the margin the
#: figures above were measured with.
BREAK_MARGIN_S: Final = 1.5
#: The running median's width over the per-frame ratio: the smoothing the figures above
#: were measured with.
RATIO_SMOOTH_S: Final = 1.0
#: Added to each level before its logarithm, so a silent frame reads as very quiet
#: rather than as log10(0).
LEVEL_FLOOR: Final = 1e-9
#: A level ratio in decibels is 20 times its base-10 logarithm.
DB_PER_DECADE: Final = 20.0

#: A half-open frame range, [start, end).
FrameRange = tuple[int, int]


@dataclass(frozen=True)
class Levels:
    """Each emission frame's RMS level: the vocal stem's, and the other stems' combined."""

    vocal: tuple[float, ...]
    rest: tuple[float, ...]


def combined_level(stems: Sequence[Sequence[float]]) -> list[float]:
    """Each frame's root sum of squares over *stems*' per-frame levels."""
    return [
        math.sqrt(sum(level * level for level in frame))
        for frame in zip(*stems, strict=True)
    ]


def _decibels(level: float) -> float:
    """*level*, an RMS level, in decibels; refused when it is no RMS level."""
    if not math.isfinite(level) or level < 0:
        raise ValueError(f"a level must be finite and not negative: {level!r}")
    return DB_PER_DECADE * math.log10(level + LEVEL_FLOOR)


def ratio_db(vocal: Sequence[float], rest: Sequence[float]) -> list[float]:
    """Each frame's vocal level against the rest's, in decibels."""
    return [
        _decibels(voice) - _decibels(band)
        for voice, band in zip(vocal, rest, strict=True)
    ]


def _frames(seconds: float, frame_seconds: float) -> int:
    """*seconds* as a whole number of frames, rounded half to even as measured."""
    return round(seconds / frame_seconds)


def smoothed(values: Sequence[float], frame_seconds: float) -> list[float]:
    """*values* under a running median `RATIO_SMOOTH_S` wide, one value a frame.

    The ends are padded with copies of the first and last value, ``w // 2`` on the left
    and ``w - 1 - w // 2`` on the right, as numpy's edge pad and sliding window give; an
    even window's median is the mean of its two middle values.
    """
    if not values:
        return []
    width = max(1, _frames(RATIO_SMOOTH_S, frame_seconds))
    left, right = width // 2, width - 1 - width // 2
    padded = [values[0]] * left + list(values) + [values[-1]] * right
    return [
        statistics.median(padded[index : index + width]) for index in range(len(values))
    ]


def _runs_under(values: Sequence[float], threshold: float) -> list[FrameRange]:
    """The maximal runs of frames whose value is under *threshold*."""
    runs: list[FrameRange] = []
    start: int | None = None
    for index, value in enumerate(values):
        if value < threshold and start is None:
            start = index
        elif value >= threshold and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(values)))
    return runs


def _trimmed(run: FrameRange, frame_seconds: float) -> FrameRange | None:
    """*run* with its margins trimmed, or None when it is too short to be a break."""
    start, end = run
    if (end - start) * frame_seconds < BREAK_MIN_S:
        return None
    margin = _frames(BREAK_MARGIN_S, frame_seconds)
    if end - start <= 2 * margin:
        return None
    return start + margin, end - margin


def ratio_breaks(
    ratio: Sequence[float], frame_seconds: float, after_frame: int
) -> tuple[FrameRange, ...]:
    """The breaks in a per-frame vocal-to-band *ratio*, each starting after *after_frame*.

    *after_frame* is where the second line starts in the first alignment: a quiet stretch
    before it is the intro, which the first-line flag already covers, not a break.
    """
    if not math.isfinite(frame_seconds) or frame_seconds <= 0:
        raise ValueError(f"a frame must last a positive time: {frame_seconds!r}")
    runs = _runs_under(smoothed(ratio, frame_seconds), BREAK_RATIO_DB)
    kept = (_trimmed(run, frame_seconds) for run in runs)
    return tuple(run for run in kept if run is not None and run[0] > after_frame)


# fuzz-target
def break_ranges(
    vocal: Sequence[float],
    rest: Sequence[float],
    frame_seconds: float,
    after_frame: int,
) -> tuple[FrameRange, ...]:
    """The breaks in a track's per-frame levels, sorted, as half-open frame ranges."""
    return ratio_breaks(ratio_db(vocal, rest), frame_seconds, after_frame)


def second_line_frame(line_of: Sequence[int], spans: Sequence[Span]) -> int | None:
    """The frame the second line starts at in an alignment, None with no second line.

    The lines counted run to the last one with a letter, so the second line's frame is
    its own first letter's, or the next aligned line's when it has none, as
    `sy_lines.line_starts` reads it.
    """
    line_count = max(line_of, default=NO_LINE) + 1
    if line_count < 2:
        return None
    return line_start_frames(line_of, spans, line_count)[1]
