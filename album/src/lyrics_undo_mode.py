"""`--mode undo`'s report: the newer-run warning, the changed list and the summary.

The undo itself is `lyrics_modes.undo_album`'s; this module says what it did. The
newer-run warning and the changed list's notices go through the notice writer the
entry point passes; the summary is printed to stdout, and the exit code is then
`lyrics_exit.report_exit`'s. The newer-run helpers (`newer_overlap`, `newer_warnings`)
read only.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

import lyrics_changed
import lyrics_exit
import lyrics_summary
from lyrics_backup import newer_runs
from lyrics_model import ManifestSchemaError
from lyrics_records import (
    UndoReport,
    UndoStatus,
    UndoTrackResult,
    run_folder,
)
from lyrics_stages import Stages
from lyrics_wiring import Wiring


def report_undo(
    args: argparse.Namespace,
    wiring: Wiring,
    prog: str,
    album: Path,
    report: UndoReport,
    notice: Callable[[str], None],
) -> int:
    """Warn of a newer run, write the --changed-list, and print an undo's report."""
    # Only a newer run changing a track this undo restored, or began to, is warned of.
    if not report.newest:
        restored = restored_paths(report.tracks)
        # Read after the lock is released: a run started since can add a false alarm,
        # never hide a warning.
        overlap = newer_overlap(album, report.run_id, restored, wiring.stages)
        for line in newer_warnings(overlap, report.run_id):
            notice(line)
    written = lyrics_changed.write_changed_list(
        args.changed_list,
        lambda: lyrics_changed.undo_changed(album, report.tracks),
        notice,
    )
    print(
        lyrics_summary.format_undo(report, album=album, prog=prog),
        end="",
        file=sys.stdout,
    )
    return lyrics_exit.report_exit(
        report, prog, written, notice, recorded="the tracks undone"
    )


@dataclass(frozen=True)
class NewerOverlap:
    """Each newer run holding a track an undo restored, with those tracks; or *unreadable*."""

    shared: tuple[tuple[str, tuple[str, ...]], ...]
    unreadable: bool = False


def restored_paths(results: Iterable[UndoTrackResult]) -> tuple[str, ...]:
    """The tracks an undo restored, or began restoring: an ERROR that saved counts too.

    An error after the MP3 was saved, or after a sidecar was set aside, has already
    overwritten what a newer run left, so it is warned of as a finished one is.
    """
    return tuple(
        t.path
        for t in results
        if t.status is UndoStatus.UNDONE or t.saved or t.displaced
    )


def newer_overlap(
    album: Path, run_id: str, restored: Collection[str], stages: Stages
) -> NewerOverlap:
    """Each run newer than *run_id* that holds a track in *restored*, with those tracks.

    A newer run's track already undone is left out: that run's change is gone. Runs
    that cannot be listed, or a newer manifest that cannot be read, make the overlap
    *unreadable*, so the caller warns as it did before it could tell: the warning
    never fails open. Those are the errors `read_manifest` raises (a malformed
    manifest's, and the read's own) and a failed listing's `OSError`.
    """
    try:
        return NewerOverlap(_shared(album, run_id, frozenset(restored), stages))
    except (ManifestSchemaError, OSError):
        return NewerOverlap((), unreadable=True)


def _shared(
    album: Path, run_id: str, wanted: frozenset[str], stages: Stages
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """`newer_overlap`'s runs and tracks; raises what listing or reading them raises."""
    shared: list[tuple[str, tuple[str, ...]]] = []
    for newer in newer_runs(album, run_id):
        manifest = stages.read_manifest(run_folder(album, newer))
        paths = tuple(
            t.path for t in manifest.tracks if t.path in wanted and not t.undone
        )
        if paths:
            shared.append((newer, paths))
    return tuple(shared)


def newer_warnings(overlap: NewerOverlap, run_id: str) -> tuple[str, ...]:
    """The undo's warning lines for *overlap*, one per newer run sharing a track."""
    if overlap.unreadable:
        return (
            f"warning: run {run_id} is not the newest; a newer run's changes "
            "to the same tracks were overwritten",
        )
    return tuple(
        f"warning: run {newer} changed {', '.join(paths)} after run {run_id}; "
        "this undo overwrote those changes"
        for newer, paths in overlap.shared
    )
