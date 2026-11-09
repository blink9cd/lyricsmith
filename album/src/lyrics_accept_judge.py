"""`--mode accept`'s judgment: what an album's ``.lyricsmith`` holds, and what deleting it costs.

Accept removes the whole run folder, every run's backup with it, so before anything goes
the album is judged first:

- **Refused whole** when the folder holds what accept cannot vouch for: an unfinished
  (``backed_up``) run, a manifest that cannot be read, a run whose ``undo`` folder
  cannot be listed, any entry lyricsmith did not write (each named), or a link at the
  folder or its lock.
- **At risk, by track**: for every run, not only the newest, each track whose backup is
  the only copy of what the run changed there. A newer run's backup holds the older
  run's output, never the older run's ``before``, so each run is judged on its own. A
  track counts only when its ``before`` holds a backup file (a frame export, or a
  sidecar copy rather than a link) that the run touched, and it was not undone; a
  sidecar undo set aside counts, undone or not. An ``error`` outcome counts whenever
  its ``before`` holds a backup file, whatever it recorded: a write can change the
  file and raise before its outcome records the change, so the record can lag it.
- **Held**: each run's counts of frame exports, sidecar copies and set-aside files.

`read_contents` is the one read, and lists the folder through prune's own `scan`, so a
run-id entry is read exactly as prune reads it; `judge_contents` and everything under it
are pure. What lyricsmith itself places directly in ``.lyricsmith`` is the lock, the
run-id folders and prune's ``.pruning`` renames: every atomic-write temporary lands beside
its target inside a run-id folder, never beside them. A ``.lyricsmith.accepting`` beside
the folder is an earlier accept's rename, noted for the next one to finish.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from lyrics_categories import removed_reason
from lyrics_manifest import read_manifest
from lyrics_prune import Found, Kind, scan
from lyrics_records import Manifest, ManifestTrack, OutcomeStatus, RunState
from lyrics_records import TrackBefore, TrackOutcome
from lyrics_run_folders import LOCK_NAME, RUN_FOLDER_NAME, is_link_or_junction
from lyrics_stages import ReadManifest
from lyrics_undo import UNDO_DIR

#: The ending accept renames ``.lyricsmith`` to before deleting it: a stopped accept
#: then never leaves a half-deleted folder that still reads as the album's runs.
ACCEPTING_SUFFIX: Final = ".accepting"
#: The renamed folder's name, a sibling of ``.lyricsmith`` in the album.
ACCEPTING_NAME: Final = f"{RUN_FOLDER_NAME}{ACCEPTING_SUFFIX}"
#: The detail of an ``undo`` folder file that no track's ``displaced`` list names.
UNLISTED: Final = "not listed by any track"
#: The detail of a track whose run recorded no outcome for it.
NO_OUTCOME: Final = "no outcome recorded"
#: How a set-aside track's paths are joined in its detail.
_JOINER: Final = ", "


class RefusalKind(StrEnum):
    """Why an album is refused whole, before anything is touched."""

    UNFINISHED = "unfinished run"
    UNREADABLE = "unreadable manifest"
    #: The manifest read, but the run folder itself could not be listed.
    UNREADABLE_FOLDER = "unreadable run folder"
    UNKNOWN = "unknown entry"
    LINKED = "linked"


class AtRiskReason(StrEnum):
    """Why a track's backup is the only copy of something, by its run's outcome."""

    REMOVED = "removed"
    STRIPPED = "stripped"
    REPLACED = "replaced"
    SET_ASIDE = "set aside"
    UNFINISHED = "unfinished"
    #: The track's ``--mode check`` flags now; the check itself fills these in.
    CHECK_FLAG = "check flags"
    #: The track's check raised, so what it would say is unknown; the detail is why.
    CHECK_FAILED = "check could not run"


@dataclass(frozen=True)
class Refusal:
    """One reason the album is refused; *detail* names the run or entry."""

    kind: RefusalKind
    detail: str


@dataclass(frozen=True)
class AtRisk:
    """One track a run changed whose only copy goes with the backup.

    *track* is the album-relative path, or an ``undo`` folder file's own album path
    when no track names it. *detail* is a removal's landed reason, the outcome's
    status, or a set-aside track's paths.
    """

    track: str
    run_id: str
    reason: AtRiskReason
    detail: str


@dataclass(frozen=True)
class RunHeld:
    """What one run's backup holds: frame exports, sidecar copies, set-aside files.

    *state* is None for a folder holding no manifest, a backup that never finished.
    """

    run_id: str
    state: RunState | None
    frames: int
    sidecars: int
    set_aside: int


@dataclass(frozen=True)
class RunEntry:
    """One run-id folder as read: its kind, manifest or error, and ``undo`` files.

    *kind* is `Kind.RUN`, `Kind.UNREADABLE` or `Kind.NO_MANIFEST`. *undo_files* are
    album-relative POSIX paths, as a track's ``displaced`` list names them.
    *folder_unreadable* marks a `Kind.UNREADABLE` run whose manifest read but whose
    ``undo`` folder could not be listed: its refusal names the folder, not the manifest.
    """

    name: str
    order: tuple[str, int]
    kind: Kind
    manifest: Manifest | None = None
    error: str | None = None
    undo_files: tuple[str, ...] = ()
    folder_unreadable: bool = False


@dataclass(frozen=True)
class Contents:
    """What an album's ``.lyricsmith`` holds, read once; empty when it is absent.

    *runs* are oldest first and *unknown* names every entry lyricsmith did not write;
    the lock and prune's ``.pruning`` leftovers are known, so neither is listed. A
    linked folder is not listed through. *leftover* says a ``.lyricsmith.accepting``
    sits beside it.
    """

    runs: tuple[RunEntry, ...] = ()
    unknown: tuple[str, ...] = ()
    folder_linked: bool = False
    lock_linked: bool = False
    leftover: bool = False


@dataclass(frozen=True)
class Judgment:
    """The album's refusals, at-risk tracks (by track, then run) and runs held."""

    refusals: tuple[Refusal, ...]
    at_risk: tuple[AtRisk, ...]
    runs: tuple[RunHeld, ...]


# --- Reading the run folder ----------------------------------------------------------


def read_contents(album: Path, read: ReadManifest = read_manifest) -> Contents:
    """List *album*'s ``.lyricsmith``, classifying each entry; empty when it is absent.

    Raises the `OSError` listing the folder raises; one run that cannot be read is
    that run's `Kind.UNREADABLE` entry instead.
    """
    leftover = os.path.lexists(album / ACCEPTING_NAME)
    folder = album / RUN_FOLDER_NAME
    # Defense in depth: accept_album cannot reach this, since album_lock refuses a
    # linked .lyricsmith first, but any other caller is still never listed through.
    if is_link_or_junction(folder):
        return Contents(folder_linked=True, leftover=leftover)
    if not os.path.lexists(folder):
        return Contents(leftover=leftover)
    if not folder.is_dir():
        return Contents(unknown=(RUN_FOLDER_NAME,), leftover=leftover)
    manifests: dict[str, Manifest] = {}

    def recording(run_dir: Path) -> Manifest:
        """Read *run_dir*'s manifest through *read*, keeping it for the entry."""
        manifests[run_dir.name] = manifest = read(run_dir)
        return manifest

    found = sorted(scan(folder, recording), key=lambda f: f.order)
    runs = tuple(
        _run_entry(album, f, manifests.get(f.name))
        for f in found
        if f.kind not in (Kind.LEFTOVER, Kind.NOT_FOLDER)
    )
    lock_linked, others = _others(folder, {f.name for f in found})
    return Contents(
        runs=runs,
        unknown=tuple(f.name for f in found if f.kind is Kind.NOT_FOLDER) + others,
        lock_linked=lock_linked,
        leftover=leftover,
    )


def _others(folder: Path, scanned: set[str]) -> tuple[bool, tuple[str, ...]]:
    """Whether *folder*'s lock is a link, and every name neither scanned nor the lock.

    A lock that is a folder is no lock run_lock made, so it is an unknown entry. A
    linked lock is defense in depth: accept_album cannot reach it, since album_lock
    refuses a linked lock first, but any other caller still has it refused.
    """
    linked = False
    unknown: list[str] = []
    for entry in sorted(folder.iterdir()):
        if entry.name in scanned:
            continue
        if entry.name == LOCK_NAME and (
            is_link_or_junction(entry) or not entry.is_dir()
        ):
            linked = is_link_or_junction(entry)
        else:
            unknown.append(entry.name)
    return linked, tuple(unknown)


def _run_entry(album: Path, found: Found, manifest: Manifest | None) -> RunEntry:
    """*found* as a run entry, a readable run's ``undo`` files listed."""
    if found.kind is not Kind.RUN:
        return RunEntry(found.name, found.order, found.kind, error=found.detail)
    try:
        undo = _undo_files(album, found.parent / found.name)
    except OSError as error:
        return RunEntry(
            found.name,
            found.order,
            Kind.UNREADABLE,
            error=str(error),
            folder_unreadable=True,
        )
    return RunEntry(found.name, found.order, Kind.RUN, manifest, undo_files=undo)


def _undo_files(album: Path, run_dir: Path) -> tuple[str, ...]:
    """Every file under *run_dir*'s ``undo`` folder, as a sorted album-relative path."""
    undo = run_dir / UNDO_DIR
    if not os.path.lexists(undo):
        return ()
    return tuple(sorted(p.relative_to(album).as_posix() for p in _files_under(undo)))


def _files_under(path: Path) -> Iterator[Path]:
    """*path* itself when it is a link or no folder, else every such entry below it.

    A link or a junction is never followed: an ``undo`` entry that is one is listed,
    as a file is.
    The walk keeps its own stack of folders still to list, so a folder nested
    however deep is listed, never a `RecursionError`.
    """
    pending = [path]
    while pending:
        entry = pending.pop()
        if is_link_or_junction(entry) or not entry.is_dir():
            yield entry
        else:
            pending.extend(entry.iterdir())


# --- The judgment --------------------------------------------------------------------


def judge_contents(contents: Contents) -> Judgment:
    """*contents*' refusals, tracks at risk over every run, and each run's holdings."""
    runs = sorted(contents.runs, key=lambda r: r.order)
    risks = [risk for run in runs for risk in _run_risks(run)]
    # A stable sort by track keeps each track's runs in the oldest-first order above.
    risks.sort(key=lambda r: r.track)
    held = tuple(_held(run) for run in runs if run.kind is not Kind.UNREADABLE)
    return Judgment(_refusals(contents, runs), tuple(risks), held)


def _refusals(contents: Contents, runs: Sequence[RunEntry]) -> tuple[Refusal, ...]:
    """Every reason *contents* is refused: links, unknown entries, unreadable, unfinished."""
    refusals: list[Refusal] = []
    # LINKED is defense in depth: accept_album never gets here with a link, since
    # album_lock refuses a linked .lyricsmith or lock first; other callers still do.
    linked = (
        (contents.folder_linked, RUN_FOLDER_NAME),
        (contents.lock_linked, f"{RUN_FOLDER_NAME}/{LOCK_NAME}"),
    )
    refusals.extend(
        Refusal(RefusalKind.LINKED, f"{name} is a symbolic link or junction")
        for is_link, name in linked
        if is_link
    )
    refusals.extend(Refusal(RefusalKind.UNKNOWN, name) for name in contents.unknown)
    for run in runs:
        if run.kind is Kind.UNREADABLE:
            detail = f"{run.name}: {run.error}"
            kind = (
                RefusalKind.UNREADABLE_FOLDER
                if run.folder_unreadable
                else RefusalKind.UNREADABLE
            )
            refusals.append(Refusal(kind, detail))
        elif run.manifest is not None and run.manifest.state is RunState.BACKED_UP:
            detail = f"{run.name} is unfinished: undo it, or run the album again, first"
            refusals.append(Refusal(RefusalKind.UNFINISHED, detail))
    return tuple(refusals)


def _run_risks(run: RunEntry) -> Iterator[AtRisk]:
    """Each track *run* changed whose only copy is its backup, then its set-asides."""
    if run.manifest is None:
        return
    named: set[str] = set()
    for track in run.manifest.tracks:
        change = _change(track)
        if change is not None:
            yield AtRisk(track.path, run.name, *change)
        if track.displaced:
            aside = _JOINER.join(sorted(set(track.displaced)))
            yield AtRisk(track.path, run.name, AtRiskReason.SET_ASIDE, aside)
        named.update(track.displaced)
    for path in run.undo_files:
        if path not in named:
            yield AtRisk(path, run.name, AtRiskReason.SET_ASIDE, UNLISTED)


def _change(track: ManifestTrack) -> tuple[AtRiskReason, str] | None:
    """Why *track*'s backup is the only copy of what its run changed, or None."""
    if track.before is None or track.undone:
        return None
    after = track.after
    if after is None:
        return AtRiskReason.UNFINISHED, NO_OUTCOME
    if after.status is OutcomeStatus.ERROR:
        # The record can lag the write: a read-back failure, a part-applied sidecar
        # plan or a failed strip raises after the file changed and before the outcome
        # names it, so any backup file is taken as touched.
        return (
            (AtRiskReason.REPLACED, after.status.value)
            if _holds(track.before)
            else None
        )
    if after.status is OutcomeStatus.UNCHANGED or not _touched(track.before, after):
        return None
    if after.status is OutcomeStatus.REMOVED:
        return AtRiskReason.REMOVED, removed_reason(after).value
    if after.status is OutcomeStatus.STRIPPED:
        return AtRiskReason.STRIPPED, after.status.value
    return AtRiskReason.REPLACED, after.status.value


def _holds(before: TrackBefore) -> bool:
    """Whether *before* holds any backup file: a frame export or a sidecar copy."""
    return bool(before.frames) or any(c.file is not None for c in before.sidecars)


def _touched(before: TrackBefore, after: TrackOutcome) -> bool:
    """Whether *after* changed a backup file of *before*: its frames or a sidecar copy.

    A sidecar recorded as a link was never copied, so the backup holds nothing of it.
    A rename touches both names: its ``new`` replaced whatever stood there.
    """
    copied = {copy.name for copy in before.sidecars if copy.file is not None}
    changed = {
        after.sidecar_written,
        *after.sidecars_removed,
        *(rename.old for rename in after.sidecars_renamed),
        *(rename.new for rename in after.sidecars_renamed),
    }
    return (after.frames_written and bool(before.frames)) or not copied.isdisjoint(
        changed
    )


def _held(run: RunEntry) -> RunHeld:
    """*run*'s counts: frame exports, sidecar file copies, set-aside files once each."""
    manifest = run.manifest
    if manifest is None:
        return RunHeld(run.name, None, 0, 0, 0)
    backups = [t.before for t in manifest.tracks if t.before is not None]
    frames = sum(len(b.frames) for b in backups)
    sidecars = sum(c.file is not None for b in backups for c in b.sidecars)
    aside = {p for t in manifest.tracks for p in t.displaced} | set(run.undo_files)
    return RunHeld(run.name, manifest.state, frames, sidecars, len(aside))
