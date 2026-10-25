"""The per-provider tally a run's summary prints: each provider's tracks asked and won.

A provider that answers every request with plausible but useless data (an HTTP 200
whose hits the matcher rejects) wins no track and raises no error, so a summary that
counted only winners would never show it had stopped working. The tally shows it as
``0/<n>``. A provider that refused or failed instead of answering, with an error status,
a refusal code or a timeout, shows how many tracks it did so for, ``0/<n> (<r>
refused)``, so a refusing provider is told apart from one that answered with nothing;
a provider that refused no track shows no such count.

Counted per track, not per call: a provider asked for synced lyrics and then plain
text for one track counts once, and so does a track the stripped-title retry asked it
for twice. Every attempt the fetch records counts as asked, including a step that
read an answer its provider already gave for the track; a step the fetch skipped
because its provider said it would send no request for the query records none, so it
is not counted. A provider the run's table does not hold (Genius without a key, every
provider offline) has no count at all. Refused is counted the same way, once per track
however many of the provider's attempts for it raised or timed out. Pure: no I/O,
nothing printed. It also holds `track_count`, the one plural the summary's header and
the run's stderr progress line share: `lyrics_run` cannot import `lyrics_summary`,
which imports it.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from lyrics_fetch import CHAIN
from lyrics_model import REFUSED_OUTCOMES, ProviderAttempt, Source

#: Every provider, in the order the chain first asks it.
PROVIDERS: tuple[Source, ...] = tuple(dict.fromkeys(key.source for key in CHAIN))
#: What a provider outside the run's table shows instead of a count.
_ABSENT = "-"
#: How the line opens.
_LABEL = "providers:"
#: Between the entries of a counts line: this one's, and every report's closing one.
DOT = " · "


@dataclass(frozen=True)
class TrackFetch:
    """One track's fetch as the tally reads it.

    *attempts* is every provider call the fetch recorded. *accepted* is the provider
    whose answer the run kept for the track, or None: nothing found, a sidecar won,
    or a fresh run declined the answer.
    """

    attempts: Sequence[ProviderAttempt]
    accepted: Source | None


@dataclass(frozen=True)
class ProviderCount:
    """One provider's tally: tracks whose answer was kept, and tracks it was asked for.

    *asked* is None when the run's table did not hold the provider. *refused* is the
    tracks it refused or failed for, at any of its steps. *own* is the tracks compare
    set aside because the provider wrote their lyrics; a run's tally leaves it 0.
    """

    source: Source
    accepted: int
    asked: int | None
    refused: int = 0
    own: int = 0

    def describe(self) -> str:
        """This provider's entry on the line: ``NetEase 5/12 (3 refused)``, or ``-``."""
        if self.asked is None:
            return f"{self.source} {_ABSENT}"
        parts = [f"{self.own} own"] if self.own else []
        if self.refused:
            parts.append(f"{self.refused} refused")
        noted = f" ({', '.join(parts)})" if parts else ""
        return f"{self.source} {self.accepted}/{self.asked}{noted}"


def _count(source: Source, fetches: Sequence[TrackFetch]) -> ProviderCount:
    """*source*'s tally over *fetches*, each track counted once."""
    asked = sum(1 for f in fetches if any(a.key.source is source for a in f.attempts))
    accepted = sum(1 for f in fetches if f.accepted is source)
    refused = sum(
        1
        for f in fetches
        if any(
            a.key.source is source and a.outcome in REFUSED_OUTCOMES for a in f.attempts
        )
    )
    return ProviderCount(source, accepted, asked, refused)


def tally_providers(
    in_table: Collection[Source], fetches: Iterable[TrackFetch]
) -> tuple[ProviderCount, ...]:
    """Every provider's tally over *fetches*, in chain order.

    *in_table* is the providers the run's table held; any other has no count.
    """
    done = tuple(fetches)
    return tuple(
        _count(source, done) if source in in_table else ProviderCount(source, 0, None)
        for source in PROVIDERS
    )


def format_tally(tally: Iterable[ProviderCount]) -> str:
    """The summary's line: ``providers: LRCLIB 0/12 · NetEase 5/12 (3 refused) · Genius -``."""
    return f"{_LABEL} {DOT.join(count.describe() for count in tally)}"


def track_count(count: int) -> str:
    """``1 track`` or ``<n> tracks``."""
    return f"{count} track" if count == 1 else f"{count} tracks"
