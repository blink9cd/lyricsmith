"""The delete prune and accept remove a folder with: never out of the folder it is given.

The walk runs here on real links, as any host makes them, and on a Windows-shaped
stand-in for `os.lstat` that marks a chosen entry as a reparse point (a junction, or any
other), so the junction branches run on every host. Every folder is under the test's own
folder, and every file holds invented bytes.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import stat
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_delete as ld  # noqa: E402 - import follows the sys.path bootstrap above

#: What every file outside the deleted folder holds, so a test can see it was not read.
_KEPT = b"not the deleted folder's\n"
#: The bits Windows sets on a junction or a folder symbolic link, and on a file link.
_REPARSE = stat.FILE_ATTRIBUTE_REPARSE_POINT
_FOLDER_REPARSE = stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DIRECTORY


@dataclasses.dataclass(frozen=True)
class WindowsStat:
    """A stat result shaped as Windows gives one: with its file attributes."""

    st_mode: int
    st_dev: int
    st_ino: int
    st_file_attributes: int


def windows_lstat(
    marked: dict[Path, int], mode: dict[Path, int] | None = None
) -> Callable[[Path], WindowsStat]:
    """An `os.lstat` stand-in adding *marked*'s attributes, and *mode*'s file type.

    Every path not named keeps its real mode and no attributes, as a plain Windows
    file or folder has none of the bits a test looks at.
    """
    modes = mode or {}

    def lstat(path: Path) -> WindowsStat:
        real = os.lstat(path)
        kind = modes.get(path, stat.S_IFMT(real.st_mode))
        return WindowsStat(
            kind | stat.S_IMODE(real.st_mode),
            real.st_dev,
            real.st_ino,
            marked.get(path, 0),
        )

    return lstat


def swapped_after(
    folder: Path, calls: int
) -> Callable[[Path], os.stat_result | WindowsStat]:
    """An `os.lstat` stand-in under which *folder* reads as another after *calls* reads.

    It stands for a folder swapped, between two of the walk's reads, for another
    folder or a junction: the same name, another identity.
    """
    seen = [0]

    def lstat(path: Path) -> os.stat_result | WindowsStat:
        real = os.lstat(path)
        if path != folder:
            return real
        seen[0] += 1
        if seen[0] <= calls:
            return real
        return WindowsStat(real.st_mode, real.st_dev, real.st_ino + 1, 0)

    return lstat


class DeleteCase(fixtures.IsolatedCase):
    """A folder to delete, holding files and a subfolder, and a folder outside it."""

    def setUp(self) -> None:
        """The folder to delete, ``doomed``, and ``outside`` with one file to keep."""
        super().setUp()
        self.doomed = self.tmp / "doomed"
        (self.doomed / "sub" / "deeper").mkdir(parents=True)
        (self.doomed / "top.bin").write_bytes(b"invented top\n")
        (self.doomed / "sub" / "middle.bin").write_bytes(b"invented middle\n")
        (self.doomed / "sub" / "deeper" / "bottom.bin").write_bytes(b"invented\n")
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.kept = self.outside / "keep.bin"
        self.kept.write_bytes(_KEPT)

    def assert_outside_untouched(self) -> None:
        """The folder outside still holds its one file, unchanged."""
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["keep.bin"])
        self.assertEqual(self.kept.read_bytes(), _KEPT)


class ChooserTest(fixtures.IsolatedCase):
    """The fd-based `shutil.rmtree` where it resists links, else this module's walk."""

    def test_a_link_proof_rmtree_is_kept(self) -> None:
        self.assertIs(ld.chosen_delete(avoids_symlink_attacks=True), shutil.rmtree)

    def test_an_rmtree_that_can_follow_a_link_is_replaced_by_the_walk(self) -> None:
        self.assertIs(ld.chosen_delete(avoids_symlink_attacks=False), ld.delete_tree)

    def test_none_reads_this_pythons_own_flag_at_the_call(self) -> None:
        for flag, expected in ((True, shutil.rmtree), (False, ld.delete_tree)):
            with self.subTest(flag=flag):
                with mock.patch.object(shutil.rmtree, "avoids_symlink_attacks", flag):
                    self.assertIs(ld.chosen_delete(), expected)

    def test_a_patched_rmtree_is_the_one_chosen(self) -> None:
        # A test that patches shutil.rmtree, to fail it, still reaches its stand-in.
        with mock.patch.object(shutil, "rmtree") as patched:
            patched.avoids_symlink_attacks = True
            self.assertIs(ld.chosen_delete(), patched)


class WalkTest(DeleteCase):
    """The walk deletes the whole folder, and removes every link in it as itself."""

    def test_a_plain_tree_goes_whole(self) -> None:
        ld.delete_tree(self.doomed)
        self.assertFalse(os.path.lexists(self.doomed))

    def test_a_link_to_a_folder_outside_goes_as_the_link(self) -> None:
        (self.doomed / "out").symlink_to(self.outside, target_is_directory=True)
        ld.delete_tree(self.doomed)
        self.assertFalse(os.path.lexists(self.doomed))
        self.assert_outside_untouched()

    def test_a_link_to_a_file_outside_goes_as_the_link(self) -> None:
        (self.doomed / "out.bin").symlink_to(self.kept)
        ld.delete_tree(self.doomed)
        self.assertFalse(os.path.lexists(self.doomed))
        self.assert_outside_untouched()

    def test_a_nested_link_goes_as_the_link(self) -> None:
        deeper = self.doomed / "sub" / "deeper"
        (deeper / "out").symlink_to(self.outside, target_is_directory=True)
        (deeper / "out.bin").symlink_to(self.kept)
        ld.delete_tree(self.doomed)
        self.assertFalse(os.path.lexists(self.doomed))
        self.assert_outside_untouched()

    def test_a_dangling_link_goes_too(self) -> None:
        (self.doomed / "gone").symlink_to(self.tmp / "missing")
        ld.delete_tree(self.doomed)
        self.assertFalse(os.path.lexists(self.doomed))

    def test_a_link_at_the_top_is_refused_and_nothing_goes(self) -> None:
        top = self.tmp / "top-link"
        top.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(ld.TreeChangedError) as caught:
            ld.delete_tree(top)
        self.assertIsInstance(caught.exception, OSError)
        self.assertIn("top-link", str(caught.exception))
        self.assertTrue(top.is_symlink())
        self.assert_outside_untouched()

    def test_a_file_at_the_top_is_refused(self) -> None:
        with self.assertRaises(ld.TreeChangedError):
            ld.delete_tree(self.kept)
        self.assert_outside_untouched()

    def test_a_missing_top_raises_its_os_error(self) -> None:
        with self.assertRaises(FileNotFoundError):
            ld.delete_tree(self.tmp / "missing")


class ReparsePointTest(DeleteCase):
    """Under the Windows-shaped stand-in, a reparse point is removed and never entered."""

    def test_a_junction_is_removed_as_a_folder_link(self) -> None:
        # An empty folder the stand-in marks as a junction: rmdir takes it, as it
        # takes a real junction, whatever the junction points to.
        junction = self.doomed / "junction"
        junction.mkdir()
        ld.delete_tree(self.doomed, lstat=windows_lstat({junction: _FOLDER_REPARSE}))
        self.assertFalse(os.path.lexists(self.doomed))

    def test_a_junction_is_never_entered(self) -> None:
        # A non-empty folder marked a junction: entered, it would lose its file; as a
        # link, its rmdir is refused, so its file stays and the walk raises that error.
        junction = self.doomed / "junction"
        junction.mkdir()
        inside = junction / "inside.bin"
        inside.write_bytes(_KEPT)
        lstat = windows_lstat({junction: _FOLDER_REPARSE})
        with self.assertRaises(OSError):
            ld.delete_tree(self.doomed, lstat=lstat)
        self.assertEqual(inside.read_bytes(), _KEPT)

    def test_a_folder_symbolic_link_is_removed_as_a_folder(self) -> None:
        # Windows reads a folder symbolic link as a link carrying the folder bit, and
        # removes it with rmdir.
        link = self.doomed / "folder-link"
        link.mkdir()
        lstat = windows_lstat({link: _FOLDER_REPARSE}, {link: stat.S_IFLNK})
        ld.delete_tree(self.doomed, lstat=lstat)
        self.assertFalse(os.path.lexists(self.doomed))

    def test_a_reparse_file_is_unlinked(self) -> None:
        marked = self.doomed / "top.bin"
        ld.delete_tree(self.doomed, lstat=windows_lstat({marked: _REPARSE}))
        self.assertFalse(os.path.lexists(self.doomed))

    def test_a_junction_at_the_top_is_refused(self) -> None:
        lstat = windows_lstat({self.doomed: _FOLDER_REPARSE})
        with self.assertRaises(ld.TreeChangedError):
            ld.delete_tree(self.doomed, lstat=lstat)
        self.assertTrue((self.doomed / "top.bin").is_file())

    def test_a_real_junction_to_a_folder_outside_goes_as_itself(self) -> None:
        if sys.platform != "win32":
            self.skipTest("a junction is a Windows folder link")
        import _winapi  # only Windows has it, so it is imported only here

        _winapi.CreateJunction(str(self.outside), str(self.doomed / "junction"))
        ld.delete_tree(self.doomed)
        self.assertFalse(os.path.lexists(self.doomed))
        self.assert_outside_untouched()


class SwappedTest(DeleteCase):
    """A folder that reads as another between two of the walk's reads stops the walk."""

    def assert_refused(self, folder: Path, calls: int) -> None:
        """The walk raises once *folder* reads as another, after *calls* reads of it."""
        with self.assertRaises(ld.TreeChangedError) as caught:
            ld.delete_tree(self.doomed, lstat=swapped_after(folder, calls))
        self.assertIn(folder.name, str(caught.exception))

    def test_a_subfolder_swapped_before_it_is_listed(self) -> None:
        sub = self.doomed / "sub"
        self.assert_refused(sub, 1)
        self.assertTrue((sub / "middle.bin").is_file())

    def test_a_folder_swapped_while_it_is_listed(self) -> None:
        sub = self.doomed / "sub"
        self.assert_refused(sub, 2)
        self.assertTrue((sub / "middle.bin").is_file())

    def test_a_folder_swapped_before_an_entry_is_removed(self) -> None:
        self.assert_refused(self.doomed, 3)
        self.assertTrue((self.doomed / "top.bin").is_file())

    def test_a_folder_swapped_before_its_own_rmdir(self) -> None:
        deeper = self.doomed / "sub" / "deeper"
        # Read when decided on, before and after its listing, and before its one file
        # goes; the fifth read, before its rmdir, finds it swapped.
        reads = 4
        self.assert_refused(deeper, reads)
        self.assertTrue(deeper.is_dir())


if __name__ == "__main__":  # pragma: no cover - run directly, outside the gate
    unittest.main()
