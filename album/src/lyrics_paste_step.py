"""A paste's per-track step: the pasted text written in place of the wrong one.

`paste_step`, bound to its `PastePlan`, is the `lyrics_run.TrackStep` ``--mode paste``
passes to `lyrics_run.run_album`, so a paste shares a run's lock, backup barrier, error
boundary, per-track record, changed list and undo, as a fix does (`lyrics_fix_step`).
The mode judged the paste sung before the run (`lyrics_paste.judge_paste`); the step
reads the track's text again under the lock, and a track no longer one a paste takes
(`lyrics_paste.paste_refusal`), or no longer the file the plan matched to its record
(`PastePlan.identity`, `lyrics_fix_text.held_to_planned_file`), is its error, nothing
changed; so is every open of the track after that check, each write's, that finds
another file under its name. Otherwise the text is written, under the run's ``--lang``,
through the run's own write
(`lyrics_write_answer.write_answer`), replacing the old frames and sidecars, an untimed
``.txt`` and an untimed frame, as a fix writes its winner. The outcome records the
source Genius, its origin `lyrics_records.PASTED_BY_HAND` and no provider asked. Never a
removal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lyrics_fix_text import TextChangedError, held_to_planned_file
from lyrics_heard_store import FileIdentity, HeardRecord
from lyrics_held import read_held
from lyrics_model import FetchResult, Lyrics, Source
from lyrics_paste import paste_refusal
from lyrics_records import (
    PASTED_BY_HAND,
    ErrorStage,
    ManifestTrack,
    TrackBefore,
    TrackOutcome,
)
from lyrics_run import track_query
from lyrics_write_answer import write_answer

if TYPE_CHECKING:
    from lyrics_outcomes import TrackState
    from lyrics_run import TrackContext


@dataclass(frozen=True)
class PastePlan:
    """What the paste settled before its run: the track's record and the text to write.

    *identity* is the track's file as it was matched to *record* and judged.
    """

    record: HeardRecord
    lyrics: Lyrics
    identity: FileIdentity


def paste_step(
    plan: PastePlan,
    ctx: TrackContext,
    track: ManifestTrack,
    before: TrackBefore,
    state: TrackState,
) -> TrackOutcome:
    """Write the pasted text to one backed-up track, its guards read again first.

    Bound to *plan* (`functools.partial`), it is a `lyrics_run.TrackStep`.
    """
    path, stages, lang = ctx.album / track.path, ctx.stages, ctx.options.lang
    tags = stages.read_tags(path)
    state.query = query = track_query(tags, path)
    state.stage = ErrorStage.SIDECAR
    state.held_kind = read_held(stages, ctx.album, path, tags.frames, lang)
    taken = paste_refusal(stages, ctx.album, path, plan.record, lang, tags=tags)
    if isinstance(taken, str):
        raise TextChangedError(taken)
    # Every open of the track from here on, each write's, must be the planned file.
    with held_to_planned_file(path, plan.identity):
        state.result = FetchResult(plan.lyrics, Source.GENIUS, query, ())
        state.origin = PASTED_BY_HAND
        return write_answer(
            ctx, path, before, tags, plan.lyrics, state, afresh=True, sidecar=True
        )
