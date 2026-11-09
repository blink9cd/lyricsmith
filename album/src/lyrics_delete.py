"""The delete prune and accept remove a folder with, which never leaves that folder.

`chosen_delete` picks it, on every host. Where ``shutil.rmtree.avoids_symlink_attacks``
is true it is `shutil.rmtree`, whose walk holds each folder open by descriptor and so
cannot be led out of it. Where that flag is false (every Windows Python, and a POSIX
one without the descriptor calls) it is `delete_tree`, this module's own walk.

**What it guards against.** The folder deleted is a run folder inside the album, and an
album is a folder anyone may have put anything in: a symbolic link, a Windows
junction, or another reparse point, to a folder or a file outside. The delete must take
such an entry away as the link it is and never touch what it points to. It must also
not be led out by a folder swapped for a link while the walk is in it.

**The walk.** `delete_tree` reads every entry with `os.lstat`, which never follows the
entry itself. An entry is a link when its mode says so or, on Windows, when its
``st_file_attributes`` carry ``FILE_ATTRIBUTE_REPARSE_POINT``, which marks junctions
and every other reparse point too. A link is removed as itself: with `os.rmdir` when
it is a folder link or a junction, which removes the link and never its target, and
with `os.unlink` otherwise. A folder that is no link is entered, its entries read to a
list first, and deleted last, once empty, from an explicit stack, so no depth of
folders can exhaust the interpreter's stack. The folder given is refused, with
`TreeChangedError`, when it is itself a link or no folder, as `shutil.rmtree` refuses
one.

**The identity check.** Each folder's `os.lstat` is kept from the moment the walk
decided to enter it. Before the folder is listed, after it is listed, before each entry
in it is removed, and before the folder's own `os.rmdir`, a fresh `os.lstat` of the
folder's path must still be a folder, no link, with the same device and inode (on
Windows, the volume serial and file ID). The path is looked up from the top each time,
so a swap anywhere above the folder changes what it names and is caught too. A
mismatch raises `TreeChangedError` and deletes nothing more.

**The race that remains.** Each check is a separate call from the removal it guards.
A folder swapped for a junction or a link after a check and before the call that
follows it is not caught: that one call (an unlink, an rmdir or a listing) then acts
on the same name inside the swapped-in target. Only removal relative to a held handle
would close that window, and Windows Python has no descriptor-relative unlink. Mounting
the swap needs write access to the album and a race lost on purpose, within one call's
window; the check narrows it, and does not close it. A file system that reports no
inode (an inode of 0) narrows it less, since two folders there read alike.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Final, Protocol


class StatLike(Protocol):
    """The parts of an `os.lstat` result the walk reads."""

    @property
    def st_mode(self) -> int:
        """The file type and permission bits."""

    @property
    def st_dev(self) -> int:
        """The device (on Windows, the volume serial) the entry is on."""

    @property
    def st_ino(self) -> int:
        """The entry's inode (on Windows, its file ID)."""


#: How the walk reads one entry without following it; a test passes a stand-in.
Lstat = Callable[[Path], StatLike]
#: How a folder is deleted, as prune and accept call it.
DeleteFolder = Callable[[Path], None]

#: The Windows attribute bits a link is known by, read with `getattr` so any host's
#: result serves: POSIX results carry no ``st_file_attributes`` at all.
_REPARSE: Final = stat.FILE_ATTRIBUTE_REPARSE_POINT
_FOLDER_BIT: Final = stat.FILE_ATTRIBUTE_DIRECTORY
#: What a refused folder's error says.
_CHANGED: Final = "is a link, not a folder, or another folder than the one entered"


class TreeChangedError(OSError):
    """A folder the walk was given or entered is a link, no folder, or another since."""


def chosen_delete(avoids_symlink_attacks: bool | None = None) -> DeleteFolder:
    """`shutil.rmtree` where it resists links, else `delete_tree`.

    *avoids_symlink_attacks* None reads this Python's own flag, and reads
    `shutil.rmtree` now rather than at import, so a test that patches either one
    reaches what it patched.
    """
    rmtree = shutil.rmtree
    if avoids_symlink_attacks is None:
        avoids_symlink_attacks = rmtree.avoids_symlink_attacks
    return rmtree if avoids_symlink_attacks else delete_tree


def is_link(found: StatLike) -> bool:
    """Whether *found* is a symbolic link, a junction, or any other reparse point."""
    attributes = int(getattr(found, "st_file_attributes", 0))
    return stat.S_ISLNK(found.st_mode) or bool(attributes & _REPARSE)


def is_folder(found: StatLike) -> bool:
    """Whether *found* is a folder the walk may enter: a folder, and no link."""
    return stat.S_ISDIR(found.st_mode) and not is_link(found)


def same_folder(now: StatLike, decided: StatLike) -> bool:
    """Whether *now* is still the folder *decided* was: no link, the same identity."""
    return is_folder(now) and (now.st_dev, now.st_ino) == (
        decided.st_dev,
        decided.st_ino,
    )


def delete_tree(top: Path, *, lstat: Lstat = os.lstat) -> None:
    """Delete the folder *top* and everything in it, never through a link.

    Raises `TreeChangedError` when *top* is a link or no folder, or when a folder the
    walk entered reads as another before a step inside it; the `OSError` a removal
    raises, as a busy or read-only entry's would, stops it too. Whatever is not yet
    deleted then stays.
    """
    first = lstat(top)
    if not is_folder(first):
        raise TreeChangedError(errno.ENOTDIR, _CHANGED, str(top))
    pending: list[tuple[Path, StatLike, bool]] = [(top, first, False)]
    while pending:
        folder, decided, emptied = pending.pop()
        if emptied:
            _confirm(folder, decided, lstat)
            os.rmdir(folder)
            continue
        # Pushed back first, so it is popped, and removed, after every folder in it.
        pending.append((folder, decided, True))
        pending.extend(_clear(folder, decided, lstat))


def _clear(
    folder: Path, decided: StatLike, lstat: Lstat
) -> list[tuple[Path, StatLike, bool]]:
    """Remove every entry of *folder* that is no folder; return its folders to enter.

    The entries are listed whole before any is removed, so the listing never reads a
    folder that is changing under it.
    """
    _confirm(folder, decided, lstat)
    with os.scandir(folder) as entries:
        names = [entry.name for entry in entries]
    _confirm(folder, decided, lstat)
    inner: list[tuple[Path, StatLike, bool]] = []
    for name in names:
        entry = folder / name
        found = lstat(entry)
        if is_folder(found):
            inner.append((entry, found, False))
            continue
        _confirm(folder, decided, lstat)
        _remove_entry(entry, found)
    return inner


def _remove_entry(entry: Path, found: StatLike) -> None:
    """Remove *entry*, a file or a link, as itself and never what it points to.

    A folder link or a junction carries the folder bit on Windows, and `os.rmdir`
    removes it without touching its target; a POSIX link to a folder carries none, and
    `os.unlink` removes it.
    """
    attributes = int(getattr(found, "st_file_attributes", 0))
    if stat.S_ISDIR(found.st_mode) or attributes & _FOLDER_BIT:
        os.rmdir(entry)
    else:
        os.unlink(entry)


def _confirm(folder: Path, decided: StatLike, lstat: Lstat) -> None:
    """Raise `TreeChangedError` unless *folder*'s path still names the folder decided on."""
    if not same_folder(lstat(folder), decided):
        raise TreeChangedError(errno.ENOTDIR, _CHANGED, str(folder))
