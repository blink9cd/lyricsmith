"""The hearing's report: each sample group's `not sung` rate, the estimate, and the trust measures.

`hear_report_mode` is ``sweep --mode hear-report``. It reads the strata file the draw
wrote (``--strata``, `sweep_strata.read_strata`), the hearing log sync's hearing mode
wrote (``--hearing-log``, `lyrics_hearing_log.list_hearings`), and the library's album
texts, and prints the report (`sweep_hear_format`) to stdout. It plays no audio, asks no
provider and writes nothing: the report is all it makes.

**Each group** (`collect`): a strata track the log holds a hearing of by ``--model`` is
heard and counted by its verdict (`sweep_hear_tally.GroupTally`); one with none is
skipped, by the track's timing now as the draw reads it (`track_timing`): `not heard`
while it is still of its group's kind (provider-timed, or untimed for ``withheld``),
else its timing (``frames-only``, say). A record whose track no strata row names is
counted apart, never used; a log file that cannot be read is said and counted.

**The shift test** (`sweep_hear_shift`) runs on each `not sung` hearing against the
other texts of its album, ``library/<album>`` as the strata row names it.

**The estimate** needs each group's size in the library, which is read only when the
sample holds a group the estimate weighs: through the draw's own reader
(`sweep_draw.classified_library`) and its sizing (`sweep_draw.sample_sizes`, the counts
every draw makes whatever its seed), so a group is sized by the very rule that drew it.
A ``withheld`` list reads no library and has no estimate.

**With ``--compare-model``**, each strata track heard by both models (`compare`).

**The model's default** is the pinned turbo's file name, written here: sweep imports
no transcribe code (the dependency direction is sync, then sweep, then album), so it
cannot read ``at_models``' constant, and a test holds the two equal.

**A strata row** must name a track inside its album, directly or in a disc folder of
it, an album folder under ``--library`` reached through no link and resolving inside it
(`outside_row`), since the report reads that album's files.

Exit 0 once the report prints; 2 for a strata file that cannot be read or names a track
outside ``--library``, a hearing log that is not a folder or cannot be listed, or a
ledger that cannot be read (the draw's reader says which).
"""

from __future__ import annotations

import argparse
import os
import posixpath
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_hearing_log import HeardVerdict, list_hearings
from lyrics_run_folders import is_link_or_junction
from stdio_utf8 import spell_unsafe
from sweep_draw import DRAWN_GROUPS, classified_library, sample_sizes
from sweep_draw_classify import LeftOut, Timing
from sweep_draw_library import ClassifiedTrack, track_timing
from sweep_hear_shift import (
    ShiftFound,
    ShiftOutcome,
    genre_tally,
    shift_test,
    tag_stages,
)
from sweep_hear_format import format_report
from sweep_hear_tally import (
    NOT_HEARD,
    Estimate,
    GroupTally,
    HearReport,
    Hearings,
    compare,
    estimate,
    index_hearings,
)
from sweep_run import EXIT_OK, EXIT_USAGE
from sweep_strata import Strata, StrataError, StrataGroup, StrataRow, read_strata
from sweep_survey import is_linked

#: The pinned turbo's file name, the model the hearing hears with by default.
DEFAULT_MODEL: Final = "ggml-large-v3-turbo.bin"
#: The groups the estimate weighs: a sample holding none of them has no estimate.
_ESTIMATED: Final = frozenset({StrataGroup.WRONG_TEXT_ALBUMS, *DRAWN_GROUPS})
#: What a library read gives: every track classified, or None when it was refused.
TracksRead = Callable[[], tuple[ClassifiedTrack, ...] | None]


def _notice(text: str) -> None:
    """One line of notice, on stderr, its control characters made visible."""
    print(spell_unsafe(text), file=sys.stderr)


#: The parts of an album's path below the library, ``Artist/Album``, as
#: `library_walk.album_dirs` finds every album the draw reads.
ALBUM_PARTS: Final = 2


def _linked_below(album: Path, track: Path) -> bool:
    """True when *track*, or a folder between *album* and it, is a link or a junction.

    A step that cannot be looked at counts as one, so the row is refused, never read.
    """
    step = album
    for part in track.relative_to(album).parts:
        step = step / part
        try:
            if is_link_or_junction(step):
                return True
        # A folder the report may not search: refusing the row is the safe answer.
        except OSError:
            return True
    return False


def album_is_normal(album: str) -> bool:
    """True when *album* is in the form the draw writes it: POSIX, normal, with no ``..``.

    The draw writes ``as_posix()`` on every host, so this checks with `posixpath`: on
    Windows `os.path.normpath` would turn each ``/`` into a backslash and refuse every row.
    A backslash is refused outright, so no second separator can slip in.
    """
    return (
        posixpath.normpath(album) == album
        and "\\" not in album
        and posixpath.pardir not in album.split("/")
    )


def _row_inside(row: StrataRow, library: Path) -> bool:
    """True when *row*'s track is in its album, a real folder under *library*.

    Both must already be lexically normal, as the draw writes them: the report reads
    through the raw paths, and the OS follows a link before it applies a ``..``, so a
    check of the normalized form would pass a row whose read goes through one. An
    album must be an ``Artist/Album`` folder under *library* (`ALBUM_PARTS`), as the
    survey's walk finds albums, never *library* or an artist folder. The track may sit
    in a folder of its album, as a multi-disc album's ``Disc 1/`` holds its tracks and
    the draw lists them. Then neither the album nor its artist folder may be a link,
    nor may it resolve outside *library*, by the rule the survey drops an album by
    (`sweep_survey.is_linked`); nor may the track, or a folder between the album and
    it, be a link, since its tags are read through it.
    """
    if os.path.normpath(row.path) != row.path:
        return False
    if not album_is_normal(row.album):
        return False
    album, track = library / row.album, Path(row.path)
    inside = album.is_relative_to(library)
    if not inside or len(album.relative_to(library).parts) != ALBUM_PARTS:
        return False
    if track == album or not track.is_relative_to(album):
        return False
    if _linked_below(album, track):
        return False
    return not is_linked(album, library)


def outside_row(strata: Strata, library: Path) -> str | None:
    """The first strata path not in its album under *library*; None when none."""
    for row in strata.rows:
        if not _row_inside(row, library):
            return row.path
    return None


def _skip_reason(row: StrataRow, library: Path) -> str:
    """Why *row*'s track holds no hearing: not heard, or its timing is not its group's."""
    timing = track_timing(Path(row.path), library / row.album)
    kind = (
        Timing.UNTIMED if row.group is StrataGroup.WITHHELD else Timing.PROVIDER_TIMED
    )
    return NOT_HEARD if timing is kind else str(timing)


def _tally_rows(
    rows: Sequence[StrataRow], hearings: Hearings, library: Path, model: str
) -> tuple[dict[StrataGroup, GroupTally], list[ShiftFound]]:
    """Each group's tally of *rows* under *model*, and every shift test run."""
    tallies: dict[StrataGroup, GroupTally] = {}
    found: list[ShiftFound] = []
    stages = tag_stages()
    for row in rows:
        tally = tallies.setdefault(row.group, GroupTally())
        record = hearings.by_key.get((row.path, model))
        if record is None:
            tally.add_skip(_skip_reason(row, library))
            continue
        tally.add_record(record)
        if record.verdict is not HeardVerdict.NOT_SUNG:
            continue
        album = library / row.album
        shift = shift_test(record.transcript, Path(row.path), album, stages)
        found.append(shift)
        if shift.outcome is not ShiftOutcome.UNTESTED:
            tally.add_shift(shifted=shift.outcome is ShiftOutcome.SHIFT)
    ordered = {group: tallies[group] for group in StrataGroup if group in tallies}
    return ordered, found


def _library_part(
    strata: Strata, tallies: Mapping[StrataGroup, GroupTally], tracks: TracksRead
) -> tuple[Estimate | None, Mapping[LeftOut, int]] | None:
    """The estimate and the library's left-out counts; None when the library read failed."""
    if not any(row.group in _ESTIMATED for row in strata.rows):
        return None, {}
    read = tracks()
    if read is None:
        return None
    sizes, left_out = sample_sizes(read)
    return estimate(tallies, sizes), left_out


def collect(
    strata: Strata,
    library: Path,
    log: Path,
    *,
    models: tuple[str, str | None],
    tracks: TracksRead,
) -> HearReport | None:
    """The report over *strata*, *library* and the hearing *log*; None when *tracks* fails.

    *models* are the model reported and the one compared with it, or None. Raises the
    `OSError` listing *log* raises.
    """
    listing = list_hearings(log)
    hearings = index_hearings(listing.records, strata.rows)
    model, other = models
    tallies, found = _tally_rows(strata.rows, hearings, library, model)
    library_part = _library_part(strata, tallies, tracks)
    if library_part is None:
        return None
    untested = sum(one.outcome is ShiftOutcome.UNTESTED for one in found)
    return HearReport(
        seed=strata.seed,
        models=models,
        tallies=tallies,
        estimate=library_part[0],
        left_out=library_part[1],
        genres=genre_tally(found),
        untested=untested,
        unread=sum(one.unread for one in found),
        outside=hearings.outside,
        problems=listing.problems,
        comparison=None
        if other is None
        else compare(strata.rows, hearings, model, other),
    )


def _read_inputs(args: argparse.Namespace, library: Path) -> Strata | None:
    """The strata file, checked against *library*, and the log checked a folder; None, said."""
    try:
        strata = read_strata(args.strata)
    except StrataError as error:
        _notice(f"--strata: {error}")
        return None
    stray = outside_row(strata, library)
    if stray is not None:
        _notice(f"--strata names a track outside --library: {stray}")
        return None
    if not args.hearing_log.is_dir():
        _notice(f"--hearing-log is not a folder: {args.hearing_log}")
        return None
    return strata


def hear_report_mode(
    args: argparse.Namespace, library: Path, _wiring: object, _words: object
) -> int:
    """``--mode hear-report`` over *library*, resolved: read, tally, print; the exit code."""
    strata = _read_inputs(args, library)
    if strata is None:
        return EXIT_USAGE
    try:
        report = collect(
            strata,
            library,
            args.hearing_log,
            models=(args.model, args.compare_model),
            tracks=lambda: classified_library(library),
        )
    except OSError as error:
        _notice(f"--hearing-log {args.hearing_log}: cannot be read ({error})")
        return EXIT_USAGE
    if report is None:
        return EXIT_USAGE
    for problem in report.problems:
        _notice(f"--hearing-log: {problem}")
    print(format_report(report), end="", file=sys.stdout)
    return EXIT_OK
