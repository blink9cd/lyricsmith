"""lyrics_files: a file or a link replaced atomically, and a write confirmed by reading back.

A write lands whole or not at all: a failure before the replace leaves the old file and
no temporary beside it, and the folder is synced after the replace so the new name is on
disk too. A link is replaced the same way, by a temporary link renamed over the target,
so whatever the target held is never followed. Every test works in its own temporary
directory.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above


class AtomicWriteTest(fixtures.IsolatedCase):
    """A write lands whole or not at all, and is read back before it counts."""

    def test_a_failed_replace_leaves_the_old_file_and_no_temporary(self) -> None:
        target = self.tmp / "out.json"
        target.write_bytes(b"old")
        with (
            mock.patch.object(os, "replace", side_effect=OSError("disk gone")),
            self.assertRaises(OSError),
        ):
            lyrics_files.replace_atomically(target, b"new")
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(
            [p.name for p in self.tmp.iterdir() if p.is_file()], ["out.json"]
        )

    def test_the_folder_is_synced_after_the_replace(self) -> None:
        target = self.tmp / "out.json"
        synced: list[bool] = []
        real = os.fsync

        def record(descriptor: int) -> None:
            synced.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
            real(descriptor)

        with mock.patch.object(os, "fsync", record):
            lyrics_files.replace_atomically(target, b"new")
        self.assertEqual(synced, [False, True])

    def test_a_folder_that_cannot_be_opened_is_not_synced(self) -> None:
        target = self.tmp / "out.json"
        real = os.open

        def refuse_folders(path: object, flags: int, *args: int) -> int:
            if Path(cast(str, path)) == self.tmp:
                raise PermissionError("a folder cannot be opened here")
            return real(cast(str, path), flags, *args)

        with mock.patch.object(os, "open", refuse_folders):
            lyrics_files.replace_atomically(target, b"new")
        self.assertEqual(target.read_bytes(), b"new")

    def fail_folder_sync(self, number: int) -> None:
        """Make every folder fsync in this test raise an OS error numbered *number*."""
        real = os.fsync

        def refuse_folders(descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError(number, os.strerror(number))
            real(descriptor)

        self.enterContext(mock.patch.object(os, "fsync", refuse_folders))

    def test_a_folder_that_cannot_be_synced_keeps_the_replace(self) -> None:
        target = self.tmp / "out.json"
        self.fail_folder_sync(errno.EINVAL)
        lyrics_files.replace_atomically(target, b"new")
        self.assertEqual(target.read_bytes(), b"new")

    def test_any_other_folder_sync_failure_is_an_error(self) -> None:
        self.fail_folder_sync(errno.EIO)
        with self.assertRaises(OSError):
            lyrics_files.replace_atomically(self.tmp / "out.json", b"new")

    def test_missing_parent_folders_are_created(self) -> None:
        target = self.tmp / "a" / "b" / "c.txt"
        lyrics_files.write_confirmed(target, b"line\r\n")
        self.assertEqual(target.read_bytes(), b"line\r\n")

    def test_a_write_that_does_not_read_back_is_an_error(self) -> None:
        target = self.tmp / "c.txt"

        def short_write(path: Path, data: bytes, *, mode: int) -> None:
            path.write_bytes(data[:-1])

        with (
            mock.patch.object(lyrics_files, "replace_atomically", short_write),
            self.assertRaises(lm.ReadBackError),
        ):
            lyrics_files.write_confirmed(target, b"abc")

    @unittest.skipIf(not getattr(os, "O_NOFOLLOW", 0), "no O_NOFOLLOW to refuse a link")
    def test_a_link_swapped_in_after_the_replace_is_refused_on_read_back(self) -> None:
        target = self.tmp / "c.txt"
        elsewhere = self.tmp / "elsewhere.txt"
        elsewhere.write_bytes(b"abc")
        real = lyrics_files.replace_atomically

        def replacing_then_linking(path: Path, data: bytes, *, mode: int) -> None:
            real(path, data, mode=mode)
            path.unlink()
            try:
                path.symlink_to(elsewhere)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlinks unavailable: {error}")

        with (
            mock.patch.object(
                lyrics_files, "replace_atomically", replacing_then_linking
            ),
            self.assertRaises(OSError),
        ):
            lyrics_files.write_confirmed(target, b"abc")


class ReplaceWithLinkTest(fixtures.IsolatedCase):
    """A link replaces whatever the name held, in one step, and is read back."""

    def test_a_file_is_replaced_by_the_link_and_never_followed(self) -> None:
        shared = self.tmp / "shared.txt"
        shared.write_bytes(b"kept")
        target = self.tmp / "01.txt"
        target.write_bytes(b"replaced")
        lyrics_files.replace_with_link(target, "shared.txt")
        self.assertEqual(os.readlink(target), "shared.txt")
        self.assertEqual(shared.read_bytes(), b"kept")

    def test_a_link_is_replaced_and_its_old_target_survives(self) -> None:
        old = self.tmp / "old.txt"
        old.write_bytes(b"old")
        target = self.tmp / "01.txt"
        target.symlink_to(old)
        lyrics_files.replace_with_link(target, "gone.txt")
        self.assertEqual(os.readlink(target), "gone.txt")
        self.assertEqual(old.read_bytes(), b"old")

    def test_a_failed_replace_leaves_the_old_entry_and_no_temporary(self) -> None:
        target = self.tmp / "01.txt"
        target.write_bytes(b"old")
        with (
            mock.patch.object(os, "replace", side_effect=OSError("disk gone")),
            self.assertRaises(OSError),
        ):
            lyrics_files.replace_with_link(target, "elsewhere.txt")
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(
            sorted(p.name for p in self.tmp.iterdir() if p.name != "home"), ["01.txt"]
        )

    def test_a_link_that_does_not_read_back_is_an_error(self) -> None:
        target = self.tmp / "01.txt"
        with (
            mock.patch.object(os, "readlink", return_value="other.txt"),
            self.assertRaises(lm.ReadBackError),
        ):
            lyrics_files.replace_with_link(target, "elsewhere.txt")


if __name__ == "__main__":
    unittest.main()
