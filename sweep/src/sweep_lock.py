"""One sweep per library at a time: a lock held beside the ledger for the whole run.

Two sweeps on one library would each read the ledger, then each rewrite it whole, so
one's entries would replace the other's. So a run takes an advisory, non-blocking
``flock`` on ``<ledger name>.lock`` beside the ledger, and a second run on the same
library is refused at once rather than made to wait (`held`). A lock file that cannot
be opened, or a lock ``flock`` refuses for any other reason, is refused the same way,
naming the file. The operating system lets go of the lock when its holder ends, however
it ends, so a crashed sweep leaves no stale lock; the lock file itself stays, empty, and
is never removed, since removing it while another sweep opens it would let two sweeps
lock two different files.

POSIX only, like a run itself: ``fcntl`` is imported when a lock is taken, so the
survey still imports and runs where it is missing. Nothing here prints.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: The lock file's suffix, in place of the ledger's own.
_LOCK_SUFFIX = ".lock"
#: The lock file's mode when it is made: the sweep's own state, read by no one else.
_LOCK_MODE = 0o600


class SweepLockError(Exception):
    """The lock could not be taken: another sweep holds it, or it cannot be made."""


def lock_path(ledger: Path) -> Path:
    """The lock file beside the ledger at *ledger*."""
    return ledger.with_suffix(_LOCK_SUFFIX)


def _open(path: Path) -> int:
    """A descriptor on the lock file at *path*, made with its folder when missing."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(path, os.O_RDWR | os.O_CREAT, _LOCK_MODE)
    except OSError as error:
        raise SweepLockError(f"the lock cannot be made: {path}: {error}") from error


@contextmanager
def held(path: Path) -> Iterator[None]:
    """Hold the lock at *path* for the ``with`` body; raise `SweepLockError` if not.

    Never waits: a lock another sweep holds is refused at once.
    """
    if sys.platform == "win32":  # pragma: no cover - the gate runs on POSIX only
        raise SweepLockError("a sweep's lock needs a POSIX system")
    import fcntl  # POSIX only: imported here so the survey runs without it

    descriptor = _open(path)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SweepLockError(
                f"another sweep is running on this library; it holds {path}"
            ) from error
        # Any other refusal (a file system that keeps no locks, say) is no other
        # sweep's doing: named as the lock's own failure, so the run still ends with
        # exit 2 rather than a traceback.
        except OSError as error:
            raise SweepLockError(
                f"the lock cannot be taken: {path}: {error}"
            ) from error
        yield
    finally:
        # Closing the descriptor lets go of the lock.
        os.close(descriptor)
