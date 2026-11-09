#!/usr/bin/env python3
"""Tests for run_lock: contention and probing in one process, then the kill boundary, live."""

from __future__ import annotations

import errno
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_lock

#: Upper bound on waiting for the live holder. A safety net for a loaded machine; the child
#: normally reports in well under a second.
_LIVE_CEILING = 30.0

#: The live holder: take the lock, report it through a marker file written atomically (so the
#: parent never reads it half-written), then wait to be killed long before the sleep ends.
_HOLDER = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import run_lock\n"
    "lock = run_lock.acquire(Path(sys.argv[2]))\n"
    "tmp = Path(sys.argv[3] + '.tmp')\n"
    "tmp.write_text('held' if lock is not None else 'refused', encoding='utf-8', newline='\\n')\n"
    "tmp.replace(sys.argv[3])\n"
    "time.sleep(600)\n"
)


class _TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lock_path = Path(tmp.name) / "state" / "run.lock"


class AcquireTests(_TempDirCase):
    def test_a_second_acquire_is_refused_until_the_first_releases(self) -> None:
        """flock belongs to the open file, so contention shows even inside one process."""
        first = run_lock.acquire(self.lock_path)
        assert first is not None
        self.assertIsNone(run_lock.acquire(self.lock_path))
        first.release()
        second = run_lock.acquire(self.lock_path)
        assert second is not None
        second.release()

    def test_the_with_block_releases_on_exit_even_when_it_raises(self) -> None:
        lock = run_lock.acquire(self.lock_path)
        assert lock is not None
        with self.assertRaises(RuntimeError), lock:
            raise RuntimeError("inside the held block")
        self.assertFalse(run_lock.is_held(self.lock_path))

    def test_release_twice_is_a_no_op(self) -> None:
        lock = run_lock.acquire(self.lock_path)
        assert lock is not None
        lock.release()
        lock.release()
        self.assertFalse(run_lock.is_held(self.lock_path))

    def test_the_lock_file_and_its_parent_are_created_and_kept(self) -> None:
        """Never deleted: unlinking races a concurrent opener into a second 'the' lock."""
        lock = run_lock.acquire(self.lock_path)
        assert lock is not None
        lock.release()
        self.assertTrue(self.lock_path.is_file())

    def test_the_lock_file_is_opened_with_the_mode_asked_for(self) -> None:
        """The mode reaches the open itself, the default and an asked one alike."""
        modes: list[int] = []
        real_open = os.open

        def recording_open(path: Path, flags: int, mode: int = 0o777) -> int:
            modes.append(mode)
            return real_open(path, flags, mode)

        with mock.patch.object(run_lock.os, "open", side_effect=recording_open):
            default = run_lock.acquire(self.lock_path)
            assert default is not None
            default.release()
            private = run_lock.acquire(self.lock_path, mode=0o600)
            assert private is not None
            private.release()
        self.assertEqual(modes, [0o644, 0o600])

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not meaningful on NTFS")
    def test_a_new_lock_file_takes_the_mode_asked_for(self) -> None:
        """A caller keeping private state makes its lock file private too."""
        old_umask = os.umask(0)
        self.addCleanup(os.umask, old_umask)
        lock = run_lock.acquire(self.lock_path, mode=0o600)
        assert lock is not None
        lock.release()
        self.assertEqual(self.lock_path.stat().st_mode & 0o777, 0o600)

    def test_an_error_other_than_contention_raises_and_closes_the_descriptor(
        self,
    ) -> None:
        closed: list[int] = []
        failure = OSError(errno.EIO, "injected")
        with mock.patch.object(run_lock, "_try_lock", side_effect=failure):
            with mock.patch.object(
                run_lock.os, "close", side_effect=_close_recorder(closed)
            ):
                with self.assertRaises(OSError) as caught:
                    run_lock.acquire(self.lock_path)
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(closed), 1, "a failed attempt must hold nothing open")

    def test_a_refused_acquire_closes_its_descriptor(self) -> None:
        """Contention is the common refusal: a launch that meets a running holder takes it."""
        held = run_lock.acquire(self.lock_path)
        assert held is not None
        self.addCleanup(held.release)
        closed: list[int] = []
        with mock.patch.object(
            run_lock.os, "close", side_effect=_close_recorder(closed)
        ):
            self.assertIsNone(run_lock.acquire(self.lock_path))
        self.assertEqual(len(closed), 1, "a refused attempt must hold nothing open")


def _load_as_windows(locking: Callable[[int, int, int], None]) -> ModuleType:
    """A fresh copy of run_lock built down its win32 branch, over a stand-in ``msvcrt``.

    The branch is chosen when the module body runs, so the body runs again here with
    ``sys.platform`` reading win32 and ``msvcrt`` answering through *locking*. That
    puts the Windows locking code under test on every host, and on Windows it is the
    only way to make ``msvcrt.locking`` fail with an error other than contention.
    """
    fake = ModuleType("msvcrt")
    # A bare ModuleType declares no attributes, so mypy refuses each assignment below.
    fake.LK_NBLCK = 2  # type: ignore[attr-defined]  # bare ModuleType; msvcrt's value
    fake.LK_UNLCK = 0  # type: ignore[attr-defined]  # bare ModuleType; msvcrt's value
    fake.locking = locking  # type: ignore[attr-defined]  # bare ModuleType
    spec = importlib.util.spec_from_file_location(
        "run_lock_as_windows", run_lock.__file__
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with (
        # The module itself joins too, for the import only: a dataclass in it would read
        # sys.modules[cls.__module__] while its body runs. patch.dict removes both after.
        mock.patch.dict(sys.modules, {"msvcrt": fake, spec.name: module}),
        mock.patch.object(sys, "platform", "win32"),
    ):
        spec.loader.exec_module(module)
    return module


class WindowsTryLockTests(_TempDirCase):
    """The win32 ``_try_lock``: contention is False, and any other error propagates."""

    def try_lock_failing_with(self, code: int) -> bool:
        """``_try_lock`` on a real descriptor while ``msvcrt.locking`` raises *code*."""
        failure = OSError(code, os.strerror(code))
        windows = _load_as_windows(mock.Mock(side_effect=failure))
        self.lock_path.parent.mkdir(parents=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, fd)
        result: bool = windows._try_lock(fd)
        return result

    def test_a_free_byte_is_locked_and_then_unlocked(self) -> None:
        """Both calls lock the first byte, so each first rewinds to it."""
        positions: list[int] = []

        def record_position(fd: int, _mode: int, _count: int) -> None:
            # msvcrt.locking acts at the current position, so that is what is recorded.
            positions.append(os.lseek(fd, 0, os.SEEK_CUR))

        locking = mock.Mock(side_effect=record_position)
        windows = _load_as_windows(locking)
        self.lock_path.parent.mkdir(parents=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, fd)
        os.write(fd, b"moved")
        self.assertTrue(windows._try_lock(fd))
        os.lseek(fd, 3, os.SEEK_SET)
        windows._unlock(fd)
        self.assertEqual(
            locking.call_args_list, [mock.call(fd, 2, 1), mock.call(fd, 0, 1)]
        )
        self.assertEqual(positions, [0, 0])

    def test_contention_reads_as_not_locked(self) -> None:
        self.assertFalse(self.try_lock_failing_with(errno.EACCES))

    def test_an_error_other_than_contention_propagates(self) -> None:
        """A lock the OS refused for another reason is unknown, never "held elsewhere"."""
        with self.assertRaises(OSError) as caught:
            self.try_lock_failing_with(errno.EBADF)
        self.assertEqual(caught.exception.errno, errno.EBADF)


class IsHeldTests(_TempDirCase):
    def test_an_absent_file_is_free_and_is_not_created(self) -> None:
        self.assertFalse(run_lock.is_held(self.lock_path))
        self.assertFalse(self.lock_path.exists())

    def test_a_held_lock_reads_held_and_the_probe_does_not_steal_it(self) -> None:
        lock = run_lock.acquire(self.lock_path)
        assert lock is not None
        self.addCleanup(lock.release)
        self.assertTrue(run_lock.is_held(self.lock_path))
        self.assertIsNone(run_lock.acquire(self.lock_path))

    def test_a_released_lock_reads_free_and_the_probe_leaves_it_free(self) -> None:
        lock = run_lock.acquire(self.lock_path)
        assert lock is not None
        lock.release()
        self.assertFalse(run_lock.is_held(self.lock_path))
        again = run_lock.acquire(self.lock_path)
        assert again is not None, "the probe must release what it took"
        again.release()


class LiveKillBoundaryTests(_TempDirCase):
    """The property the library exists for: the kernel frees a killed holder's lock."""

    def test_a_killed_holder_leaves_the_lock_free(self) -> None:
        self.lock_path.parent.mkdir(parents=True)
        marker = self.lock_path.parent / "holder.ready"
        here = str(Path(__file__).resolve().parent)
        holder = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, here, str(self.lock_path), str(marker)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_reap, holder)
        _wait_for(marker, holder)
        self.assertEqual(marker.read_text(encoding="utf-8"), "held")
        self.assertTrue(run_lock.is_held(self.lock_path))
        self.assertIsNone(run_lock.acquire(self.lock_path))
        # SIGKILL on POSIX, TerminateProcess on Windows: the holder gets no chance to clean up.
        holder.kill()
        holder.wait(timeout=_LIVE_CEILING)
        if sys.platform == "win32":
            # Two lags POSIX does not have, so here the lock is polled for rather than read at
            # once. A venv's python.exe is a launcher running the interpreter as its child, so
            # the kill reaches the real holder only through the launcher's job; and Windows
            # frees a dead process's byte-range locks after it exits, not as it exits.
            _wait_until_free(self.lock_path)
        self.assertFalse(run_lock.is_held(self.lock_path))
        lock = run_lock.acquire(self.lock_path)
        assert lock is not None
        lock.release()


def _close_recorder(closed: list[int]) -> Callable[[int], None]:
    """An ``os.close`` stand-in that records each descriptor into *closed*, then closes it.

    Call it before the patch takes effect, so the real ``os.close`` is the one captured.
    """
    real_close = run_lock.os.close

    def record_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    return record_close


def _wait_for(marker: Path, holder: subprocess.Popen[bytes]) -> None:
    """Poll for the holder's marker; fail loudly if it dies first or never reports."""
    deadline = time.monotonic() + _LIVE_CEILING
    while not marker.exists():
        if holder.poll() is not None:
            raise AssertionError(f"the lock holder exited early ({holder.returncode})")
        if time.monotonic() > deadline:
            raise AssertionError("the lock holder never reported holding the lock")
        time.sleep(0.05)


def _wait_until_free(lock_path: Path) -> None:
    """Poll until no holder has *lock_path*; fail loudly if it stays held past the ceiling."""
    deadline = time.monotonic() + _LIVE_CEILING
    while run_lock.is_held(lock_path):
        if time.monotonic() > deadline:
            raise AssertionError("the killed holder's lock was never freed")
        time.sleep(0.05)


def _reap(holder: subprocess.Popen[bytes]) -> None:
    """Kill and wait for the holder if a failed assertion left it running."""
    if holder.poll() is None:
        holder.kill()
    holder.wait(timeout=_LIVE_CEILING)


if __name__ == "__main__":
    unittest.main()
