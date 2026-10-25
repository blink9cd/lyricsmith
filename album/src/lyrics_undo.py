"""Undo: put an album, or one track, back as a run's backup found it.

`undo_run` is handed the run its caller chose (`undo_album` chooses it once, through
`Stages`) and that run's manifest, and for each selected track restores two things, then
marks the track undone in the manifest. When every track of the run is undone, the run's
state becomes `RunState.UNDONE`.

- **Frames.** Every `SYLT` and `USLT` frame is replaced by the backed-up ones, rebuilt
  from their proven exports or exact JSON copies, every field as recorded, and saved
  at the recorded ID3 version; `restore_frames` reads the file back to confirm. A
  track whose frames and version already match the backup is not rewritten, so the
  MP3 of a track the run never changed is never saved. A track that had no ID3v2 header
  before the run had no lyrics frame either: its frames are removed through
  `strip_lyrics`, and the v2.4 tag the run's write gave it stays with every other
  frame it holds (the ID3v1 fields that write carried over, among them), since undo
  never removes a frame it cannot prove the run wrote.
- **Sidecars, by name.** A track's sidecar slot is its two names, ``<stem>.lrc`` and
  ``<stem>.txt``. Each sidecar the backup copied is written back under its own name,
  whatever kind the run left there, and a file under either name the backup did not
  find is removed. That holds whether or not the run recorded what it wrote, so a run
  killed part-way is undone the same way. A recorded name outside the slot is refused.
  A sidecar the backup recorded as a symbolic link is put back as that link, by its
  recorded target, in one atomic replace; a link already pointing there is left alone.
  A symbolic link under a slot name is otherwise removed or replaced itself, never the
  file it points at, and is not displaced; a folder, a pipe or a device there is the
  track's error, raised before anything of the track changes, and is left as it is.
- **Displaced, never destroyed.** A person may have made or edited a sidecar since
  the run. So before undo removes a sidecar file, or replaces one whose bytes differ
  from the backed-up copy or that stands where the backup found a link, it copies the
  file into the run folder at
  ``undo/<the track's folder>/<name>``, confirmed by reading it back, with ``-2``,
  ``-3`` and so on before the suffix when an earlier undo left that name; only then is
  the sidecar removed or replaced. Two kinds of sidecar go without a copy: one that
  matches its backup, which is left alone, and one whose bytes still hash to the
  SHA-256 the run recorded for that name, which is provably the run's own. A run
  killed before recording its hashes proves nothing, so what it wrote is displaced.
  The track's result names each copy by its path in the album, an error's result too
  (a copy made before a later step failed is still there), and the manifest keeps the
  same list in the track's record.

Undoing an undone track is a no-op reported as `UndoStatus.ALREADY_UNDONE`; so is a
track whose backup failed, which the run never wrote. One track's error (a track deleted
or renamed since the run, one turned into a link or moved behind a linked folder since,
which undo refuses so that it never writes outside the album, a tag rewritten at another
ID3 version since, backed-up frames one tag cannot hold together, a backup copy
reached through a link out of the run folder, which undo never reads, an ``undo/``
folder linked out of it, which undo never writes through, a sidecar or backup copy
past its cap, which undo never reads whole) is that track's result, and the other
tracks are still undone. Undoing a run that is not the newest is allowed; the report
says so, and the caller warns. A manifest that cannot be written
after a track stops the undo there, with a report of every track it reached. A Ctrl-C
waits for the track being restored and its record, then stops the undo with the same
kind of report, the tracks it never began named as not reached. The run folder is kept.
The caller holds the album lock, and nothing here prints.
"""

from __future__ import annotations

import dataclasses
import os
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import cast

import lyrics_files
import lyrics_manifest
from lyrics_album import deferred_interrupt
from lyrics_backup import load_before_frames
from lyrics_model import LyricsError, ManifestSchemaError
from lyrics_records import (
    ErrorStage,
    Manifest,
    ManifestTrack,
    RunState,
    SidecarCopy,
    StageError,
    TrackBefore,
    UndoReport,
    UndoStatus,
    UndoTrackResult,
)
from lyrics_sidecar import read_bounded, sidecar_digest
from lyrics_slots import Slot, check_slot, sidecar_slot, slot_entry
from lyrics_stages import UndoRun
from lyrics_tags import read_tags, restore_frames, strip_lyrics

#: The run folder's subfolder holding the sidecars an undo displaced.
UNDO_DIR = "undo"
#: The number the first displaced copy of a name takes, in effect: no suffix.
_FIRST_COPY = 1


class TrackRefusedError(LyricsError):
    """A track undo will not touch in place: gone, a link, or reached through one.

    Raised inside the per-track boundary, so it is that track's error, never the run's.
    """


def _restore_track_frames(
    mp3: Path, run_dir: Path, version: tuple[int, int] | None, before: TrackBefore
) -> bool:
    """Put the frames *before* records back into *mp3*, unless they are already there.

    *version* is the ID3 version the backup recorded; None means the track had no
    ID3v2 header, so no lyrics frame, and removing the run's frames is the restore.
    Return whether *mp3* was saved.
    """
    if version is None:
        return strip_lyrics(mp3)
    frames = load_before_frames(run_dir, before)
    current = read_tags(mp3)
    unchanged = current.id3_version == version
    if unchanged and Counter(current.frames) == Counter(frames):
        return False
    restore_frames(mp3, frames, version)
    return True


def _check_sidecar_names(mp3: Path, before: TrackBefore) -> None:
    """Refuse a backed-up sidecar whose name is not one of *mp3*'s two sidecar names."""
    stray = {copy.name for copy in before.sidecars} - sidecar_slot(mp3)
    if stray:
        raise ManifestSchemaError(f"not a sidecar of {mp3.name}: {sorted(stray)}")


def _free_name(folder: Path, name: str) -> Path:
    """*name* in *folder*, or ``-2``, ``-3`` and so on before its suffix when taken.

    A numbered name that would pass `lyrics_files.NAME_MAX` has its stem shortened
    (`lyrics_files.fit_name`), so a sidecar named in the whole limit is still kept.
    """
    target, count = folder / name, _FIRST_COPY
    while target.exists():
        count += 1
        stem, suffix = Path(name).stem, Path(name).suffix
        target = folder / lyrics_files.fit_name(stem, f"-{count}{suffix}")
    return target


def _undo_folder(run_dir: Path, relative: str) -> Path:
    """The run folder's *relative* undo folder, made, and proven still inside it.

    The folder is checked before it is made and again after, so a link leading out
    of the run folder, there already or put there while undo runs, raises
    `lyrics_files.OutsideRunFolderError` and no file is written through it. Under the
    accepted race, a link put in the run folder while undo runs (which needs write
    access to it during the run), ``mkdir`` can make empty folders outside before
    the second check refuses the track, and a link put there between that check and
    `_displace`'s write would receive the copy: the second check narrows the window
    to that write, it does not close it. The path returned is the run
    folder's own, not the resolved one, so a copy is still named by its path in the
    album.
    """
    lyrics_files.inside_run_folder(run_dir, relative)
    folder = run_dir / relative
    folder.mkdir(parents=True, exist_ok=True)
    lyrics_files.inside_run_folder(run_dir, relative)
    return folder


def _displace(name: str, data: bytes, run_dir: Path, relative: str) -> Path:
    """Keep sidecar *name*'s bytes *data* in the run folder's *relative* undo folder.

    The copy is confirmed and takes a name no copy has. Return its path. Only once
    this returns may the sidecar be replaced or removed: an earlier displaced copy is
    never overwritten.
    """
    target = _free_name(_undo_folder(run_dir, relative), name)
    lyrics_files.write_confirmed(target, data)
    return target


@dataclass
class _SetAside:
    """Where one track's undo keeps copies, what the run recorded, and what it did.

    Copies go to *undo_folder*, a path relative to *run_dir*, checked as each copy is
    made (`_undo_folder`). *recorded* maps each sidecar name the run recorded leaving
    to the SHA-256 of its bytes. *copies* grows as each copy lands, so a failure later
    in the track still finds every copy already made; *mp3_saved* likewise outlives a
    failure.
    """

    run_dir: Path
    undo_folder: str
    recorded: Mapping[str, str]
    copies: list[Path] = field(default_factory=list)
    #: True once the track's MP3 was saved, or may have been: set before its frames'
    #: restore starts, then to the restore's own answer once it returns, so a restore
    #: that raises (a save whose read-back failed) still counts as a possible save.
    mp3_saved: bool = False

    def clear(self, target: Path) -> None:
        """Copy *target* into the run folder unless it is the run's own, unchanged.

        The run's own is a file whose bytes still hash to what the run recorded under
        its name: it can go without a copy. Anything else is displaced first. *target*
        is read once, under the lyrics cap (`read_bounded`), so one past it raises
        `SidecarTooLargeError`, unread past the cap and never copied.
        """
        data = read_bounded(target, target.name)
        recorded = self.recorded.get(target.name)
        if recorded is not None and sidecar_digest(data) == recorded:
            return
        copy = _displace(target.name, data, self.run_dir, self.undo_folder)
        self.copies.append(copy)


def _restore_file(target: Path, data: bytes, aside: _SetAside) -> None:
    """Put backed-up bytes *data* back under *target*, unless they are already there.

    A file holding other bytes is set aside first. A link is replaced itself, never
    followed, and never displaced: its bytes, if any, live elsewhere. Sizes are
    compared first, so a file of another length, one past the cap among them, is
    never read here.
    """
    if slot_entry(target) is Slot.FILE:
        if target.stat().st_size == len(data) and target.read_bytes() == data:
            return
        aside.clear(target)
    lyrics_files.write_confirmed(target, data)


def _restore_link(target: Path, link: str, aside: _SetAside) -> None:
    """Put the backed-up link to *link* back under *target*, unless it is already there.

    A file in its place is set aside first; another link is replaced without a copy.
    """
    entry = slot_entry(target)
    if entry is Slot.LINK and os.readlink(target) == link:
        return
    if entry is Slot.FILE:
        aside.clear(target)
    lyrics_files.replace_with_link(target, link)


def _restore_copy(
    folder: Path, run_dir: Path, copy: SidecarCopy, aside: _SetAside
) -> None:
    """Put one backed-up sidecar back into *folder*: its link, or its copy's bytes.

    The copy is read under the lyrics cap, as the backup read the sidecar it copied,
    so one grown past it since raises `SidecarTooLargeError` and is never written back.
    """
    target = folder / copy.name
    if copy.link is not None:
        _restore_link(target, copy.link, aside)
        return
    # A record holds exactly one of a link and a file copy (SidecarCopy refuses
    # anything else), so this one names its copy.
    source = lyrics_files.inside_run_folder(run_dir, cast(str, copy.file))
    _restore_file(target, read_bounded(source, copy.name), aside)


def _restore_sidecars(
    mp3: Path, run_dir: Path, before: TrackBefore, aside: _SetAside
) -> None:
    """Put each backed-up sidecar back by name; remove a slot name it did not hold.

    A file about to be replaced or removed is set aside first (`_SetAside.clear`);
    one that already matches its backup is left alone. A link is removed or replaced
    itself, never its target, and never displaced: its bytes live elsewhere. The
    restores land first and the removals follow, so a failure part-way leaves an extra
    sidecar rather than a missing one.
    """
    for copy in before.sidecars:
        _restore_copy(mp3.parent, run_dir, copy, aside)
    kept = {copy.name for copy in before.sidecars}
    for name in sorted(sidecar_slot(mp3) - kept):
        target = mp3.parent / name
        entry = slot_entry(target)
        if entry is Slot.ABSENT:
            continue
        if entry is Slot.FILE:
            aside.clear(target)
        target.unlink()


def _recorded_hashes(track: ManifestTrack) -> dict[str, str]:
    """Each sidecar the run recorded leaving for *track*, by name; none when unrecorded."""
    after = track.after
    return {} if after is None else {h.name: h.sha256 for h in after.sidecar_hashes}


def _restore_track(
    mp3: Path,
    run_dir: Path,
    track: ManifestTrack,
    before: TrackBefore,
    aside: _SetAside,
) -> None:
    """Restore one track's frames and sidecars, refusing a stray name before either."""
    _check_sidecar_names(mp3, before)
    check_slot(mp3)
    aside.mp3_saved = True
    aside.mp3_saved = _restore_track_frames(mp3, run_dir, track.id3_version, before)
    _restore_sidecars(mp3, run_dir, before, aside)


def _track_refusal(album: Path, mp3: Path, relpath: str) -> str | None:
    """Why the track at *mp3* cannot be undone in place, or None when it can.

    A track gone since the run is refused, and so is one that has become a link or
    that resolves outside the album through a linked folder: undo writes only inside
    the album, never through a link to somewhere else.
    """
    if mp3.is_symlink():
        return f"{relpath} is a symbolic link; left as it is"
    if not mp3.is_file():
        return f"{relpath} is missing from the album"
    if not mp3.resolve().is_relative_to(album.resolve()):
        return f"{relpath} resolves outside the album; left as it is"
    return None


def _undo_track(album: Path, run_dir: Path, track: ManifestTrack) -> UndoTrackResult:
    """Undo one track; any failure is this track's result, never the run's.

    The result names each sidecar copy set aside, by its album path, on an error as
    well as on success, and says whether the MP3 was saved, or may have been.
    """
    if track.undone or track.before is None:
        return UndoTrackResult(track.path, UndoStatus.ALREADY_UNDONE)
    mp3 = album / track.path
    undo_folder = (
        PurePosixPath(UNDO_DIR) / PurePosixPath(track.path).parent
    ).as_posix()
    aside = _SetAside(run_dir, undo_folder, _recorded_hashes(track))
    status, failure = UndoStatus.UNDONE, None
    try:
        # Inside the boundary: checking a link or resolving a path can raise too.
        refusal = _track_refusal(album, mp3, track.path)
        if refusal is not None:
            raise TrackRefusedError(refusal)
        _restore_track(mp3, run_dir, track, track.before, aside)
    except Exception as error:
        # The per-track boundary: a refusal, a TagVersionError or ReadBackError from
        # the restore, an edited run folder, an I/O error. The others are still undone.
        name = type(error).__name__
        status = UndoStatus.ERROR
        failure = StageError(ErrorStage.UNDO, name, str(error) or name)
    copies = tuple(path.relative_to(album).as_posix() for path in aside.copies)
    return UndoTrackResult(track.path, status, failure, copies, aside.mp3_saved)


def _recorded(manifest: Manifest, index: int, result: UndoTrackResult) -> Manifest:
    """*manifest* with track *index*'s undo recorded, and the run undone once all are.

    The track is marked undone unless its undo errored (a track whose backup failed
    has nothing to undo, so it counts as undone); any copies it set aside are added
    to its record either way.
    """
    tracks = list(manifest.tracks)
    entry = tracks[index]
    tracks[index] = dataclasses.replace(
        entry,
        undone=result.status is not UndoStatus.ERROR,
        displaced=entry.displaced + result.displaced,
    )
    every = all(entry.undone for entry in tracks)
    state = RunState.UNDONE if every else manifest.state
    return dataclasses.replace(manifest, tracks=tuple(tracks), state=state)


def _changes_record(entry: ManifestTrack, result: UndoTrackResult) -> bool:
    """True when *result* is something *entry*'s record does not hold yet."""
    newly_undone = result.status is not UndoStatus.ERROR and not entry.undone
    return newly_undone or bool(result.displaced)


@dataclass
class _UndoProgress:
    """One undo so far: the manifest as last recorded, each result, and why it stopped."""

    album: Path
    run_dir: Path
    manifest: Manifest
    results: list[UndoTrackResult] = field(default_factory=list)
    record_error: str | None = None

    def undo_one(self, index: int, entry: ManifestTrack) -> None:
        """Undo track *index* and record it; a failed record is kept, never raised.

        Called with the interrupt deferred, so a record that fails behind a held
        Ctrl-C is caught here, where the interrupt raised as the hold ends cannot
        replace it.
        """
        result = _undo_track(self.album, self.run_dir, entry)
        self.results.append(result)
        if not _changes_record(entry, result):
            return
        self.manifest = _recorded(self.manifest, index, result)
        try:
            lyrics_manifest.write_manifest(self.run_dir, self.manifest)
        except lyrics_files.RECORD_ERRORS as error:
            self.record_error = f"could not record the undo in {self.run_dir}: {error}"


def _undo_selected(progress: _UndoProgress, selected: frozenset[str]) -> bool:
    """Undo each selected track in manifest order; True when a Ctrl-C stopped it.

    Each track's restore and its record run as one held write: a Ctrl-C waits for
    both, so no MP3 is left half-saved and no displaced copy goes unrecorded. A record
    that cannot be written stops the undo at that track.
    """
    try:
        for index, entry in enumerate(progress.manifest.tracks):
            if entry.path not in selected:
                continue
            with deferred_interrupt():
                progress.undo_one(index, entry)
            if progress.record_error is not None:
                return False
    except KeyboardInterrupt:
        return True
    return False


def undo_run(
    album: Path,
    run_dir: Path,
    manifest: Manifest,
    *,
    newest: bool,
    track: str | None,
) -> UndoReport:
    """Restore *album*, or its *track*, from the run in *run_dir*, whose *manifest* it is.

    The caller chose the run and read its manifest; *newest* says whether that run is
    the album's newest, for the report. *track* is an album-relative path, as the check
    mode takes it.

    Each track's undo is recorded in the manifest as it finishes, by an atomic replace,
    so an undo stopped part-way leaves a manifest that says which tracks it reached.
    When that record cannot be written (an OS error, or a write that does not read
    back), undo stops at once and returns what it did, that track included, with
    *record_error* saying why. A Ctrl-C stops it after the track in hand, returning
    the tracks it reached, *interrupted* set, and the rest as *not_reached*. Raise
    `TrackNotInRunError`, before any track changes, when *track* is not one the run
    lists. The caller holds the album lock.
    """
    selected = frozenset(entry.path for entry in manifest.selected(track))
    progress = _UndoProgress(album, run_dir, manifest)
    interrupted = _undo_selected(progress, selected)
    reached = {result.path for result in progress.results}
    not_reached = tuple(
        entry.path
        for entry in manifest.tracks
        if interrupted and entry.path in selected and entry.path not in reached
    )
    return UndoReport(
        manifest.run_id,
        newest,
        tuple(progress.results),
        progress.record_error,
        interrupted,
        not_reached,
    )


#: Bound to its protocol, so the type checker holds the signature.
STAGE: UndoRun = undo_run
