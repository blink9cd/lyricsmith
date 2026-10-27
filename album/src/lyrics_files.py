"""Replacing one file, or one link, atomically and confirmed: the primitives every write uses.

`replace_atomically` writes through a temporary file beside the target, fsynced, then one
`os.replace` and a sync of the folder, so a reader sees the old file or the new one and
never half of either. `write_confirmed` adds a read-back, so a write that did not land as
written is an error rather than a success. `replace_with_link` does the same for a
symbolic link: made under a temporary name, then renamed over the target in one step, and
confirmed by reading the link back. A replace acts on the name itself: a link already
under the target's name is replaced, never followed, so nothing is ever written through a
link to somewhere else.

`read_regular` and `open_regular` are the reads: each opens without blocking and
refuses anything but a regular file by the opened descriptor itself, so a FIFO pipe
swapped in after a name was looked at is an error at once, never a hang.

`inside_run_folder` is the matching guard for where a read goes: a path a run folder's
manifest names, resolved, must still lie inside that run folder, so a link put there
since (a linked ``frames/`` or ``sidecars/`` folder, or a linked file) never makes undo
or the check read from somewhere else.

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
import stat
import sys
import uuid
from pathlib import Path
from typing import BinaryIO, Literal, cast

from lyrics_model import LyricsError, ReadBackError

#: The mode a new file is created with, before the process umask narrows it, unless
#: a write asks for another: a state file asks to be readable by its writer alone.
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


class NotRegularFileError(OSError):
    """A file to read is something else: a folder, a FIFO pipe, a device."""


#: Read until the end of the file: `read_regular`'s default limit.
_WHOLE = -1
#: Never block opening: a FIFO pipe with no writer otherwise waits forever. POSIX-only,
#: so 0 on Windows, which has no FIFO in its file system.
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
#: Added to refuse a link at the last step; 0 where the platform lacks it.
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)
#: The modes `open_regular` takes: reading, and updating in place.
OpenMode = Literal["rb", "r+b"]


def read_flags() -> int:
    """The flags `read_regular` opens a file with: read-only, never blocking, binary.

    O_CLOEXEC is POSIX-only and O_BINARY Windows-only; a flag a platform lacks is 0
    there, leaving the open as it was. Read at each open, never once at import, so a
    test can stand in Windows' O_BINARY on any host. transcribe opens a ``.lrc`` with
    flags of its own, pinned to these by a test.
    """
    return (
        os.O_RDONLY
        | _NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )


def _refuse_unless_regular(descriptor: int, path: Path, *, block: bool = False) -> None:
    """Raise `NotRegularFileError` unless *descriptor* is a regular file; close it on error.

    Told by the opened descriptor's own `fstat`, so a file swapped since it was looked
    at cannot slip past; the error names *path*'s last part. With *block*, blocking is
    then turned back on. Any error raised here, the refusal or a failed reset alike,
    closes *descriptor* first, so a refused open never leaks one.
    """
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise NotRegularFileError(f"{path.name} is not a regular file")
        if block:
            _turn_blocking_on(descriptor)
    except BaseException:
        os.close(descriptor)
        raise


def _turn_blocking_on(descriptor: int) -> None:
    """Make *descriptor*, opened without blocking, block as a plain open's would."""
    # Windows has no O_NONBLOCK, so it has nothing to turn back off.
    if sys.platform == "win32":
        return  # pragma: only-on win32
    os.set_blocking(descriptor, True)  # pragma: only-on posix


def read_regular(path: Path, limit: int = _WHOLE, *, follow: bool = True) -> bytes:
    """The first *limit* bytes of the regular file at *path*, all of it by default.

    Opened without blocking (``O_NONBLOCK``), so a FIFO pipe with no writer opens at
    once instead of waiting forever, then told by the opened descriptor's own `fstat`:
    anything but a regular file raises `NotRegularFileError`, naming *path*'s last
    part, before a byte is read. A link is followed, as a plain open follows it, and
    the caller checks where it leads; without *follow*, a link at the last step is
    refused by the open itself (``O_NOFOLLOW``, an `OSError`), so a file swapped for
    a link since it was looked at is never read through. Binary mode, so the bytes
    are the file's own on every platform.
    """
    descriptor = os.open(path, read_flags() | (0 if follow else _NO_FOLLOW))
    # Told before `fdopen`, which itself refuses a folder with an error of its own.
    _refuse_unless_regular(descriptor, path)
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read(limit)


def open_regular(path: Path, mode: OpenMode) -> BinaryIO:
    """The regular file at *path*, opened in *mode*, for a caller that streams it.

    For a reader that takes a file object rather than bytes, mutagen's among them.
    Opened without blocking and told by the opened descriptor's own `fstat`, as
    `read_regular` is, so a FIFO pipe or a device swapped in since the name was
    looked at raises `NotRegularFileError` at once. Blocking is then turned back on,
    so the file behaves as a plain `open` would. A link is followed. No mode creates
    a file: a missing one raises `FileNotFoundError`, so a track removed in the
    middle of a write is never made again as a stub. A failure after the open closes
    the descriptor before it is raised.
    """

    def opener(name: str, flags: int) -> int:
        """Open *name* with the flags `open` chose, never blocking; no mode creates."""
        descriptor = os.open(name, flags | _NONBLOCK)
        _refuse_unless_regular(descriptor, path, block=True)
        return descriptor

    return cast(BinaryIO, open(path, mode, opener=opener))


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


def replace_atomically(
    target: Path, data: bytes, *, mode: int = _NEW_FILE_MODE
) -> None:
    """Write *data* to *target* through a fsynced temporary file and one replace.

    The new file is made with *mode*, narrowed by the umask, and replaces whatever
    mode the old one had. Missing parent folders are created. The temporary file is
    removed if anything fails before the replace, which leaves the old *target*, if
    any, as it was. The folder is synced after the replace, so the new name is on
    disk too.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = _temporary_beside(target)
    try:
        # O_BINARY exists only on Windows, where a descriptor opened without it is in
        # text mode and each LF written lands as CRLF: undo restores these bytes exactly.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(temp, flags, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    sync_folder(target.parent)


def sync_folder(folder: Path) -> None:
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


def write_confirmed(target: Path, data: bytes, *, mode: int = _NEW_FILE_MODE) -> None:
    """Replace *target* atomically with *data*; raise `ReadBackError` unless it reads back.

    *mode* is the new file's, as `replace_atomically` takes it. The read-back never
    follows a link: the replace left a regular file under the name, so a link found
    there since is refused (an `OSError`), never read through.
    """
    replace_atomically(target, data, mode=mode)
    if read_regular(target, follow=False) != data:
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
    sync_folder(target.parent)
    if os.readlink(target) != link:
        raise ReadBackError(f"{target.name} did not read back as the link recorded")
