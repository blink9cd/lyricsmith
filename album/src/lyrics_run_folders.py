"""Where an album's runs live: the ``.lyricsmith`` run folder.

Every run is written under ``<album>/.lyricsmith/<run-id>/``, which also holds the album
lock. The runs of an album are listed from that folder ordered by run id, which orders
by start time, so "the newest run" is the last of them.

A run's id is its start time written with `RUN_ID_FORMAT`, and a listing reads a
folder name back with `RUN_ID_PATTERN`, derived from that same format by
`run_id_pattern`, so the two cannot drift apart. The format lives here rather than with
the backup that writes it, because `lyrics_backup` imports this module: deriving the
pattern there would make the import run both ways. `lyrics_backup` re-exports it.

A run is a real folder under a run id's name holding a ``manifest.json`` entry, tested
by `manifest_present` without following it, the same rule prune applies: a manifest
that is a link, dangling or leading out of the run folder, makes a run, which its
reader then refuses (`lyrics_manifest.read_manifest`), so it is never passed over.

`refuse_linked_run_folder` refuses a link at the folder, or at the lock, before anything
reads or creates a path through it; `run_parent` passes over a linked folder for the
readers that must not raise. This module imports nothing of the tool's but its errors,
so `lyrics_records` and `lyrics_album` re-export its names without a cycle.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from types import MappingProxyType
from typing import Final

from lyrics_errors import AlbumUnusableError, NoRunError

#: The hidden folder under an album that holds the lock and each run's folder.
RUN_FOLDER_NAME: Final = ".lyricsmith"
#: The index a run folder's undo reads.
MANIFEST_NAME: Final = "manifest.json"
#: The lock file's name in the run folder. It stays between runs: deleting it
#: would race another run opening it, and two runs could then each hold a lock.
LOCK_NAME: Final = "lock"

#: The run id's form: the run's start time in UTC (`lyrics_backup.run_id_base`).
RUN_ID_FORMAT: Final = "%Y%m%dT%H%M%SZ"
#: The suffix the first run of a second takes, in effect: none.
NO_SUFFIX: Final = 1
#: What joins a taken run id to its collision suffix (`make_exclusive_dir`).
_SUFFIX_JOIN: Final = "-"
#: A new folder's mode unless a caller asks for less: ``mkdir``'s own, before the umask.
_NEW_FOLDER_MODE: Final = 0o777
#: The suffixes `make_exclusive_dir` writes, from ``NO_SUFFIX + 1``: a number with no
#: leading zero, in ASCII digits, as an ``int`` formats one.
_SUFFIX: Final = "([2-9]|[1-9][0-9]+)"
#: How many digits each strftime directive a run id may use always writes. ``%Y``
#: writes four for any year from 1000; a run starts long after that.
_DIRECTIVE_DIGITS: Final = MappingProxyType(
    {"Y": 4, "m": 2, "d": 2, "H": 2, "M": 2, "S": 2}
)
#: A strftime directive, or a lone ``%`` at the end, as `run_id_pattern` splits a form.
_DIRECTIVE: Final = re.compile("(%.?)", re.DOTALL)


class RunIdFormatError(ValueError):
    """A run id format uses a directive with no fixed-width pattern to read it back."""


def _directive_pattern(directive: str) -> str:
    """The pattern matching what strftime writes for *directive*, ``%`` and its letter.

    Raise `RunIdFormatError` for one whose width varies or is unknown here, so a format
    change can never derive a pattern that silently misreads the ids it writes.
    """
    code = directive[1:]
    if code == "%":
        return re.escape("%")
    width = _DIRECTIVE_DIGITS.get(code)
    if width is None:
        raise RunIdFormatError(
            f"run id format directive {directive!r} has no fixed-width pattern"
        )
    # ASCII digits, never `\d`: strftime writes them alone, and `\d` takes any script's.
    return f"[0-9]{{{width}}}"


def run_id_pattern(form: str) -> str:
    """The regular expression matching exactly the run ids strftime writes for *form*.

    Each directive becomes its fixed width of digits and every literal is escaped; the
    result holds no group. Raise `RunIdFormatError` on a directive it cannot read back.
    """
    pieces = _DIRECTIVE.split(form)
    # `split` with one group alternates literal text (even places) and directives.
    return "".join(
        _directive_pattern(piece) if index % 2 else re.escape(piece)
        for index, piece in enumerate(pieces)
    )


def run_id_regex(form: str) -> re.Pattern[str]:
    """A run folder's name for run ids written with *form*: group 1 the id, 2 its suffix."""
    return re.compile(
        f"({run_id_pattern(form)})(?:{re.escape(_SUFFIX_JOIN)}{_SUFFIX})?"
    )


#: A run folder's name: the run id, with an optional collision suffix from ``-2``.
RUN_ID_PATTERN: Final = run_id_regex(RUN_ID_FORMAT)


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


def manifest_present(run_dir: Path) -> bool:
    """Whether *run_dir* holds a ``manifest.json`` entry, tested without following it.

    Any entry counts, a link included, dangling or leading out of the run folder, and
    any non-regular file, a folder or a FIFO pipe: its reader refuses it
    (`lyrics_manifest.read_manifest`, which opens a pipe without waiting on a writer),
    so a run whose manifest is a link is reported, never taken for a backup that
    never finished. Only a name truly absent is no manifest; any other `OSError` (a
    folder not open to search) is raised, as nobody can tell, for the caller to decide.
    """
    try:
        os.lstat(run_dir / MANIFEST_NAME)
    except FileNotFoundError:
        return False
    return True


def _is_run(entry: Path) -> bool:
    """Whether *entry* is a run: a real folder, never a link, holding a manifest.

    A folder with no manifest is a backup that never finished, which changed nothing,
    so it is not a run to undo or check. Nor is a symbolic link under a run id's name,
    known by its own entry, never followed: a run created its folder there, so a link
    is something else, and reading through it would judge a run folder outside the
    album. Any entry named `MANIFEST_NAME` counts, any non-regular file (a folder, a
    FIFO pipe) included: that run is then refused when its manifest is read, at once
    and failing closed. A folder whose manifest cannot be told is a run, as prune keeps
    one: its reader then fails on it and says why, where passing over it would let a
    plain undo land on an older run.
    """
    if entry.is_symlink() or not entry.is_dir():
        return False
    try:
        return manifest_present(entry)
    except OSError:
        return True


def run_folder(album: Path, run_id: str) -> Path:
    """Where run *run_id* lives, or would be created: its name in the run folder."""
    return album / RUN_FOLDER_NAME / run_id


# fuzz-target
def run_order(name: str) -> tuple[str, int] | None:
    """A run folder name's sort key, as `select_run` sorts runs; None for any other name.

    A run sorts by its start time, then by its suffix as a number, so ``-10`` comes
    after ``-2``.
    """
    match = RUN_ID_PATTERN.fullmatch(name)
    if match is None:
        return None
    return match.group(1), int(match.group(2) or NO_SUFFIX)


def make_exclusive_dir(
    parent: Path, base: str, *, mode: int = _NEW_FOLDER_MODE
) -> Path:
    """Create and return a new folder named *base* under *parent*, never a taken one.

    When *base* is taken, ``-2``, ``-3`` and so on are tried in turn, the suffixes
    `run_order` reads, so a concurrent caller can never land in the same folder. Each
    try is one exclusive ``mkdir``; *parent* must exist. The folder is made with
    *mode*, narrowed by the umask, so it never exists with a wider one.
    """
    suffix = NO_SUFFIX
    while True:
        named = base if suffix == NO_SUFFIX else f"{base}{_SUFFIX_JOIN}{suffix}"
        folder = parent / named
        try:
            folder.mkdir(mode=mode)
        except FileExistsError:
            suffix += 1
            continue
        return folder


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
