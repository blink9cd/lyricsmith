"""Refusals by provider and reason, counted from a manifest's tracks.

The album summary's ``refusals:`` line, ``refusals: LRCLIB overloaded 12, unreachable
1; Genius blocked 1``, counts tracks, not calls: a track counts once for each provider
and reason among its outcome's refusals (``after.refusals``), however many of that
provider's steps met it, so an LRC provider refusing both its synced and its plain step
counts one. A step its provider's open breaker skipped sent no request, so it counts
under ``breaker open`` (`Unsent`), never under the class that opened the breaker: one
Genius challenge and the twenty lookups it left unsent read ``Genius blocked 1, breaker
open 20``, not twenty-one blocks. Built from `ManifestTrack` alone, so the sweep can
count a finished album the same way. Providers follow the chain's order
(`lyrics_tally.PROVIDERS`), one no step asks any more last; reasons follow `Refusal`'s,
``breaker open`` after them all. Pure: no I/O, nothing printed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum

from lyrics_model import Source
from lyrics_records import ManifestTrack
from lyrics_refusal import ProviderRefusal, Refusal
from lyrics_tally import PROVIDERS


class Unsent(StrEnum):
    """Why a refused step sent no request, counted apart from every `Refusal`.

    Stored in the sweep's ledger by its words, beside the classes' own.
    """

    BREAKER_OPEN = "breaker open"


#: What a refusal counts under: its class, or why it was never sent.
RefusalKey = Refusal | Unsent
#: Tracks refused, by provider and key.
RefusalCounts = dict[Source, dict[RefusalKey, int]]
#: How the line opens, and what parts its providers and their reasons.
_LABEL = "refusals:"
_PROVIDER_GAP, _REASON_GAP = "; ", ", "
#: Each key's place on the line: the class order, then why a step went unsent.
_KEY_ORDER: Mapping[RefusalKey, int] = {
    key: index for index, key in enumerate((*Refusal, *Unsent))
}
#: The words of each `Unsent` member, to tell them from a class's when read back.
_UNSENT_WORDS = frozenset(member.value for member in Unsent)


def refusal_key(words: str) -> RefusalKey:
    """The key *words* name, a class or why a step went unsent; ValueError otherwise."""
    return Unsent(words) if words in _UNSENT_WORDS else Refusal(words)


def _key(refusal: ProviderRefusal) -> RefusalKey:
    """What *refusal* counts under: ``breaker open`` when unsent, else its class."""
    return Unsent.BREAKER_OPEN if refusal.unsent else refusal.reason


def count_refusals(tracks: Iterable[ManifestTrack]) -> RefusalCounts:
    """The tracks each provider refused under each key, each track counted once."""
    counts: RefusalCounts = {}
    for track in tracks:
        if track.after is None:
            continue
        met = {(refusal.provider, _key(refusal)) for refusal in track.after.refusals}
        for provider, key in met:
            by_key = counts.setdefault(provider, {})
            by_key[key] = by_key.get(key, 0) + 1
    return counts


def _provider_place(source: Source) -> tuple[int, str]:
    """*source*'s place on the line: the chain's order, any other after, by name."""
    if source in PROVIDERS:
        return PROVIDERS.index(source), ""
    return len(PROVIDERS), source.value


def format_refusals[Key: RefusalKey](
    counts: Mapping[Source, Mapping[Key, int]], *, label: str = _LABEL
) -> str | None:
    """The summary's ``refusals:`` line, or None when nothing was refused.

    *label* opens the line: the sweep counts its retries by provider and reason the
    same way, under ``retried:``, whose keys are classes alone.
    """
    entries = []
    for source in sorted(counts, key=_provider_place):
        reasons = sorted(counts[source].items(), key=lambda item: _KEY_ORDER[item[0]])
        parts = _REASON_GAP.join(f"{reason} {count}" for reason, count in reasons)
        entries.append(f"{source} {parts}")
    return f"{label} {_PROVIDER_GAP.join(entries)}" if entries else None
