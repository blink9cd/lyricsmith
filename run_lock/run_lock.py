"""Hold an exclusive OS lock on a file for a process's lifetime, and probe whether one is held.

Why this exists
---------------
A scheduled job that must never run twice, and a status reader that must tell a RUNNING job
from a DEAD one, need the same primitive: a lock the kernel releases when its holder dies,
SIGKILL included. With it, "the record says in progress and the lock is free" means the holder
died, with no process-ID guesswork. Process IDs recycle fast enough on a busy machine that a
recorded one proves nothing an hour later.

``flock(2)`` on POSIX and ``msvcrt.locking`` on Windows both have that property. Each lock
belongs to the OPEN FILE rather than the process, so a second :func:`acquire` of the same path
is refused even inside the process that holds the first.

Windows frees a dead holder's lock after the process exits rather than as it exits, so there a
probe made just after a kill can still read held for a moment. A free lock still proves its
holder is gone; only a held one can briefly outlive it.

The lock file is never deleted. Unlinking it races a concurrent opener: the second process can
lock a file the first has just removed, and two holders then each own "the" lock.

Entry point assumptions
-----------------------
Import-only: no CLI and no output. Standard library only. Paths come from the caller.
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from types import TracebackType

# An ``if``, not a module-level assert: python_gate type-checks this file as win32, linux and
# darwin, and an assert would hide every name below it from the other platforms' passes.
if sys.platform == "win32":
    import msvcrt  # pragma: only-on win32

    #: A probe needs only read access; LockFile accepts a read-only handle.
    _OPEN_PROBE = os.O_RDONLY  # pragma: only-on win32

    def _try_lock(fd: int) -> bool:  # pragma: only-on win32
        """Lock the file's first byte without waiting; False when another handle holds it.

        ``msvcrt.locking`` locks from the CURRENT position, so the position is reset first.
        Locking past the end of an empty file is allowed, and contention raises EACCES.
        """
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno == errno.EACCES:
                return False
            raise
        return True

    def _unlock(fd: int) -> None:  # pragma: only-on win32
        """Release the byte :func:`_try_lock` locked."""
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:  # pragma: only-on posix
    import fcntl

    #: Read-only, and never blocking in open() on a FIFO planted where the lock file should be.
    _OPEN_PROBE = os.O_RDONLY | os.O_NONBLOCK

    def _try_lock(fd: int) -> bool:
        """Take ``flock`` without waiting; False when another open file holds it."""
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fd: int) -> None:
        """Release the ``flock`` :func:`_try_lock` took."""
        fcntl.flock(fd, fcntl.LOCK_UN)


class Lock:
    """A held lock. Leave the ``with`` block, or call :meth:`release`, to drop it."""

    def __init__(self, fd: int) -> None:
        """Wrap *fd*, an open descriptor this process has already locked."""
        self._fd: "int | None" = fd

    def release(self) -> None:
        """Drop the lock if still held. The descriptor is closed even when unlocking raises."""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> Lock:
        """Return the lock itself, already held."""
        return self

    def __exit__(
        self,
        exc_type: "type[BaseException] | None",
        exc: "BaseException | None",
        traceback: "TracebackType | None",
    ) -> None:
        """Release on the way out, whether or not the block raised."""
        self.release()


def acquire(path: Path) -> "Lock | None":
    """Take the lock on *path* without waiting; None when another holder has it.

    Creates *path* and its parent directories when absent. Any failure other than contention
    (an unwritable directory, a path that is a directory) raises OSError after the descriptor
    is closed, so a failed attempt holds nothing open.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        held = _try_lock(fd)
    except OSError:
        os.close(fd)
        raise
    if not held:
        os.close(fd)
        return None
    return Lock(fd)


def is_held(path: Path) -> bool:
    """Whether another holder has the lock on *path*, asked without waiting or creating it.

    An absent file is free, because every holder creates it. The probe takes the lock and lets
    it go at once, so it can briefly contend with a real :func:`acquire`, which then returns
    None: the same answer as losing to any other holder. An existing file that cannot be opened
    raises OSError, since whether it is held is then unknown.
    """
    try:
        fd = os.open(path, _OPEN_PROBE)
    except FileNotFoundError:
        return False
    try:
        if not _try_lock(fd):
            return True
        _unlock(fd)
        return False
    finally:
        os.close(fd)
