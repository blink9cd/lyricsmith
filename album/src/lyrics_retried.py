"""The refusals a retry got past: counted per provider and reason over a run.

`lyrics_http`'s retrying GET tells its policy's hook of each retry it makes, whether
the request then answered or not; the run's `RetryTally` is that hook
(`RetryTally.count`), given to the table's retrying clients (`lyrics_table`). The run
keeps the counts in its manifest as the manifest-level ``retried`` map, provider to
reason to count (`read_retried` reads it back), and its summary's provider line shows
each provider's total (`lyrics_tally`). A manifest written before the map holds none.
MusicBrainz's requests are Bandcamp's step's, so they count under Bandcamp. Genius
retries a challenge on its own schedule, outside `lyrics_http`; its step tells the same
hook of each retry it makes (`lyrics_genius.GeniusProvider.on_retry`). Nothing here
prints.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypeAlias

from lyrics_manifest_fields import Fields, malformed, member, number_value
from lyrics_model import Source
from lyrics_refusal import Refusal

#: Retries made, by the provider asked and the class of the refusal retried.
RetriedCounts: TypeAlias = dict[Source, dict[Refusal, int]]


def no_retries() -> RetriedCounts:
    """No retries made: what a run with no tally records."""
    return {}


@dataclass
class RetryTally:
    """A run's retries, counted as they are made, from any thread.

    Two tallies holding the same counts are equal, so a run's settings compare by
    what they hold.
    """

    _counts: RetriedCounts = field(default_factory=dict)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def count(self, source: Source, reason: Refusal) -> None:
        """Count one retry of *source*'s request after a refusal of *reason*."""
        with self._lock:
            by_reason = self._counts.setdefault(source, {})
            by_reason[reason] = by_reason.get(reason, 0) + 1

    def counts(self) -> RetriedCounts:
        """A copy of the counts so far."""
        with self._lock:
            return {source: dict(by) for source, by in self._counts.items()}


def total(counts: Mapping[Source, Mapping[Refusal, int]], source: Source) -> int:
    """*source*'s retries over every reason; 0 when it made none."""
    return sum(counts.get(source, {}).values())


def _reason_counts(value: object, where: str) -> dict[Refusal, int]:
    """One provider's counts: each a known reason's count of zero or more."""
    if not isinstance(value, dict):
        raise malformed(where, "an object")
    counts: dict[Refusal, int] = {}
    for key, count in value.items():
        at = f"{where}.{key}"
        reason = member(Refusal)(key, at)
        number = number_value(count, at)
        if number < 0:
            raise malformed(at, "a count of zero or more")
        counts[reason] = number
    return counts


def read_retried(fields: Fields, key: str) -> RetriedCounts:
    """The manifest's *key* map, or none when an older manifest lacks it.

    Each provider and reason must be a known one, each count an integer of zero or
    more; anything else, null included, is a `ManifestSchemaError`.
    """
    if not fields.has(key):
        return no_retries()
    value, where = fields.value(key), fields.at(key)
    if not isinstance(value, dict):
        raise malformed(where, "an object")
    return {
        member(Source)(provider, f"{where}.{provider}"): _reason_counts(
            by_reason, f"{where}.{provider}"
        )
        for provider, by_reason in value.items()
    }
