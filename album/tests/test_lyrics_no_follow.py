"""A read that never follows a link, and a read of anything but a file, on every host.

POSIX refuses a link at the last step in the open itself (``O_NOFOLLOW``); Windows has
no such flag, so a link there was followed. `lyrics_files.read_regular` now looks at
the name first, never following it, refuses a link or a junction by that look, and
after the open compares the opened file with the one it looked at, so a link swapped
in between is refused too. Each test here patches the flag to 0, Windows' own value,
so the port is proven on a POSIX gate box as well.

Windows refuses to open a folder at all (``PermissionError``) where POSIX opens it
and the descriptor's own `fstat` refuses it, so a folder is refused by the look
before any open, with one error on every host; the stand-in open here refuses a
folder as Windows' does.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above

#: Windows' reparse tags for a symbolic link and a junction, and for a cloud file
#: placeholder, which is no link and is read as the file it stands for.
_TAG_SYMLINK = 0xA000000C
_TAG_JUNCTION = 0xA0000003
_TAG_CLOUD = 0x9000001A


def _status(mode: int, tag: int = 0) -> os.stat_result:
    """A stand-in `os.stat_result` with *mode* and, as on Windows, a reparse *tag*."""
    return cast(os.stat_result, SimpleNamespace(st_mode=mode, st_reparse_tag=tag))


class WindowsShapedCase(fixtures.IsolatedCase):
    """A file, a file elsewhere, and the open flags as Windows has them: no O_NOFOLLOW."""

    def setUp(self) -> None:
        """The two files, and ``O_NOFOLLOW`` patched to 0 for the whole test."""
        super().setUp()
        self.file = self.tmp / "record.json"
        self.file.write_bytes(b"mine\n")
        self.elsewhere = self.tmp / "elsewhere.json"
        self.elsewhere.write_bytes(b"not mine\n")
        self.enterContext(mock.patch.object(lyrics_files, "_NO_FOLLOW", 0))

    def assert_link_refused(self, error: OSError) -> None:
        """Assert *error* is the read's own refusal of a link, as every host words it."""
        self.assertIs(type(error), OSError)
        self.assertEqual(error.errno, errno.ELOOP)
        self.assertEqual(error.strerror, lyrics_files.LINK_REFUSED)


class LinkAtTheNameTest(WindowsShapedCase):
    """A link under the name is refused by the look, never read through."""

    def test_a_link_to_a_file_is_refused(self) -> None:
        link = self.tmp / "link.json"
        link.symlink_to(self.elsewhere)
        with self.assertRaises(OSError) as caught:
            lyrics_files.read_regular(link, follow=False)
        self.assert_link_refused(caught.exception)
        # The error names the link, and prints it as `OSError` prints any file name:
        # through repr, which doubles each backslash of a Windows path.
        self.assertEqual(caught.exception.filename, str(link))
        self.assertIn(repr(str(link)), str(caught.exception))

    def test_a_dangling_link_is_refused_as_a_link(self) -> None:
        link = self.tmp / "link.json"
        link.symlink_to(self.tmp / "gone.json")
        with self.assertRaises(OSError) as caught:
            lyrics_files.read_regular(link, follow=False)
        self.assert_link_refused(caught.exception)

    def test_a_link_is_still_followed_when_asked(self) -> None:
        link = self.tmp / "link.json"
        link.symlink_to(self.elsewhere)
        self.assertEqual(lyrics_files.read_regular(link), b"not mine\n")

    def test_a_file_is_read(self) -> None:
        self.assertEqual(lyrics_files.read_regular(self.file, follow=False), b"mine\n")


class SwapAfterTheLookTest(WindowsShapedCase):
    """A link swapped in between the look and the open is refused after the open."""

    def swap_at_open(self) -> None:
        """Make the next open of the file first replace it with a link elsewhere."""
        real = os.open

        def swapping(name: str, flags: int, *args: int) -> int:
            if Path(name) == self.file:
                self.file.unlink()
                self.file.symlink_to(self.elsewhere)
            return real(name, flags, *args)

        self.enterContext(mock.patch.object(os, "open", swapping))

    def test_the_swapped_in_file_is_never_read(self) -> None:
        self.swap_at_open()
        with self.assertRaises(lyrics_files.SwappedFileError) as caught:
            lyrics_files.read_regular(self.file, follow=False)
        self.assertIn(self.file.name, str(caught.exception))
        self.assertIsInstance(caught.exception, OSError)

    def test_the_refused_descriptor_is_closed(self) -> None:
        self.swap_at_open()
        closed: list[int] = []
        real_close = os.close

        def recording(descriptor: int) -> None:
            closed.append(descriptor)
            real_close(descriptor)

        with (
            mock.patch.object(os, "close", recording),
            self.assertRaises(lyrics_files.SwappedFileError),
        ):
            lyrics_files.read_regular(self.file, follow=False)
        self.assertEqual(len(closed), 1)

    @unittest.skipIf(not getattr(os, "O_NOFOLLOW", 0), "no O_NOFOLLOW to refuse a link")
    def test_with_the_real_flag_the_open_itself_refuses_the_swap(self) -> None:
        # The residual on POSIX: a link swapped in after the look meets O_NOFOLLOW,
        # whose refusal is the kernel's own ELOOP.
        self.swap_at_open()
        with (
            mock.patch.object(lyrics_files, "_NO_FOLLOW", getattr(os, "O_NOFOLLOW", 0)),
            self.assertRaises(OSError) as caught,
        ):
            lyrics_files.read_regular(self.file, follow=False)
        self.assertEqual(caught.exception.errno, errno.ELOOP)


class LinkEntryTest(fixtures.IsolatedCase):
    """`is_link_entry`: a symbolic link or a name surrogate (a junction), from a look."""

    def test_a_symbolic_link_is_one(self) -> None:
        self.assertTrue(lyrics_files.is_link_entry(_status(stat.S_IFLNK | 0o777)))

    def test_a_windows_link_or_junction_is_one(self) -> None:
        for tag in (_TAG_SYMLINK, _TAG_JUNCTION):
            with self.subTest(tag=hex(tag)):
                status = _status(stat.S_IFDIR | 0o777, tag)
                self.assertTrue(lyrics_files.is_link_entry(status))

    def test_a_cloud_placeholder_is_a_file(self) -> None:
        status = _status(stat.S_IFREG | 0o666, _TAG_CLOUD)
        self.assertFalse(lyrics_files.is_link_entry(status))

    def test_a_plain_file_is_none(self) -> None:
        self.assertFalse(lyrics_files.is_link_entry(_status(stat.S_IFREG | 0o666)))

    def test_a_posix_look_has_no_tag_and_is_judged_by_its_mode(self) -> None:
        status = cast(os.stat_result, SimpleNamespace(st_mode=stat.S_IFREG | 0o644))
        self.assertFalse(lyrics_files.is_link_entry(status))

    def test_a_junction_under_the_name_is_refused_as_a_link(self) -> None:
        # The look is all that matters, so no file is made: a junction is refused
        # before anything is opened.
        junction = _status(stat.S_IFDIR | 0o777, _TAG_JUNCTION)
        with (
            mock.patch.object(os, "lstat", return_value=junction),
            self.assertRaises(OSError) as caught,
        ):
            lyrics_files.read_regular(Path("album") / "01.txt", follow=False)
        self.assertEqual(caught.exception.strerror, lyrics_files.LINK_REFUSED)


def _refuse_folders_as_windows_does(case: unittest.TestCase) -> list[str]:
    """Make `os.open` refuse a folder with ``PermissionError``; return each name opened."""
    opened: list[str] = []
    real = os.open

    def windows_open(name: str, flags: int, *args: int) -> int:
        opened.append(str(name))
        if os.path.isdir(name):
            raise PermissionError(errno.EACCES, "Permission denied", str(name))
        return real(name, flags, *args)

    case.enterContext(mock.patch.object(os, "open", windows_open))
    return opened


class FolderTest(fixtures.IsolatedCase):
    """A folder is refused as no regular file before any open, on every host."""

    def test_each_read_refuses_a_folder_unopened(self) -> None:
        opened = _refuse_folders_as_windows_does(self)
        for follow in (True, False):
            with self.subTest(follow=follow):
                with self.assertRaisesRegex(
                    lyrics_files.NotRegularFileError, "not a regular file"
                ):
                    lyrics_files.read_regular(self.tmp, follow=follow)
        self.assertEqual(opened, [])

    def test_a_stream_refuses_a_folder_unopened(self) -> None:
        opened = _refuse_folders_as_windows_does(self)
        for mode in ("rb", "r+b"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(
                    lyrics_files.NotRegularFileError, "not a regular file"
                ):
                    lyrics_files.open_regular(self.tmp, mode)
        self.assertEqual(opened, [])

    def test_a_folder_swapped_in_after_the_look_is_refused_by_the_descriptor(
        self,
    ) -> None:
        path = self.tmp / "plain.json"
        path.write_bytes(b"{}\n")
        folder = _status(stat.S_IFDIR | 0o755)
        with (
            mock.patch.object(os, "fstat", return_value=folder),
            self.assertRaisesRegex(
                lyrics_files.NotRegularFileError, "not a regular file"
            ),
        ):
            lyrics_files.read_regular(path)


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
