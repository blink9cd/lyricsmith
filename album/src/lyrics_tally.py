"""The per-provider tally a run's summary prints: each provider's tracks asked and won.

A provider that answers every request with plausible but useless data (an HTTP 200
whose hits the matcher rejects) wins no track and raises no error, so a summary that
counted only winners would never show it had stopped working. The tally shows it as
``0/<n>``. A provider that refused or failed instead of answering, with an error status,
a refusal code or a timeout, shows how many tracks it did so for, ``0/<n> (<r>
refused)``, so a refusing provider is told apart from one that answered with nothing;
a provider that refused no track shows no such count.

Counted per track, not per call: a provider asked for synced lyrics and then plain text
for one track counts once, and so does a track the stripped-title retry asked it for
twice. Every attempt the fetch records counts as asked, including a step that read an
answer its provider already gave for the track; a step the fetch skipped because its
provider said it would send no request for the query records none, so it is not counted.
A provider the run's table does not hold (Genius without a key, every provider offline)
has no count at all. Refused is counted the same way, once per track however many of the
provider's attempts for it raised or timed out. A track whose every refusal from the
provider was a step its open breaker skipped, sending nothing, counts as ``breaker
open`` instead, ``0/21 (1 refused, 20 breaker open)``: one block and the lookups it left
unsent, not twenty-one refusals heard. Such a track is still asked, so the denominator
keeps it; one that also met a refusal heard counts as refused. A provider retried
after a refusal shows how many retries were made, ``(<r> refused, <n> retried)``,
counted per retry rather than per track (`lyrics_retried`). A run's accepted answers
are split by the kept answer's kind ahead of those counts, ``(24 synced, 6 untimed;
3 refused)``: an answer by kind is a track whose kept lyrics came from the provider,
so the kinds add up to the tracks won. Each count shows only when it is not zero.
Pure: no I/O, nothing printed. It also holds `track_count`, the one plural the
summary's header and the run's stderr progress line share: `lyrics_run` cannot import
`lyrics_summary`, which imports it.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from lyrics_fetch import CHAIN
from lyrics_model import REFUSED_OUTCOMES, LyricsKind, ProviderAttempt, Source
from lyrics_refusal import Refusal
from lyrics_retried import total

#: Every provider, in the order the chain first asks it.
PROVIDERS: tuple[Source, ...] = tuple(dict.fromkeys(key.source for key in CHAIN))
#: What a provider outside the run's table shows instead of a count.
_ABSENT = "-"
#: How the line opens.
_LABEL = "providers:"
#: Between an entry's bracketed parts, and between its kinds and its other counts.
_PART_GAP, _SIDE_GAP = ", ", "; "
#: Between the entries of a counts line: this one's, and every report's closing one.
DOT = " · "


@dataclass(frozen=True)
class TrackFetch:
    """One track's fetch as the tally reads it.

    *attempts* is every provider call the fetch recorded. *accepted* is the provider
    whose answer the run kept for the track, or None: nothing found, a sidecar won,
    or a fresh run declined the answer. *kind* is the kept answer's kind, None when
    nothing was kept or the caller does not split by kind (compare).
    """

    attempts: Sequence[ProviderAttempt]
    accepted: Source | None
    kind: LyricsKind | None = None


@dataclass(frozen=True)
class ProviderCount:
    """One provider's tally: tracks whose answer was kept, and tracks it was asked for.

    *asked* is None when the run's table did not hold the provider. *refused* is the
    tracks it refused or failed for, at any of its steps. *own* is the tracks compare
    set aside because the provider wrote their lyrics; a run's tally leaves it 0.
    *retried* is the retries its requests made past a refusal, over the whole run.
    *synced* and *untimed* split *accepted* by the kept answer's kind; an answer kept
    with no recorded kind is in neither. *breaker_open* is the tracks whose only
    refusals from it were steps its open breaker skipped; they are not in *refused*.
    """

    source: Source
    accepted: int
    asked: int | None
    refused: int = 0
    own: int = 0
    retried: int = 0
    synced: int = 0
    untimed: int = 0
    breaker_open: int = 0

    def describe(self) -> str:
        """This provider's entry: ``LRCLIB 30/50 (24 synced, 6 untimed; 3 refused)``, or ``-``.

        The kinds come first, then the own, refused, breaker open and retried counts,
        each only when it is not zero; the ``;`` parts the two sides only when both
        have one.
        """
        if self.asked is None:
            return f"{self.source} {_ABSENT}"
        kinds = _nonzero((self.synced, "synced"), (self.untimed, "untimed"))
        counts = _nonzero(
            (self.own, "own"),
            (self.refused, "refused"),
            (self.breaker_open, "breaker open"),
            (self.retried, "retried"),
        )
        sides = _SIDE_GAP.join(side for side in (kinds, counts) if side)
        noted = f" ({sides})" if sides else ""
        return f"{self.source} {self.accepted}/{self.asked}{noted}"


def _nonzero(*pairs: tuple[int, str]) -> str:
    """``<n> <name>`` for each non-zero pair, comma-parted; empty when all are zero."""
    return _PART_GAP.join(f"{count} {name}" for count, name in pairs if count)


def _count(
    source: Source, fetches: Sequence[TrackFetch], retried: int
) -> ProviderCount:
    """*source*'s tally over *fetches*, each track counted once, with its *retried*."""
    asked = sum(1 for f in fetches if any(a.key.source is source for a in f.attempts))
    accepted = sum(1 for f in fetches if f.accepted is source)
    synced, untimed = (
        sum(1 for f in fetches if f.accepted is source and f.kind is kind)
        for kind in (LyricsKind.SYNCED, LyricsKind.UNSYNCED)
    )
    refusals = [_refusals_of(source, f) for f in fetches]
    unsent = sum(1 for met in refusals if met and all(map(_unsent, met)))
    return ProviderCount(
        source,
        accepted,
        asked,
        sum(1 for met in refusals if met) - unsent,
        retried=retried,
        synced=synced,
        untimed=untimed,
        breaker_open=unsent,
    )


def _refusals_of(source: Source, fetch: TrackFetch) -> list[ProviderAttempt]:
    """*source*'s attempts in *fetch* that raised or timed out."""
    return [
        a
        for a in fetch.attempts
        if a.key.source is source and a.outcome in REFUSED_OUTCOMES
    ]


def _unsent(attempt: ProviderAttempt) -> bool:
    """True when *attempt* is a step its open breaker skipped, no request sent."""
    return attempt.facts is not None and attempt.facts.unsent


def tally_providers(
    in_table: Collection[Source],
    fetches: Iterable[TrackFetch],
    *,
    retried: Mapping[Source, Mapping[Refusal, int]] | None = None,
) -> tuple[ProviderCount, ...]:
    """Every provider's tally over *fetches*, in chain order.

    *in_table* is the providers the run's table held; any other has no count.
    *retried* is the run's retries by provider and reason, none when None.
    """
    done, counts = tuple(fetches), retried or {}
    return tuple(
        _count(source, done, total(counts, source))
        if source in in_table
        else ProviderCount(source, 0, None)
        for source in PROVIDERS
    )


def format_tally(tally: Iterable[ProviderCount]) -> str:
    """The summary's line: ``providers: LRCLIB 0/12 · NetEase 5/12 (3 refused) · Genius -``."""
    return f"{_LABEL} {DOT.join(count.describe() for count in tally)}"


def track_count(count: int) -> str:
    """``1 track`` or ``<n> tracks``."""
    return f"{count} track" if count == 1 else f"{count} tracks"
