"""The hearing report's text: each group, the estimate, the shift test, the comparison.

`format_report` turns a `sweep_hear_tally.HearReport` into the lines the report prints.
Every count goes through the draw's own rounding (`sweep_draw.approximate`: none, under
10, or about it to two figures), and a rate prints as a percentage to one decimal place,
so every line but the comparison's per-track block holds no exact collection figure and
can be recorded as it stands. That block names each track by its absolute path, with
its exact share and verdict under each model (a share as the hearing's own rows print
one, `lyrics_heard.shown_share`), so it opens with `PER_TRACK_HEADER`, which says its
lines are not for the record. Each line has its control characters made visible, since
paths and genre tags come from the library. Pure: nothing here reads or prints.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_heard import MIN_HEARD_SHARE, Heard, shown_share
from stdio_utf8 import spell_unsafe
from sweep_draw import approximate
from sweep_draw_classify import LeftOut
from sweep_hear_tally import Comparison, Estimate, GroupTally, HearReport, Side
from sweep_strata import StrataGroup

#: The line opening the comparison's per-track block, the one part not to record.
PER_TRACK_HEADER: Final = (
    "per track (these lines name tracks and give exact shares: not for the record):"
)


def _percent(rate: float) -> str:
    """*rate*, a share from 0 to 1, as a percentage to one decimal place."""
    return f"{rate:.1%}"


def _side(side: Side) -> str:
    """A hearing's share as the hearing's own rows print it, then its verdict."""
    return f"{shown_share(Heard(0, side.share))} {side.verdict}"


def _group_lines(group: StrataGroup, tally: GroupTally) -> list[str]:
    """One group's line, then a line for each reason any of its tracks was skipped."""
    rate = tally.rate()
    rated = (
        "no rate, none heard" if rate is None else f"{_percent(rate)} of those heard"
    )
    lines = [
        f"{group}: {approximate(tally.rows)} in the sample, {approximate(tally.heard)} heard; "
        f"not sung {approximate(tally.not_sung)} ({rated}); "
        f"little heard {approximate(tally.little_heard)}; "
        f"shifts {approximate(tally.shifts)}; "
        f"suspect misses {approximate(tally.suspects)}"
    ]
    lines += [
        f"{group}, skipped, {reason}: {approximate(count)}"
        for reason, count in tally.skipped.items()
    ]
    return lines


def _estimate_lines(found: Estimate, left_out: Mapping[LeftOut, int]) -> list[str]:
    """The estimate, what each group gave it, then the library's left-out counts."""
    share = _percent(found.not_sung / found.tracks) if found.tracks else "no tracks"
    lines = [
        f"library-wide estimate: {approximate(round(found.not_sung))} not sung of "
        f"{approximate(found.tracks)} provider-timed tracks ({share})"
    ]
    lines += [
        f"  {group}: {approximate(size)} in the library at {_percent(rate)}"
        for group, size, rate in found.weighted
    ]
    lines += [
        f"  {group}: {approximate(size)} in the library, none heard: "
        "left out of the estimate"
        for group, size in found.unheard
    ]
    not_sung, size = found.wrong_text
    lines.append(
        f"  {StrataGroup.WRONG_TEXT_ALBUMS}: {approximate(not_sung)} not sung as "
        f"heard, of {approximate(size)}"
    )
    lines += [
        f"left out of every group, {reason}: {approximate(count)}"
        for reason, count in left_out.items()
    ]
    return lines


def _shift_lines(report: HearReport) -> list[str]:
    """The suspect misses by genre tag, most first, and what the shift test could not read."""
    ranked = sorted(report.genres.items(), key=lambda item: (-item[1], item[0]))
    lines = [
        f"suspect misses by genre tag, {genre}: {approximate(count)}"
        for genre, count in ranked
    ]
    if report.untested:
        lines.append(
            f"shift test not run, album unread: {approximate(report.untested)}"
        )
    if report.unread:
        lines.append(f"sibling texts unread: {approximate(report.unread)}")
    return lines


def _comparison_lines(found: Comparison, model: str, other: str) -> list[str]:
    """How many both heard, each track's hearing under both, then how many rose."""
    lines = [f"compared with {other}: {approximate(len(found.tracks))} heard by both"]
    if found.tracks:
        lines.append(PER_TRACK_HEADER)
    lines += [
        f"  {track.path}: {_side(track.first)} with {model}, "
        f"{_side(track.second)} with {other}"
        for track in found.tracks
    ]
    lines.append(
        f"rising to {MIN_HEARD_SHARE} or over with {other} alone: "
        f"{approximate(found.crossed)}"
    )
    return lines


def format_report(report: HearReport) -> str:
    """The printed report: the seed and model, each group, the estimate, the trust measures."""
    model, other = report.models
    lines = [f"seed: {report.seed}", f"model: {model}"]
    for group, tally in report.tallies.items():
        lines += _group_lines(group, tally)
    if report.estimate is not None:
        lines += _estimate_lines(report.estimate, report.left_out)
    lines += _shift_lines(report)
    lines.append(f"log records in no strata row: {approximate(report.outside)}")
    lines.append(f"log files unreadable: {approximate(len(report.problems))}")
    if report.comparison is not None and other is not None:
        lines += _comparison_lines(report.comparison, model, other)
    return "".join(spell_unsafe(line) + "\n" for line in lines)
