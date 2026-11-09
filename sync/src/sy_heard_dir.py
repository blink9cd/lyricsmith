"""The ``--heard-dir`` store as a run keeps it: what whisper-cli heard of each text not sung.

The store itself, its files and its bounds, is album's `lyrics_heard_store`; this module
is when a run reads, cleans and writes it. A run reads the whole store before any track
is planned (`read_store`), so a track with a record that is not skipped (its text
changed, or its row was removed) is judged against what was heard of it instead of
being heard again (`sy_plan`). At the
start of a run, never a dry run, `clean` removes each record whose track, under the
run's --path, is gone, or now holds a timed ``.lrc`` that releases it: one this tool
timed, or one whose words are not the record's (`_is_released`). Such a record can
serve nothing. A provider's timed text heard not sung keeps its record until it is
fixed, so a timing run between the hearing and the fix never erases it. A path with a
``..`` part is never under --path, so no sidecar outside it is read.
Records outside --path are left alone, so a run over one folder never empties the store
of a library that is not there to look at. A file in the store that is not a record
this tool can read is reported, never removed.

As each track ends, `Keeper.keep` writes a not-sung track's record, under the path the
walk named it by and the fingerprint of the text it was judged against, and removes the
record of a track timed, re-timed or left low-confidence, whose text was heard sung. A
track whose text is longer than the track keeps its record, so its next text is judged
against it. A transcript longer than the store keeps is said beneath the row, unkept.
Its caller holds Ctrl-C around it and the track's ``--by-hand-list`` row together, so a
row and its record are kept or lost as one. A failed write or removal is said beneath
the track's row, and the run then exits 1.
Nothing here prints.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
import sy_by_hand
import sy_kind
import sy_plan
from lyrics_heard import text_fingerprint
from lyrics_hearing_log import HeardKind
from lyrics_heard_store import (
    MAX_TRANSCRIPT_BYTES,
    STORE_ERRORS,
    HeardRecord,
    Listing,
    delete_record,
    list_records,
    write_record,
)
from lyrics_sidecar import UNREADABLE
from sy_types import WRITTEN, Action, Outcome, Planned, TrackResult

#: The option's name, as each line it adds to the output starts.
OPTION: Final = "--heard-dir"
#: The errors that say a path names nothing: the track is gone.
_GONE: Final = frozenset({errno.ENOENT, errno.ENOTDIR})
#: The path part that climbs to the folder above.
_PARENT: Final = ".."
#: The outcomes that end a track's record: it was timed, or heard and aligned, so its
#: text is sung. A text longer than the track keeps its record, for the next text.
_ENDS_RECORD: Final = WRITTEN | {Outcome.LOW_CONFIDENCE}
#: The note beneath a not-sung row whose transcript is longer than the store keeps.
UNKEPT: Final = (
    f"{OPTION}: what was heard is over {MAX_TRANSCRIPT_BYTES // 1024} KB"
    " and was not kept"
)


@dataclass(frozen=True)
class Cleaned:
    """The store after `clean`: the records left, the lines to print, any failure."""

    listing: Listing
    lines: tuple[str, ...]
    failed: bool


def read_store(folder: Path | None) -> tuple[Listing, list[str]]:
    """The store's records, and the setup problem when it cannot be read.

    With no option, or no folder yet, the store is empty. A name holding anything but
    a folder is a problem, and so is a folder that cannot be listed.
    """
    empty = Listing((), ())
    if folder is None:
        return empty, []
    if folder.exists() and not folder.is_dir():
        return empty, [f"{OPTION} is not a folder: {folder}"]
    try:
        return list_records(folder), []
    except OSError as error:
        return empty, [f"{OPTION} {folder}: cannot be read ({error})"]


def by_path(listing: Listing) -> dict[str, HeardRecord]:
    """Each record by its track's path, exactly as the walk that wrote it named it."""
    return {str(record.path): record for record in listing.records}


def problem_lines(listing: Listing) -> list[str]:
    """One line for each file in the store that is not a record this tool can read."""
    return [f"{OPTION}: {problem}" for problem in listing.problems]


def _is_gone(track: Path) -> bool:
    """True when *track* names nothing; a path that cannot be looked at is not gone.

    A path holding a NUL byte, which no file system path holds, names nothing: the OS
    layer refuses it with `ValueError` before it is looked at.
    """
    try:
        os.lstat(track)
    except ValueError:
        return True
    except OSError as error:
        return error.errno in _GONE
    return False


def _is_timed(track: Path) -> bool:
    """True when *track* now holds a timed ``.lrc``, by album's own reading of it."""
    return sy_plan.plan_track(track, overwrite=False).action is Action.SKIP_TIMED


def _is_under(track: Path, scope: Path) -> bool:
    """True when *track* is *scope* or lies under it, never climbing out with ``..``.

    The store keeps a path as the walk named it, never normalized, so a ``..`` in a
    record's path could name a file outside *scope* while its text lies under it: such
    a path is not under *scope*, so no sidecar outside --path is ever read for it.
    """
    if _PARENT in track.parts:
        return False
    return track == scope or scope in track.parents


def _is_released(track: Path, fingerprint: str) -> bool:
    """True when *track*'s timed text no longer needs the record of *fingerprint*.

    A text this tool timed was heard sung to be timed, so it releases the record as
    ever; any other timed text, a provider's, releases it only once its words are
    not the record's: fixed, then timed. A provider's text heard not sung, still in
    place, keeps its record for the fix. A text that cannot be read releases nothing.
    """
    try:
        found = sy_kind.text_kind(track)
    except (*UNREADABLE, OSError):
        return False
    if found is None or found.kind is HeardKind.UNTIMED:
        return False
    if found.kind is HeardKind.MACHINE_TIMED:
        return True
    return text_fingerprint(found.lines) != fingerprint


def _is_spent(record: HeardRecord, scope: Path) -> bool:
    """True when *record*'s track, under *scope*, is gone, or timed and released."""
    track = record.path
    if not _is_under(track, scope):
        return False
    if _is_gone(track):
        return True
    return _is_timed(track) and _is_released(track, record.fingerprint)


def _removal_failure(track: Path, error: Exception) -> str:
    """The line saying *track*'s record could not be removed."""
    return f"{OPTION}: could not remove the record of {track}: {error}"


def clean(folder: Path, listing: Listing, scope: Path) -> Cleaned:
    """Remove each record whose track under *scope* is gone or timed; what is left.

    A record that cannot be removed stays listed, its failure a line.
    """
    kept: list[HeardRecord] = []
    failures: list[str] = []
    for record in listing.records:
        if not _is_spent(record, scope):
            kept.append(record)
            continue
        try:
            delete_record(folder, record.path)
        except OSError as error:
            kept.append(record)
            failures.append(_removal_failure(record.path, error))
    removed = len(listing.records) - len(kept)
    said = f"{OPTION}: removed {removed} record(s) of tracks gone or timed"
    lines = ((f"{said} from {folder}",) if removed else ()) + tuple(failures)
    left = Listing(tuple(kept), listing.problems)
    return Cleaned(left, lines, failed=bool(failures))


class Keeper:
    """The store as this run keeps it: each not-sung track's record, each timed one's gone.

    *held* names, by path, every track the store held a record of at the start.
    """

    def __init__(self, folder: Path | None, held: Iterable[str]) -> None:
        """Keep records in *folder*; with no *folder*, keep nothing."""
        self.folder = folder
        self.held = set(held)
        #: How many records this run wrote, and whether any write or removal failed.
        self.added = 0
        self.failed = False

    def keep(self, planned: Planned, result: TrackResult) -> TrackResult:
        """Write or remove *result*'s record as it ended; *result*, any failure noted.

        The caller holds Ctrl-C around this and the track's list row.
        """
        if self.folder is None:
            return result
        transcript = result.transcript
        if result.outcome is Outcome.NOT_SUNG:
            if transcript is None:
                return replace(result, detail=(*result.detail, UNKEPT))
            record = HeardRecord(
                result.audio, text_fingerprint(planned.lines), transcript
            )
            return self._write(self.folder, record, result)
        if result.outcome in _ENDS_RECORD and str(result.audio) in self.held:
            return self._remove(self.folder, result)
        return result

    def _write(
        self, folder: Path, record: HeardRecord, result: TrackResult
    ) -> TrackResult:
        """Keep *record*, what was heard of *result*'s track, in *folder*."""
        try:
            write_record(folder, record)
        except STORE_ERRORS as error:
            self.failed = True
            note = f"{OPTION}: could not keep what was heard in {folder}: {error}"
            return replace(result, detail=(*result.detail, note))
        self.held.add(str(result.audio))
        self.added += 1
        return result

    def _remove(self, folder: Path, result: TrackResult) -> TrackResult:
        """Remove the record of *result*'s track, timed now."""
        try:
            delete_record(folder, result.audio)
        except OSError as error:
            self.failed = True
            note = _removal_failure(result.audio, error)
            return replace(result, detail=(*result.detail, note))
        self.held.discard(str(result.audio))
        return result


def open_store(
    folder: Path | None, store: Listing, scope: Path, *, dry_run: bool
) -> tuple[Keeper, Listing, list[str]]:
    """The run's keeper, the store as it plans with, and the lines to print first.

    The store's unreadable files are said either way. Its spent records (under *scope*,
    the run's --path, of tracks gone or timed) are removed only by a run that times,
    never a *dry_run*, which leaves the store as it found it.
    """
    lines = problem_lines(store)
    if folder is None or dry_run:
        return Keeper(folder, ()), store, lines
    cleaned = clean(folder, store, scope)
    keeper = Keeper(folder, by_path(cleaned.listing))
    keeper.failed = cleaned.failed
    return keeper, cleaned.listing, [*lines, *cleaned.lines]


@dataclass(frozen=True)
class Kept:
    """Where each finished track is recorded: the --by-hand-list, and the --heard-dir."""

    recorder: sy_by_hand.Recorder
    keeper: Keeper

    def record(self, planned: Planned, result: TrackResult) -> TrackResult:
        """*result* kept in the store, then listed; any note for its row added.

        The store first: the list's save holds Ctrl-C itself and raises it once saved,
        so taken second, a Ctrl-C in it can never part the row from the record.
        """
        return self.recorder.record(self.keeper.keep(planned, result))


def kept_lines(keeper: Keeper) -> list[str]:
    """The footer's line naming the store, when this run kept records in it."""
    if not keeper.added or keeper.folder is None:
        return []
    return [
        f"kept what was heard of {keeper.added} track(s) not sung in {keeper.folder}"
    ]
