"""The sweep's totals over the albums it visited: the final report's lines and progress.

Summed from the visits' ledger entries, never from their manifests again: the written
tracks by category, the removed ones by reason and the kept ones whose removal a fix
withheld on an album that looks spoken (`lyrics_categories`), the tracks
each provider refused by reason and the retries by provider and reason
(`lyrics_refusal_tally`, a lookup an open breaker left unsent under ``breaker open``),
each formatted by the album summary's own formatter, so the
sweep's lines read word for word as an album's do, in the same order, non-zero counts
only; and for the fix pass, the tracks it errored on, each text kept (`fix_lines`).
An entry with no tally made adds nothing. Pure: nothing here reads a file or prints.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_categories import (
    OutcomeCategories,
    RemovedReason,
    WrittenCategory,
    format_removed,
    format_withheld,
    format_written,
)
from lyrics_model import Source
from lyrics_refusal_tally import format_refusals
from lyrics_tally import track_count
from sweep_ledger import LedgerEntry, Outcome

#: How many albums visited between two progress lines.
PROGRESS_EVERY_ALBUMS = 25
#: How the retries' total line opens; the refusals' keeps the album summary's own.
_RETRIED_LABEL = "retried:"
#: What parts the progress line's counts from its refusals.
_PROGRESS_GAP = "; "


def album_count(count: int) -> str:
    """``1 album`` or ``<n> albums``, as `lyrics_tally.track_count` counts tracks."""
    return f"{count} album" if count == 1 else f"{count} albums"


def _add[Key](total: dict[Key, int], counts: Mapping[Key, int]) -> None:
    """Add each of *counts* into *total*."""
    for key, number in counts.items():
        total[key] = total.get(key, 0) + number


def sum_by_reason[Key](
    maps: Iterable[Mapping[Source, Mapping[Key, int]]],
) -> dict[Source, dict[Key, int]]:
    """*maps*, each a count per provider and reason (or refusal key), summed into one."""
    total: dict[Source, dict[Key, int]] = {}
    for counts in maps:
        for provider, reasons in counts.items():
            _add(total.setdefault(provider, {}), reasons)
    return total


def sum_categories(entries: Iterable[LedgerEntry]) -> OutcomeCategories:
    """The written tracks by category, the removed by reason, the withheld, over *entries*."""
    written: dict[WrittenCategory, int] = {}
    removed: dict[RemovedReason, int] = {}
    counted = [entry.counts for entry in entries if entry.counts is not None]
    for counts in counted:
        _add(written, counts.written_by)
        _add(removed, counts.removed_by)
    withheld = sum(counts.withheld for counts in counted)
    return OutcomeCategories(written, removed, withheld)


def total_lines(entries: Sequence[LedgerEntry]) -> list[str]:
    """The report's ``written:``, ``removed:``, ``removal withheld:``, ``refusals:``, ``retried:``.

    Each only when it counts something, in that order.
    """
    categories = sum_categories(entries)
    lines = (
        format_written(categories),
        format_removed(categories),
        format_withheld(categories),
        format_refusals(sum_by_reason(entry.refusals for entry in entries)),
        format_refusals(
            sum_by_reason(entry.retried for entry in entries), label=_RETRIED_LABEL
        ),
    )
    return [line for line in lines if line is not None]


def fix_lines(entries: Sequence[LedgerEntry]) -> list[str]:
    """The fix pass's ``errors:`` line, the tracks whose wrong text a fix kept.

    Each such track was left as it was, its text and its record kept, so a later
    fix pass tries it again; none when no track errored.
    """
    errors = sum(entry.counts.errors for entry in entries if entry.counts is not None)
    return [f"errors: {track_count(errors)} kept for a later fix"] if errors else []


def progress_line(entries: Sequence[LedgerEntry]) -> str:
    """The progress line: albums visited, done and owed so far, and the refusals."""
    outcomes = [entry.outcome for entry in entries]
    line = (
        f"progress: {album_count(len(entries))} visited, "
        f"{outcomes.count(Outcome.DONE)} done, {outcomes.count(Outcome.OWED)} owed"
    )
    refusals = format_refusals(sum_by_reason(entry.refusals for entry in entries))
    return line if refusals is None else f"{line}{_PROGRESS_GAP}{refusals}"


def progress_due(visited: int) -> bool:
    """True when a progress line follows the *visited*-th album visited, from 1."""
    return visited % PROGRESS_EVERY_ALBUMS == 0
