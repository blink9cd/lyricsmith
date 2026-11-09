"""`--mode accept`'s records outside the library, and the removal of ``.lyricsmith``.

Accept keeps no backup, but it does keep what each run recorded: every run's
``manifest.json`` is copied, byte for byte, into one records folder per accept under the
state directory (`records_root`), beside an ``album.txt`` naming the album. The records
hold no lyrics, only the manifests. Then the album's ``.lyricsmith`` goes: renamed to
``.lyricsmith.accepting`` first, so an accept stopped part-way never leaves a
half-deleted folder that still reads as the album's runs, then deleted.

**Where a folder holding an open file cannot be renamed.** Windows refuses to rename a
folder while a file inside it is open, and accept holds the album lock, the ``lock``
file inside ``.lyricsmith``, through the removal. There (`RENAMES_HELD_FOLDERS` false)
the folder is not renamed whole: a new ``.lyricsmith.accepting`` is made and each
entry but the lock is moved into it, still under the lock, oldest run first and any
``.pruning`` leftover before them, then that folder is deleted. Once the lock is let
go, `drop_lock_folder` removes the lock and ``.lyricsmith``, unless a run has taken the
folder since. Each run folder still moves whole, so none is ever half deleted under a
run-id name; but an accept stopped between two moves leaves ``.lyricsmith`` holding
the newer runs, each whole, beside a ``.lyricsmith.accepting`` holding the older ones.
That reads as an album whose older runs are gone, a state undo handles as it handles
any, and the next accept finishes the leftover and takes the rest.

**The order, so a failure leaves the recoverable state.** The caller makes the folder
(`make_records_folder`) and writes the records (`write_records`), which reads every file
back against its source; only once that returns does it call `remove_lyricsmith`. A
failed write or read-back raises before any rename, so it deletes nothing: the album's
runs are all still there, and the worst left behind is a partial records folder. A
``.lyricsmith.accepting`` left by an earlier accept killed between its rename and its
delete is finished first (`finish_leftover`), because the rename never replaces one.

The delete is `lyrics_delete.chosen_delete`'s, as prune's is: `shutil.rmtree` where it
resists links, else a walk that never enters one, so accept runs on every host. What
is checked here is the one thing that delete is never given: a ``.lyricsmith.accepting``
(or a ``.lyricsmith``) that is a link, a junction or not a folder is refused, never
deleted through.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Final

from lyrics_accept_judge import ACCEPTING_NAME, RunEntry
from lyrics_backup import run_id_base
from lyrics_delete import StatLike, chosen_delete, same_folder
from lyrics_files import inside_run_folder, read_regular, sync_folder
from lyrics_manifest import MAX_MANIFEST_BYTES
from lyrics_model import LyricsError, ManifestSchemaError, ReadBackError
from lyrics_prune import Delete, Kind
from lyrics_records import MANIFEST_NAME
from lyrics_run_folders import LOCK_NAME, RUN_FOLDER_NAME, is_link_or_junction
from lyrics_run_folders import make_exclusive_dir, run_order
from lyrics_state import (
    STATE_FOLDER_MODE,
    make_state_folder,
    state_root,
    write_state_file,
)

#: How one record file is written; a test passes one that fails or writes wrong bytes.
Write = Callable[[Path, bytes], None]
#: How a folder's entries are flushed to disk; a test passes one that records the call.
Sync = Callable[[Path], None]

#: The records' place inside the state folder (`lyrics_state.state_root`).
RECORDS_DIR_NAME: Final = Path("lyricsmith") / "accepted"
#: The file naming the album the records came from.
ALBUM_FILE: Final = "album.txt"
#: Each run's manifest copy is named for its run id, with this suffix.
RECORD_SUFFIX: Final = ".json"
#: How many hex digits of the album path's sha256 name its records folder.
_HASH_DIGITS: Final = 16
_ENCODING: Final = "utf-8"
#: The run-id kinds whose folder holds a manifest file, read or not.
_WITH_MANIFEST: Final = frozenset({Kind.RUN, Kind.UNREADABLE})
#: Whether this host renames a folder while a file inside it is open. Windows refuses
#: to, and the album lock is such a file inside ``.lyricsmith`` while accept holds it.
RENAMES_HELD_FOLDERS: Final = sys.platform != "win32"
#: Why `drop_lock_folder` kept ``.lyricsmith``: it reads as another folder now.
KEPT_ANOTHER: Final = "it is another folder than the one accept held"
#: Why `drop_lock_folder` kept ``.lyricsmith``: it holds more than its lock.
KEPT_FILLED: Final = "it holds more than its lock"


class RecordsReadBackError(ReadBackError):
    """A records file did not read back as the bytes it was copied from."""


class UnsafeAcceptingError(LyricsError):
    """``.lyricsmith`` or ``.lyricsmith.accepting`` is a link, a junction, or no folder."""


class LeftoverAcceptingError(LyricsError):
    """A ``.lyricsmith.accepting`` is already there: finish it before renaming again."""


# --- The records folder --------------------------------------------------------------


def records_root(environ: Mapping[str, str]) -> Path:
    """The folder every accept's records go under, by the state directory *environ* names.

    ``$XDG_STATE_HOME`` when it holds an absolute path, else ``.local/state`` under
    *environ*'s HOME: `lyrics_state.state_root`, the rule the cool-down files and the
    sweep's ledger follow too.
    """
    return state_root(environ) / RECORDS_DIR_NAME


def make_records_folder(
    root: Path, album: Path, now: datetime, *, sync: Sync = sync_folder
) -> Path:
    """Create and return this accept's own records folder under *root*.

    Named ``<UTC time>-<short sha256 of the album's resolved path>``, the time in the
    run-id form. Created exclusively by `lyrics_run_folders.make_exclusive_dir`, the
    helper `lyrics_backup.create_run_dir` uses for run ids, so two accepts never share
    a folder. Missing parents of *root* are made, each 0o700, as the folder itself
    is (`lyrics_state.make_state_folder`): the records name the album and its runs.
    *root* is then synced through *sync*, so the new folder's entry survives a power
    cut before the album's ``.lyricsmith`` is deleted.
    """
    make_state_folder(root)
    digest = hashlib.sha256(str(album.resolve()).encode(_ENCODING)).hexdigest()
    name = f"{run_id_base(now)}-{digest[:_HASH_DIGITS]}"
    folder = make_exclusive_dir(root, name, mode=STATE_FOLDER_MODE)
    sync(root)
    return folder


def _record_sources(
    folder: Path, album: Path, runs: Iterable[RunEntry]
) -> dict[Path, bytes]:
    """Each records file under *folder*, to the bytes it must hold.

    ``album.txt`` holds *album*'s resolved absolute path, in its file-system bytes, on
    one line, so a relative *album* is still recorded as a place; each
    run whose folder holds a manifest gives its manifest's bytes, unparsed
    (`_manifest_copy`).
    """
    sources = {folder / ALBUM_FILE: os.fsencode(album.resolve()) + b"\n"}
    for run in runs:
        if run.kind in _WITH_MANIFEST:
            run_dir = album / RUN_FOLDER_NAME / run.name
            sources[folder / f"{run.name}{RECORD_SUFFIX}"] = _manifest_copy(run_dir)
    return sources


def _manifest_copy(run_dir: Path) -> bytes:
    """*run_dir*'s manifest bytes, read again as the judgment read them, and capped.

    The file may have changed since the judgment, so it is held to the same rules
    again: a link out of the run folder raises `OutsideRunFolderError`, a link swapped
    in after that check is refused by the open itself, a FIFO pipe or a folder is a
    `NotRegularFileError` at once, and a file over `MAX_MANIFEST_BYTES` is a
    `ManifestSchemaError`, known by the one byte read past the cap.
    """
    path = inside_run_folder(run_dir, MANIFEST_NAME)
    data = read_regular(path, MAX_MANIFEST_BYTES + 1, follow=False)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestSchemaError(
            f"{run_dir.name}/{MANIFEST_NAME} is too large to copy: "
            f"over {MAX_MANIFEST_BYTES} bytes"
        )
    return data


def write_records(
    folder: Path,
    album: Path,
    runs: Iterable[RunEntry],
    *,
    write: Write = write_state_file,
) -> None:
    """Write ``album.txt`` and every run's manifest copy into *folder*, then check all.

    Each file goes through *write*, an atomic replace that reads itself back, 0o600
    by default (`lyrics_state.write_state_file`). Once all
    are written, every one is read back again against its source, so a file changed
    after its own write is caught too. Raise `RecordsReadBackError` naming the first
    that differs; a failed write raises its own error, and so does a file swapped for
    a link since, which is never read through. A manifest changed since the judgment
    into one `_manifest_copy` refuses raises before anything is written. Nothing in
    *album* is changed.
    """
    sources = _record_sources(folder, album, runs)
    for target, data in sources.items():
        write(target, data)
    # Each write left a regular file under its name: a link there now is not ours.
    for target, data in sources.items():
        if read_regular(target, follow=False) != data:
            raise RecordsReadBackError(f"{target} did not read back as copied")


# --- Removing .lyricsmith ------------------------------------------------------------


def refuse_unsafe_accepting(path: Path) -> None:
    """Raise `UnsafeAcceptingError` when *path* is a link, a junction, or no folder."""
    if is_link_or_junction(path) or (os.path.lexists(path) and not path.is_dir()):
        raise UnsafeAcceptingError(
            f"{path.name} in {path.parent} is a symbolic link or junction, or not a "
            "folder; it is not deleted"
        )


def remove_lyricsmith(
    album: Path, delete: Delete | None = None, *, rename_whole: bool | None = None
) -> str | None:
    """Rename *album*'s ``.lyricsmith`` to ``.lyricsmith.accepting``, then delete it.

    Returns the `OSError`'s text when the rename or the delete fails, as
    `lyrics_prune.remove_folder` does, or None when the folder is gone. A failed
    delete leaves ``.lyricsmith.accepting`` for `finish_leftover`. Raises
    `LeftoverAcceptingError` when one is already there, since the rename must never
    replace it, and `UnsafeAcceptingError` for a link or non-folder at either name.
    *rename_whole* False moves each entry but the lock instead (`_move_entries`),
    leaving ``.lyricsmith`` holding only its lock for `drop_lock_folder`; None is
    `RENAMES_HELD_FOLDERS`. *delete* None is `lyrics_delete.chosen_delete`'s, chosen
    now rather than at import.
    """
    delete = chosen_delete() if delete is None else delete
    if rename_whole is None:
        rename_whole = RENAMES_HELD_FOLDERS
    folder, renamed = album / RUN_FOLDER_NAME, album / ACCEPTING_NAME
    refuse_unsafe_accepting(renamed)
    if os.path.lexists(renamed):
        raise LeftoverAcceptingError(
            f"{ACCEPTING_NAME} in {album} is left by an earlier accept; finish it first"
        )
    refuse_unsafe_accepting(folder)
    try:
        if rename_whole:
            folder.rename(renamed)
        else:
            _move_entries(folder, renamed)
        delete(renamed)
    except OSError as error:
        return str(error)
    return None


def _move_entries(folder: Path, renamed: Path) -> None:
    """Make *renamed* and move every entry of *folder* but its lock into it.

    *renamed* is made exclusively, so a folder that appeared under the name since the
    caller's check is never moved into. A ``.pruning`` leftover goes first, then each
    run oldest first, so a stop part-way leaves the newest runs in place, each whole.
    """
    os.mkdir(renamed)
    names = sorted((n for n in os.listdir(folder) if n != LOCK_NAME), key=_move_order)
    for name in names:
        os.rename(folder / name, renamed / name)


def _move_order(name: str) -> tuple[bool, tuple[str, int], str]:
    """*name*'s place in the moves: what is no run id first, then runs oldest first."""
    order = run_order(name)
    return order is not None, order or ("", 0), name


def drop_lock_folder(album: Path, decided: StatLike) -> str | None:
    """Remove ``.lyricsmith`` once it holds nothing but its lock, after the lock is let go.

    *decided* is the folder's `os.lstat` taken while the lock was held: a folder that
    reads as another now, a link included, is left alone (`KEPT_ANOTHER`), and so is
    one holding anything else (`KEPT_FILLED`). Returns why the folder was kept, an
    `OSError`'s text included, or None when it went or was already gone. Nothing is
    raised: a folder this leaves is one holding only its lock, or one a run has taken
    since, and either is the album's as any other.
    """
    folder = album / RUN_FOLDER_NAME
    try:
        first = os.lstat(folder)
    except FileNotFoundError:
        # Reading is the existence check, so no gap opens between a check and a read.
        return None
    except OSError as error:
        return str(error)
    try:
        if not same_folder(first, decided):
            return KEPT_ANOTHER
        if os.listdir(folder) != [LOCK_NAME]:
            return KEPT_FILLED
        # Each removal re-reads the folder first, as `lyrics_delete.delete_tree` does,
        # so a folder swapped in since the last read loses neither its lock nor itself.
        state = _reads_as_decided(folder, decided)
        if state is not True:
            return _kept_unless_gone(state)
        os.unlink(folder / LOCK_NAME)
        state = _reads_as_decided(folder, decided)
        if state is not True:
            return _kept_unless_gone(state)
        os.rmdir(folder)
    except OSError as error:
        # Each failure here means another has the folder now, and the folder is then
        # theirs to keep: a run that took the lock since holds the file open, which
        # Windows will not delete, and one that has made its run folder fails the
        # rmdir. A folder left holding only its lock is harmless, and the next accept
        # removes it with no table; the reason is reported, never raised.
        return str(error)
    return None


def _reads_as_decided(folder: Path, decided: StatLike) -> bool | None:
    """Whether *folder* still reads as *decided*: None when it is gone, as another removed it."""
    try:
        return same_folder(os.lstat(folder), decided)
    except FileNotFoundError:
        return None


def _kept_unless_gone(state: bool | None) -> str | None:
    """The kept reason for a re-read that failed: none for a folder gone, else `KEPT_ANOTHER`."""
    return None if state is None else KEPT_ANOTHER


def finish_leftover(album: Path, delete: Delete | None = None) -> str | None:
    """Delete the ``.lyricsmith.accepting`` an earlier, stopped accept left in *album*.

    Returns the `OSError`'s text when the delete fails, or None when it is gone or
    was never there. Raises `UnsafeAcceptingError` for a link or a non-folder there,
    which is never deleted through. *delete* None is `lyrics_delete.chosen_delete`'s.
    """
    delete = chosen_delete() if delete is None else delete
    leftover = album / ACCEPTING_NAME
    refuse_unsafe_accepting(leftover)
    if not os.path.lexists(leftover):
        return None
    try:
        delete(leftover)
    except OSError as error:
        return str(error)
    return None
