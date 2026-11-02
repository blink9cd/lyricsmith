"""Refusals by provider and reason, counted from a manifest's tracks.

The album summary's ``refusals:`` line, ``refusals: LRCLIB overloaded 12, unreachable
1; Genius blocked 1``, counts tracks, not calls: a track counts once for each provider
and reason among its outcome's refusals (``after.refusals``), however many of that
provider's steps met it, so an LRC provider refusing both its synced and its plain step
counts one. A step its provider's open breaker skipped sent no request, so it counts
under ``breaker open`` (`Unsent`), never under the class that opened the breaker: one
Genius challenge and the twenty lookups it left unsent read ``Genius blocked 1, breaker
open 20``, not twenty-one blocks. A refusal that kept a Genius host is named by the
request it was (`lyrics_refusal_words.request_label`) after its count:
``Genius blocked 3 (unkeyed site)`` when every track counted under that reason named
the one request, else each request with its own count, ``blocked 3 (keyed API 1,
unkeyed site 2)``, in the labels' order; a refusal recorded with no host (an older
manifest, a timeout, a reply refused as too large, an unsent step) is counted but named
by none, and a reason whose refusals named none reads as before (`count_requests`).
Built from `ManifestTrack` alone, so the sweep can count a finished album the same way;
its own lines pass no requests, so they read as before. Providers follow the chain's
order (`lyrics_tally.PROVIDERS`), one no step asks any more last; reasons follow `Refusal`'s,
``breaker open`` after them all. Pure: no I/O, nothing printed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum

from lyrics_model import Source
from lyrics_records import ManifestTrack
from lyrics_refusal import ProviderRefusal, Refusal
from lyrics_refusal_words import REQUEST_LABELS, request_label
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
#: Tracks refused, by provider, key and the request each named (`request_label`).
RequestCounts = dict[Source, dict[RefusalKey, dict[str, int]]]
#: How the line opens, and what parts its providers and their reasons.
_LABEL = "refusals:"
_PROVIDER_GAP, _REASON_GAP = "; ", ", "
#: Each key's place on the line: the class order, then why a step went unsent.
_KEY_ORDER: Mapping[RefusalKey, int] = {
    key: index for index, key in enumerate((*Refusal, *Unsent))
}
#: The words of each `Unsent` member, to tell them from a class's when read back.
_UNSENT_WORDS = frozenset(member.value for member in Unsent)
#: Each request's place inside a reason's parentheses: the labels' own order.
_REQUEST_ORDER: Mapping[str, int] = {
    label: index for index, label in enumerate(REQUEST_LABELS.values())
}


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


def _named_requests(
    refusals: Iterable[ProviderRefusal],
) -> set[tuple[Source, RefusalKey, str]]:
    """The provider, key and named request of each of *refusals* that names one."""
    labeled = ((refusal, request_label(refusal.host)) for refusal in refusals)
    return {
        (refusal.provider, _key(refusal), label)
        for refusal, label in labeled
        if label is not None
    }


def count_requests(tracks: Iterable[ManifestTrack]) -> RequestCounts:
    """The tracks each provider refused under each key from each named request.

    Each track counts once per provider, key and request; a refusal whose host names
    no request (`request_label`) is left out, so a key with none is absent.
    """
    counts: RequestCounts = {}
    for track in tracks:
        if track.after is None:
            continue
        for provider, key, label in _named_requests(track.after.refusals):
            by_label = counts.setdefault(provider, {}).setdefault(key, {})
            by_label[label] = by_label.get(label, 0) + 1
    return counts


def _requests_part(count: int, by_label: Mapping[str, int]) -> str:
    """The parentheses after a reason's *count*: the requests it named, or nothing.

    One request naming every track counted is named alone; otherwise each is named
    with its own count, in the labels' order.
    """
    if not by_label:
        return ""
    if len(by_label) == 1 and sum(by_label.values()) == count:
        return f" ({next(iter(by_label))})"
    ordered = sorted(by_label.items(), key=lambda item: _REQUEST_ORDER[item[0]])
    return f" ({_REASON_GAP.join(f'{label} {n}' for label, n in ordered)})"


def _provider_place(source: Source) -> tuple[int, str]:
    """*source*'s place on the line: the chain's order, any other after, by name."""
    if source in PROVIDERS:
        return PROVIDERS.index(source), ""
    return len(PROVIDERS), source.value


def format_refusals[Key: RefusalKey](
    counts: Mapping[Source, Mapping[Key, int]],
    *,
    label: str = _LABEL,
    requests: Mapping[Source, Mapping[Key, Mapping[str, int]]] | None = None,
) -> str | None:
    """The summary's ``refusals:`` line, or None when nothing was refused.

    *label* opens the line: the sweep counts its retries by provider and reason the
    same way, under ``retried:``, whose keys are classes alone. *requests*, from
    `count_requests`, names the request each reason's refusals came from; None names
    none.
    """
    named = requests or {}
    entries = []
    for source in sorted(counts, key=_provider_place):
        reasons = sorted(counts[source].items(), key=lambda item: _KEY_ORDER[item[0]])
        by_key = named.get(source, {})
        parts = _REASON_GAP.join(
            f"{reason} {count}{_requests_part(count, by_key.get(reason, {}))}"
            for reason, count in reasons
        )
        entries.append(f"{source} {parts}")
    return f"{label} {_PROVIDER_GAP.join(entries)}" if entries else None
