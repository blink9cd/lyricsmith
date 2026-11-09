"""The hearing report's arithmetic: each group's tally, the library-wide estimate, the comparison.

Pure functions over hearing records (`lyrics_hearing_log`) and strata rows
(`sweep_strata`); the report (`sweep_hear_report`) reads both and prints what these
give. Nothing here reads a file or prints.

**A group's rate** is its `not sung` hearings over the tracks of it heard, every verdict
counted, so a group's size times its rate is how many of its tracks the hearing would
find not sung. A group none of whose tracks was heard has no rate.

**The estimate** (`estimate`), as the sample's design weighs it: each drawn
provenance group's rate weighted by that group's size in the library, which the draw's
own classifier counts outside the wrong-text albums, plus the wrong-text group's own
`not sung` count as it stands, since that group was taken whole and never drawn. A
drawn group with tracks in the library but none heard is left out and named, and its
size is left out of the total too, so the share printed is of the tracks the estimate
covers; the ``withheld`` group is never part of it.

**The comparison** (`compare`): each strata track heard by both models,
with its share and verdict under each, and how many rise from under the hearing's share
(`lyrics_heard.MIN_HEARD_SHARE`) with the first model to `sung` with the second, which
is at or over that share on enough pairs (`lyrics_heard.MIN_HEARD_PAIRS`): misses that
are the model's, not the text's. A second hearing over the share but `little heard`
does not count.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final, NamedTuple

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_heard import MIN_HEARD_SHARE
from lyrics_hearing_log import HeardVerdict, HearingRecord
from sweep_draw import DRAWN_GROUPS
from sweep_draw_classify import LeftOut
from sweep_strata import StrataGroup, StrataRow

#: The skip reason of a strata track with no hearing by the model, still of its kind.
NOT_HEARD: Final = "not heard"
#: A hearing's key: the track's path as the strata file spells it, and the model.
Key = tuple[str, str]


@dataclass(frozen=True)
class Hearings:
    """The log's records of strata tracks, by path and model, and how many were not."""

    by_key: Mapping[Key, HearingRecord]
    outside: int


def index_hearings(
    records: Iterable[HearingRecord], rows: Iterable[StrataRow]
) -> Hearings:
    """*records* of the tracks *rows* name, by path and model; the rest only counted."""
    paths = {row.path for row in rows}
    by_key: dict[Key, HearingRecord] = {}
    outside = 0
    for record in records:
        if record.path in paths:
            by_key[record.path, record.model] = record
        else:
            outside += 1
    return Hearings(by_key, outside)


@dataclass
class GroupTally:
    """One group's strata tracks: heard, by verdict, skipped by reason, and shift-tested."""

    rows: int = 0
    heard: int = 0
    not_sung: int = 0
    little_heard: int = 0
    shifts: int = 0
    suspects: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def add_record(self, record: HearingRecord) -> None:
        """Count a track heard, by its verdict."""
        self.rows += 1
        self.heard += 1
        self.not_sung += record.verdict is HeardVerdict.NOT_SUNG
        self.little_heard += record.verdict is HeardVerdict.LITTLE_HEARD

    def add_skip(self, reason: str) -> None:
        """Count a track not heard, by *reason*."""
        self.rows += 1
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def add_shift(self, *, shifted: bool) -> None:
        """Count a `not sung` track's shift test: a shift, or a suspect miss."""
        if shifted:
            self.shifts += 1
        else:
            self.suspects += 1

    def rate(self) -> float | None:
        """The share of the tracks heard found `not sung`; None when none was heard."""
        return self.not_sung / self.heard if self.heard else None


@dataclass(frozen=True)
class Estimate:
    """The library-wide `not sung` estimate and what it was made of.

    *not_sung* is the estimated count, of *tracks* provider-timed tracks in the
    groups it covers (each drawn group heard, and the wrong-text group); *weighted* each drawn group's size and rate, *unheard* each drawn
    group left out (with its size), *wrong_text* that group's count heard `not sung`
    and its size.
    """

    not_sung: float
    tracks: int
    weighted: tuple[tuple[StrataGroup, int, float], ...]
    unheard: tuple[tuple[StrataGroup, int], ...]
    wrong_text: tuple[int, int]


def estimate(
    tallies: Mapping[StrataGroup, GroupTally], sizes: Mapping[StrataGroup, int]
) -> Estimate:
    """The estimate from each group's *tallies* and each group's *sizes* in the library."""
    weighted: list[tuple[StrataGroup, int, float]] = []
    unheard: list[tuple[StrataGroup, int]] = []
    for group in DRAWN_GROUPS:
        size = sizes.get(group, 0)
        tally = tallies.get(group)
        rate = tally.rate() if tally is not None else None
        if rate is not None:
            weighted.append((group, size, rate))
        elif size:
            unheard.append((group, size))
    wrong = tallies.get(StrataGroup.WRONG_TEXT_ALBUMS)
    wrong_text = (
        wrong.not_sung if wrong else 0,
        sizes.get(StrataGroup.WRONG_TEXT_ALBUMS, 0),
    )
    # An unheard group is named apart, never in the total: it adds nothing to not_sung.
    total = sum(size for _, size, _ in weighted) + wrong_text[1]
    not_sung = sum(size * rate for _, size, rate in weighted) + wrong_text[0]
    return Estimate(not_sung, total, tuple(weighted), tuple(unheard), wrong_text)


class Side(NamedTuple):
    """One model's hearing of a compared track: its share and its verdict."""

    share: float
    verdict: HeardVerdict


@dataclass(frozen=True)
class Compared:
    """One track's hearing under the first model and under the second."""

    path: str
    first: Side
    second: Side

    def crossed(self) -> bool:
        """True when under the share with the first model, and sung with the second."""
        sung = self.second.verdict is HeardVerdict.SUNG
        return self.first.share < MIN_HEARD_SHARE <= self.second.share and sung


@dataclass(frozen=True)
class Comparison:
    """Every strata track heard by both models, in strata order, and those crossing."""

    tracks: tuple[Compared, ...]
    crossed: int


def compare(
    rows: Iterable[StrataRow], hearings: Hearings, model: str, other: str
) -> Comparison:
    """Each of *rows*' tracks heard by *model* and *other*, and how many cross the share."""
    found: list[Compared] = []
    for row in rows:
        first = hearings.by_key.get((row.path, model))
        second = hearings.by_key.get((row.path, other))
        if first is not None and second is not None:
            found.append(
                Compared(
                    row.path,
                    Side(first.share, first.verdict),
                    Side(second.share, second.verdict),
                )
            )
    crossed = sum(track.crossed() for track in found)
    return Comparison(tuple(found), crossed)


@dataclass(frozen=True)
class HearReport:
    """Everything the report prints, each count exact; the formatter rounds them.

    *models* are ``--model`` and ``--compare-model`` (or None); *tallies* each group's,
    in the groups' own order; *estimate* None, and *left_out* empty, for a sample
    holding no group the estimate weighs. *genres* tallies the suspect misses;
    *untested* counts the shift tests not run, *unread* the sibling texts passed over,
    *outside* the log's records of tracks in no strata row, and *problems* says each
    log file that could not be read.
    """

    seed: int
    models: tuple[str, str | None]
    tallies: Mapping[StrataGroup, GroupTally]
    estimate: Estimate | None
    left_out: Mapping[LeftOut, int]
    genres: Mapping[str, int]
    untested: int
    unread: int
    outside: int
    problems: tuple[str, ...]
    comparison: Comparison | None
