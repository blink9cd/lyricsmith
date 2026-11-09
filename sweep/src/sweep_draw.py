"""The sample's draw: provider-timed tracks to hear, by group, into a track list and a strata file.

`draw_mode` is ``sweep --mode draw``. It reads the ledger, read-only, classifies
every track of the library (`sweep_draw_library.classify_library`), draws, and writes
two files into ``--out``: ``tracks.txt``, the ``--changed-list`` format that
``sync --tracks-from`` reads, made by `lyrics_changed.format_changed_list`, and
``strata.tsv`` (`sweep_strata.format_strata`), the same paths with each one's group
and album under a header naming the seed. Both go through
`lyrics_state.write_state_file` (0o600, atomic, read back), so a rerun replaces them.
It plays no audio, asks no provider and writes nothing in the library: an ``--out``
inside the library is refused before anything is read.

**The draw** (`draw_sample`). The wrong-text group is taken whole. Each provenance
group, NetEase, YouTube Music, LRCLIB and the older sidecars, is drawn to
`GROUP_SHARE` tracks, an equal share each, by one `random.Random` seeded with ``--seed``, the groups
drawn in that order, each from its tracks sorted by path, so the same seed over the
same library draws the same tracks whatever order the walk found them in. A group
with fewer tracks than its share is taken whole, and the summary says it is short.
A track whose path or album the two files cannot hold (`LeftOut.UNLISTABLE`) is set
aside before the draw, so it never takes a drawn track's place.

**``--withheld``** (`draw_withheld`) lists instead the tracks to hear again, whole and
with no draw: each marked withheld on an entry not undone and still untimed in a
sidecar, in the ``withheld`` group. One marked but timed since is left out, counted,
as is one whose path or album the two files cannot hold.

**The summary** on stdout gives the seed, then each group's size and what was drawn,
then each left-out reason, every count approximate (`approximate`), so it can be
recorded as it stands; notices go to stderr. Exit 0 when both files are written, 2 for
an ``--out`` inside the library, a ledger that cannot be read, a library that cannot
be listed (raised to the entry point), or a file that cannot be written.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_changed import format_changed_list
from sweep_run import EXIT_OK, EXIT_USAGE
from lyrics_model import ReadBackError
from lyrics_state import write_state_file
from stdio_utf8 import spell_unsafe
from sweep_draw_classify import LeftOut, Stratum, Timing
from sweep_draw_library import ClassifiedTrack, classify_library, fix_text_albums
from sweep_ledger import ledger_path, read_records
from sweep_strata import (
    StrataError,
    StrataGroup,
    StrataRow,
    format_strata,
)

#: Each provenance group's share of the random part of the sample: equal, so each group's rate stands alone.
GROUP_SHARE: Final = 250
#: The provenance groups, in the order the seeded generator draws them.
DRAWN_GROUPS: Final = (
    StrataGroup.NETEASE,
    StrataGroup.YOUTUBE_MUSIC,
    StrataGroup.LRCLIB,
    StrataGroup.OLDER_SIDECARS,
)
#: The groups and reasons a sample's summary counts, in the order it prints them.
_SAMPLE_GROUPS: Final = (StrataGroup.WRONG_TEXT_ALBUMS, *DRAWN_GROUPS)
_SAMPLE_REASONS: Final = tuple(r for r in LeftOut if r is not LeftOut.NO_LONGER_UNTIMED)
_WITHHELD_REASONS: Final = (LeftOut.NO_LONGER_UNTIMED, LeftOut.UNLISTABLE)
#: The names of the two files written into --out.
TRACKS_NAME: Final = "tracks.txt"
STRATA_NAME: Final = "strata.tsv"
#: Below this a count prints as "under" it; at and above it, to two figures.
_SMALL: Final = 10
_FIGURES: Final = 2


@dataclass(frozen=True)
class Draw:
    """What a draw wrote and counted.

    *rows* are the drawn tracks, in group then path order; *sizes* each group's tracks
    in the library before the draw; *left_out* each reason's count.
    """

    rows: tuple[StrataRow, ...]
    sizes: Mapping[StrataGroup, int]
    left_out: Mapping[LeftOut, int]


def _row(track: ClassifiedTrack, group: StrataGroup) -> StrataRow:
    """*track*'s row in *group*."""
    return StrataRow(str(track.path), group, track.album)


def _listable(track: ClassifiedTrack) -> bool:
    """True when both files can hold *track*: the list its path, the strata its row.

    The strata file's row check is the whole test: it refuses every path the track
    list refuses (`lyrics_changed.refusal`: a linefeed, a NUL, a carriage return,
    text that is not UTF-8, a relative path), and a tab or an unholdable album too.
    """
    try:
        format_strata(0, (_row(track, StrataGroup.NETEASE),))
    except StrataError:
        return False
    return True


def _by_stratum(
    tracks: Iterable[ClassifiedTrack], of: Callable[[ClassifiedTrack], Stratum]
) -> dict[Stratum, list[ClassifiedTrack]]:
    """*tracks* by the stratum *of* gives each, unlistable ones set aside, by path."""
    found: dict[Stratum, list[ClassifiedTrack]] = {}
    for track in tracks:
        stratum = of(track) if _listable(track) else LeftOut.UNLISTABLE
        found.setdefault(stratum, []).append(track)
    for listed in found.values():
        listed.sort(key=lambda track: str(track.path))
    return found


def _counted(
    found: Mapping[Stratum, list[ClassifiedTrack]],
    groups: Sequence[StrataGroup],
    reasons: Sequence[LeftOut],
) -> tuple[dict[StrataGroup, int], dict[LeftOut, int]]:
    """Each of *groups*' and *reasons*' counts in *found*, zero included."""
    sizes = {group: len(found.get(group, ())) for group in groups}
    return sizes, {reason: len(found.get(reason, ())) for reason in reasons}


def _sample_strata(
    tracks: Iterable[ClassifiedTrack],
) -> dict[Stratum, list[ClassifiedTrack]]:
    """*tracks* by their own stratum, as the sample sorts them, by path."""
    return _by_stratum(tracks, lambda track: track.stratum)


def sample_sizes(
    tracks: Iterable[ClassifiedTrack],
) -> tuple[dict[StrataGroup, int], dict[LeftOut, int]]:
    """Each sample group's size in *tracks*, and each left-out reason's count.

    What a draw of *tracks* counts (`Draw.sizes`, `Draw.left_out`) by any seed: the
    seed picks which tracks are drawn, never how many a group holds.
    """
    return _counted(_sample_strata(tracks), _SAMPLE_GROUPS, _SAMPLE_REASONS)


def draw_sample(
    tracks: Iterable[ClassifiedTrack], seed: int, *, share: int = GROUP_SHARE
) -> Draw:
    """The sample of *tracks*: the wrong-text group whole, each other to *share*."""
    found = _sample_strata(tracks)
    generator = random.Random(seed)
    group = StrataGroup.WRONG_TEXT_ALBUMS
    rows = [_row(track, group) for track in found.get(group, ())]
    for group in DRAWN_GROUPS:
        eligible = found.get(group, [])
        if len(eligible) > share:
            eligible = sorted(
                generator.sample(eligible, share), key=lambda track: str(track.path)
            )
        rows += [_row(track, group) for track in eligible]
    sizes, left_out = _counted(found, _SAMPLE_GROUPS, _SAMPLE_REASONS)
    return Draw(tuple(rows), sizes, left_out)


def _withheld_stratum(track: ClassifiedTrack) -> Stratum:
    """A marked track's stratum for --withheld: the group while it is still untimed."""
    still = track.timing is Timing.UNTIMED
    return StrataGroup.WITHHELD if still else LeftOut.NO_LONGER_UNTIMED


def draw_withheld(tracks: Iterable[ClassifiedTrack]) -> Draw:
    """The withheld list: every track marked withheld and still untimed, whole, no draw."""
    found = _by_stratum((t for t in tracks if t.withheld), _withheld_stratum)
    group = StrataGroup.WITHHELD
    rows = tuple(_row(track, group) for track in found.get(group, ()))
    sizes, left_out = _counted(found, (group,), _WITHHELD_REASONS)
    return Draw(rows, sizes, left_out)


def approximate(count: int) -> str:
    """*count* as the summary prints it: none, under 10, or about it to two figures.

    Never finer than the ten, so a count under 100 rounds to one figure, and no small
    exact count is printed either.
    """
    if count == 0:
        return "none"
    if count < _SMALL:
        return f"under {_SMALL}"
    places = max(1, len(str(count)) - _FIGURES)
    return f"about {round(count, -places):,}"


def _group_line(group: StrataGroup, size: int, drawn: int) -> str:
    """One group's summary line: what was drawn of it, or that it was taken whole."""
    if group in DRAWN_GROUPS and drawn < size:
        return f"{group}: {drawn} drawn of {approximate(size)}"
    short = f", short of {GROUP_SHARE}" if group in DRAWN_GROUPS else ""
    return f"{group}: {approximate(size)}, taken whole{short}"


def format_summary(draw: Draw, *, seed: int) -> str:
    """The printed summary of *draw*: the seed, each group, each left-out reason."""
    drawn = {group: 0 for group in draw.sizes}
    for row in draw.rows:
        drawn[row.group] += 1
    lines = [f"seed: {seed}"]
    lines += [_group_line(g, size, drawn[g]) for g, size in draw.sizes.items()]
    lines += [f"left out, {r}: {approximate(n)}" for r, n in draw.left_out.items()]
    return "".join(line + "\n" for line in lines)


# --- The mode ------------------------------------------------------------------------


def _notice(text: str) -> None:
    """One line of notice, on stderr, its control characters made visible."""
    print(spell_unsafe(text), file=sys.stderr)


def _write(out: Path, seed: int, rows: Sequence[StrataRow]) -> tuple[Path, Path]:
    """Write the track list and the strata file into *out*; their paths.

    Both are formatted before either is written, so a refused row (`StrataError`)
    leaves no track list without its strata file.
    """
    tracks, strata = out / TRACKS_NAME, out / STRATA_NAME
    data, _left = format_changed_list(row.path for row in rows)
    strata_data = format_strata(seed, rows)
    write_state_file(tracks, data)
    write_state_file(strata, strata_data)
    return tracks, strata


def classified_library(library: Path) -> tuple[ClassifiedTrack, ...] | None:
    """Every track of *library* classified, by the ledger's fixes; None, said, if it is unread.

    The hearing's report counts each group's size in the library through this too, so
    the draw and the report read the library by one rule.
    """
    ledger = ledger_path(library, os.environ)
    try:
        records = read_records(ledger, _notice)
    except OSError as error:
        _notice(f"the ledger cannot be read: {ledger}: {error}")
        return None
    return classify_library(library, fix_text_albums(records), notice=_notice)


def _drawn(args: argparse.Namespace, library: Path) -> Draw | None:
    """The draw *args* ask for over *library*; None, said, when the ledger is unread."""
    tracks = classified_library(library)
    if tracks is None:
        return None
    return draw_withheld(tracks) if args.withheld else draw_sample(tracks, args.seed)


def draw_mode(
    args: argparse.Namespace, library: Path, _wiring: object, _words: object
) -> int:
    """``--mode draw`` over *library*, resolved: draw, write, print; the exit code."""
    out: Path = args.out
    if out.resolve().is_relative_to(library):
        _notice(f"--out is inside the library, which the draw never writes: {out}")
        return EXIT_USAGE
    draw = _drawn(args, library)
    if draw is None:
        return EXIT_USAGE
    try:
        written = _write(out, args.seed, draw.rows)
    except (OSError, ReadBackError) as error:
        _notice(f"--out cannot be written: {out}: {error}")
        return EXIT_USAGE
    print(format_summary(draw, seed=args.seed), end="", file=sys.stdout)
    for path in written:
        print(spell_unsafe(f"wrote: {path}"), file=sys.stdout)
    return EXIT_OK
