"""One track, timed or skipped: the child's reply turned into its outcome and its .lrc."""

from __future__ import annotations

import tempfile
from pathlib import Path

import at_engine
import sy_engine
import sy_lrc
from sy_lines import Span, first_line_stretched, low_confidence, measure
from sy_request import STATUS_TOO_LONG, Request
from sy_text import Tokens, tokenize
from sy_types import (
    TEMP_PREFIX,
    UNTIMED_OUTCOMES,
    Action,
    Label,
    Outcome,
    Planned,
    TrackResult,
)


def time_or_skip(
    planned: Planned, model_dir: Path, label: Label, run: at_engine.Runner
) -> TrackResult:
    """*planned*'s outcome: its skip, or the child run and the .lrc it gives."""
    skipped = UNTIMED_OUTCOMES.get(planned.action)
    if skipped is not None:
        return TrackResult(
            planned.audio, skipped, (planned.detail,) if planned.detail else ()
        )
    tokens = tokenize(planned.lines)
    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as work:
        request = Request(planned.audio, tokens.ids, model_dir)
        aligned = sy_engine.align(
            request, Path(work), run, sy_engine.track_seconds(planned.audio)
        )
    if aligned.reply is None:
        return TrackResult(planned.audio, Outcome.ERROR_ALIGN, aligned.detail)
    if aligned.reply.status == STATUS_TOO_LONG:
        return TrackResult(planned.audio, Outcome.TOO_LONG)
    return _written(
        planned, tokens, aligned.reply.spans, aligned.reply.frame_seconds, label
    )


def _written(
    planned: Planned,
    tokens: Tokens,
    spans: tuple[Span, ...],
    frame_seconds: float,
    label: Label,
) -> TrackResult:
    """The checks on a good reply, then the .lrc written when they pass."""
    timing = measure(tokens.line_of, spans, len(planned.lines), frame_seconds)
    if low_confidence(timing):
        detail = (f"median line confidence {timing.confidence:.2f}",)
        return TrackResult(planned.audio, Outcome.LOW_CONFIDENCE, detail)
    try:
        sy_lrc.write_lrc(
            planned.audio, sy_lrc.lrc_text(label, planned.lines, timing.starts)
        )
    except OSError as error:
        return TrackResult(planned.audio, Outcome.ERROR_WRITE, (str(error),))
    outcome = Outcome.RETIMED if planned.action is Action.RETIME else Outcome.TIMED
    first = sy_lrc.stamp(timing.starts[0]) if first_line_stretched(timing) else None
    return TrackResult(
        planned.audio, outcome, first_line=first, unaligned=timing.unaligned
    )
