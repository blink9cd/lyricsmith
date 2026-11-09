"""Replacing one file, or one link, atomically and confirmed: the primitives every write uses.

`replace_atomically` writes through a temporary file beside the target, fsynced, then one
`os.replace` and a sync of the folder, so a reader sees the old file or the new one and
never half of either. `write_confirmed` adds a read-back, so a write that did not land as
written is an error rather than a success. `replace_with_link` does the same for a
symbolic link: made under a temporary name, then renamed over the target in one step, and
confirmed by reading the link back. A replace acts on the name itself: a link already
under the target's name is replaced, never followed, so nothing is ever written through a
link to somewhere else.

`read_regular` and `open_regular` are the reads: each looks at the name first and
refuses anything but a regular file unopened, then opens without blocking and refuses
it again by the opened descriptor itself, so a FIFO pipe swapped in after the name was
looked at is an error at once, never a hang. A read that never follows a link refuses
one on every host, Windows included, which has no ``O_NOFOLLOW`` (`read_regular`
states the one race left there). `read_link` reads a link's target as it was made,
without the extended-length prefix Windows' readlink adds.

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
from pathlib import Path, PureWindowsPath
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


class SwappedFileError(OSError):
    """The file opened is not the one looked at under its name a moment before."""


#: Read until the end of the file: `read_regular`'s default limit.
_WHOLE = -1
#: Never block opening: a FIFO pipe with no writer otherwise waits forever. POSIX-only,
#: so 0 on Windows, which has no FIFO in its file system.
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
#: Added to refuse a link at the last step; 0 where the platform lacks it (Windows),
#: where the look before the open and the identity check after it refuse one instead.
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)
#: What a read that never follows a link says of a link under its name, with errno
#: ``ELOOP`` as POSIX's own refusal has it: the same words on every host.
LINK_REFUSED = "a link here is never read through"
#: Windows' reparse-tag bit marking a name surrogate: a symbolic link, a junction or a
#: mount point, a name that leads elsewhere. A tag without it (a cloud placeholder, a
#: deduplicated file) is the file itself, read as one.
_NAME_SURROGATE_BIT = 0x20000000
#: Whether a folder can be opened to flush it: POSIX can. Windows' open refuses a
#: folder outright, so there the sync is skipped by name, not by a refused open.
FOLDERS_SYNC = os.name != "nt"
#: Whether a link's target comes back in Windows' extended-length form: on Windows,
#: `os.readlink` gives an absolute target as ``\\?\C:\...``, not as it was made.
LINKS_READ_EXTENDED = os.name == "nt"
#: That extended-length prefix, and its form before a network share.
_EXTENDED = "\\\\?\\"
_EXTENDED_UNC = "UNC\\"
#: What a share's path starts with once the prefix is dropped.
_SHARE = "\\\\"
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


def is_link_entry(status: os.stat_result) -> bool:
    """Whether *status*, a look at a name that did not follow it, is of a link.

    A symbolic link on any host, or on Windows any name surrogate: a junction or a
    mount point, which leads elsewhere as a link does. POSIX's look has no reparse tag.
    """
    tag = getattr(status, "st_reparse_tag", 0)
    return stat.S_ISLNK(status.st_mode) or bool(tag & _NAME_SURROGATE_BIT)


def _not_regular(path: Path) -> NotRegularFileError:
    """The refusal of *path* as something other than a regular file, by its last part."""
    return NotRegularFileError(f"{path.name} is not a regular file")


def _look(path: Path, *, follow: bool) -> os.stat_result:
    """*path*'s status, read before any open; raise unless it is a regular file.

    With *follow* the name's links are followed, as the open will follow them;
    without it a link under the name, a junction included, raises an `OSError` with
    errno ``ELOOP`` and `LINK_REFUSED`, the same on every host. Anything else that is
    no regular file (a folder, a FIFO pipe) raises `NotRegularFileError` without
    being opened, so a folder is that error on Windows too, which refuses to open
    one, and a FIFO pipe is never waited on.
    """
    status = os.stat(path) if follow else os.lstat(path)
    if not follow and is_link_entry(status):
        raise OSError(errno.ELOOP, LINK_REFUSED, str(path))
    if not stat.S_ISREG(status.st_mode):
        raise _not_regular(path)
    return status


def _refuse_unless_regular(
    descriptor: int,
    path: Path,
    *,
    block: bool = False,
    looked: os.stat_result | None = None,
) -> None:
    """Raise `NotRegularFileError` unless *descriptor* is a regular file; close it on error.

    Told by the opened descriptor's own `fstat`, so a file swapped since it was looked
    at cannot slip past; the error names *path*'s last part. With *looked*, the look
    `_look` took without following, the descriptor must be that very file (one device
    and file number), else `SwappedFileError`: a link swapped in between the look and
    an open that followed it (Windows has no ``O_NOFOLLOW``) leads to another file.
    With *block*, blocking is then turned back on. Any error raised here, a refusal
    or a failed reset alike, closes *descriptor* first, so one never leaks.
    """
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _not_regular(path)
        if looked is not None and not os.path.samestat(looked, opened):
            raise SwappedFileError(f"{path.name} changed while it was opened")
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

    The name is looked at first (`_look`): anything but a regular file raises
    `NotRegularFileError`, naming *path*'s last part, unopened. It is then opened
    without blocking (``O_NONBLOCK``), so a FIFO pipe swapped in since opens at once
    instead of waiting forever, and told again by the opened descriptor's own
    `fstat` before a byte is read. A link is followed, as a plain open follows it,
    and the caller checks where it leads.

    Without *follow*, a link under the name is never read through, on any host: the
    look refuses one (an `OSError`, errno ``ELOOP``, `LINK_REFUSED`); on POSIX the
    open adds ``O_NOFOLLOW``, so a link swapped in after the look is refused by the
    kernel itself; and the descriptor must be the very file the look saw, else
    `SwappedFileError`. That last check is Windows' whole guard after the look, since
    it has no ``O_NOFOLLOW``, and it leaves one race there: a link swapped in between
    the look and the open is followed by the open itself, so the open reaches what
    it leads to (a named pipe, a share) before the check refuses it; nothing is read
    from it. Binary mode, so the bytes are the file's own on every platform.
    """
    looked = _look(path, follow=follow)
    descriptor = os.open(path, read_flags() | (0 if follow else _NO_FOLLOW))
    # Told before `fdopen`, which itself refuses a folder with an error of its own.
    _refuse_unless_regular(descriptor, path, looked=None if follow else looked)
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read(limit)


def open_regular(path: Path, mode: OpenMode) -> BinaryIO:
    """The regular file at *path*, opened in *mode*, for a caller that streams it.

    For a reader that takes a file object rather than bytes, mutagen's among them.
    Looked at first, then opened without blocking and told by the opened descriptor's
    own `fstat`, as `read_regular` is, so a folder is refused unopened on every host
    and a FIFO pipe or a device swapped in since the name was looked at raises
    `NotRegularFileError` at once. Blocking is then turned back on,
    so the file behaves as a plain `open` would. A link is followed. No mode creates
    a file: a missing one raises `FileNotFoundError`, so a track removed in the
    middle of a write is never made again as a stub. A failure after the open closes
    the descriptor before it is raised.
    """

    def opener(name: str, flags: int) -> int:
        """Open *name* with the flags `open` chose, never blocking; no mode creates."""
        _look(path, follow=True)
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

    A file's own fsync covers its bytes, not the name pointing at them. On a host
    that cannot open a folder at all (`FOLDERS_SYNC` false: Windows, whose file
    system makes a rename durable without it), nothing is done. Where a folder cannot
    be opened for reading, or its file system cannot sync a folder, the replace
    still stands; the sync is skipped. Any other sync failure is raised.
    """
    if not FOLDERS_SYNC:
        return
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
    if read_link(target) != link:
        raise ReadBackError(f"{target.name} did not read back as the link recorded")


def plain_target(target: str) -> str:
    r"""*target*, a link's target as `os.readlink` gave it, in the form it was made.

    Where `LINKS_READ_EXTENDED` (Windows), an absolute target comes back with the
    extended-length prefix ``\\?\``: ``\\?\C:\a`` for ``C:\a``, ``\\?\UNC\srv\share``
    for ``\\srv\share``. Dropped from those two forms, so a link recorded and put
    back is the link the user made, not one with a different printed target; any
    other prefixed form (a volume's own name) names nothing shorter, and is kept. Off
    Windows the prefix is four ordinary characters of a name, and nothing changes.
    """
    if not LINKS_READ_EXTENDED or not target.startswith(_EXTENDED):
        return target
    rest = target[len(_EXTENDED) :]
    if rest.upper().startswith(_EXTENDED_UNC):
        return _SHARE + rest[len(_EXTENDED_UNC) :]
    drive = PureWindowsPath(rest).drive
    return rest if len(drive) == len("C:") else target


def read_link(path: Path) -> str:
    """The target of the link at *path*, as it was made: `plain_target` of its readlink."""
    return plain_target(os.readlink(path))
