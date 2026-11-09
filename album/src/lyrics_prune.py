"""`--mode prune`: which of an album's run folders nothing needs, and removing them.

A run folder goes only when it holds nothing that is not already back in the files: a
run whose every track is undone with nothing set aside in its ``undo`` folder, a backup
that never finished, or a folder an earlier prune renamed and did not finish deleting.
The newest readable run goes only when every run left behind is undone, so a plain
``--mode undo`` after the prune can only land on a run it has nothing left to do for. This
module's rules (`classify`, `judge`) are pure; `scan`, `remove_folder` and
`prune_album` read and change the ``.lyricsmith`` run folder, which itself stays.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from lyrics_album import AcquireLock, album_lock
from lyrics_delete import chosen_delete
from lyrics_manifest import read_manifest
from lyrics_model import ManifestSchemaError
from lyrics_records import Manifest, RunState
from lyrics_run_folders import (
    is_link_or_junction,
    manifest_present,
    refuse_linked_run_folder,
    run_order,
    run_parent,
)
from lyrics_stages import ReadManifest
from lyrics_undo import UNDO_DIR

#: How a folder is deleted once renamed; a test passes one that fails.
Delete = Callable[[Path], None]

#: What a folder is renamed to end with before it is deleted: no run-id name ends so.
PRUNING_SUFFIX = ".pruning"


class Kind(StrEnum):
    """What a run-id entry of a run folder turned out to be when read."""

    RUN = "run"
    UNREADABLE = "unreadable"
    NO_MANIFEST = "no manifest"
    NOT_FOLDER = "not a folder"
    LEFTOVER = "leftover"


class Outcome(StrEnum):
    """What prune does with one entry, as its report row says it."""

    REMOVE_UNDONE = "removed: undone"
    REMOVE_UNFINISHED = "removed: unfinished backup"
    REMOVE_LEFTOVER = "removed: left by an earlier prune"
    KEEP_NOT_FOLDER = "kept: not a run folder"
    KEEP_UNREADABLE = "kept: unreadable manifest"
    KEEP_NOT_UNDONE = "kept: not undone"
    KEEP_DISPLACED = "kept: holds displaced sidecars"
    KEEP_NEWEST = "kept: newest, another run is not undone"


#: The outcomes that remove their folder.
_REMOVALS = frozenset(
    {Outcome.REMOVE_UNDONE, Outcome.REMOVE_UNFINISHED, Outcome.REMOVE_LEFTOVER}
)
#: The kinds `select_run` takes for a run: a folder holding a manifest, read or not.
_RUN_KINDS = frozenset({Kind.RUN, Kind.UNREADABLE})


@dataclass(frozen=True)
class Found:
    """One run-id entry as a scan read it: its name, sort key, kind and manifest facts.

    *state* and *displaced* are read only for a `Kind.RUN`; *detail* is an unreadable
    manifest's reason. *parent* is the run folder holding the entry.
    """

    name: str
    order: tuple[str, int]
    kind: Kind
    state: RunState | None = None
    displaced: bool = False
    detail: str | None = None
    parent: Path = field(kw_only=True)


@dataclass(frozen=True)
class Verdict:
    """One entry's outcome, with the state and reason its report row shows.

    *parent* is the run folder holding the entry, where its removal renames it.
    """

    name: str
    state: RunState | None
    outcome: Outcome
    detail: str | None = None
    parent: Path = field(kw_only=True)

    @property
    def path(self) -> Path:
        """The entry's folder: its name in the run folder."""
        return self.parent / self.name

    @property
    def removes(self) -> bool:
        """Whether the outcome removes the folder."""
        return self.outcome in _REMOVALS

    @property
    def leftover(self) -> bool:
        """Whether the folder is one an earlier prune left: removed before any rename."""
        return self.outcome is Outcome.REMOVE_LEFTOVER


def classify(found: Found) -> Verdict:
    """*found*'s outcome by its kind and state, before the newest-run rule."""
    by_kind = {
        Kind.NOT_FOLDER: Outcome.KEEP_NOT_FOLDER,
        Kind.LEFTOVER: Outcome.REMOVE_LEFTOVER,
        Kind.NO_MANIFEST: Outcome.REMOVE_UNFINISHED,
        Kind.UNREADABLE: Outcome.KEEP_UNREADABLE,
    }
    if found.kind in by_kind:
        outcome = by_kind[found.kind]
    elif found.state is not RunState.UNDONE:
        outcome = Outcome.KEEP_NOT_UNDONE
    elif found.displaced:
        outcome = Outcome.KEEP_DISPLACED
    else:
        outcome = Outcome.REMOVE_UNDONE
    return Verdict(found.name, found.state, outcome, found.detail, parent=found.parent)


def judge(entries: Iterable[Found]) -> tuple[Verdict, ...]:
    """Every entry's verdict, oldest first, the newest-run rule applied.

    Plain undo and check act on the newest run, so that run goes only when every run
    left behind is undone. Left behind is every other run kept, a newer unreadable one
    included, and an unreadable one counts as not undone: `select_run` takes it as a
    run, and undo refuses it, but the run behind it must still be there once it is
    mended or removed by hand. Older removable runs go either way.
    """
    ordered = sorted(entries, key=lambda e: e.order)
    verdicts = [classify(e) for e in ordered]
    runs = [i for i, e in enumerate(ordered) if e.kind in _RUN_KINDS]
    readable = [i for i in runs if ordered[i].kind is Kind.RUN]
    if readable and verdicts[readable[-1]].removes:
        newest = readable[-1]
        left = (verdicts[i] for i in runs if i != newest and not verdicts[i].removes)
        if any(v.state is not RunState.UNDONE for v in left):
            verdicts[newest] = replace(verdicts[newest], outcome=Outcome.KEEP_NEWEST)
    return tuple(verdicts)


# --- Reading the run folder ----------------------------------------------------------


def scan(parent: Path, read: ReadManifest) -> tuple[Found, ...]:
    """Each run-id entry of the run folder *parent*, a leftover ``.pruning`` too.

    Any other name, the lock's included, is not listed. Listing the folder raises the
    `OSError` it raises; one entry that cannot be read is that entry's kind instead.
    """
    found: list[Found] = []
    for entry in parent.iterdir():
        base = entry.name.removesuffix(PRUNING_SUFFIX)
        order = run_order(base)
        if order is not None:
            leftover = base != entry.name
            found.append(_read_entry(entry, order, read, leftover=leftover))
    return tuple(found)


def _read_entry(
    entry: Path, order: tuple[str, int], read: ReadManifest, *, leftover: bool
) -> Found:
    """What one run-id entry is: its kind, and a readable run's state and set-asides."""
    parent = entry.parent
    # A junction reads as a folder, so it is tested for as a link is: either one under
    # a run id or a .pruning name is never read, nor deleted, as a run.
    if is_link_or_junction(entry) or not entry.is_dir():
        return Found(entry.name, order, Kind.NOT_FOLDER, parent=parent)
    if leftover:
        return Found(entry.name, order, Kind.LEFTOVER, parent=parent)
    try:
        # The listing's own rule: a link counts as a manifest, read, refused and kept,
        # never taken for an unfinished backup. A failure to tell (a folder not open to
        # search) is an entry nobody can vouch for.
        present = manifest_present(entry)
    except OSError as error:
        return Found(
            entry.name, order, Kind.UNREADABLE, detail=str(error), parent=parent
        )
    if not present:
        return Found(entry.name, order, Kind.NO_MANIFEST, parent=parent)
    try:
        manifest = read(entry)
        displaced = _holds_displaced(entry, manifest)
    except (ManifestSchemaError, OSError) as error:
        return Found(
            entry.name, order, Kind.UNREADABLE, detail=str(error), parent=parent
        )
    return Found(entry.name, order, Kind.RUN, manifest.state, displaced, parent=parent)


def _holds_displaced(run_dir: Path, manifest: Manifest) -> bool:
    """Whether undo set anything aside: a track lists one, or ``undo`` is not empty.

    An ``undo`` entry that is a link, a junction or a file is not a folder undo made, so
    it counts as holding something, and so does one that cannot be listed.
    """
    if any(track.displaced for track in manifest.tracks):
        return True
    undo = run_dir / UNDO_DIR
    if not os.path.lexists(undo):
        return False
    if is_link_or_junction(undo) or not undo.is_dir():
        return True
    try:
        return any(undo.iterdir())
    except OSError:
        return True


# --- Removing ------------------------------------------------------------------------


def remove_folder(verdict: Verdict, delete: Delete) -> str | None:
    """Rename *verdict*'s folder out of the run-id names, then delete it; the error.

    Renamed first, inside the run folder, so a prune stopped part-way never
    leaves a half-deleted folder that still reads as a run. A leftover already carries
    the ending. Returns the `OSError`'s text, or None when the folder is gone.
    """
    path = verdict.path
    try:
        if not verdict.leftover:
            renamed = verdict.parent / f"{verdict.name}{PRUNING_SUFFIX}"
            path.rename(renamed)
            path = renamed
        delete(path)
    except OSError as error:
        return str(error)
    return None


@dataclass(frozen=True)
class PruneRow:
    """One entry's verdict, and its removal's error when it failed."""

    verdict: Verdict
    error: str | None = None


@dataclass(frozen=True)
class PruneReport:
    """A prune's rows, oldest first, and whether it was a dry run."""

    rows: tuple[PruneRow, ...]
    dry_run: bool

    @property
    def removed(self) -> int:
        """The folders removed, or that a dry run would remove."""
        return sum(r.verdict.removes and r.error is None for r in self.rows)

    @property
    def kept(self) -> int:
        """The folders kept by the rules."""
        return sum(not r.verdict.removes for r in self.rows)

    @property
    def errors(self) -> int:
        """The removals that failed."""
        return sum(r.error is not None for r in self.rows)


def prune_album(
    album: Path,
    *,
    dry_run: bool,
    acquire_lock: AcquireLock,
    read: ReadManifest = read_manifest,
    delete: Delete | None = None,
) -> PruneReport:
    """Judge *album*'s run folders and remove the ones nothing needs, holding its lock.

    An album with no run folder has nothing to prune, and nothing is created. A
    dry run holds the lock too, so its table is the one a pass would act on. Leftovers
    go first, so none blocks the rename of the run it is named for.
    *delete* None is `lyrics_delete.chosen_delete`'s, chosen now, not at import.
    Raises `RunFolderLinkError`, `LockHeldError` and the listing's `OSError`.
    """
    delete = chosen_delete() if delete is None else delete
    refuse_linked_run_folder(album)
    parent = run_parent(album)
    if parent is None:
        return PruneReport((), dry_run)
    with album_lock(album, acquire=acquire_lock):
        verdicts = judge(scan(parent, read))
        errors: dict[Path, str | None] = {}
        if not dry_run:
            for verdict in sorted(
                (v for v in verdicts if v.removes), key=lambda v: not v.leftover
            ):
                errors[verdict.path] = remove_folder(verdict, delete)
    return PruneReport(
        tuple(PruneRow(v, errors.get(v.path)) for v in verdicts), dry_run
    )
