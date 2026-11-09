"""Accept's removal of ``.lyricsmith`` where a folder holding an open file cannot be renamed.

Windows refuses to rename a folder while a file inside it is open, and accept holds the
album lock, a file inside ``.lyricsmith``, through its removal. A rename stand-in
refuses as Windows does while that lock is held, so these run on every host: renaming
the whole folder fails there, and moving its entries out, the lock left in place, does
not. Every album and run is built under the test's own folder; every lyric is invented.
"""

from __future__ import annotations

import datetime as dt
import errno
import os
import shutil
import sys
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_judge as laj  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_mode as lam  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_records as lar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_delete as ld  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_lock  # noqa: E402 - import follows the sys.path bootstrap above
import test_lyrics_accept_mode as tam  # noqa: E402 - import follows the sys.path bootstrap above
import test_lyrics_junction_guards as tjg  # noqa: E402 - follows the bootstrap above

#: A fixed accept time.
_NOW = dt.datetime(2026, 10, 25, 9, 8, 7, tzinfo=dt.UTC)
_LOCK = "lock"
_DENIED = "Access is denied"
_REAL_RENAME = os.rename
_REAL_UNLINK = os.unlink


def lock_held_under(folder: Path) -> bool:
    """Whether an album lock somewhere under *folder* is held, by any open file."""
    return any(run_lock.is_held(path) for path in folder.rglob(_LOCK))


def windows_rename(src: str | Path, dst: str | Path) -> None:
    """`os.rename`, refusing a folder holding a held lock as Windows refuses it."""
    source = Path(src)
    if source.is_dir() and not source.is_symlink() and lock_held_under(source):
        raise PermissionError(errno.EACCES, _DENIED, str(src))
    _REAL_RENAME(src, dst)


def windows_unlink(path: str | Path) -> None:
    """`os.unlink`, refusing a held lock file as Windows refuses an open one."""
    if Path(path).name == _LOCK and run_lock.is_held(Path(path)):
        raise PermissionError(errno.EACCES, _DENIED, str(path))
    _REAL_UNLINK(path)


@contextmanager
def renames_as_windows(
    rename: Callable[[str | Path, str | Path], None] = windows_rename,
) -> Iterator[None]:
    """Hold *rename* in `os.rename`'s place, which `Path.rename` calls too."""
    with mock.patch.object(os, "rename", rename):
        yield


class HeldRenameCase(tam.AcceptCase):
    """The one-run album of the accept suite, accepted with or without the whole rename."""

    def accept_with(
        self, *, rename_whole: bool | None, delete: lam.Delete | None = None
    ) -> lam.AcceptReport:
        """Accept with apply, renaming ``.lyricsmith`` whole or moving its entries."""
        return lam.accept_album(
            self.album,
            apply=True,
            acquire_lock=run_lock.acquire,
            stages=album.real_wiring().stages,
            environ=self.environ,
            now=lambda: _NOW,
            delete=delete,
            rename_whole=rename_whole,
        )

    def assert_removed(self, report: lam.AcceptReport, before: dict[str, str]) -> None:
        """Every run removed, both lyricsmith folders gone, every album file kept."""
        self.assertEqual({r.outcome for r in report.rows}, {lam.REMOVED})
        self.assertEqual(report.errors, 0)
        self.assertFalse(os.path.lexists(self.album / lr.RUN_FOLDER_NAME))
        self.assertFalse(os.path.lexists(self.album / laj.ACCEPTING_NAME))
        self.assertEqual(tam.outside_hashes(self.album), before)


class AcceptAlbumTest(HeldRenameCase):
    """The pass, under a rename that refuses a folder holding the held lock."""

    def test_renaming_the_whole_folder_fails_as_it_does_on_windows(self) -> None:
        with renames_as_windows():
            report = self.accept_with(rename_whole=True)
        (row,) = report.rows
        self.assertTrue(row.outcome.startswith(lam.ERROR_PREFIX))
        self.assertIn(_DENIED, row.outcome)
        self.assertTrue(lr.run_folder(self.album, self.run_id).is_dir())
        self.assertIsNotNone(report.records)

    def test_moving_the_entries_out_removes_lyricsmith(self) -> None:
        before = tam.outside_hashes(self.album)
        with renames_as_windows():
            report = self.accept_with(rename_whole=False)
        self.assert_removed(report, before)
        (folder,) = self.records_folders()
        self.assertTrue((folder / f"{self.run_id}.json").is_file())

    def test_this_hosts_rule_is_read_when_none_is_given(self) -> None:
        before = tam.outside_hashes(self.album)
        with (
            mock.patch.object(lam, "RENAMES_HELD_FOLDERS", False),
            renames_as_windows(),
        ):
            report = self.accept_with(rename_whole=None)
        self.assert_removed(report, before)

    def test_a_records_failure_keeps_even_a_lock_only_folder(self) -> None:
        shutil.rmtree(lr.run_folder(self.album, self.run_id))
        self.state.write_text(
            "a file where the state folder goes", encoding="utf-8", newline="\n"
        )
        with renames_as_windows():
            report = self.accept_with(rename_whole=False)
        self.assertIsNotNone(report.error)
        folder = self.album / lr.RUN_FOLDER_NAME
        self.assertEqual([p.name for p in folder.iterdir()], [_LOCK])
        self.assertIsNone(report.kept_folder)

    def test_a_folder_a_run_takes_since_is_kept_and_reported(self) -> None:
        later = self.album / lr.RUN_FOLDER_NAME / "20261101T000000Z"

        def delete_then_a_run(path: Path) -> None:
            ld.chosen_delete()(path)
            later.mkdir()

        with renames_as_windows():
            report = self.accept_with(rename_whole=False, delete=delete_then_a_run)
        self.assertEqual((report.errors, report.kept_folder), (0, lar.KEPT_FILLED))
        self.assertTrue(later.is_dir())
        text = lam.format_accept(report, album=self.album, prog="album")
        self.assertIn(f"{lr.RUN_FOLDER_NAME} kept: {lar.KEPT_FILLED}\n", text)

    def test_a_removed_folder_reports_nothing_kept(self) -> None:
        with renames_as_windows():
            report = self.accept_with(rename_whole=False)
        self.assertIsNone(report.kept_folder)
        text = lam.format_accept(report, album=self.album, prog="album")
        self.assertNotIn(" kept: ", text)

    def test_the_lock_is_held_through_every_move_and_the_delete(self) -> None:
        lock = self.album / lr.RUN_FOLDER_NAME / _LOCK
        held: list[bool] = []

        def watching_rename(src: str | Path, dst: str | Path) -> None:
            held.append(run_lock.is_held(lock))
            windows_rename(src, dst)

        def watching_delete(path: Path) -> None:
            held.append(run_lock.is_held(lock))
            ld.chosen_delete()(path)

        with renames_as_windows(watching_rename):
            report = self.accept_with(rename_whole=False, delete=watching_delete)
        self.assertEqual(report.errors, 0)
        self.assertEqual(held, [True, True])

    def test_a_failed_move_keeps_the_rest_and_the_next_accept_finishes(self) -> None:
        newer = self.made_run(5)
        before = tam.outside_hashes(self.album)

        def second_refused(src: str | Path, dst: str | Path) -> None:
            if Path(src).name == newer:
                raise PermissionError(errno.EACCES, _DENIED, str(src))
            windows_rename(src, dst)

        with renames_as_windows(second_refused):
            failed = self.accept_with(rename_whole=False)
        # Both runs share the one removal, so each row carries its error.
        self.assertEqual({row.failed for row in failed.rows}, {True})
        # The older run went first: what is left is the newest run, whole, and the lock.
        remaining = sorted(p.name for p in (self.album / lr.RUN_FOLDER_NAME).iterdir())
        self.assertEqual(remaining, sorted([newer, _LOCK]))
        moved = sorted(p.name for p in (self.album / laj.ACCEPTING_NAME).iterdir())
        self.assertEqual(moved, [self.run_id])
        with renames_as_windows():
            again = self.accept_with(rename_whole=False)
        assert again.leftover is not None
        self.assertEqual(again.leftover.outcome, f"{lam.REMOVED}: {lam.LEFT_BY}")
        self.assert_removed(again, before)


class RemoveLyricsmithTest(HeldRenameCase):
    """`remove_lyricsmith`'s entry-by-entry removal, the lock left behind."""

    def test_entries_move_oldest_first_and_a_pruning_leftover_before_them(self) -> None:
        newer = self.made_run(5)
        pruning = f"{newer}{lyrics_prune.PRUNING_SUFFIX}"
        (self.album / lr.RUN_FOLDER_NAME / pruning).mkdir()
        moved: list[str] = []

        def recording(src: str | Path, dst: str | Path) -> None:
            moved.append(Path(src).name)
            _REAL_RENAME(src, dst)

        with renames_as_windows(recording):
            self.assertIsNone(lar.remove_lyricsmith(self.album, rename_whole=False))
        self.assertEqual(moved, [pruning, self.run_id, newer])
        folder = self.album / lr.RUN_FOLDER_NAME
        self.assertEqual([p.name for p in folder.iterdir()], [_LOCK])
        self.assertFalse(os.path.lexists(self.album / laj.ACCEPTING_NAME))

    def test_this_hosts_rule_is_read_when_none_is_given(self) -> None:
        with mock.patch.object(lar, "RENAMES_HELD_FOLDERS", False):
            self.assertIsNone(lar.remove_lyricsmith(self.album))
        folder = self.album / lr.RUN_FOLDER_NAME
        self.assertEqual([p.name for p in folder.iterdir()], [_LOCK])

    def test_the_rule_is_windows_alone(self) -> None:
        self.assertEqual(lar.RENAMES_HELD_FOLDERS, sys.platform != "win32")


class JunctionTest(HeldRenameCase):
    """A junction at ``.lyricsmith.accepting`` is refused as a link is, never entered."""

    def setUp(self) -> None:
        """A folder outside the album, holding one file a junction could lead to."""
        super().setUp()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.kept = self.outside / "keep.bin"
        self.kept.write_bytes(b"not the album's\n")
        self.accepting = self.album / laj.ACCEPTING_NAME

    def test_a_path_read_as_a_junction_is_refused(self) -> None:
        self.accepting.mkdir()
        with tjg.read_as_junctions(self.accepting):
            with self.assertRaises(lar.UnsafeAcceptingError):
                lar.finish_leftover(self.album)
        self.assertTrue(self.accepting.is_dir())

    def test_a_real_junction_is_refused_and_its_target_untouched(self) -> None:
        if sys.platform != "win32":
            self.skipTest("a junction is a Windows folder link")
        import _winapi  # only Windows has it, so it is imported only here

        _winapi.CreateJunction(str(self.outside), str(self.accepting))
        with self.assertRaises(lar.UnsafeAcceptingError):
            lar.finish_leftover(self.album)
        self.assertEqual(self.kept.read_bytes(), b"not the album's\n")


class DropLockFolderTest(HeldRenameCase):
    """After the lock is let go, ``.lyricsmith`` goes when it holds nothing but the lock."""

    def setUp(self) -> None:
        """The album's runs moved out and deleted, as the removal leaves it."""
        super().setUp()
        self.folder = self.album / lr.RUN_FOLDER_NAME
        self.decided = os.lstat(self.folder)
        self.assertIsNone(lar.remove_lyricsmith(self.album, rename_whole=False))

    def test_a_folder_holding_only_its_lock_goes(self) -> None:
        self.assertIsNone(lar.drop_lock_folder(self.album, self.decided))
        self.assertFalse(os.path.lexists(self.folder))

    def test_a_folder_a_run_has_written_to_since_stays(self) -> None:
        (self.folder / "20261101T000000Z").mkdir()
        kept = lar.drop_lock_folder(self.album, self.decided)
        self.assertEqual(kept, lar.KEPT_FILLED)
        self.assertEqual(
            sorted(p.name for p in self.folder.iterdir()),
            sorted([_LOCK, "20261101T000000Z"]),
        )

    def test_a_folder_that_is_another_since_stays(self) -> None:
        other = self.tmp / "other"
        other.mkdir()
        kept = lar.drop_lock_folder(self.album, os.lstat(other))
        self.assertEqual(kept, lar.KEPT_ANOTHER)
        self.assertEqual([p.name for p in self.folder.iterdir()], [_LOCK])

    def test_a_lock_a_run_holds_since_stays_under_a_windows_unlink(self) -> None:
        lock = run_lock.acquire(self.folder / _LOCK)
        assert lock is not None
        self.addCleanup(lock.release)
        with mock.patch.object(os, "unlink", windows_unlink):
            kept = lar.drop_lock_folder(self.album, self.decided)
        self.assertTrue(run_lock.is_held(self.folder / _LOCK))
        self.assertIn(_DENIED, kept or "")

    def test_a_lock_a_run_holds_since_stays_on_windows(self) -> None:
        if sys.platform != "win32":
            self.skipTest("only Windows refuses to delete an open file")
        lock = run_lock.acquire(self.folder / _LOCK)
        assert lock is not None
        self.addCleanup(lock.release)
        lar.drop_lock_folder(self.album, self.decided)
        self.assertTrue(run_lock.is_held(self.folder / _LOCK))

    def test_a_folder_already_gone_is_nothing_to_do(self) -> None:
        (self.folder / _LOCK).unlink()
        self.folder.rmdir()
        self.assertIsNone(lar.drop_lock_folder(self.album, self.decided))
        self.assertFalse(os.path.lexists(self.folder))

    def test_a_folder_gone_before_its_first_read_is_nothing_to_do(self) -> None:
        # The existence check and the read are one step: a folder gone between an
        # earlier check and the read is the gone folder it would have been.
        (self.folder / _LOCK).unlink()
        self.folder.rmdir()
        lexists = mock.Mock(return_value=True)
        with mock.patch.object(lar.os.path, "lexists", lexists):
            self.assertIsNone(lar.drop_lock_folder(self.album, self.decided))
        lexists.assert_not_called()

    def test_a_folder_that_cannot_be_read_is_kept_with_the_reason(self) -> None:
        denied = PermissionError(
            errno.EACCES, os.strerror(errno.EACCES), str(self.folder)
        )
        with mock.patch.object(lar.os, "lstat", side_effect=denied):
            kept = lar.drop_lock_folder(self.album, self.decided)
        self.assertEqual(kept, str(denied))
        self.assertEqual([p.name for p in self.folder.iterdir()], [_LOCK])

    def test_a_folder_another_removes_after_the_unlink_is_nothing_to_do(self) -> None:
        real_unlink = os.unlink

        def unlink_then_remove(path: Path) -> None:
            real_unlink(path)
            self.folder.rmdir()

        with mock.patch.object(lar.os, "unlink", unlink_then_remove):
            self.assertIsNone(lar.drop_lock_folder(self.album, self.decided))
        self.assertFalse(os.path.lexists(self.folder))

    def test_a_folder_another_removes_before_the_unlink_is_nothing_to_do(self) -> None:
        real_listdir = os.listdir

        def listing_then_remove(path: Path) -> list[str]:
            names = real_listdir(path)
            (self.folder / _LOCK).unlink()
            self.folder.rmdir()
            return names

        with mock.patch.object(lar.os, "listdir", listing_then_remove):
            self.assertIsNone(lar.drop_lock_folder(self.album, self.decided))
        self.assertFalse(os.path.lexists(self.folder))

    def swap_folder(self, *, locked: bool) -> Path:
        """Move ``.lyricsmith`` aside and make another in its place, *locked* or empty."""
        aside = self.tmp / "aside"
        _REAL_RENAME(self.folder, aside)
        self.folder.mkdir()
        if locked:
            (self.folder / _LOCK).write_bytes(b"")
        return aside

    def test_a_folder_swapped_before_the_unlink_keeps_its_lock(self) -> None:
        real_listdir = os.listdir
        swapped: list[Path] = []

        def listing_then_swap(path: Path) -> list[str]:
            names = real_listdir(path)
            swapped.append(self.swap_folder(locked=True))
            return names

        with mock.patch.object(lar.os, "listdir", listing_then_swap):
            kept = lar.drop_lock_folder(self.album, self.decided)
        self.assertEqual(kept, lar.KEPT_ANOTHER)
        self.assertEqual([p.name for p in self.folder.iterdir()], [_LOCK])
        self.assertEqual([p.name for p in swapped[0].iterdir()], [_LOCK])

    def test_a_folder_swapped_before_the_rmdir_stays(self) -> None:
        real_unlink = os.unlink
        swapped: list[Path] = []

        def unlink_then_swap(path: Path) -> None:
            real_unlink(path)
            swapped.append(self.swap_folder(locked=False))

        with mock.patch.object(lar.os, "unlink", unlink_then_swap):
            kept = lar.drop_lock_folder(self.album, self.decided)
        # An emptied folder put in its place would go to a bare rmdir: it must stay.
        self.assertEqual(kept, lar.KEPT_ANOTHER)
        self.assertTrue(self.folder.is_dir())
        self.assertTrue(swapped[0].is_dir())


if __name__ == "__main__":  # pragma: no cover - run directly, outside the gate
    unittest.main()
