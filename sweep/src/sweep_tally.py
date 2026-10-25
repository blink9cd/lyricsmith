"""One album's run, counted from the album tool's manifest, and whether its providers refused.

The counts are the tracks' end states as the manifest records them: written, unchanged,
not found (a track whose implausible old lyrics a run removed among them), no
lyrics expected (a track whose genre expects none, which nothing was found for), silent
(a track whose audio measured as silence, which nothing was found for), kept
(nothing new found, the old lyrics kept), errors, and those whose check flagged. A track
the run never reached has no end state and is counted nowhere.

Refusals are counted by TRACK and by PROVIDER, never refusal by refusal: one failed call
to a provider can leave a refusal at the synced step and another at the untimed step,
which re-reads the first one's failure, so a raw count would double it. Every refusal is
alike here, whatever its detail: an HTTP status, a provider's refusal code, a timeout,
or the name of a transport error.

**When an album counts as refused.** Only when no provider answered it: at least one
track met a refusal, and no track shows a provider answering. The album tool records a
track whose every provider failed as the track's fetch error, so any other end after a
lookup heard from some provider, whatever else refused beside it. A track shows an
answer when a provider's lyrics were taken for it, whatever happened after; when it
ended with nothing new found (not found, no lyrics expected, its old lyrics kept, or
removed as implausible: the providers answered, emptily); or when it was served its
own untimed lyrics or a machine-labeled .lrc and not every LRC provider's synced step
refused (such a track is looked up for synced lyrics only, so a synced step that did
not refuse answered, emptily). A run given ``--no-netease`` asked LRCLIB's synced step
alone, so LRCLIB's refusal is every synced step refusing. YouTube Music's synced step is left out of that rule: a
run may not ask it at all (off, cooling down, stopped, or a query with no artist),
which records nothing, so its silence cannot be read as an answer. A track served
other synced lyrics it already held asked no provider, and a track that failed before
its lookup met none, so neither says anything about the providers either way. Nothing
here prints.
"""

from __future__ import annotations

from dataclasses import dataclass

from lyrics_fetch import LRC_PROVIDERS
from lyrics_model import LyricsKind, Source
from lyrics_records import (
    CheckVerdict,
    Manifest,
    OutcomeStatus,
    TrackOutcome,
)
from lyrics_sidecar import MACHINE_LABELS

#: The sources a machine-labeled .lrc serves as: each is still looked up for synced
#: lyrics, which `shows_an_answer` reads.
_MACHINE_SOURCES = frozenset(MACHINE_LABELS.values())
#: The sources that are the track's own files.
_OWN_FILES = frozenset({Source.SIDECAR, Source.FRAMES, *_MACHINE_SOURCES})
#: The end states that mean nothing new was found: a refetch's removal of implausible
#: old lyrics among them, which follows only an answer, empty, from some provider.
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
#: The synced steps every run's table holds, by provider and kind: the LRC providers'.
#: A track holding untimed lyrics is looked up with the synced steps only; YouTube
#: Music's is not here, since a run that never asked it records nothing for it.
_SYNCED_STEPS = frozenset((source, LyricsKind.SYNCED) for source in LRC_PROVIDERS)
#: NetEase's synced step, which a run given ``--no-netease`` never asks.
_NETEASE_SYNCED = (Source.NETEASE, LyricsKind.SYNCED)


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


@dataclass(frozen=True)
class AlbumTally:
    """An album's counts, its refusing tracks and providers, and whether it was refused.

    *refusing_providers* holds each refusing provider's label once, sorted.
    """

    counts: Counts
    refusing_tracks: int
    refusing_providers: tuple[str, ...]
    refused: bool


def _count(outcomes: list[TrackOutcome]) -> Counts:
    """The tracks of *outcomes* counted by end state, and those the check flagged."""
    statuses = [outcome.status for outcome in outcomes]
    return Counts(
        written=statuses.count(OutcomeStatus.WRITTEN),
        unchanged=statuses.count(OutcomeStatus.UNCHANGED),
        # A track a run removed implausible lyrics from found nothing, and now
        # holds nothing: not found, in a ledger whose counts predate the status.
        not_found=statuses.count(OutcomeStatus.NOT_FOUND)
        + statuses.count(OutcomeStatus.REMOVED),
        no_lyrics_expected=statuses.count(OutcomeStatus.NO_LYRICS_EXPECTED),
        silent=statuses.count(OutcomeStatus.SILENT),
        kept=statuses.count(OutcomeStatus.KEPT_EXISTING),
        errors=statuses.count(OutcomeStatus.ERROR),
        flagged=sum(
            1
            for outcome in outcomes
            if outcome.check is not None and outcome.check.verdict is CheckVerdict.FLAG
        ),
    )


def _every_synced_step_refused(outcome: TrackOutcome, *, netease: bool) -> bool:
    """True when each synced step the run asked refused for *outcome*'s track.

    A run without NetEase (*netease* False) asked LRCLIB's alone.
    """
    refused = {(refusal.provider, refusal.step) for refusal in outcome.refusals}
    asked = _SYNCED_STEPS if netease else _SYNCED_STEPS - {_NETEASE_SYNCED}
    return asked <= refused


def _looked_up_synced_only(outcome: TrackOutcome) -> bool:
    """True when *outcome*'s track held its own words and asked only the synced steps."""
    return outcome.kind is LyricsKind.UNSYNCED or outcome.source in _MACHINE_SOURCES


def shows_an_answer(outcome: TrackOutcome, *, netease: bool = True) -> bool:
    """True when *outcome* shows a provider answering, as the module docstring says.

    *netease* says whether the run asked NetEase.
    """
    if outcome.source is not None and outcome.source not in _OWN_FILES:
        return True
    if outcome.status in _EMPTY_HANDED:
        return True
    return (
        outcome.status in _ACCEPTED
        and _looked_up_synced_only(outcome)
        and not _every_synced_step_refused(outcome, netease=netease)
    )


def tally_manifest(manifest: Manifest, *, netease: bool = True) -> AlbumTally:
    """Count *manifest*'s tracks, their refusals, and decide whether it was refused.

    *netease* is False for a run given ``--no-netease``, which never asked NetEase.
    """
    outcomes = [track.after for track in manifest.tracks if track.after is not None]
    refusing = [outcome for outcome in outcomes if outcome.refusals]
    providers = {
        refusal.provider.label for outcome in refusing for refusal in outcome.refusals
    }
    answered = any(shows_an_answer(outcome, netease=netease) for outcome in outcomes)
    refused = bool(refusing) and not answered
    return AlbumTally(
        _count(outcomes), len(refusing), tuple(sorted(providers)), refused
    )
