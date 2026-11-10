"""A fix's per-track step: a sung text written in place of the wrong one, or it removed.

`fix_step`, bound to its `FixPlan`, is the `lyrics_run.TrackStep` a fix passes to
`lyrics_run.run_album`, so a fix shares a run's lock, backup barrier, error boundary,
per-track record, changed list and undo. Each track it is given has a record in the
transcript store; the step reads the track's text again under the lock, and a track no
longer one the fix takes (`lyrics_fix_text.skip_reason`), or no longer the file the plan
matched to its record (`FixPlan.identities`, `lyrics_fix_text.held_to_planned_file`), is
its error, nothing changed and no provider asked. Every open of the track after that
check, each write's, must be the planned file too, or it is the track's error.

The candidates are the album's other stored untimed texts, read before the run, then each
provider's untimed answer (`lyrics_compare.answers_in_turn`), each provider asked only
once every earlier candidate failed, its answer judged against the track's duration as a
run's is. A provider-timed track's (`lyrics_fix_text.text_of`) are the album's other
texts, timed ones as their words alone, then the whole chain, its synced steps first
(`lyrics_compare.timed_answers_in_turn`). So a sibling winner is written untimed, for
sync to time against this track, while a synced answer keeps its stamps. The first the
transcript sings (`lyrics_fix_text.first_sung`) is written through
the run's own write (`lyrics_write_answer.write_answer`), replacing the old frames and
sidecars.

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
nor a refusal, so it is counted apart and can be listed later. A fix given
``--hold-for-genius`` (`FixPlan.hold`) withholds every such removal the same way, its
reason `HELD_FOR_GENIUS`: every source it asked answered, none sang, and Genius, which
it never asks, might still have the track, so the fix lists it for a later pass. The
hold is asked before the spoken album, so a held track on a spoken album is recorded
held for Genius and still listed. Either way the track's record stays in the store: the
fix only reads it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING

from lyrics_compare import COMPARED, answers_in_turn, timed_answers_in_turn
from lyrics_cooldown import table_stop
from lyrics_fix_text import (
    SKIP_NOT_UNTIMED,
    Candidate,
    SiblingTexts,
    TextChangedError,
    first_sung,
    fix_skip_reason,
    held_to_planned_file,
    text_of,
    words_of_text,
)
from lyrics_heard_store import FileIdentity, HeardRecord
from lyrics_held import read_held
from lyrics_model import (
    REFUSED_OUTCOMES,
    FetchResult,
    Lyrics,
    ProviderAttempt,
    Source,
    TrackQuery,
    TrackTags,
)
from lyrics_outcomes import (
    FETCH_ERROR,
    HELD_FOR_GENIUS,
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

    *records* maps each track the fix takes, by album-relative path, to its record,
    and *identities* each to its file's identity as read before that record was matched
    (None when it could not be read then).
    *cooling* holds each provider the run left out because it is cooling down, which
    a run would otherwise have asked. *spoken* says why the album looks spoken
    (`lyrics_spoken.spoken_reason`), so no removal is made; None when it does not.
    *hold* is True under ``--hold-for-genius``: no removal is made, each is held.
    """

    records: Mapping[str, HeardRecord]
    identities: Mapping[str, FileIdentity | None]
    siblings: SiblingTexts
    cooling: frozenset[Source] = field(default_factory=frozenset)
    spoken: str | None = None
    hold: bool = False


def _answers(
    ctx: TrackContext,
    query: TrackQuery,
    tags: TrackTags,
    attempts: list[ProviderAttempt],
    *,
    timed: bool,
) -> Iterator[Candidate]:
    """Each provider's answer, as drawn; every attempt is kept in *attempts*.

    The untimed steps alone, or, for a *timed* text, the whole chain, its synced
    steps first (`timed_answers_in_turn`).
    """
    deadline_s = ctx.options.deadline_s
    draw = timed_answers_in_turn if timed else answers_in_turn
    asked = draw(query, ctx.providers, deadline_s, duration_s=tags.duration_s)
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

    A removal the guards allow is withheld when the fix holds for Genius, else on an
    album that looks spoken.
    """
    path = ctx.album / track.path
    attempts = list(state.result.attempts) if state.result is not None else []
    kept = _kept(plan, ctx, track.path, attempts)
    if kept is not None:
        return state.outcome(OutcomeStatus.ERROR, kept)
    withheld = HELD_FOR_GENIUS if plan.hold else plan.spoken
    if withheld is not None:
        state.withheld = withheld
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
    stored = text_of(stages, ctx.album, path, tags, lang)
    reason, refused = fix_skip_reason(stored, record), stored.lyrics
    if refused is None or reason is not None:
        raise TextChangedError(reason or SKIP_NOT_UNTIMED)
    # Every open of the track from here on, each write's, must be the planned file.
    with held_to_planned_file(path, plan.identities[track.path]):
        turn = _Turn(record, tags, query, refused, timed=stored.timed)
        return _fix_held(plan, ctx, track, before, state, turn)


@dataclass(frozen=True)
class _Turn:
    """What a track's step read before its candidates: its record, tags and wrong text."""

    record: HeardRecord
    tags: TrackTags
    query: TrackQuery
    refused: Lyrics
    timed: bool


def _fix_held(
    plan: FixPlan,
    ctx: TrackContext,
    track: ManifestTrack,
    before: TrackBefore,
    state: TrackState,
    turn: _Turn,
) -> TrackOutcome:
    """Write the first sung candidate for the track, or remove its wrong text."""
    path, query, tags = ctx.album / track.path, turn.query, turn.tags
    attempts: list[ProviderAttempt] = []
    state.stage, state.in_fetch = ErrorStage.FETCH, True
    siblings = plan.siblings
    others = (siblings.every_besides if turn.timed else siblings.besides)(track.path)
    answers = _answers(ctx, query, tags, attempts, timed=turn.timed)
    candidates = chain(others, answers)
    refused_words = words_of_text(turn.refused.plain_text())
    winner = first_sung(candidates, turn.record.transcript, refused_words)
    state.in_fetch = False
    if winner is None:
        state.result = FetchResult(None, None, query, tuple(attempts))
        return _remove(plan, ctx, track, tags, state)
    state.result = FetchResult(winner.lyrics, winner.source, query, tuple(attempts))
    state.origin = winner.origin
    return write_answer(
        ctx, path, before, tags, winner.lyrics, state, afresh=True, sidecar=True
    )
