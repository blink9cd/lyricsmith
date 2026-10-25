"""One sweep per library at a time: the lock beside the ledger.

The lock is taken twice in this one process, through two descriptors, which an advisory
``flock`` keeps apart just as it keeps two processes apart.
"""

from __future__ import annotations

import errno
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_lock  # noqa: E402 - import follows the sys.path bootstrap above

# os.geteuid exists only off Windows, so it is looked up; root can write into a
# read-only folder, so the unwritable-folder test skips under it.
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0


class LockTest(sf.IsolatedCase):
    """The lock is held for the whole run, refused to a second taker, then let go."""

    def setUp(self) -> None:
        """Name a lock beside a ledger that does not exist yet."""
        super().setUp()
        ledger = self.state / "lyricsmith" / "sweep" / "ledger-0123.jsonl"
        self.path = sweep_lock.lock_path(ledger)

    def test_the_lock_lives_beside_the_ledger(self) -> None:
        self.assertEqual(self.path.parent, self.state / "lyricsmith" / "sweep")
        self.assertEqual(self.path.name, "ledger-0123.lock")

    def test_a_second_taker_is_refused_while_the_first_holds_it(self) -> None:
        with sweep_lock.held(self.path):
            with self.assertRaises(sweep_lock.SweepLockError) as caught:
                with sweep_lock.held(self.path):
                    self.fail("the lock was taken twice")
        self.assertIn("another sweep", str(caught.exception))
        self.assertIn(str(self.path), str(caught.exception))

    def test_the_lock_is_let_go_at_the_end(self) -> None:
        with sweep_lock.held(self.path):
            pass
        with sweep_lock.held(self.path):
            self.assertTrue(self.path.is_file())

    def test_the_lock_is_let_go_when_the_run_raises(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            with sweep_lock.held(self.path):
                raise KeyboardInterrupt
        with sweep_lock.held(self.path):
            pass

    @unittest.skipIf(_IS_ROOT, "root can write a read-only folder")
    def test_a_lock_that_cannot_be_made_is_named(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.parent.chmod(0o500)
        self.addCleanup(self.path.parent.chmod, 0o755)
        with self.assertRaises(sweep_lock.SweepLockError) as caught:
            with sweep_lock.held(self.path):
                self.fail("a lock was made in a read-only folder")
        self.assertIn(str(self.path), str(caught.exception))

    def test_a_lock_that_cannot_be_taken_for_another_reason_is_named(self) -> None:
        # A file system that keeps no locks answers flock with ENOLCK, which is not
        # another sweep holding it.
        no_locks = OSError(errno.ENOLCK, "No locks available")
        with mock.patch("fcntl.flock", side_effect=no_locks):
            with self.assertRaises(sweep_lock.SweepLockError) as caught:
                with sweep_lock.held(self.path):
                    self.fail("a lock was taken that flock refused")
        self.assertIn(str(self.path), str(caught.exception))
        self.assertNotIn("another sweep", str(caught.exception))
