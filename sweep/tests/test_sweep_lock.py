"""One sweep per library at a time: the lock beside the ledger.

The lock is taken twice in this one process, through two descriptors, which the
operating system's lock keeps apart just as it keeps two processes apart.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import denied_fixtures as dn  # noqa: E402 - import follows the sys.path bootstrap above
import run_lock  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_lock  # noqa: E402 - import follows the sys.path bootstrap above


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

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not meaningful on NTFS")
    def test_the_lock_is_made_for_its_owner_alone(self) -> None:
        # Under a umask of 0 the modes asked for are the modes made, so a lock file made
        # at the shared default (0o644) would show here.
        previous = os.umask(0)
        self.addCleanup(os.umask, previous)
        with sweep_lock.held(self.path):
            pass
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    @unittest.skipUnless(dn.READ_ONLY_STOPS_A_FILE, dn.NO_READ_ONLY)
    def test_a_lock_that_cannot_be_made_is_named(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.parent.chmod(0o500)
        self.addCleanup(self.path.parent.chmod, 0o755)
        with self.assertRaises(sweep_lock.SweepLockError) as caught:
            with sweep_lock.held(self.path):
                self.fail("a lock was made in a read-only folder")
        self.assertIn(str(self.path), str(caught.exception))

    def test_a_lock_whose_folder_cannot_be_made_is_named(self) -> None:
        # Patched rather than played by a read-only folder, so it holds on every host.
        denied = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(sweep_lock, "make_state_folder", side_effect=denied):
            with self.assertRaises(sweep_lock.SweepLockError) as caught:
                with sweep_lock.held(self.path):
                    self.fail("a lock was taken whose folder could not be made")
        self.assertIn("cannot be made", str(caught.exception))
        self.assertIn(str(self.path), str(caught.exception))

    def test_a_lock_that_cannot_be_taken_for_another_reason_is_named(self) -> None:
        # A file system that keeps no locks answers with ENOLCK, which is not another
        # sweep holding it. The lock's own refusal is played at the lock, on either host.
        no_locks = OSError(errno.ENOLCK, "No locks available")
        with mock.patch.object(run_lock, "acquire", side_effect=no_locks):
            with self.assertRaises(sweep_lock.SweepLockError) as caught:
                with sweep_lock.held(self.path):
                    self.fail("a lock was taken that the system refused")
        self.assertIn("cannot be taken", str(caught.exception))
        self.assertIn(str(self.path), str(caught.exception))
        self.assertNotIn("another sweep", str(caught.exception))
