"""One sweep per library at a time: a lock held beside the ledger for the whole run.

Two sweeps on one library would each read the ledger, then each rewrite it whole, so
one's entries would replace the other's. So a run takes the operating system's
non-blocking lock (`run_lock`: ``flock`` on POSIX, ``msvcrt.locking`` on Windows) on
``<ledger name>.lock`` beside the ledger, and a second run on the same library is
refused at once rather than made to wait (`held`). A lock whose folder cannot be made, a
lock file that cannot be opened, or a lock the system refuses for any other reason is
refused the same way, naming the file. The operating system lets go of the lock when its
holder ends, however it ends, so a crashed sweep leaves no stale lock; the lock file
itself stays, empty, and is never removed, since removing it while another sweep opens it
would let two sweeps lock two different files. Nothing here prints.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import run_lock
from lyrics_state import make_state_folder

#: The lock file's suffix, in place of the ledger's own.
_LOCK_SUFFIX = ".lock"
#: The lock file's mode when it is made: the sweep's own state, read by no one else.
_LOCK_MODE = 0o600


class SweepLockError(Exception):
    """The lock could not be taken: another sweep holds it, or it cannot be made."""


def lock_path(ledger: Path) -> Path:
    """The lock file beside the ledger at *ledger*."""
    return ledger.with_suffix(_LOCK_SUFFIX)


@contextmanager
def held(path: Path) -> Iterator[None]:
    """Hold the lock at *path* for the ``with`` body; raise `SweepLockError` if not.

    Never waits: a lock another sweep holds is refused at once. The lock file is made
    0o600 in a folder made 0o700, as the ledger's write makes it
    (`lyrics_state.make_state_folder`).
    """
    try:
        make_state_folder(path.parent)
    except OSError as error:
        raise SweepLockError(f"the lock cannot be made: {path}: {error}") from error
    try:
        lock = run_lock.acquire(path, mode=_LOCK_MODE)
    # Any refusal but another holder's (a lock file that cannot be opened, a file system
    # that keeps no locks) is no other sweep's doing: named as the lock's own failure,
    # so the run still ends with exit 2 rather than a traceback.
    except OSError as error:
        raise SweepLockError(f"the lock cannot be taken: {path}: {error}") from error
    if lock is None:
        raise SweepLockError(
            f"another sweep is running on this library; it holds {path}"
        )
    # Leaving the block lets go of the lock, however the body ends.
    with lock:
        yield
