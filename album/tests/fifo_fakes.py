"""FIFO pipes for the tests, and the deadline that keeps a blocked read from hanging a suite.

Opening a FIFO for reading blocks until a writer comes, so a reader that forgets to open
without blocking would hang its suite for good. `within_deadline` runs the read on a
worker thread instead; a reader still blocked at the deadline is freed by opening the
pipe's other end, and the test fails rather than hangs. Pure standard library, so any of
lyricsmith's suites can load it.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

_T = TypeVar("_T")

#: How long a read may take before it counts as blocked: far past any real read.
DEADLINE_SECONDS = 10.0

# mkfifo(3) is POSIX-only. The split is an `if` so a win32 mypy pass still sees the
# name; the win32 arm exists only for that pass, and skips each test using it.
if sys.platform == "win32":

    def make_pipe(target: Path) -> None:
        """Skip: Windows has no named pipes in its file system."""
        raise unittest.SkipTest("Windows has no named pipes in its file system")

else:

    def make_pipe(target: Path) -> None:
        """A FIFO pipe under *target*'s name."""
        os.mkfifo(target)


def release(pipe: Path) -> None:
    """Open and close *pipe*'s writing end, so a reader blocked on it sees its end.

    With no reader waiting, a non-blocking open for writing fails (``ENXIO``): there is
    then nothing to free, so that error is no failure here.
    """
    with contextlib.suppress(OSError):
        os.close(os.open(pipe, os.O_WRONLY | getattr(os, "O_NONBLOCK", 0)))


def within_deadline(case: unittest.TestCase, pipe: Path, work: Callable[[], _T]) -> _T:
    """*work*'s result, run on a thread; fail *case* if it was still blocked at the deadline.

    A reader still blocked is freed through `release` first, so the thread ends and the
    suite goes on. An exception *work* raised is raised here.
    """
    outcome: list[_T] = []
    errors: list[BaseException] = []

    def attempt() -> None:
        """Run *work*, keeping its result or its exception for the test's thread."""
        try:
            outcome.append(work())
        except BaseException as error:  # noqa: BLE001 - handed back to the test's thread
            errors.append(error)

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(DEADLINE_SECONDS)
    blocked = worker.is_alive()
    release(pipe)
    worker.join(DEADLINE_SECONDS)
    case.assertFalse(blocked, f"blocked opening the pipe {pipe.name}")
    if errors:
        raise errors[0]
    return outcome[0]
