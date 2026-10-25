"""A fresh run's decline: an answer unlike the backup, or untimed over synced lyrics.

`declines` judges the answer with the check's unlike-backup row alone and records each
reason as a FLAG in the track's check.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from lyrics_model import (
    FrameRecord,
    Lyrics,
    PlainLyrics,
    SyncedLyrics,
    lyrics_from_frames,
)
from lyrics_outcomes import TrackState
from lyrics_placeholder import unless_placeholder, without_placeholder_frames
from lyrics_records import CheckReason, CheckResult, CheckRule, ErrorStage, TrackBefore
from lyrics_refetch import worded_sylt

if TYPE_CHECKING:
    from lyrics_run import TrackContext

#: Why a fresh run declines an untimed answer for a track whose lyrics were synced.
_UNTIMED = CheckReason(CheckRule.UNTIMED, "existing lyrics synced")


def _had_synced(
    ctx: TrackContext, path: Path, frames: Sequence[FrameRecord], before: TrackBefore
) -> bool:
    """True when the track's lyrics were synced before the run: a SYLT or a timed .lrc.

    *frames* are the backed-up frames with placeholders set aside. A SYLT frame with
    a worded entry counts, in any language and time unit; one of stamps alone holds
    no lyrics to keep, as a stamps-only .lrc does not. Else a sidecar the backup
    recorded counts when the sidecar stage would serve it as synced and it is no
    placeholder, so never one a link leads to outside the album. A fresh run has
    changed no sidecar yet, so what lies beside the MP3 is what the backup recorded.
    """
    if worded_sylt(frames):
        return True
    if not before.sidecars:
        return False
    state = ctx.stages.normalize_sidecars(path, album=ctx.album, apply=False)
    return isinstance(unless_placeholder(state.lyrics), SyncedLyrics)


def declines(
    ctx: TrackContext,
    path: Path,
    before: TrackBefore,
    lyrics: Lyrics,
    duration_s: float | None,
    state: TrackState,
) -> bool:
    """True when a fresh run declines *lyrics*: unlike the backup, or untimed over synced.

    The check itself judges the answer against the backed-up lyrics, before anything
    is written, so the rule and its threshold live in one place; only its
    unlike-backup row decides. Placeholder frames (`lyrics_placeholder`) are set aside
    first, so the backup is the best frame holding real lyrics, and a track whose
    backup held no lyrics frames, or only placeholders, gets no such row. An untimed
    answer is declined when the track's lyrics were synced (`_had_synced`). A
    declined track keeps its old lyrics, as when nothing is found, and records each
    reason as a FLAG in its check, to say why.
    """
    state.stage = ErrorStage.CHECK
    frames = without_placeholder_frames(ctx.stages.load_before(ctx.run_dir, before))
    backup = lyrics_from_frames(frames, ctx.options.lang)
    judged = ctx.stages.check(lyrics, backup, duration_s)
    reasons = [f for f in judged.flags if f.rule is CheckRule.UNLIKE_BACKUP]
    if isinstance(lyrics, PlainLyrics) and _had_synced(ctx, path, frames, before):
        reasons.append(_UNTIMED)
    if reasons:
        state.check = CheckResult(tuple(reasons), ())
    return bool(reasons)
