"""The one way an accepted answer is written: refusals first, one held write, the check.

`write_answer` is the write a run makes for every answer it accepts (`lyrics_run`), and
the write a fix makes for the text it chose (`lyrics_fix_step`), so the two never drift
apart. Nothing here prints.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lyrics_decline import declines
from lyrics_judge import check_written
from lyrics_model import Lyrics, TrackTags
from lyrics_outcomes import TrackState
from lyrics_records import ErrorStage, OutcomeStatus, TrackBefore, TrackOutcome
from lyrics_refetch import strip_if_changing

if TYPE_CHECKING:
    from lyrics_run import TrackContext


def write_answer(
    ctx: TrackContext,
    path: Path,
    before: TrackBefore,
    tags: TrackTags,
    lyrics: Lyrics,
    state: TrackState,
    *,
    afresh: bool,
    sidecar: bool,
) -> TrackOutcome:
    """Write the accepted *lyrics* to the track at *path*, then check what was written.

    An answer the embed cannot encode is refused first, and a fresh run's declined
    answer kept out (`lyrics_decline.declines`), so either leaves the old lyrics whole.
    Then, in one held write: a track replacing its old lyrics, *afresh*, is checked as
    one the embed can save and stripped unless its frames already are the answer's
    (`strip_if_changing`); the frames are written; and the sidecar, when *sidecar* says
    the answer needs one. *tags* are the track's, read before anything changed.
    """
    stages, lang = ctx.stages, ctx.options.lang
    # Refused before anything changes, labeled as the embed would label it, so an
    # answer the embed cannot encode leaves the old lyrics whole.
    state.stage = ErrorStage.EMBED
    stages.check_encodable(lyrics, lang)
    duration_s = tags.duration_s
    if ctx.options.fresh and declines(ctx, path, before, lyrics, duration_s, state):
        return state.outcome(OutcomeStatus.KEPT_EXISTING)
    with state.held():
        if afresh:
            # Likewise a track the embed foreseeably cannot save, before the strip.
            state.stage = ErrorStage.EMBED
            stages.check_writable(path)
            strip_if_changing(stages, path, tags, lyrics, lang, state)
        state.stage = ErrorStage.EMBED
        state.frames_written = stages.write_lyrics(path, lyrics, lang)
        if sidecar:
            state.written = stages.write_sidecar(path, lyrics)
    state.stage = ErrorStage.CHECK
    state.check = check_written(stages, path, ctx.run_dir, before, lang)
    return state.accepted()
