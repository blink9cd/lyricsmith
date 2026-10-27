"""A provider's challenge: which refusals were one, and the run's word on an album put last.

A challenge is a lookup that was sent and refused as blocked (`was_challenged`): a
``403`` from a challenge page, or Tekstowo's challenge, which it records as blocked
with no status, so the status is never read. The album tool stops asking that
provider once one meets it: its breaker opens, or, for a cooling source such as
Tekstowo, it stops for the run. While it is stopped no lookup of the provider is sent.
An open breaker records each skipped lookup as a refusal of the class that opened it,
marked unsent (`ProviderRefusal.unsent`), which the album tool's reports count as
``breaker open``; a stopped cooling source records none. An unsent lookup asked
nothing, so it is no challenge. That is the refusal record's own fact, never read from
its detail text.

An owed album whose last visit was challenged by a provider it still owes is visited
after every other album to visit (`sweep_earlier.LedgerPlan.challenged`,
`sweep_survey.plan_visits`), so its challenge, still sent once a sweep, no longer
costs every album after it that provider. The run says so once per such album
(`last_notices`). Nothing here reads a file or prints.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_model import Source
from lyrics_records import TrackOutcome
from lyrics_refusal import ProviderRefusal, Refusal
from lyrics_tally import PROVIDERS


def was_challenged(refusal: ProviderRefusal) -> bool:
    """True when *refusal* was sent and refused as blocked: a provider's challenge."""
    return refusal.reason is Refusal.BLOCKED and not refusal.unsent


def challenged_providers(outcomes: Iterable[TrackOutcome]) -> tuple[Source, ...]:
    """The askable providers a track of *outcomes* was challenged by, in chain order."""
    hit = {
        refusal.provider
        for outcome in outcomes
        for refusal in outcome.refusals
        if was_challenged(refusal)
    }
    return tuple(source for source in PROVIDERS if source in hit)


def challenged_notice(name: str, providers: Sequence[Source]) -> str:
    """The line saying album *name* is visited last, challenged by *providers*."""
    labels = ", ".join(map(str, providers))
    return f"{name}: visited last, its last visit challenged by {labels}"


def last_notices(
    library: Path,
    to_visit: Iterable[Path],
    challenged_by: Mapping[str, Sequence[Source]],
) -> list[str]:
    """A `challenged_notice` for each album of *to_visit* that *challenged_by* names.

    In visit order; *challenged_by* names each album as the ledger does, relative to
    *library*, with the providers its last visit was challenged by that it still owes.
    """
    names = (album.relative_to(library).as_posix() for album in to_visit)
    return [
        challenged_notice(name, challenged_by[name])
        for name in names
        if name in challenged_by
    ]
