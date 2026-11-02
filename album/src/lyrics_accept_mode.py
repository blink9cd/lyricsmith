"""`--mode accept`: keep an album's results and drop every run's backup, or report it.

`accept_album` is the in-process pass the album mode and the sweep both call, and
`format_accept` its report; `accept_mode` is the album tool's entry for it. The rules
are `lyrics_accept_judge`'s and `lyrics_accept_check`'s, the records and the removal
`lyrics_accept_records`'s; this module puts them in the order that leaves the
recoverable state when a step fails:

1. An album holding neither ``.lyricsmith`` nor a ``.lyricsmith.accepting`` has no
   run, and nothing is touched or created.
2. An album holding only a ``.lyricsmith.accepting``, an earlier accept's leftover,
   is finished without the album lock: taking the lock would create ``.lyricsmith``,
   and the leftover is no folder a run reads, writes or locks, so a run starting
   meanwhile makes its own ``.lyricsmith`` and shares nothing with it.
3. Otherwise, under the album lock: read and judge every run; any refusal stops the
   pass with nothing touched; check the tracks now, since check reads the backups.
   Without *apply* that is the whole pass. With it, finish any leftover first, then
   make the records folder and write and read back every manifest, and only then
   rename ``.lyricsmith``, lock inside, and delete it. A records failure deletes
   nothing more, though a leftover finished first stays finished. The rename happens
   while the lock is held, so no run can start between the judgment and the removal.
   (A system that cannot rename a folder holding an open file never gets here: the
   caller's rmtree guard refuses Windows first. Were it reached, the rename would
   fail after the records exist, and nothing would be lost.)

A link or a non-folder at ``.lyricsmith.accepting`` is refused on every pass, a
report-only one too, so the report never promises a removal ``--apply`` would refuse.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

import lyrics_album
import lyrics_exit
import lyrics_prune_mode
from lyrics_accept_check import check_flags
from lyrics_accept_judge import ACCEPTING_NAME, AtRisk, Contents, Refusal, RunHeld
from lyrics_accept_judge import judge_contents, read_contents
from lyrics_accept_records import finish_leftover, make_records_folder
from lyrics_accept_records import records_root, refuse_unsafe_accepting
from lyrics_accept_records import remove_lyricsmith, write_records
from lyrics_album import AcquireLock, album_lock
from lyrics_exit import EXIT_CANNOT_RUN, EXIT_OK, EXIT_TRACK_ERRORS
from lyrics_model import LyricsError
from lyrics_prune import Delete as Delete
from lyrics_run_folders import RUN_FOLDER_NAME, run_order
from lyrics_stages import Stages
from lyrics_summary import counts_line, header_lines, report_text, table
from lyrics_wiring import Wiring

#: A row's outcome: a report-only pass, an applied one, and one not reached.
WOULD_REMOVE: Final = "would remove"
REMOVED: Final = "removed"
KEPT: Final = "kept"
#: How a failed step's outcome begins.
ERROR_PREFIX: Final = "ERROR: "
#: What the leftover's row says it is.
LEFT_BY: Final = "left by an earlier accept"
#: How a report-only pass ends, naming the option that would act on it.
HINT: Final = "report only: nothing was removed; add --apply to remove it"
#: How a refused album's report ends.
REFUSED_END: Final = "refused: nothing was removed"
#: How a records failure's error begins.
_RECORDS_FAILED: Final = "the records could not be written"
#: A cell with nothing to show.
_DASH: Final = "-"


@dataclass(frozen=True)
class AcceptRow:
    """One row of the run table: a run folder, or the leftover when *held* is None."""

    name: str
    held: RunHeld | None
    outcome: str

    @property
    def failed(self) -> bool:
        """Whether this row's removal failed."""
        return self.outcome.startswith(ERROR_PREFIX)


@dataclass(frozen=True)
class AcceptReport:
    """What one accept found and did.

    *found* is False for an album with no run at all. *run_folders* counts the
    run-id folders found, a refused album's included. *refusals* stop the pass with
    nothing touched. *at_risk* holds the judgment's and the check's rows, by track
    then run, a track once per run and reason. *records* is the records folder
    written, or None. *error* says why the records could not be written, when that
    stopped the removal, and *partial* is the records folder made before it, if any.
    """

    apply: bool
    found: bool = True
    refusals: tuple[Refusal, ...] = ()
    at_risk: tuple[AtRisk, ...] = ()
    rows: tuple[AcceptRow, ...] = ()
    leftover: AcceptRow | None = None
    records: Path | None = None
    error: str | None = None
    run_folders: int = 0
    partial: Path | None = None

    @property
    def tracks_at_risk(self) -> int:
        """The distinct tracks at risk, however many runs and reasons each has."""
        return len({risk.track for risk in self.at_risk})

    @property
    def refused(self) -> bool:
        """Whether the album was refused."""
        return bool(self.refusals)

    @property
    def removed(self) -> int:
        """The run folders removed; the leftover is not one."""
        return sum(row.outcome == REMOVED for row in self.rows)

    @property
    def errors(self) -> int:
        """The failed removals, the leftover's included, and a records failure."""
        rows = (*self.rows, *([self.leftover] if self.leftover else []))
        return sum(row.failed for row in rows) + (self.error is not None)


# --- The pass ------------------------------------------------------------------------


def accept_album(
    album: Path,
    *,
    apply: bool,
    acquire_lock: AcquireLock,
    stages: Stages,
    environ: Mapping[str, str],
    now: Callable[[], datetime],
    delete: Delete | None = None,
) -> AcceptReport:
    """Judge *album*'s runs and, with *apply*, record their manifests and remove them.

    *environ* names the state folder the records go under, *now* their time; *delete*
    None is `shutil.rmtree`. Raises `RunFolderLinkError`, `LockHeldError`,
    `UnsafeAcceptingError` and the `OSError` reading the run folder raises, each
    before anything is changed. The caller runs the rmtree guard first.
    """
    refuse_unsafe_accepting(album / ACCEPTING_NAME)
    if not os.path.lexists(album / RUN_FOLDER_NAME):
        if not os.path.lexists(album / ACCEPTING_NAME):
            return AcceptReport(apply, found=False)
        return AcceptReport(apply, leftover=_leftover(album, apply, delete))
    with album_lock(album, acquire=acquire_lock):
        contents = read_contents(album, stages.read_manifest)
        judgment = judge_contents(contents)
        found = len(contents.runs)
        if judgment.refusals:
            return AcceptReport(apply, refusals=judgment.refusals, run_folders=found)
        flags = check_flags(album, contents, stages)
        risks = sorted((*judgment.at_risk, *flags), key=_risk_order)
        if not apply:
            leftover = _leftover(album, False, None) if contents.leftover else None
            rows = _rows(judgment.runs, WOULD_REMOVE)
            return AcceptReport(
                apply,
                at_risk=tuple(risks),
                rows=rows,
                leftover=leftover,
                run_folders=found,
            )
        done = _apply(album, contents, environ, now, delete)
    return AcceptReport(
        apply,
        at_risk=tuple(risks),
        rows=_rows(judgment.runs, done.outcome),
        leftover=done.leftover,
        records=done.records,
        error=done.error,
        run_folders=found,
        partial=done.partial,
    )


@dataclass(frozen=True)
class _Applied:
    """What an applied pass did: every run's one outcome, the leftover, the records."""

    outcome: str
    leftover: AcceptRow | None
    records: Path | None = None
    error: str | None = None
    partial: Path | None = None


def _risk_order(risk: AtRisk) -> tuple[str, bool, tuple[str, int], str]:
    """*risk*'s sort key: by track, then its run in run order, so ``-10`` follows ``-2``.

    A name that is no run id sorts after every run, by name, so the order stays total.
    """
    order = run_order(risk.run_id)
    return risk.track, order is None, order or ("", 0), risk.run_id


def _rows(runs: tuple[RunHeld, ...], outcome: str) -> tuple[AcceptRow, ...]:
    """One *outcome* row per run: the folder goes, or stays, as one."""
    return tuple(AcceptRow(held.run_id, held, outcome) for held in runs)


def _leftover(album: Path, apply: bool, delete: Delete | None) -> AcceptRow:
    """The leftover's row: finished with *apply*, else what would be."""
    if not apply:
        return AcceptRow(ACCEPTING_NAME, None, f"{WOULD_REMOVE}: {LEFT_BY}")
    error = finish_leftover(album, delete)
    outcome = f"{REMOVED}: {LEFT_BY}" if error is None else f"{ERROR_PREFIX}{error}"
    return AcceptRow(ACCEPTING_NAME, None, outcome)


def _apply(
    album: Path,
    contents: Contents,
    environ: Mapping[str, str],
    now: Callable[[], datetime],
    delete: Delete | None,
) -> _Applied:
    """Finish a leftover, write the records, then remove ``.lyricsmith``: in that order.

    A leftover that cannot go stops the pass before the records, since the rename
    must never replace it; a records failure stops it before anything is deleted.
    """
    leftover = _leftover(album, True, delete) if contents.leftover else None
    if leftover is not None and leftover.failed:
        return _Applied(KEPT, leftover)
    folder: Path | None = None
    try:
        folder = make_records_folder(records_root(environ), album, now())
        write_records(folder, album, contents.runs)
    except (LyricsError, OSError) as error:
        reason = f"{_RECORDS_FAILED}: {error}"
        return _Applied(KEPT, leftover, error=reason, partial=folder)
    failed = remove_lyricsmith(album, delete)
    outcome = REMOVED if failed is None else f"{ERROR_PREFIX}{failed}"
    return _Applied(outcome, leftover, folder)


# --- The report ----------------------------------------------------------------------


def format_accept(
    report: AcceptReport, *, album: Path, prog: str, hint: bool = True
) -> str:
    """The accept report: at-risk tracks first, the run table, the records, the counts.

    A report-only report ends with `HINT` unless *hint* is False, as the sweep asks,
    which says it once for the whole library.
    """
    stem = Path(prog).stem
    if not report.found:
        return report_text([f"{stem} accept: {album.name} has no run"])
    count = report.run_folders
    folders = f"{count} run folder" if count == 1 else f"{count} run folders"
    lines = header_lines(prog, "accept", folders, album)
    if report.refused:
        lines += [f"refused: {r.kind}: {r.detail}" for r in report.refusals]
        return report_text([*lines, REFUSED_END])
    lines += _at_risk_lines(report.at_risk)
    lines += _run_lines(report)
    if report.records is not None:
        lines.append(f"records: {report.records}")
    if report.partial is not None:
        lines.append(f"partial records: {report.partial}")
    if report.error is not None:
        lines.append(f"{ERROR_PREFIX}{report.error}")
    verb = REMOVED if report.apply else WOULD_REMOVE
    done = report.removed if report.apply else len(report.rows)
    lines.append(
        counts_line(
            (verb, done), ("at risk", report.tracks_at_risk), ("errors", report.errors)
        )
    )
    if hint and not report.apply:
        lines.append(HINT)
    return report_text(lines)


def _at_risk_lines(at_risk: tuple[AtRisk, ...]) -> list[str]:
    """The at-risk tracks' table and a blank line after it, or nothing at all."""
    if not at_risk:
        return []
    rows = [(r.track, r.run_id, str(r.reason), r.detail) for r in at_risk]
    headers = ("#", "track", "run", "reason", "detail")
    return [*table(headers, rows, ["" for _ in rows]), ""]


def _run_lines(report: AcceptReport) -> list[str]:
    """The run table, the leftover's row last, and a blank line; nothing when empty."""
    rows = [_run_cells(row) for row in report.rows]
    if report.leftover is not None:
        rows.append(_run_cells(report.leftover))
    if not rows:
        return []
    headers = ("#", "run", "state", "frames", "sidecars", "set aside", "outcome")
    return [*table(headers, rows, ["" for _ in rows]), ""]


def _run_cells(row: AcceptRow) -> tuple[str, str, str, str, str, str]:
    """One row's cells: its name, state, held counts and outcome."""
    held = row.held
    if held is None:
        return row.name, _DASH, _DASH, _DASH, _DASH, row.outcome
    state = str(held.state) if held.state is not None else _DASH
    counts = (str(held.frames), str(held.sidecars), str(held.set_aside))
    return row.name, state, *counts, row.outcome


# --- The mode ------------------------------------------------------------------------


def accept_exit(report: AcceptReport) -> int:
    """0 finished, 1 a removal failed, 2 refused or the records could not be written."""
    if report.refused or report.error is not None:
        return EXIT_CANNOT_RUN
    return EXIT_TRACK_ERRORS if report.errors else EXIT_OK


def accept_mode(args: argparse.Namespace, wiring: Wiring, prog: str) -> int:
    """Accept --album's runs, or report what accepting them would remove.

    Refuses to start, exit 2, where `shutil.rmtree` is not proof against links, as
    prune does. A report that cannot be written is the lost-report exit, 3.
    """
    album = lyrics_album.resolve_album(args.album)
    lyrics_prune_mode.refuse_unsafe_delete("accept")
    report = accept_album(
        album,
        apply=bool(args.apply),
        acquire_lock=wiring.acquire_lock,
        stages=wiring.stages,
        environ=wiring.environ,
        now=wiring.now,
    )
    with lyrics_exit.report_writes():
        print(format_accept(report, album=album, prog=prog), end="", file=sys.stdout)
        return accept_exit(report)
