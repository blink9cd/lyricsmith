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

#: Why a test of a real folder's fsync skips: the host cannot open a folder to sync it.
_NO_FOLDER_SYNC = "this host cannot open a folder to sync it (Windows)"
#: Windows' extended-length prefix, as its readlink puts it on an absolute target.
_EXTENDED = "\\\\?\\"


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

    @unittest.skipUnless(lyrics_files.FOLDERS_SYNC, _NO_FOLDER_SYNC)
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

        # Asked to sync, as POSIX is, so the refused open is reached on Windows too.
        with (
            mock.patch.object(lyrics_files, "FOLDERS_SYNC", True),
            mock.patch.object(os, "open", refuse_folders),
        ):
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

    @unittest.skipUnless(lyrics_files.FOLDERS_SYNC, _NO_FOLDER_SYNC)
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


class FolderSyncSeamTest(fixtures.IsolatedCase):
    """`FOLDERS_SYNC`: a host that cannot open a folder skips its sync by name.

    The folder's descriptor is stood in by a file's here, so the sync's own branches
    run on a host that cannot open a folder, Windows, as well as on POSIX.
    """

    def setUp(self) -> None:
        """The host can sync folders, and an open of the folder gives a file's descriptor."""
        super().setUp()
        self.enterContext(mock.patch.object(lyrics_files, "FOLDERS_SYNC", True))
        stand_in = self.tmp / "stand-in"
        stand_in.write_bytes(b"")
        self.folder_descriptors: list[int] = []
        real = os.open

        def folder_as_file(path: object, flags: int, *args: int) -> int:
            if Path(cast(str, path)) == self.tmp:
                self.folder_descriptors.append(real(stand_in, os.O_RDONLY))
                return self.folder_descriptors[-1]
            return real(cast(str, path), flags, *args)

        self.enterContext(mock.patch.object(os, "open", folder_as_file))

    def sync_raising(self, number: int | None) -> list[int]:
        """Make the folder's fsync raise errno *number*, None for none; record each.

        The stand-in's own descriptor is never really synced: it is open read-only,
        which Windows' fsync refuses. Every other descriptor is synced as it would be.
        """
        synced: list[int] = []
        real = os.fsync

        def fsync(descriptor: int) -> None:
            if descriptor not in self.folder_descriptors:
                real(descriptor)
                return
            synced.append(descriptor)
            if number is not None:
                raise OSError(number, os.strerror(number))

        self.enterContext(mock.patch.object(os, "fsync", fsync))
        return synced

    def test_the_folder_is_synced_where_the_host_can(self) -> None:
        synced = self.sync_raising(None)
        lyrics_files.replace_atomically(self.tmp / "out.json", b"new")
        self.assertEqual(synced, self.folder_descriptors)
        self.assertEqual(len(synced), 1)

    def test_a_host_that_cannot_sync_a_folder_skips_it_unopened(self) -> None:
        synced = self.sync_raising(None)
        with mock.patch.object(lyrics_files, "FOLDERS_SYNC", False):
            lyrics_files.replace_atomically(self.tmp / "out.json", b"new")
        self.assertEqual((synced, self.folder_descriptors), ([], []))
        self.assertEqual((self.tmp / "out.json").read_bytes(), b"new")

    def test_an_unsupported_folder_sync_keeps_the_replace(self) -> None:
        self.sync_raising(errno.EINVAL)
        lyrics_files.replace_atomically(self.tmp / "out.json", b"new")
        self.assertEqual((self.tmp / "out.json").read_bytes(), b"new")

    def test_any_other_folder_sync_failure_is_raised(self) -> None:
        self.sync_raising(errno.EIO)
        with self.assertRaises(OSError) as caught:
            lyrics_files.replace_atomically(self.tmp / "out.json", b"new")
        self.assertEqual(caught.exception.errno, errno.EIO)


class ReadLinkTest(fixtures.IsolatedCase):
    """`read_link`: a link's target as it was made, Windows' extended prefix dropped."""

    def read_as_windows(self, raw: str) -> str:
        """What `read_link` makes of *raw*, a target as Windows' readlink gives it."""
        with (
            mock.patch.object(lyrics_files, "LINKS_READ_EXTENDED", True),
            mock.patch.object(os, "readlink", return_value=raw),
        ):
            return lyrics_files.read_link(self.tmp / "01.txt")

    def test_a_drive_path_loses_the_prefix(self) -> None:
        self.assertEqual(
            self.read_as_windows(_EXTENDED + "C:\\Music\\shared.txt"),
            "C:\\Music\\shared.txt",
        )

    def test_a_unc_path_comes_back_as_a_share(self) -> None:
        self.assertEqual(
            self.read_as_windows(_EXTENDED + "UNC\\server\\share\\a.txt"),
            "\\\\server\\share\\a.txt",
        )

    def test_a_volume_path_is_kept_whole(self) -> None:
        volume = _EXTENDED + "Volume{00000000-0000-0000-0000-000000000000}\\a.txt"
        self.assertEqual(self.read_as_windows(volume), volume)

    def test_a_relative_target_is_kept(self) -> None:
        self.assertEqual(self.read_as_windows("..\\shared.txt"), "..\\shared.txt")

    def test_off_windows_a_target_is_kept_byte_for_byte(self) -> None:
        # On POSIX that prefix is four ordinary characters of a file name.
        raw = _EXTENDED + "C:\\shared.txt"
        with (
            mock.patch.object(lyrics_files, "LINKS_READ_EXTENDED", False),
            mock.patch.object(os, "readlink", return_value=raw),
        ):
            self.assertEqual(lyrics_files.read_link(self.tmp / "01.txt"), raw)

    def test_a_real_link_reads_back_as_made(self) -> None:
        target = self.tmp / "shared.txt"
        link = self.tmp / "01.txt"
        link.symlink_to(target)
        self.assertEqual(lyrics_files.read_link(link), str(target))

    def test_a_link_made_by_the_replace_reads_back_through_the_prefix(self) -> None:
        target = self.tmp / "01.txt"
        with (
            mock.patch.object(lyrics_files, "LINKS_READ_EXTENDED", True),
            mock.patch.object(os, "readlink", return_value=_EXTENDED + "C:\\a.txt"),
        ):
            lyrics_files.replace_with_link(target, "C:\\a.txt")


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
