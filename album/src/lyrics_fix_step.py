"""A fix's per-track step: a sung text written in place of the wrong one, or it removed.

`fix_step`, bound to its `FixPlan`, is the `lyrics_run.TrackStep` a fix passes to
`lyrics_run.run_album`, so a fix shares a run's lock, backup barrier, error boundary,
per-track record, changed list and undo. Each track it is given has a record in the
transcript store; the step reads the track's text again under the lock, and a track no
longer one the fix takes (`lyrics_fix_text.skip_reason`) is its error, nothing changed.

The candidates are the album's other stored untimed texts, read before the run, then each
provider's untimed answer (`lyrics_compare.answers_in_turn`), each provider asked only
once every earlier candidate failed, its answer judged against the track's duration as a
run's is. The first the transcript sings (`lyrics_fix_text.first_sung`) is written
through the run's own write (`lyrics_write_answer.write_answer`), replacing the old
frames and sidecars.

With none sung, the wrong text is removed through a refetch's removal
(`lyrics_refetch.remove_implausible`): every lyrics frame and both sidecar names, backed
up, recorded ``wrong text removed``. It is removed only when every provider the run
meant to ask was asked and none failed (`_kept`). So it is never removed when another
track's text could not be read, since that text might have been the track's; when any
provider asked failed (an error, a timeout, an open breaker), since its answer might
have been sung; when a provider the run meant to ask was not asked because it is
cooling down, from an earlier refusal (`FixPlan.cooling`) or a stop part-way through
this run; or when no provider was asked at all. The track is then its error, nothing
changed. A provider left out on purpose (``--no-*``, ``--only-providers``, Genius with
no key) is not one the run meant to ask, and keeps nothing.

On an album that looks spoken (`FixPlan.spoken`, `lyrics_spoken`) a removal those
guards allow is withheld: the track keeps its text and nothing changes, and it ends
``kept existing`` with the reason recorded (`TrackOutcome.withheld`), neither an error
nor a refusal, so it is counted apart and can be listed later. Either way the track's
record stays in the store: the fix only reads it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING

from lyrics_compare import COMPARED, answers_in_turn, judged_lyrics
from lyrics_cooldown import table_stop
from lyrics_fix_text import (
    SKIP_NOT_UNTIMED,
    Candidate,
    SiblingTexts,
    TextChangedError,
    first_sung,
    skip_reason,
    words_of_text,
)
from lyrics_heard_store import HeardRecord
from lyrics_held import read_held
from lyrics_model import (
    REFUSED_OUTCOMES,
    FetchResult,
    ProviderAttempt,
    Source,
    TrackQuery,
    TrackTags,
)
from lyrics_outcomes import (
    FETCH_ERROR,
    WRONG_TEXT,
    TrackState,
    every_provider_failed,
)
from lyrics_records import (
    ErrorStage,
    ManifestTrack,
    OutcomeStatus,
    StageError,
    TrackBefore,
    TrackOutcome,
)
from lyrics_refetch import remove_implausible
from lyrics_run import track_query
from lyrics_write_answer import write_answer

if TYPE_CHECKING:
    from lyrics_run import TrackContext

#: Why a track no provider answered keeps its wrong text, when none was asked.
NO_PROVIDER_ANSWERED = "no provider answered: nothing removed"
#: The error type of a track whose wrong text stays because another text was unread.
SIBLING_UNREAD = "SiblingUnreadError"


@dataclass(frozen=True)
class FixPlan:
    """What a fix settled before its run: each track's record, the album's texts.

    *records* maps each track the fix takes, by album-relative path, to its record.
    *cooling* holds each provider the run left out because it is cooling down, which
    a run would otherwise have asked. *spoken* says why the album looks spoken
    (`lyrics_spoken.spoken_reason`), so no removal is made; None when it does not.
    """

    records: Mapping[str, HeardRecord]
    siblings: SiblingTexts
    cooling: frozenset[Source] = field(default_factory=frozenset)
    spoken: str | None = None


def _answers(
    ctx: TrackContext,
    query: TrackQuery,
    tags: TrackTags,
    attempts: list[ProviderAttempt],
) -> Iterator[Candidate]:
    """Each provider's untimed answer, as drawn; every attempt is kept in *attempts*."""
    deadline_s = ctx.options.deadline_s
    asked = answers_in_turn(
        query, ctx.providers, deadline_s, duration_s=tags.duration_s
    )
    for source, lyrics, tried in asked:
        attempts.append(tried)
        if lyrics is not None:
            yield Candidate(lyrics, source)


def _names(sources: Sequence[Source]) -> str:
    """*sources*' labels, each once, in their order."""
    return ", ".join(dict.fromkeys(source.label for source in sources))


def _unasked(plan: FixPlan, ctx: TrackContext) -> list[Source]:
    """The providers the run meant to ask but did not: cooling down, or stopped since."""
    return [
        source
        for source in COMPARED
        if source in plan.cooling or table_stop(ctx.providers, source) is not None
    ]


def _fetch_kept(message: str) -> StageError:
    """The fetch error of a track whose wrong text stays because of *message*."""
    return StageError(ErrorStage.FETCH, FETCH_ERROR, f"{message}: nothing removed")


def _kept(
    plan: FixPlan, ctx: TrackContext, rel: str, attempts: list[ProviderAttempt]
) -> StageError | None:
    """Why the track keeps its wrong text though nothing was sung; None to remove it.

    Another track's text that could not be read; else every provider asked failed,
    or some did; else a provider the run meant to ask was not asked; else none was.
    """
    unread = [name for name in plan.siblings.unreadable if name != rel]
    if unread:
        names = ", ".join(unread)
        message = f"could not read {names}: nothing removed"
        return StageError(ErrorStage.SIDECAR, SIBLING_UNREAD, message)
    outcomes = [tried.outcome for tried in attempts]
    failed = [t.key.source for t in attempts if t.outcome in REFUSED_OUTCOMES]
    if attempts and len(failed) == len(attempts):
        return every_provider_failed(outcomes)
    if failed:
        return _fetch_kept(f"{_names(failed)} failed")
    unasked = _unasked(plan, ctx)
    if unasked:
        return _fetch_kept(f"{_names(unasked)} not asked (cooling down)")
    if not attempts:
        return StageError(ErrorStage.FETCH, FETCH_ERROR, NO_PROVIDER_ANSWERED)
    return None


def _remove(
    plan: FixPlan,
    ctx: TrackContext,
    track: ManifestTrack,
    tags: TrackTags,
    state: TrackState,
) -> TrackOutcome:
    """No text sung: remove the wrong one, unless a guard keeps it (`_kept`).

    A removal the guards allow is withheld on an album that looks spoken.
    """
    path = ctx.album / track.path
    attempts = list(state.result.attempts) if state.result is not None else []
    kept = _kept(plan, ctx, track.path, attempts)
    if kept is not None:
        return state.outcome(OutcomeStatus.ERROR, kept)
    if plan.spoken is not None:
        state.withheld = plan.spoken
        return state.outcome(OutcomeStatus.KEPT_EXISTING)
    state.implausible = WRONG_TEXT
    state.remove_frames, state.remove_sidecars = bool(tags.frames), True
    return remove_implausible(ctx.stages, path, state)


def fix_step(
    plan: FixPlan,
    ctx: TrackContext,
    track: ManifestTrack,
    before: TrackBefore,
    state: TrackState,
) -> TrackOutcome:
    """Fix one backed-up track: write the first sung candidate, or remove the wrong text.

    Bound to *plan* (`functools.partial`), it is a `lyrics_run.TrackStep`.
    """
    path, stages, lang = ctx.album / track.path, ctx.stages, ctx.options.lang
    record = plan.records[track.path]
    tags = stages.read_tags(path)
    state.query = query = track_query(tags, path)
    state.stage = ErrorStage.SIDECAR
    state.held_kind = read_held(stages, ctx.album, path, tags.frames, lang)
    refused = judged_lyrics(stages, ctx.album, path, tags, lang)
    reason = skip_reason(refused, record)
    if refused is None or reason is not None:
        raise TextChangedError(reason or SKIP_NOT_UNTIMED)
    attempts: list[ProviderAttempt] = []
    state.stage, state.in_fetch = ErrorStage.FETCH, True
    candidates = chain(
        plan.siblings.besides(track.path), _answers(ctx, query, tags, attempts)
    )
    refused_words = words_of_text(refused.plain_text())
    winner = first_sung(candidates, record.transcript, refused_words)
    state.in_fetch = False
    if winner is None:
        state.result = FetchResult(None, None, query, tuple(attempts))
        return _remove(plan, ctx, track, tags, state)
    state.result = FetchResult(winner.lyrics, winner.source, query, tuple(attempts))
    state.origin = winner.origin
    return write_answer(
        ctx, path, before, tags, winner.lyrics, state, afresh=True, sidecar=True
    )
