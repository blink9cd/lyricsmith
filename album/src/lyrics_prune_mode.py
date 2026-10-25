"""`--mode prune`'s entry: the delete guard, the pass, its report and its exit code.

The rules and the removal are `lyrics_prune`'s; this module refuses to start where a
delete could follow a link out of the album, prints the report and maps it to an exit.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import lyrics_album
import lyrics_exit
import lyrics_prune
from lyrics_exit import EXIT_OK, EXIT_TRACK_ERRORS
from lyrics_model import LyricsError
from lyrics_summary import counts_line, header_lines, report_text, table
from lyrics_wiring import Wiring

#: What a dry run says instead of "removed", in its rows and its counts line.
_WOULD = "would remove"


class UnsafeDeleteError(LyricsError):
    """This Python's `shutil.rmtree` could follow a link out of the folder it deletes."""


def refuse_unsafe_delete() -> None:
    """Raise `UnsafeDeleteError` where `shutil.rmtree` is not proof against links."""
    if not shutil.rmtree.avoids_symlink_attacks:
        raise UnsafeDeleteError(
            "this Python's shutil.rmtree can follow a symbolic link out of the folder "
            "it deletes, so prune will not run"
        )


def _row(row: lyrics_prune.PruneRow, dry_run: bool) -> tuple[str, str, str]:
    """One row's run, state and outcome columns."""
    verdict = row.verdict
    outcome = str(verdict.outcome)
    if dry_run and verdict.removes:
        outcome = outcome.replace("removed", _WOULD, 1)
    if row.error is not None:
        outcome = f"ERROR: {row.error}"
    elif verdict.detail is not None:
        outcome = f"{outcome} ({verdict.detail})"
    state = str(verdict.state) if verdict.state is not None else "-"
    return verdict.name, state, outcome


def format_prune(report: lyrics_prune.PruneReport, *, album: Path, prog: str) -> str:
    """The prune report: one row per run-id entry, oldest first, then the counts."""
    count = len(report.rows)
    if not count:
        return report_text(
            [f"{Path(prog).stem} prune: no run folders under {album.name}"]
        )
    folders = f"{count} run folder" if count == 1 else f"{count} run folders"
    lines = header_lines(prog, "prune", folders, album)
    rows = [_row(r, report.dry_run) for r in report.rows]
    lines += table(("#", "run", "state", "outcome"), rows, ["" for _ in rows])
    removed = _WOULD if report.dry_run else "removed"
    lines.append(
        counts_line(
            (removed, report.removed), ("kept", report.kept), ("errors", report.errors)
        )
    )
    if report.dry_run:
        lines.append("dry run: nothing was removed")
    return report_text(lines)


def prune_mode(args: argparse.Namespace, wiring: Wiring, prog: str) -> int:
    """Prune --album's run folders, or plan it for a dry run, and print the report.

    Exit 0 once the pass finished, 1 when a removal failed; a report that cannot be
    written is the lost-report exit, through `lyrics_exit.report_writes`.
    """
    album = lyrics_album.resolve_album(args.album)
    refuse_unsafe_delete()
    report = lyrics_prune.prune_album(
        album, dry_run=args.dry_run, acquire_lock=wiring.acquire_lock
    )
    with lyrics_exit.report_writes():
        print(format_prune(report, album=album, prog=prog), end="", file=sys.stdout)
        # The tool's one "finished, with errors" code, as a track error is a run's.
        return EXIT_TRACK_ERRORS if report.errors else EXIT_OK
