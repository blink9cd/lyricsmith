"""What an earlier run owes: the debt of an album swept before the owed outcome existed.

A ledger line written before ``owed_providers`` was recorded reads ``done`` however its
providers refused, so a resume passes the album over. ``--owe-earlier-runs`` reads such
a line again (`plan_ledger`): an *earlier* entry (`is_earlier`) is one whose outcome is
done and whose line held no ``owed_providers`` at all. A line that holds the field,
even empty, is never read again, so a later done that names a refusal but owes nothing
stays done.

An earlier entry's debt (`earlier_debt`) is the union, in the chain's order
(`lyrics_tally.PROVIDERS`), of two debts:

- each refusing provider its line names, by label (``LRCLIB``), mapped to the askable
  provider of that label (`refusal_debt`); a label no askable provider has is dropped,
  as the owed rule drops a provider no step asks;
- each provider the owed rule owes for its run's cool-downs and stops, read from its
  run's manifest (`sweep_tally.cooling_owed`): one left out by a cool-down, or stopped
  part-way, that a track the run reached and left unasked could still answer. It is
  the very rule every run's tally owes by, so an earlier run owes what a run made
  today would.

The manifest is the one in the album's run folder named by the entry's run id, read
through the album tool's own reader (`sweep_child.read_run`). A run id that is null, or
is no run's name, or a manifest that cannot be read, adds no cool-down debt, which a
line through *progress* says, naming the album; the refusals are still owed.

A non-empty debt makes the album owed for this sweep, exactly as an owed album is: out
of the done set, and into the debts the visits are handed. An empty one leaves it done.
Only an album needing a visit (`sweep_survey.needing_visit`) is read, so a fully
synced album is never visited for it.

**A challenged album.** The plan names too each album whose newest entry, the one its
debt is read from, records a challenge (``challenged_providers``) by a provider in
that debt (`challenged_debts`): the sweep visits it last (`sweep_challenged`). An
earlier entry holds no such field, so it never counts, and a done album owes nothing.
Nothing here prints or writes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_model import Source
from lyrics_records import Manifest
from lyrics_run_folders import run_order
from lyrics_tally import PROVIDERS
from sweep_child import read_run
from sweep_ledger import (
    LedgerEntry,
    LedgerRecord,
    Outcome,
    SweepPass,
    done_albums,
    newest_entries,
    owed_albums,
)
from sweep_tally import cooling_owed

#: Each askable provider by its label, as a ledger line names a refusing one.
_BY_LABEL: Mapping[str, Source] = MappingProxyType(
    {source.label: source for source in PROVIDERS}
)
#: Each album's debt, by its name in the ledger.
Debts = dict[str, tuple[Source, ...]]


@dataclass(frozen=True)
class LedgerPlan:
    """What the ledger says of a sweep's albums: done, owed, and owed by earlier runs.

    *owed* holds every album's debt, those *earlier* adds among them; *earlier* holds
    only the albums ``--owe-earlier-runs`` added, in the order of the albums needing a
    visit, each with its debt. *challenged_by* holds each album whose last visit was
    challenged by a provider it still owes, with those providers (`challenged_debts`).
    """

    done: frozenset[str]
    owed: Debts
    earlier: Debts
    challenged_by: Debts = field(default_factory=dict)

    @property
    def challenged(self) -> frozenset[str]:
        """The albums the sweep visits last: those *challenged_by* names."""
        return frozenset(self.challenged_by)


def is_earlier(entry: LedgerEntry) -> bool:
    """True when *entry* is done and its line held no owed providers at all."""
    return entry.outcome is Outcome.DONE and not entry.owed_recorded


def refusal_debt(labels: Iterable[str]) -> frozenset[Source]:
    """The askable providers *labels* name; a label none of them has is dropped."""
    return frozenset(_BY_LABEL[label] for label in labels if label in _BY_LABEL)


def _manifest_of(library: Path, entry: LedgerEntry) -> Manifest | None:
    """*entry*'s run manifest, or None for a run id that is null or no run's name."""
    if entry.run_id is None or run_order(entry.run_id) is None:
        return None
    return read_run(library / entry.album, entry.run_id)


def earlier_debt(
    library: Path, entry: LedgerEntry, progress: Callable[[str], None]
) -> tuple[Source, ...]:
    """What earlier entry *entry* owes, in chain order, as the module docstring says.

    A manifest that cannot be had is said through *progress*, naming the album.
    """
    owed = set(refusal_debt(entry.refusing_providers))
    manifest = _manifest_of(library, entry)
    if manifest is None:
        progress(
            f"{entry.album}: the manifest of its earlier run {entry.run_id or '-'} "
            "cannot be read, so it owes no provider that was cooling down"
        )
    else:
        owed |= cooling_owed(manifest)
    return tuple(source for source in PROVIDERS if source in owed)


def challenged_debts(newest: Mapping[str, LedgerEntry], owed: Debts) -> Debts:
    """Each album of *owed* whose *newest* entry was challenged by a provider it owes.

    With those providers, in chain order.
    """
    debts: Debts = {}
    for name, debt in owed.items():
        # Every debt is read from the album's newest entry, so each has one.
        hit = newest[name].challenged_providers
        both = tuple(source for source in debt if source in hit)
        if both:
            debts[name] = both
    return debts


def plan_ledger(
    records: Sequence[LedgerRecord],
    library: Path,
    needing: Sequence[str],
    *,
    lang: str,
    pass_: SweepPass,
    owe_earlier: bool,
    progress: Callable[[str], None],
) -> LedgerPlan:
    """The done set and debts *records* hold for *lang* and *pass_*.

    With *owe_earlier*, each album of *needing* whose newest entry is earlier
    (`is_earlier`) and owes a debt (`earlier_debt`) leaves the done set for the debts.
    """
    done = done_albums(records, lang, pass_)
    owed = owed_albums(records, lang, pass_)
    newest = newest_entries(records, lang, pass_)
    earlier: Debts = {}
    if owe_earlier:
        for name in needing:
            entry = newest.get(name)
            if entry is not None and is_earlier(entry):
                debt = earlier_debt(library, entry, progress)
                if debt:
                    earlier[name] = debt
    debts = {**owed, **earlier}
    return LedgerPlan(
        done - set(earlier), debts, earlier, challenged_debts(newest, debts)
    )
