"""An owed album that waits: one whose whole debt is held by a cool-down or a breaker.

A revisit made while every provider an album owes is still cooling down, or behind an
open breaker, asks none of them and ends owed again, so it would only cost a pause, a
run folder and the same ledger line. Just before visiting an owed album, the sweep reads
every provider's hold *fresh* (`read_holds`), through the album tool's own readers, at
the sweep's clock and in the state folder the environment names, which the album tool
it runs inherits: a provider is held by a **cool-down** while its cool-down file is
cooling (`lyrics_cooldown.read_cooldown`, `is_cooling`), and by a **breaker** while its
breaker file is open (`lyrics_breaker.read_breaker`, `BreakerState.is_open`); a
cool-down wins should both ever apply. A file that cannot be read, or holds what the
album tool would refuse, is no hold, as the album tool treats it, and its reader's
warning is kept.

The album's **effective debt** is what it owes less the providers the sweep's
``--no-*`` options turn off (`sweep_owed.turned_off`). When that is not empty and every
provider in it is held, the album waits on them (`waits_on`) and is passed over: not
run, no ledger line. Otherwise it is visited as ever, naming every provider it owes, a
held one included (the album tool keeps a held provider's debt itself). `HoldCheck` is
one sweep's use of the reader: none offline, none for an album owing nothing, and each
warning said once a sweep. The survey reads the holds once and splits the albums it
lists the same way (`split_waiting`). Nothing here prints: lines go to *progress*, or
are returned.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import lyrics_breaker
import lyrics_cooldown
from lyrics_model import Source
from sweep_owed import turned_off


class Hold(StrEnum):
    """What holds a provider back for now, as the progress line and report name it."""

    COOL_DOWN = "cool-down"
    BREAKER = "breaker"


@dataclass(frozen=True)
class HoldsRead:
    """One read of every provider's hold: the providers held, and the readers' warnings."""

    held: Mapping[Source, Hold] = field(default_factory=lambda: MappingProxyType({}))
    warnings: tuple[str, ...] = ()


#: Each provider an album waits on, with what holds it, in the chain's order.
Waits = tuple[tuple[Source, Hold], ...]


@dataclass(frozen=True)
class PassedOver:
    """An owed album passed over, by its name in the ledger, and what it waits on."""

    name: str
    waits: Waits


def no_holds() -> HoldsRead:
    """A read that holds nothing: the sweep's default, which never passes an album over."""
    return HoldsRead()


def read_holds(environ: Mapping[str, str], now: datetime) -> HoldsRead:
    """Every provider's hold at *now*, read from the state folder *environ* names.

    The cool-downs are laid over the breakers, so a cool-down wins for a provider both
    hold. Each file the album tool's reader ignores is no hold, its warning kept.
    """
    held: dict[Source, Hold] = {}
    warnings: list[str] = []
    for source in lyrics_breaker.BREAKER_FILES:
        breaker = lyrics_breaker.read_breaker(
            lyrics_breaker.breaker_path(environ, source), now
        )
        warnings += [] if breaker.warning is None else [breaker.warning]
        if breaker.state.is_open(now):
            held[source] = Hold.BREAKER
    for source in lyrics_cooldown.STATE_FILES:
        cooling = lyrics_cooldown.read_cooldown(
            lyrics_cooldown.cooldown_path(environ, source), now
        )
        warnings += [] if cooling.warning is None else [cooling.warning]
        if lyrics_cooldown.is_cooling(cooling.cool_down, now):
            held[source] = Hold.COOL_DOWN
    return HoldsRead(MappingProxyType(held), tuple(warnings))


def effective_debt(owed: Sequence[Source], skip: Sequence[str]) -> tuple[Source, ...]:
    """The providers of *owed* the sweep's ``--no-*`` options *skip* leave asked."""
    off = turned_off(skip)
    return tuple(source for source in owed if source not in off)


def waits_on(
    owed: Sequence[Source], held: Mapping[Source, Hold], skip: Sequence[str]
) -> Waits:
    """What an album owing *owed* waits on, in *owed*'s order; empty when it is visited.

    It waits only when its effective debt is not empty and *held* holds all of it.
    """
    debt = effective_debt(owed, skip)
    if not debt or any(source not in held for source in debt):
        return ()
    return tuple((source, held[source]) for source in debt)


def _waits_text(waits: Waits) -> str:
    """*waits* in words: ``Genius (breaker), Tekstowo (cool-down)``."""
    return ", ".join(f"{source} ({hold})" for source, hold in waits)


def passed_over_line(name: str, waits: Waits) -> str:
    """The progress line for album *name*, passed over waiting on *waits*."""
    return f"{name}: passed over, waits on: {_waits_text(waits)}"


def waiting_lines(passed: Sequence[PassedOver]) -> list[str]:
    """The report's and the survey's list of the albums *passed* over; none when empty."""
    if not passed:
        return []
    lines = [f"owed, waiting on a cool-down or breaker: {len(passed)}"]
    return lines + [f"  {p.name}  waits on: {_waits_text(p.waits)}" for p in passed]


def split_waiting(
    library: Path,
    albums: Sequence[Path],
    owed: Mapping[str, Sequence[Source]],
    held: Mapping[Source, Hold],
) -> tuple[tuple[Path, ...], tuple[PassedOver, ...]]:
    """*albums*, in order, split into those a run would visit and those it passes over.

    *owed* is each owed album's debt, by its name relative to *library*; the survey
    takes no ``--no-*`` option, so the whole debt is the effective one.
    """
    visit: list[Path] = []
    passed: list[PassedOver] = []
    for album in albums:
        name = album.relative_to(library).as_posix()
        waits = waits_on(owed.get(name, ()), held, ())
        if waits:
            passed.append(PassedOver(name, waits))
        else:
            visit.append(album)
    return tuple(visit), tuple(passed)


@dataclass
class HoldCheck:
    """One sweep's checks of what an owed album waits on, each a fresh read.

    *read* reads the holds; *progress* says each distinct reader warning once.
    """

    read: Callable[[], HoldsRead]
    progress: Callable[[str], None]
    said: set[str] = field(default_factory=set)

    def waits(
        self, owed: Sequence[Source], skip: Sequence[str], *, offline: bool
    ) -> Waits:
        """What an album owing *owed* waits on now; empty when it is to be visited.

        Offline no provider is asked, so nothing is waited on; an empty effective debt
        waits on nothing either. Neither reads the holds.
        """
        if offline or not effective_debt(owed, skip):
            return ()
        holds = self.read()
        for warning in holds.warnings:
            if warning not in self.said:
                self.said.add(warning)
                self.progress(warning)
        return waits_on(owed, holds.held, skip)
