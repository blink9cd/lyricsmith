"""One track, timed or skipped: the child's reply turned into its outcome and its .lrc.

A track planned with a kept transcript (an earlier text of it was not sung, and it is
no longer listed as such with that same text) is judged here first, with no child:
still not sung, it ends so at once; otherwise the child is handed the transcript and
aligns without hearing it again.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Final

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
import at_engine
import sy_engine
import sy_lrc
from lyrics_heard import (
    Heard,
    judge_words,
    little_heard,
    not_sung,
    shown_share,
    words_of_text,
)
from sy_lines import Span, first_line_stretched, low_confidence, measure
from sy_request import STATUS_TOO_LONG, NotSung, Request
from sy_text import Tokens, tokenize
from sy_types import (
    MAX_TRACK_S,
    TEMP_PREFIX,
    UNTIMED_OUTCOMES,
    Action,
    Label,
    Outcome,
    Planned,
    TrackResult,
    Whisper,
)

#: The note beneath a not-sung row judged on its kept transcript, with no child run.
REJUDGED: Final = "judged against what was heard before"


def time_or_skip(
    planned: Planned,
    model_dir: Path,
    label: Label,
    run: at_engine.Runner,
    *,
    whisper: Whisper,
) -> TrackResult:
    """*planned*'s outcome: its skip, or the child run and the .lrc it gives.

    A track whose tags claim more than `MAX_TRACK_S` seconds is skipped with no child.
    The child hears the vocals with *whisper* before it aligns any text, unless the
    plan carries a kept transcript: that is judged first, with no child.
    """
    skipped = UNTIMED_OUTCOMES.get(planned.action)
    if skipped is not None:
        return TrackResult(
            planned.audio, skipped, (planned.detail,) if planned.detail else ()
        )
    if planned.transcript is not None:
        heard = judge_words(words_of_text(planned.lines), planned.transcript)
        if not_sung(heard):
            result = _not_sung(planned, heard, planned.transcript)
            return replace(result, detail=(*result.detail, REJUDGED))
    seconds = sy_engine.track_seconds(planned.audio)
    if seconds is not None and seconds > MAX_TRACK_S:
        # The child would hold the whole decoded track in memory; none is started.
        detail = f"{seconds:.0f} s, over the {MAX_TRACK_S} s cap"
        return TrackResult(planned.audio, Outcome.SKIP_LONG_TRACK, (detail,))
    tokens = tokenize(planned.lines)
    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as work:
        request = Request(
            planned.audio,
            tokens.ids,
            model_dir,
            tokens.line_of,
            whisper.cli,
            whisper.model_dir,
            planned.transcript,
        )
        aligned = sy_engine.align(request, Path(work), run, seconds)
    reply = aligned.reply
    if reply is None:
        return TrackResult(planned.audio, Outcome.ERROR_ALIGN, aligned.detail)
    if isinstance(reply, NotSung):
        return _not_sung(planned, reply.heard, reply.transcript)
    little = reply.heard.pairs if little_heard(reply.heard) else None
    if reply.status == STATUS_TOO_LONG:
        return TrackResult(planned.audio, Outcome.TOO_LONG, little_heard=little)
    result = _written(planned, tokens, reply.spans, reply.frame_seconds, label)
    return replace(result, little_heard=little)


def _not_sung(planned: Planned, heard: Heard, transcript: str | None) -> TrackResult:
    """A text whisper-cli did not hear sung: no .lrc, the share, what was heard."""
    shown = shown_share(heard)
    detail = (f"{shown} of the {heard.pairs} word pairs heard are the text's",)
    return TrackResult(
        planned.audio, Outcome.NOT_SUNG, detail, share=shown, transcript=transcript
    )


def _written(
    planned: Planned,
    tokens: Tokens,
    spans: tuple[Span, ...],
    frame_seconds: float,
    label: Label,
) -> TrackResult:
    """The checks on a good reply, then the .lrc written when they pass."""
    lines = planned.lines
    blank = sum(1 for line in lines if not line)
    timing = measure(tokens.line_of, spans, len(lines), frame_seconds, blank=blank)
    if low_confidence(timing):
        shown = f"{timing.confidence:.2f}"
        detail = (f"median line confidence {shown}",)
        return TrackResult(
            planned.audio, Outcome.LOW_CONFIDENCE, detail, confidence=shown
        )
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
