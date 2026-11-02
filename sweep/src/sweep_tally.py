"""One album's run, counted from the album tool's manifest: its refusals, what it owes.

The counts are the tracks' end states as the manifest records them: written, unchanged,
not found, removed (a track whose implausible or placeholder old lyrics a run
removed), no lyrics expected (a track whose genre expects none, which nothing was found
for), silent
(a track whose audio measured as silence, which nothing was found for), kept
(nothing new found, the old lyrics kept), errors, and those whose check flagged; and
the written tracks by category, the removed ones by reason, and the kept ones whose
removal a fix withheld on an album that looks spoken (``withheld``, counted among the
kept too), counted as the album summary counts them (`lyrics_categories`). A track
the run never reached has no end state and is counted nowhere.

Refusals are counted by TRACK and by PROVIDER, never refusal by refusal: one failed call
to a provider can leave a refusal at the synced step and another at the untimed step,
which re-reads the first one's failure, so a raw count would double it. Every refusal is
alike here, whatever its detail: an HTTP status, a provider's refusal code, a timeout,
or the name of a transport error. A step its provider's open breaker skipped
(`ProviderRefusal.unsent`) is alike too, for the verdict and the debt: it is owed, since
the provider was never asked; only the count below sets it apart.

**When an album counts as refused.** Only when no provider answered it: at least one
track met a refusal, and no track shows a provider answering. The album tool records a
track whose every provider failed as the track's fetch error, so any other end after a
lookup heard from some provider, whatever else refused beside it. A track shows an
answer when a provider's lyrics were taken for it, whatever happened after; when it
ended with nothing new found (not found, no lyrics expected, silent, its old lyrics
kept, a fix's withheld removal among them, or its old lyrics removed as implausible
or a placeholder: the providers answered, emptily); or when it was served its
own untimed lyrics or a machine-labeled .lrc and not every LRC provider's synced step
refused (such a track is looked up for synced lyrics only, so a synced step that did
not refuse answered, emptily). Only the synced steps the run asked count: a run given
``--no-netease``, or a revisit naming LRCLIB alone, asked LRCLIB's synced step alone, so
LRCLIB's refusal is every synced step refusing, and a revisit naming no LRC provider
asked none, so such a track shows no answer at all. YouTube Music's synced step is left out of that rule: a
run may not ask it at all (off, cooling down, stopped, or a query with no artist),
which records nothing, so its silence cannot be read as an answer. A track served
other synced lyrics it already held asked no provider, and a track that failed before
its lookup met none, so neither says anything about the providers either way. Nothing
here prints.

**What an album owes.** One test says whether a provider could still answer a track
(`could_still_answer`), and every half of the rule asks it: one with a synced step in
the chain (LRCLIB, NetEase, YouTube Music) could when the track does not now hold
synced lyrics (`holds_synced`); one with only untimed steps (Tekstowo, Genius,
Bandcamp) only when the track holds no lyrics at all, ending not found or removed, or
keeping (kept existing, or an error at any stage, which left it as it was) wordless
frames or a held kind not read. A track a refetch asked again (``refetch_untimed`` or
``refetch_implausible``) was asked as though it held nothing, and any answer would
have replaced its lyrics, so an untimed-only provider could answer it too while it
holds no synced lyrics. An ordinary run looks a track holding untimed lyrics up with
the synced steps only, so an untimed-only provider can never improve it there.

A track owes a provider when that provider refused it for any reason but unrecognized
(an answer of a shape the tool does not read, or a client error such as a 400 that
says the one request was bad, which asking again would not change) and could still
answer it. A track owes, too, each provider with a cool-down (`COOLING`) that the run
left out by it (its manifest's run-level ``cooling down``: never asked, so it recorded
nothing) when that provider could still answer the track. A
provider with a cool-down that a refusal stopped part-way (its manifest's
``stopped``) left unasked the stopping track, the last reached one recording a refusal
by it, and every reached track after it, which skipped it unrecorded: each of those
owes it, by the same test, whatever the stopping refusal's reason, unrecognized
included (a stop on a reply not JSON wrote a cool-down, so it was no one bad request).
A track before the stop owes it only by its own refusals; a stop no reached track
records leaves every reached track owing it that it could still answer. A provider off
(offline, or by its option) or on is never owed so (`cooling_owed`). The album owes
the union, in the chain's order, of
the providers an option can name (`lyrics_tally.PROVIDERS`): a refusal by a provider no
step asks any more is no debt anyone can pay. The refusals are kept by provider and
reason as well (`lyrics_refusal_tally`, each track counted once, an unsent step under
``breaker open``), and the manifest's retries as they are.

**What challenged it.** The askable providers, in the chain's order, a track's lookup
was sent to and refused by as blocked (`sweep_challenged.was_challenged`): a
provider's challenge, which stops the album tool asking it. A lookup its open breaker
then kept unsent (`ProviderRefusal.unsent`, counted as ``breaker open``) is no
challenge, though it is recorded as blocked, the class that opened the breaker.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from operator import attrgetter
from types import MappingProxyType

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_categories import RemovedReason, WrittenCategory, count_categories
from lyrics_fetch import CHAIN, LRC_PROVIDERS
from lyrics_model import LyricsKind, Source
from lyrics_provider_states import CoolingState
from lyrics_records import (
    CheckVerdict,
    HeldBefore,
    Manifest,
    ManifestTrack,
    OutcomeStatus,
    TrackOutcome,
)
from lyrics_refusal import Refusal
from lyrics_refusal_tally import RefusalCounts, count_refusals
from lyrics_retried import RetriedCounts
from lyrics_sidecar import MACHINE_LABELS
from lyrics_tally import PROVIDERS
from sweep_challenged import challenged_providers

#: The sources a machine-labeled .lrc serves as: each is still looked up for synced
#: lyrics, which `shows_an_answer` reads.
_MACHINE_SOURCES = frozenset(MACHINE_LABELS.values())
#: The sources that are the track's own files.
_OWN_FILES = frozenset({Source.SIDECAR, Source.FRAMES, *_MACHINE_SOURCES})
#: The end states that mean nothing new was found: a removal of implausible or
#: placeholder old lyrics among them, which follows only an answer, empty, from some
#: provider.
_EMPTY_HANDED = frozenset(
    {
        OutcomeStatus.NOT_FOUND,
        OutcomeStatus.NO_LYRICS_EXPECTED,
        OutcomeStatus.SILENT,
        OutcomeStatus.KEPT_EXISTING,
        OutcomeStatus.REMOVED,
    }
)
#: The end states that mean lyrics were accepted for the track.
_ACCEPTED = frozenset({OutcomeStatus.WRITTEN, OutcomeStatus.UNCHANGED})
#: The providers whose synced step every run's table holds: the LRC providers. A track
#: holding untimed lyrics is looked up with the synced steps only; YouTube Music's is
#: not here, since a run that never asked it records nothing for it.
EVERY_LRC_PROVIDER = frozenset(LRC_PROVIDERS)
#: Each provider with a cool-down, and how its run-level state is read from a
#: manifest: the album tool records it per run, ``cooling down`` when it was not asked.
COOLING: Mapping[Source, Callable[[Manifest], CoolingState]] = MappingProxyType(
    {
        Source.YOUTUBE_MUSIC: attrgetter("youtube_music"),
        Source.TEKSTOWO: attrgetter("tekstowo"),
    }
)
#: The providers with a synced step in the chain: each can still answer a track not
#: holding synced lyrics. One with none answers only a track holding no lyrics.
_HAS_SYNCED_STEP = frozenset(
    key.source for key in CHAIN if key.kind is LyricsKind.SYNCED
)
#: The end states of a track left holding no lyrics at all.
_HOLDS_NONE = frozenset({OutcomeStatus.NOT_FOUND, OutcomeStatus.REMOVED})
#: What a kept track held that is no real lyrics: wordless frames, or a kind not read,
#: taken as none because a debt forgotten costs the lyrics (as `holds_synced` does).
_HELD_NONE = frozenset({HeldBefore.NOTHING, None})
#: The end states of a track left holding what it held: kept existing, or an error,
#: whatever its stage (every provider failing, an embed or a strip that failed), which
#: left the track as it was.
_KEPT_WHAT_IT_HELD = frozenset({OutcomeStatus.KEPT_EXISTING, OutcomeStatus.ERROR})


@dataclass(frozen=True)
class Counts:
    """An album's tracks by how its run ended them."""

    written: int = 0
    unchanged: int = 0
    not_found: int = 0
    no_lyrics_expected: int = 0
    silent: int = 0
    kept: int = 0
    errors: int = 0
    flagged: int = 0
    #: Counted apart from not found since the ledger first recorded it; an older
    #: ledger line's not found still includes its removals.
    removed: int = 0
    #: The kept tracks whose removal a fix withheld, the album looking spoken; each
    #: counts among the kept too. An older ledger line holds none.
    withheld: int = 0
    written_by: Mapping[WrittenCategory, int] = field(default_factory=dict)
    removed_by: Mapping[RemovedReason, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AlbumTally:
    """An album's counts, its refusing tracks and providers, and whether it was refused.

    *refusing_providers* holds each refusing provider's label once, sorted. *owed*
    holds the providers the album still owes answers from, in chain order;
    *refusals* the tracks each provider refused, by reason; *retried* the manifest's
    retries, by provider and reason; *challenged* the providers whose challenge it
    met, in chain order (the module docstring's "What challenged it").
    """

    counts: Counts
    refusing_tracks: int
    refusing_providers: tuple[str, ...]
    refused: bool
    owed: tuple[Source, ...] = ()
    refusals: RefusalCounts = field(default_factory=dict)
    retried: RetriedCounts = field(default_factory=dict)
    challenged: tuple[Source, ...] = ()


def _count(tracks: tuple[ManifestTrack, ...]) -> Counts:
    """The reached *tracks* counted by end state and category, and those flagged."""
    outcomes = [track.after for track in tracks if track.after is not None]
    statuses = [outcome.status for outcome in outcomes]
    categories = count_categories(tracks)
    return Counts(
        written=statuses.count(OutcomeStatus.WRITTEN),
        unchanged=statuses.count(OutcomeStatus.UNCHANGED),
        not_found=statuses.count(OutcomeStatus.NOT_FOUND),
        removed=statuses.count(OutcomeStatus.REMOVED),
        written_by=categories.written,
        removed_by=categories.removed,
        no_lyrics_expected=statuses.count(OutcomeStatus.NO_LYRICS_EXPECTED),
        silent=statuses.count(OutcomeStatus.SILENT),
        kept=statuses.count(OutcomeStatus.KEPT_EXISTING),
        errors=statuses.count(OutcomeStatus.ERROR),
        withheld=categories.withheld,
        flagged=sum(
            1
            for outcome in outcomes
            if outcome.check is not None and outcome.check.verdict is CheckVerdict.FLAG
        ),
    )


def _every_synced_step_refused(
    outcome: TrackOutcome, *, synced: frozenset[Source]
) -> bool:
    """True when each synced step the run asked, *synced*'s, refused *outcome*'s track.

    True, too, when the run asked no synced step: nothing answered the track.
    """
    refused = {(refusal.provider, refusal.step) for refusal in outcome.refusals}
    asked = {(source, LyricsKind.SYNCED) for source in synced}
    return asked <= refused


def _looked_up_synced_only(outcome: TrackOutcome) -> bool:
    """True when *outcome*'s track held its own words and asked only the synced steps."""
    return outcome.kind is LyricsKind.UNSYNCED or outcome.source in _MACHINE_SOURCES


def shows_an_answer(
    outcome: TrackOutcome, *, synced: frozenset[Source] = EVERY_LRC_PROVIDER
) -> bool:
    """True when *outcome* shows a provider answering, as the module docstring says.

    *synced* is the LRC providers whose synced step the run asked.
    """
    if outcome.source is not None and outcome.source not in _OWN_FILES:
        return True
    if outcome.status in _EMPTY_HANDED:
        return True
    return (
        outcome.status in _ACCEPTED
        and _looked_up_synced_only(outcome)
        and not _every_synced_step_refused(outcome, synced=synced)
    )


def holds_synced(outcome: TrackOutcome) -> bool:
    """True when *outcome*'s track now holds synced lyrics.

    A written or unchanged track holds what was accepted for it, its kind. A kept
    track holds what it held before the run (`TrackOutcome.held`): its kind, when
    recorded, is an answer a ``--fresh`` run declined, never what it holds. A kept
    track whose held kind is unknown (a manifest written before it was recorded, or
    a sidecar that could not be read) is not known to hold synced lyrics, so it owes:
    a revisit costs one more lookup, a debt forgotten costs the lyrics.
    """
    if outcome.status in _ACCEPTED:
        return outcome.kind is LyricsKind.SYNCED
    return (
        outcome.status is OutcomeStatus.KEPT_EXISTING
        and outcome.held is HeldBefore.SYNCED
    )


def _asked_afresh(outcome: TrackOutcome) -> bool:
    """True when a refetch asked *outcome*'s track again, as though it held nothing."""
    return outcome.refetch_untimed or outcome.refetch_implausible


def could_still_answer(source: Source, outcome: TrackOutcome) -> bool:
    """True when *source*, had it been asked, could still answer *outcome*'s track.

    One with a synced step could when the track does not hold synced lyrics. So could
    one with only untimed steps for a track a refetch asked again (`_asked_afresh`),
    since any answer would have replaced its lyrics; otherwise only when it holds none
    at all: it ended not found or removed, or it kept what it held, ending kept
    existing or in an error (`_KEPT_WHAT_IT_HELD`), when that was wordless frames or
    was not read.
    """
    if source in _HAS_SYNCED_STEP or _asked_afresh(outcome):
        return not holds_synced(outcome)
    if outcome.status in _KEPT_WHAT_IT_HELD:
        return outcome.held in _HELD_NONE
    return outcome.status in _HOLDS_NONE


def _left_unasked(
    source: Source, state: CoolingState, reached: Sequence[TrackOutcome]
) -> Sequence[TrackOutcome]:
    """The *reached* tracks a provider in *state* was never asked, as `cooling_owed` says.

    Cooling down, it asked none. Stopped, it left unasked the stopping track, the last
    recording a refusal by it, and every track after: the album tool reaches its tracks
    in the manifest's order, so those after it are the run's later tracks. With no
    stopping track recorded, every track is taken as unasked. Any other state left none.
    """
    if state is CoolingState.COOLING_DOWN:
        return reached
    if state is not CoolingState.STOPPED:
        return ()
    refusing = [
        index
        for index, outcome in enumerate(reached)
        if any(refusal.provider is source for refusal in outcome.refusals)
    ]
    return reached[refusing[-1] :] if refusing else reached


def cooling_owed(manifest: Manifest) -> frozenset[Source]:
    """The providers with a cool-down that *manifest*'s run left a reached track owing.

    The one rule for a cool-down's and a stop's debt: the tally owes it for every run,
    and an earlier run's debt (`sweep_earlier`) reads it from that run's manifest.
    """
    reached = [track.after for track in manifest.tracks if track.after is not None]
    return frozenset(
        source
        for source, state in COOLING.items()
        if any(
            could_still_answer(source, outcome)
            for outcome in _left_unasked(source, state(manifest), reached)
        )
    )


def owed_providers(
    outcomes: Iterable[TrackOutcome], cooling: Iterable[Source] = ()
) -> tuple[Source, ...]:
    """The providers *outcomes*' refusals and *cooling* owe, in chain order.

    *cooling* is what the run's cool-downs left owing (`cooling_owed`).
    """
    owed = {
        refusal.provider
        for outcome in outcomes
        for refusal in outcome.refusals
        if refusal.reason is not Refusal.UNRECOGNIZED
        and could_still_answer(refusal.provider, outcome)
    }
    owed.update(cooling)
    return tuple(source for source in PROVIDERS if source in owed)


def tally_manifest(
    manifest: Manifest, *, synced: frozenset[Source] = EVERY_LRC_PROVIDER
) -> AlbumTally:
    """Count *manifest*'s tracks and refusals, say what it owes and if it was refused.

    *synced* is the LRC providers whose synced step the run asked: fewer than every
    one for a run given ``--no-netease`` or a revisit naming only some providers.
    """
    outcomes = [track.after for track in manifest.tracks if track.after is not None]
    refusing = [outcome for outcome in outcomes if outcome.refusals]
    providers = {
        refusal.provider.label for outcome in refusing for refusal in outcome.refusals
    }
    answered = any(shows_an_answer(o, synced=synced) for o in outcomes)
    refused = bool(refusing) and not answered
    return AlbumTally(
        _count(manifest.tracks),
        len(refusing),
        tuple(sorted(providers)),
        refused,
        owed=owed_providers(outcomes, cooling_owed(manifest)),
        refusals=count_refusals(manifest.tracks),
        retried=manifest.retried,
        challenged=challenged_providers(outcomes),
    )
