"""Where an album's runs live: the ``.lyricsmith`` run folder.

Every run is written under ``<album>/.lyricsmith/<run-id>/``, which also holds the album
lock. The runs of an album are listed from that folder ordered by run id, which orders
by start time, so "the newest run" is the last of them.

`refuse_linked_run_folder` refuses a link at the folder, or at the lock, before anything
reads or creates a path through it; `run_parent` passes over a linked folder for the
readers that must not raise. This module imports nothing of the tool's but its errors,
so `lyrics_records` and `lyrics_album` re-export its names without a cycle.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from lyrics_errors import AlbumUnusableError, NoRunError

#: The hidden folder under an album that holds the lock and each run's folder.
RUN_FOLDER_NAME: Final = ".lyricsmith"
#: The index a run folder's undo reads.
MANIFEST_NAME: Final = "manifest.json"
#: The lock file's name in the run folder. It stays between runs: deleting it
#: would race another run opening it, and two runs could then each hold a lock.
LOCK_NAME: Final = "lock"

#: A run folder's name: the run id, with an optional collision suffix from ``-2``.
_RUN_ID = re.compile(r"(\d{8}T\d{6}Z)(?:-([2-9]|[1-9]\d+))?")
#: The suffix the first run of a second takes, in effect: none.
NO_SUFFIX: Final = 1


class RunFolderLinkError(AlbumUnusableError):
    """The album's run folder, or the lock in it, is a symbolic link."""


def refuse_linked_run_folder(album: Path) -> None:
    """Raise `RunFolderLinkError` when *album*'s run folder, or its lock, is a link.

    The folder and the lock are created by following the name (a folder made with
    ``exist_ok``, a lock opened with ``O_CREAT``), so a link at either would reach
    outside the album. Each name is read without following it; the folder first, so the
    lock is never looked for through a linked folder.
    """
    folder = album / RUN_FOLDER_NAME
    for path in (folder, folder / LOCK_NAME):
        if path.is_symlink():
            raise RunFolderLinkError(
                f"{path} is a symbolic link; the run folder must be inside the album"
            )


def run_parent(album: Path) -> Path | None:
    """*album*'s run folder when it exists and is no link, else None."""
    folder = album / RUN_FOLDER_NAME
    return folder if folder.is_dir() and not folder.is_symlink() else None


def _is_run(entry: Path) -> bool:
    """Whether *entry* is a run: a real folder, never a link, holding a manifest.

    A folder with no manifest is a backup that never finished, which changed nothing,
    so it is not a run to undo or check. Nor is a symbolic link under a run id's name,
    known by its own entry, never followed: a run created its folder there, so a link
    is something else, and reading through it would judge a run folder outside the
    album.
    """
    return not entry.is_symlink() and (entry / MANIFEST_NAME).is_file()


def run_folder(album: Path, run_id: str) -> Path:
    """Where run *run_id* lives, or would be created: its name in the run folder."""
    return album / RUN_FOLDER_NAME / run_id


def run_order(name: str) -> tuple[str, int] | None:
    """A run folder name's sort key, as `select_run` sorts runs; None for any other name.

    A run sorts by its start time, then by its suffix as a number, so ``-10`` comes
    after ``-2``.
    """
    match = _RUN_ID.fullmatch(name)
    if match is None:
        return None
    return match.group(1), int(match.group(2) or NO_SUFFIX)


def _runs(album: Path) -> dict[str, tuple[tuple[str, int], Path]]:
    """*album*'s runs by id: each one's sort order and folder.

    Each run is an entry `_is_run` takes; a linked run folder holds none (`run_parent`).
    """
    parent = run_parent(album)
    if parent is None:
        return {}
    runs: dict[str, tuple[tuple[str, int], Path]] = {}
    for entry in parent.iterdir():
        order = run_order(entry.name)
        if order and _is_run(entry):
            runs[entry.name] = (order, entry)
    return runs


def run_ids(album: Path) -> frozenset[str]:
    """The ids of *album*'s runs: each entry `_is_run` takes under a run id's name.

    A linked run folder holds none (`run_parent`), and a name that is no run id holds
    none either: a folder a stopped prune renamed still holds its manifest, but no mode
    reads it as a run.
    """
    return frozenset(_runs(album))


def _order_of(run: tuple[tuple[str, int], Path]) -> tuple[str, int]:
    """A `_runs` value's sort order."""
    return run[0]


def select_run(album: Path, run_id: str | None) -> Path:
    """The folder of run *run_id* under *album*, or of the newest run when it is None.

    A run-id folder that is a link is no run. Raise `NoRunError` when the album holds
    no run, or none named *run_id*, and
    `RunFolderLinkError` when its run folder, or the lock in it, is a link.
    """
    refuse_linked_run_folder(album)
    runs = _runs(album)
    if run_id is None:
        if not runs:
            raise NoRunError(f"no run under {album}")
        return max(runs.values(), key=_order_of)[1]
    if run_id not in runs:
        raise NoRunError(f"no run {run_id!r} under {album}")
    return runs[run_id][1]


def newer_runs(album: Path, run_id: str) -> tuple[str, ...]:
    """The ids of *album*'s runs that sort after *run_id*, oldest first.

    A run sorts as `select_run` sorts it, by its start time, then its suffix as a number.
    """
    orders = {rid: order for rid, (order, _) in _runs(album).items()}
    if run_id not in orders:
        return ()
    after = (rid for rid in orders if orders[rid] > orders[run_id])
    return tuple(sorted(after, key=orders.__getitem__))


def runs_newest_first(album: Path) -> tuple[Path, ...]:
    """*album*'s run folders, newest first, sorted as `select_run` sorts them.

    A link under a run id's name and a folder with no manifest are no run. A link at
    the run folder is not refused here, since a report must run on any album: the
    linked folder is passed over (`run_parent`), as its runs are no runs of this album's.
    """
    runs = sorted(_runs(album).values(), key=_order_of, reverse=True)
    return tuple(path for _, path in runs)
