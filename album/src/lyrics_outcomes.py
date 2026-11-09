"""One track's state through a run, and the outcome it records and reports.

`TrackState` is what a run has done to one track so far: the stage it is in, the
query, the sidecar state, the fetch result, what was written or stripped, and the
check. Whatever stops the track, an error or an interrupt, its outcome is built from
that state, so the manifest says what was done to the track, which is what undo needs,
and which providers refused or failed on the way (`fetch_refusals`).
`not_found` turns a fetch that found nothing into its outcome, and `describe_outcome`
is the verdict a progress line shows, and `changed_lyrics` whether a run's outcome
replaced what the track's lyrics are.

Pure apart from `TrackState.held`, which defers an interrupt for the length of a write;
nothing here prints.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath

from lyrics_album import deferred_interrupt
from lyrics_model import (
    REFUSED_OUTCOMES,
    AttemptOutcome,
    FetchResult,
    ProviderAttempt,
    QueryMarker,
    SidecarState,
    SidecarWrite,
    TrackQuery,
    TrackTags,
    lyrics_from_frames,
)
from lyrics_providers import TIMEOUT_DETAIL
from lyrics_records import (
    CheckReason,
    CheckResult,
    CheckRule,
    ErrorStage,
    HeldBefore,
    OutcomeStatus,
    ProviderRefusal,
    SidecarHash,
    StageError,
    StripResult,
    TrackOutcome,
)
from lyrics_silence import Level, MeasureFailed, is_silent


#: The message a track's interrupted stage records.
INTERRUPTED = "interrupted"
#: How a fetch error's message opens.
_EVERY_PROVIDER_FAILED = "every provider failed"
#: The error type a fetch records when every provider failed: no exception names it.
FETCH_ERROR = "FetchError"
#: What a strip that found no frame and no sidecar removed.
NOTHING_TO_REMOVE = "nothing to remove"
#: How a strip's removed lyrics frames are named, beside each sidecar's suffix.
_FRAMES = "frames"


def describe_failures(outcomes: Sequence[AttemptOutcome]) -> str:
    """A fetch error's message: every provider failed, each way of failing counted."""
    parts = []
    for outcome, word in (
        (AttemptOutcome.TIMEOUT, "timeout"),
        (AttemptOutcome.ERROR, "error"),
    ):
        count = sum(1 for each in outcomes if each is outcome)
        if count:
            parts.append(f"{count} {word}{'' if count == 1 else 's'}")
    return f"{_EVERY_PROVIDER_FAILED} ({', '.join(parts)})"


def every_provider_failed(outcomes: Sequence[AttemptOutcome]) -> StageError:
    """The fetch error recorded when every provider failed, in a run and in compare."""
    return StageError(ErrorStage.FETCH, FETCH_ERROR, describe_failures(outcomes))


def fetch_refusals(
    attempts: Iterable[ProviderAttempt],
) -> tuple[ProviderRefusal, ...]:
    """Each attempt that raised or timed out, as the refusal a track's outcome records.

    In the order asked. A timeout's detail is `TIMEOUT_DETAIL`, an error's the short
    form the fetch recorded (`lyrics_providers.error_detail`). An answer that was empty,
    or not accepted, is no refusal. Each keeps its attempt's facts, which the fetch
    sets on every refused attempt (`lyrics_fetch.attempt`); a refused attempt without
    them is refused with `ValueError`, so no live refusal is classed by its words.
    """
    return tuple(_refusal(a) for a in attempts if a.outcome in REFUSED_OUTCOMES)


def _refusal(attempt: ProviderAttempt) -> ProviderRefusal:
    """The refusal a refused or timed-out *attempt* records, with its facts."""
    timed_out = attempt.outcome is AttemptOutcome.TIMEOUT
    detail = TIMEOUT_DETAIL if timed_out else attempt.detail
    if attempt.facts is None:
        raise ValueError(f"a {attempt.outcome} attempt with no refusal facts")
    return ProviderRefusal.of(
        attempt.key.source, attempt.key.kind, detail, attempt.facts
    )


#: The statuses of a track whose lyrics the run accepted, whose check judges them.
ACCEPTED = frozenset({OutcomeStatus.WRITTEN, OutcomeStatus.UNCHANGED})
#: The statuses whose run always changed the track's lyrics, frames or sidecars.
_CHANGES_LYRICS = frozenset(
    {OutcomeStatus.WRITTEN, OutcomeStatus.REMOVED, OutcomeStatus.STRIPPED}
)


def changed_lyrics(outcome: TrackOutcome) -> bool:
    """True when the run that recorded *outcome* changed what the track's lyrics are.

    A run that wrote, removed or stripped lyrics replaced whatever an earlier run
    left, so an earlier run's lyrics are no longer the track's. Every other status
    left them as they were, except an error, which changed them only when it recorded
    a write before it stopped: frames saved, or a sidecar written, removed or renamed.
    Accept's check walks a track's runs newest first and stops after the first run
    this holds for (`lyrics_accept_check`); an ERROR that recorded nothing is passed
    over, since checking a run too many is never a loss and one too few can be.
    """
    if outcome.status in _CHANGES_LYRICS:
        return True
    if outcome.status is not OutcomeStatus.ERROR:
        return False
    return bool(
        outcome.frames_written
        or outcome.sidecar_written
        or outcome.sidecars_removed
        or outcome.sidecars_renamed
    )


#: The ends at which a track holds lyrics, so a marked one is flagged.
HOLDS_LYRICS = frozenset(
    {OutcomeStatus.WRITTEN, OutcomeStatus.UNCHANGED, OutcomeStatus.KEPT_EXISTING}
)


def is_genre_flagged(check: CheckResult | None) -> bool:
    """True when *check* holds the no-lyrics-genre FLAG."""
    return check is not None and any(
        reason.rule is CheckRule.NO_LYRICS_GENRE for reason in check.flags
    )


@dataclass
class TrackState:
    """One track's run so far, so an error or an interrupt records what was done."""

    stage: ErrorStage = ErrorStage.FETCH
    #: True only while a provider is being called, whose messages are not shown.
    in_fetch: bool = False
    #: True from entering a held write until it returns: an interrupt raised as the
    #: hold ends then carries, as its context, any error the write itself raised.
    holding: bool = False
    query: TrackQuery | None = None
    sidecar: SidecarState | None = None
    result: FetchResult | None = None
    stripped: StripResult | None = None
    written: SidecarWrite | None = None
    frames_written: bool = False
    check: CheckResult | None = None
    #: True once a refetch run chose to ask again for this untimed-only track.
    refetch_untimed: bool = False
    #: True once a run with --refetch-implausible chose to ask this track again.
    refetch_implausible: bool = False
    #: Why that track's old ``.txt`` could not be read, when it could not.
    sidecar_unreadable: str | None = None
    #: Why that track's old lyrics are removed if nothing answers, when they are.
    implausible: str | None = None
    #: Which of them go then: its lyrics frames, its sidecars.
    remove_frames: bool = False
    remove_sidecars: bool = False
    #: The genre, as tagged, that marks the track as one no lyrics source holds.
    no_lyrics_genre: str | None = None
    #: A miss's silence check: the silent track's peak, or why the check failed.
    peak_dbfs: float | None = None
    silence_failed: str | None = None
    #: What the track held before anything changed (`lyrics_held`), once read.
    held_kind: HeldBefore | None = None
    #: The album-relative track a fix took the accepted text from, when it did.
    origin: str | None = None
    #: Why a fix kept the wrong text it would have removed, when it did.
    withheld: str | None = None

    @contextmanager
    def held(self) -> Iterator[None]:
        """Run the block as one held write, marked so an error it raised is not lost."""
        self.holding = True
        with deferred_interrupt():
            yield
        # Deliberately not in a `finally`: it would clear the flag before the track's
        # boundary reads it.
        self.holding = False

    def masked(self, interrupt: KeyboardInterrupt) -> Exception | None:
        """The error a held write raised, which the held interrupt then replaced.

        Only a held write's: an interrupt landing elsewhere, while a fetch handles an
        error of its own say, carries a context too, but nothing it hid is the track's.
        """
        context = interrupt.__context__ if self.holding else None
        return context if isinstance(context, Exception) else None

    def hashes(self) -> tuple[SidecarHash, ...]:
        """Each sidecar the run has written or renamed into place, and not removed.

        Hashed by the stage from the bytes it wrote or read, never re-read from disk,
        so an edit landing after the write cannot pass for the run's own file.
        """
        hashes = dict(self.sidecar.hashes) if self.sidecar else {}
        if self.written is not None:
            for name in self.written.removed:
                hashes.pop(name, None)
            hashes[self.written.written] = self.written.sha256
        return tuple(SidecarHash(name, hashes[name]) for name in sorted(hashes))

    def _markers(self) -> tuple[QueryMarker, ...]:
        """The query markers to show: ``stripped`` only when it found the lyrics."""
        query = self.result.query if self.result is not None else self.query
        found = self.result is not None and self.result.found
        markers = () if query is None else query.markers
        return tuple(m for m in markers if found or m is not QueryMarker.STRIPPED)

    def _removed(self) -> tuple[str, ...]:
        """Every sidecar name removed so far: by the strip, the normalize, the write."""
        stripped, sidecar, written = self.stripped, self.sidecar, self.written
        return (
            (stripped.sidecars_removed if stripped is not None else ())
            + (sidecar.removed if sidecar is not None else ())
            + (written.removed if written is not None else ())
        )

    def changed(self) -> bool:
        """True when the run has changed anything of the track so far.

        A save of its frames, a strip, a sidecar written, renamed or removed: each is
        a change. A track a run accepted lyrics for, with none of them, is unchanged.
        """
        return bool(
            self.frames_written
            or self.stripped is not None
            or self.written is not None
            or self._removed()
            or (self.sidecar is not None and self.sidecar.renamed)
        )

    def accepted(self) -> TrackOutcome:
        """The outcome of a track whose lyrics were accepted: written, or unchanged."""
        changed = self.changed()
        return self.outcome(
            OutcomeStatus.WRITTEN if changed else OutcomeStatus.UNCHANGED
        )

    def outcome(
        self, status: OutcomeStatus, error: StageError | None = None
    ) -> TrackOutcome:
        """The track's ``after`` block, with everything recorded so far.

        Every outcome after a finished fetch carries the refusals it met, whatever the
        track's end; a fetch that asked no provider met none.
        Its frames count as written when new ones were saved or a strip removed old
        ones: either way the run saved the track's lyrics frames.
        """
        result, written, sidecar = self.result, self.written, self.sidecar
        lyrics = result.lyrics if result is not None else None
        stripped_frames = self.stripped is not None and self.stripped.frames_removed
        return TrackOutcome(
            status,
            source=result.source if result is not None else None,
            kind=lyrics.kind if lyrics is not None else None,
            query=self._markers(),
            sidecar_written=written.written if written is not None else None,
            sidecars_removed=self._removed(),
            sidecars_renamed=sidecar.renamed if sidecar is not None else (),
            sidecar_hashes=self.hashes(),
            frames_written=self.frames_written or stripped_frames,
            check=self._check_at(status),
            error=error,
            sidecar_links_ignored=sidecar.links_ignored if sidecar is not None else (),
            refusals=fetch_refusals(result.attempts) if result is not None else (),
            refetch_untimed=self.refetch_untimed,
            sidecar_unreadable=self.sidecar_unreadable,
            implausible=self.implausible,
            refetch_implausible=self.refetch_implausible,
            peak_dbfs=self.peak_dbfs,
            silence_failed=self.silence_failed,
            held=self.held_kind,
            origin=self.origin,
            withheld=self.withheld,
        )

    def _check_at(self, status: OutcomeStatus) -> CheckResult | None:
        """*status*'s check, with the genre FLAG when a marked track holds lyrics.

        Appended after any decline reasons, so a fresh run's own reasons stay first.
        """
        if self.no_lyrics_genre is None or status not in HOLDS_LYRICS:
            return self.check
        flag = CheckReason(CheckRule.NO_LYRICS_GENRE, self.no_lyrics_genre)
        if self.check is None:
            return CheckResult((flag,), ())
        return CheckResult((*self.check.flags, flag), self.check.skips)

    def failed(self, error: BaseException) -> TrackOutcome:
        """The outcome of *error* at the current stage.

        An error raised by the fetch call shows the exception's type alone: a
        provider's message may carry a request URL with a token in it. Any other error,
        a tag-read failure counted at the fetch stage included, shows its message.
        """
        name = type(error).__name__
        if isinstance(error, KeyboardInterrupt):
            message = INTERRUPTED
        elif self.in_fetch:
            message = name
        else:
            message = str(error) or name
        return self.outcome(OutcomeStatus.ERROR, StageError(self.stage, name, message))


def not_found(
    state: TrackState,
    tags: TrackTags,
    lang: str,
    *,
    kept_sidecar: bool = False,
    measure: Callable[[], Level] | None = None,
) -> TrackOutcome:
    """Nothing found: a fetch error, kept, silent, no lyrics expected, or not found.

    A fetch error when every provider failed, else kept, silent when *measure* finds
    silence, no lyrics expected for a marked track (`TrackState.no_lyrics_genre`), or
    not found.

    The track kept its lyrics when *tags* hold lyrics frames, in any language (*lang*
    only chooses among several), or when *kept_sidecar* says a sidecar stayed beside
    it: a fresh run, which leaves sidecars out of the lookup, counts one as kept
    lyrics too.

    *measure*, when given, is called only once neither of the first two holds: a
    silent track ends silent, ahead of its genre; a failed measurement is recorded
    and the track ends as it would without the check.
    """
    result = state.result
    if result is not None and result.every_provider_failed:
        error = every_provider_failed([a.outcome for a in result.attempts])
        return state.outcome(OutcomeStatus.ERROR, error)
    if kept_sidecar or lyrics_from_frames(tags.frames, lang) is not None:
        return state.outcome(OutcomeStatus.KEPT_EXISTING)
    if measure is not None and _measured_silent(state, measure()):
        return state.outcome(OutcomeStatus.SILENT)
    if state.no_lyrics_genre is not None:
        return state.outcome(OutcomeStatus.NO_LYRICS_EXPECTED)
    return state.outcome(OutcomeStatus.NOT_FOUND)


def _measured_silent(state: TrackState, level: Level) -> bool:
    """Record *level* on *state*; whether it makes the track silent."""
    if isinstance(level, MeasureFailed):
        state.silence_failed = level.reason
        return False
    state.peak_dbfs = level.dbfs if is_silent(level) else None
    return state.peak_dbfs is not None


def describe_removed(outcome: TrackOutcome) -> str:
    """What a strip removed: ``frames`` and each sidecar's suffix, or nothing to remove.

    The sidecars are named by suffix alone, in name order: the track's row already
    names its stem.
    """
    removed = [_FRAMES] if outcome.frames_written else []
    removed += [PurePosixPath(name).suffix for name in outcome.sidecars_removed]
    return ", ".join(removed) if removed else NOTHING_TO_REMOVE


#: How the reason a fresh run declined an answer opens, after ``kept existing``.
_DECLINED = "new answer"
#: Why a track a refetch asked again kept its old lyrics, after ``kept existing``.
NO_REFETCH_ANSWER = "no answer to the refetch"
#: Why a track a run found nothing for lost its old lyrics, after ``removed``.
IMPLAUSIBLE = "implausible old lyrics"
#: What a fix records, and shows as the row, for a track whose wrong untimed text it
#: removed, no sung text found (`lyrics_fix_step`): the words its reason is read by.
WRONG_TEXT = "wrong text removed"
#: How a fix's kept track whose removal it withheld names it, after ``kept existing``.
REMOVAL_WITHHELD = "removal withheld"
#: Why a fix given ``--hold-for-genius`` withheld a removal: every source it asked
#: answered, none sang, and Genius, never asked, might still have the track.
HELD_FOR_GENIUS = "held for Genius"


def _split_genre(
    check: CheckResult | None,
) -> tuple[tuple[CheckReason, ...], str]:
    """*check*'s other FLAGs, and the genre FLAG's bracketed suffix ('' when absent)."""
    flags = check.flags if check is not None else ()
    genre = tuple(r for r in flags if r.rule is CheckRule.NO_LYRICS_GENRE)
    others = tuple(r for r in flags if r not in genre)
    return others, f" ({CheckResult(genre, ()).summary()})" if genre else ""


def describe_status(outcome: TrackOutcome) -> str:
    """An outcome's status, with why a fresh run declined an answer when it did.

    Only an accepted track's check judges what it holds; any other track carries a
    check only when a fresh run declined its answer, and its FLAGs say why
    (``kept existing: new answer unlike backup (similarity 0.564 < 0.6)``). An
    unchanged track shows its check's verdict in brackets (``unchanged (PASS)``). A
    track a refetch asked again and kept says so, with why its old ``.txt`` could not
    be read when it could not (``kept existing: no answer to the refetch; <why>``),
    and one whose implausible old lyrics it removed says why they were
    (``removed: implausible old lyrics (<why>)``), with an unreadable ``.txt`` the
    removal left named after it as a kept track names it. A kept track's genre FLAG
    shows in brackets last, after a declined answer or a refetch's note
    (``kept existing (FLAG: lyrics on a no-lyrics genre (<genre>))``). A silent track
    names its peak, and a miss whose silence check failed names why. A fix's removal
    of a wrong text says so alone (``wrong text removed``): it was not implausible.
    One a fix withheld on an album that looks spoken, or held for Genius, says why
    (``kept existing: removal withheld, spoken word (genre Comedy)``).
    """
    if outcome.status is OutcomeStatus.REMOVED and outcome.implausible == WRONG_TEXT:
        return WRONG_TEXT
    if outcome.status is OutcomeStatus.KEPT_EXISTING and outcome.withheld is not None:
        return f"{outcome.status}: {REMOVAL_WITHHELD}, {outcome.withheld}"
    if outcome.status is OutcomeStatus.REMOVED:
        why = f" ({outcome.implausible})" if outcome.implausible is not None else ""
        # A .txt left in place because it is not UTF-8 is named as a kept row names
        # it; one the removal took was implausible, and the why already says so.
        left = None if outcome.sidecars_removed else outcome.sidecar_unreadable
        notes = (f"{IMPLAUSIBLE}{why}", left)
        return f"{outcome.status}: " + "; ".join(n for n in notes if n is not None)
    if outcome.status is OutcomeStatus.KEPT_EXISTING and outcome.refetch_untimed:
        notes = (NO_REFETCH_ANSWER, outcome.sidecar_unreadable)
        said = "; ".join(n for n in notes if n is not None)
        return f"{outcome.status}: {said}{_split_genre(outcome.check)[1]}"
    if outcome.status is OutcomeStatus.UNCHANGED:
        verdict = outcome.check.summary() if outcome.check is not None else None
        return f"{outcome.status} ({verdict})" if verdict else str(outcome.status)
    if outcome.status is OutcomeStatus.SILENT and outcome.peak_dbfs is not None:
        return f"{outcome.status} (peak {outcome.peak_dbfs:.1f} dBFS)"
    if outcome.silence_failed is not None:
        return f"{outcome.status} (silence check failed: {outcome.silence_failed})"
    if outcome.status is OutcomeStatus.WRITTEN or outcome.check is None:
        return str(outcome.status)
    declined, marked = _split_genre(outcome.check)
    if not declined:
        return f"{outcome.status}{marked}"
    reasons = "; ".join(reason.describe() for reason in declined)
    return f"{outcome.status}: {_DECLINED} {reasons}{marked}"


def shown_source(outcome: TrackOutcome) -> str:
    """*outcome*'s source as a row or a progress line shows it.

    A fix's text taken from another track names that track after it
    (``another track's text from 02 Song.mp3``).
    """
    if outcome.origin is not None:
        return f"{outcome.source} from {outcome.origin}"
    return str(outcome.source)


def describe_outcome(outcome: TrackOutcome) -> str:
    """One progress line's verdict: the kind and source, or what happened instead.

    An unchanged track's line opens with ``unchanged``.
    """
    if outcome.error is not None:
        return outcome.error.summary()
    if outcome.status is OutcomeStatus.WRITTEN:
        return f"{outcome.kind} ({shown_source(outcome)})"
    if outcome.status is OutcomeStatus.UNCHANGED:
        return f"{outcome.status} {outcome.kind} ({shown_source(outcome)})"
    if outcome.status is OutcomeStatus.STRIPPED:
        return f"{outcome.status} ({describe_removed(outcome)})"
    return describe_status(outcome)
