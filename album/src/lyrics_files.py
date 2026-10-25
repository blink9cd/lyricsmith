"""Replacing one file, or one link, atomically and confirmed: the primitives every write uses.

`replace_atomically` writes through a temporary file beside the target, fsynced, then one
`os.replace` and a sync of the folder, so a reader sees the old file or the new one and
never half of either. `write_confirmed` adds a read-back, so a write that did not land as
written is an error rather than a success. `replace_with_link` does the same for a
symbolic link: made under a temporary name, then renamed over the target in one step, and
confirmed by reading the link back. A replace acts on the name itself: a link already
under the target's name is replaced, never followed, so nothing is ever written through a
link to somewhere else.

`inside_run_folder` is the matching guard for reads: a path a run folder's manifest
names, resolved, must still lie inside that run folder, so a link put there since (a
linked ``frames/`` or ``sidecars/`` folder, or a linked file) never makes undo or the
check read from somewhere else.

Every name made here fits in `NAME_MAX` bytes: a temporary name keeps only as much of
its target's name as fits beside its random tag, and `fit_name` shortens a name some
other module builds from a track's, keeping a short hash of the whole, so a track named
in nearly the whole limit can still be backed up and written.

`RECORD_ERRORS` is what a confirmed write raises when it cannot record something: an OS
error, or a write that does not read back. Nothing here prints or takes the album lock.
"""

from __future__ import annotations

import errno
import hashlib
import os
import uuid
from pathlib import Path

from lyrics_model import LyricsError, ReadBackError

#: The mode a new file is created with, before the process umask narrows it.
_NEW_FILE_MODE = 0o666

#: The longest file name, in bytes, APFS, ext4 and most other file systems take.
NAME_MAX = 255
#: How names are measured against `NAME_MAX`; an undecodable byte (``surrogateescape``)
#: counts as the one byte it stands for.
_ENCODING = "utf-8"
#: How many hex digits of a whole name's hash a shortened name keeps.
_HASH_DIGITS = 8
#: Joins a shortened name's head to its hash.
_HASH_MARK = "~"
#: A temporary file's random tag and its ending.
_TEMP_TAG_DIGITS = 32
_TEMP_SUFFIX = ".tmp"
#: The bytes a temporary name adds around its target's: ``.``, ``.<tag>`` and the suffix.
_TEMP_EXTRA = len(".") + len(".") + _TEMP_TAG_DIGITS + len(_TEMP_SUFFIX)

#: What a confirmed write raises when it cannot record: an OS error, or a write that
#: does not read back as written.
RECORD_ERRORS = (OSError, ReadBackError)


class OutsideRunFolderError(LyricsError):
    """A path in a run folder resolves outside it, through a link put there since."""


def inside_run_folder(run_dir: Path, relative: str) -> Path:
    """*run_dir* / *relative*, fully resolved; refuse it unless it lies inside *run_dir*.

    Every link on the way is followed, so one leading out of the run folder, a linked
    folder or file, raises `OutsideRunFolderError` naming *relative*, and so does a
    link loop at the last component, which leads nowhere to read. A loop in a folder
    on the way (``frames`` -> ``frames``) is not caught here: `realpath` stops at it
    with a name still inside the run folder, and the open that follows fails with
    ``ELOOP``, an `OSError` the caller's per-track boundary records as that track's
    error. A link that stays inside the run folder is read as it always was.
    `os.path.realpath`, never `Path.resolve`: on Python 3.12, the floor, `resolve`
    raises `RuntimeError` on a loop, where `realpath` stops at it, leaving a name
    that is still a link.
    """
    path = Path(os.path.realpath(run_dir / relative))
    if not path.is_relative_to(os.path.realpath(run_dir)):
        raise OutsideRunFolderError(f"{relative} leads outside the run folder")
    if path.is_symlink():
        raise OutsideRunFolderError(f"{relative} is a link loop")
    return path


def truncate_utf8(text: str, limit: int) -> str:
    """*text* cut to at most *limit* bytes of UTF-8, never inside a character."""
    data = text.encode(_ENCODING, "surrogateescape")[:limit]
    return data.decode(_ENCODING, "ignore")


def fit_name(stem: str, suffix: str, reserve: int = 0) -> str:
    """*stem* + *suffix*, with *stem* shortened so the name fits a file name.

    The name may take `NAME_MAX` less *reserve* bytes, the room a caller keeps for
    what it appends later. A stem that fits is kept whole, so the name stays readable;
    a longer one keeps as much of its head as fits, then ``~`` and a short hash of the
    whole stem, so two long stems sharing a head still name two files, and one stem
    always names the same one.
    """
    room = NAME_MAX - reserve - _size(suffix)
    if _size(stem) <= room:
        return stem + suffix
    whole = stem.encode(_ENCODING, "surrogateescape")
    digest = hashlib.sha256(whole).hexdigest()[:_HASH_DIGITS]
    head = truncate_utf8(stem, room - len(_HASH_MARK) - _HASH_DIGITS)
    return f"{head}{_HASH_MARK}{digest}{suffix}"


def _size(name: str) -> int:
    """*name*'s length in the bytes a file system counts against `NAME_MAX`."""
    return len(name.encode(_ENCODING, "surrogateescape"))


def _temporary_beside(target: Path) -> Path:
    """A hidden name beside *target* that no other write uses, within `NAME_MAX`.

    The random tag makes it unique, so the target's own name is cut to the room left
    beside the tag: a target named in nearly the whole limit still gets a temporary.
    """
    head = truncate_utf8(target.name, NAME_MAX - _TEMP_EXTRA)
    return target.with_name(f".{head}.{uuid.uuid4().hex}{_TEMP_SUFFIX}")


def replace_atomically(target: Path, data: bytes) -> None:
    """Write *data* to *target* through a fsynced temporary file and one replace.

    Missing parent folders are created. The temporary file is removed if anything
    fails before the replace, which leaves the old *target*, if any, as it was. The
    folder is synced after the replace, so the new name is on disk too.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = _temporary_beside(target)
    try:
        # O_BINARY exists only on Windows, where a descriptor opened without it is in
        # text mode and each LF written lands as CRLF: undo restores these bytes exactly.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(temp, flags, _NEW_FILE_MODE)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    _sync_folder(target.parent)


def _sync_folder(folder: Path) -> None:
    """Flush *folder*'s entries to disk, so a replace inside it survives a power cut.

    A file's own fsync covers its bytes, not the name pointing at them. Where a
    folder cannot be opened for reading, or its file system cannot sync a folder, the
    replace still stands; the sync is skipped. Any other sync failure is raised.
    """
    try:
        descriptor = os.open(folder, os.O_RDONLY)
    except OSError:
        # Opening a folder is refused on some platforms and by some permissions; the
        # replace has landed either way, and only its durability is left to the OS.
        return
    try:
        os.fsync(descriptor)
    except OSError as error:
        # Some FUSE and network file systems open a folder but answer its fsync with
        # EINVAL: they do not support it, which is the refused open above in another
        # form. Anything else (an I/O error) is a real failure and is raised.
        if error.errno != errno.EINVAL:
            raise
    finally:
        os.close(descriptor)


def write_confirmed(target: Path, data: bytes) -> None:
    """Replace *target* atomically with *data*; raise `ReadBackError` unless it reads back."""
    replace_atomically(target, data)
    if target.read_bytes() != data:
        raise ReadBackError(f"{target.name} did not read back as written")


def replace_with_link(target: Path, link: str) -> None:
    """Make *target* a symbolic link to *link*, atomically; raise unless it reads back.

    The link is made under a temporary name beside *target*, then renamed over it, so
    whatever *target* held (a file, another link) is replaced in one step and never
    followed. *link* is stored as given, relative or absolute, and need not exist.
    Raise `ReadBackError` when the link left under *target* does not point at *link*.
    """
    temp = _temporary_beside(target)
    try:
        os.symlink(link, temp)
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    _sync_folder(target.parent)
    if os.readlink(target) != link:
        raise ReadBackError(f"{target.name} did not read back as the link recorded")
