"""An owed album's revisit: which providers it names, and which the sweep turned off.

The ledger keeps what an owed album owes (`sweep_ledger.owed_albums`). Its revisit
hands the album tool ``--only-providers`` naming those providers (`revisit_only`), so it
asks none that answered, beside the sweep's own ``--no-*`` options, which the album tool
lets win: a provider both named and turned off is off. An offline revisit names none,
since the album tool refuses the option offline and asks no provider anyway. The LRC
providers whose synced step a visit asks (`synced_asked`) are those the two leave on:
the tally reads a track holding its own words by those steps alone.

A provider the sweep turns off is never owed (`without_turned_off`): a run that never
asked it records nothing for it, and the sweep holds to that whatever a manifest says,
so a debt it cannot pay is never recorded. A revisit that drops a debt so, because
this sweep turns off a provider an earlier one was owed, says so (`dropped_notice`).
Nothing here reads a file or prints.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_fetch import LRC_PROVIDERS
from lyrics_model import Source
from lyrics_only import option_name
from lyrics_tally import PROVIDERS
from sweep_tally import AlbumTally

#: How a provider's own option to leave it unasked opens: ``--no-`` and its option name,
#: as the album tool and the sweep both name them (``--no-youtube-music``).
_NO_PREFIX = "--no-"


def turned_off(skip: Sequence[str]) -> frozenset[Source]:
    """The providers *skip*, the sweep's ``--no-*`` options given, leaves unasked."""
    return frozenset(
        source for source in PROVIDERS if f"{_NO_PREFIX}{option_name(source)}" in skip
    )


def without_turned_off(tally: AlbumTally, skip: Sequence[str]) -> AlbumTally:
    """*tally*, owing no provider *skip* leaves unasked."""
    off = turned_off(skip)
    owed = tuple(source for source in tally.owed if source not in off)
    return dataclasses.replace(tally, owed=owed)


def dropped_debt(owed: Sequence[Source], skip: Sequence[str]) -> tuple[Source, ...]:
    """The providers of *owed* that *skip* turns off: a debt this revisit drops."""
    off = turned_off(skip)
    return tuple(source for source in owed if source in off)


def dropped_notice(name: str, dropped: Sequence[Source]) -> str:
    """The line saying album *name* no longer owes *dropped*, turned off by the sweep."""
    labels = ", ".join(map(str, dropped))
    return f"{name}: no longer owed {labels}, which this sweep turns off"


def revisit_only(owed: Sequence[Source], *, offline: bool) -> tuple[Source, ...]:
    """The providers a visit names with ``--only-providers``: none offline or unowed."""
    return () if offline else tuple(owed)


def synced_asked(only: Sequence[Source], skip: Sequence[str]) -> frozenset[Source]:
    """The LRC providers whose synced step a visit asks, given *only* and *skip*.

    *only* is the visit's ``--only-providers`` (`revisit_only`), none naming every
    provider; a provider *skip* turns off is never asked. A debt of untimed-only
    providers alone asks no synced step at all, so the set is empty.
    """
    off = turned_off(skip)
    return frozenset(
        source
        for source in LRC_PROVIDERS
        if source not in off and (not only or source in only)
    )
