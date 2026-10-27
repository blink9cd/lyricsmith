"""A strip's per-track step: what the track held, then its frames and sidecars removed.

`strip_step` is the `lyrics_run.TrackStep` a strip passes to `lyrics_run.run_album`,
so a strip shares a run's lock, backup barrier, error boundary and per-track record.
It records what the track held (`lyrics_held`) before the strip changes anything, as
a run does, so the outcome carries it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyrics_held import read_held
from lyrics_outcomes import TrackState
from lyrics_records import (
    ErrorStage,
    ManifestTrack,
    OutcomeStatus,
    TrackBefore,
    TrackOutcome,
)

if TYPE_CHECKING:
    from lyrics_run import TrackContext


def strip_step(
    ctx: TrackContext, track: ManifestTrack, before: TrackBefore, state: TrackState
) -> TrackOutcome:
    """Strip one backed-up track: its lyrics frames and both sidecars, one held write.

    *before* goes unused: the backup has already recorded it, and undo restores the
    track from there. What the track held is read first, from its tags and its planned
    sidecars, at the strip's stage, so a track the strip could not read errors there.
    """
    path = ctx.album / track.path
    state.stage = ErrorStage.STRIP
    frames = ctx.stages.read_tags(path).frames
    state.held_kind = read_held(ctx.stages, ctx.album, path, frames, ctx.options.lang)
    with state.held():
        state.stripped = ctx.stages.strip_track(path)
    return state.outcome(OutcomeStatus.STRIPPED)
