"""Prune over real run folders: what it reads, what it removes, and what it never touches.

Each album and run folder is built under the test's own folder with the real backup and
undo; every lyric is invented.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_undo  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_stages import ReadManifest  # noqa: E402 - import follows the sys.path bootstrap above

_EDITED = b"The run's verse, edited after the run\n"


def later(minutes: int) -> dt.datetime:
    """A start *minutes* after the fixed clock's, for a second or third run."""
    return run_fakes.STARTED + dt.timedelta(minutes=minutes)


def tree(folder: Path) -> dict[str, str]:
    """Every file under *folder*, by relative path, as its SHA-256."""
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


class PruneCase(rb.UndoCase):
    """An album with real runs; prune it with the real lock unless a test swaps it."""

    def setUp(self) -> None:
        """The lock this test's prunes take, recorded."""
        super().setUp()
        self.locked: list[Path] = []
        self.mp3 = self.track("01.mp3", txt="A verse the album held\n")

    def acquire(self, path: Path) -> lyrics_album.HeldLock | None:
        """Record the lock path, and take the real album lock on it."""
        self.locked.append(path)
        return run_fakes.acquire(path)

    def made_run(self, minutes: int = 0) -> str:
        """A real run of the one track, started *minutes* after the fixed start.

        Each run writes verse text of its own: a sidecar already holding a run's lyrics
        would make the stand-in run write nothing.
        """
        verse = lyrics_model.PlainLyrics(f"Verse {minutes} the run wrote\n")
        manifest = rb.simulate_run(
            self.album, self.back_up(self.mp3, started=later(minutes)), verse
        )
        return manifest.run_id

    def prune(
        self,
        *,
        dry_run: bool = False,
        read: ReadManifest = lyrics_manifest.read_manifest,
        delete: lp.Delete | None = None,
    ) -> lp.PruneReport:
        """Prune this album through `prune_album`."""
        return lp.prune_album(
            self.album,
            dry_run=dry_run,
            acquire_lock=self.acquire,
            read=read,
            delete=delete,
        )

    def outcomes(self, report: lp.PruneReport) -> dict[str, lp.Outcome]:
        """Each row's folder name and outcome."""
        return {row.verdict.name: row.verdict.outcome for row in report.rows}

    def runs_left(self) -> list[str]:
        """The run-id folders still in the new run folder."""
        parent = self.album / lr.RUN_FOLDER_NAME
        return sorted(
            p.name for p in parent.iterdir() if p.name != lyrics_album.LOCK_NAME
        )


class RemovesTest(PruneCase):
    """The folders the rules remove are gone, and only those."""

    def test_an_undone_run_is_removed_and_the_lock_stays(self) -> None:
        self.made_run()
        self.undo()
        report = self.prune()
        self.assertEqual((report.removed, report.kept), (1, 0))
        self.assertEqual(self.runs_left(), [])
        self.assertTrue(
            (self.album / lr.RUN_FOLDER_NAME / lyrics_album.LOCK_NAME).exists()
        )

    def test_a_complete_run_is_kept(self) -> None:
        run_id = self.made_run()
        report = self.prune()
        self.assertEqual(self.outcomes(report), {run_id: lp.Outcome.KEEP_NOT_UNDONE})
        self.assertEqual((report.removed, report.kept), (0, 1))
        self.assertEqual(self.runs_left(), [run_id])

    def test_a_crashed_run_still_backed_up_is_kept(self) -> None:
        run_id = self.back_up(self.mp3).run_id
        self.assertEqual(
            self.outcomes(self.prune()), {run_id: lp.Outcome.KEEP_NOT_UNDONE}
        )

    def test_an_undone_run_holding_a_displaced_sidecar_is_kept(self) -> None:
        run_id = self.made_run()
        self.mp3.with_suffix(".txt").write_bytes(_EDITED)
        self.undo()
        self.assertEqual(
            self.outcomes(self.prune()), {run_id: lp.Outcome.KEEP_DISPLACED}
        )

    def test_an_unfinished_backup_is_removed(self) -> None:
        (self.album / lr.RUN_FOLDER_NAME / "20261001T000000Z").mkdir(parents=True)
        report = self.prune()
        self.assertEqual(
            self.outcomes(report), {"20261001T000000Z": lp.Outcome.REMOVE_UNFINISHED}
        )
        self.assertEqual(self.runs_left(), [])

    def test_other_names_are_never_listed_or_touched(self) -> None:
        notes = self.album / lr.RUN_FOLDER_NAME / "notes"
        notes.mkdir(parents=True)
        self.assertEqual(self.prune().rows, ())
        self.assertTrue(notes.is_dir())


class KeepsUnreadTest(PruneCase):
    """What prune cannot read as a run it never removes."""

    def test_a_link_under_a_run_id_is_kept_and_its_target_untouched(self) -> None:
        target = self.tmp / "elsewhere"
        target.mkdir()
        parent = self.album / lr.RUN_FOLDER_NAME
        parent.mkdir()
        (parent / "20261001T000000Z").symlink_to(target)
        self.assertEqual(
            self.outcomes(self.prune()),
            {"20261001T000000Z": lp.Outcome.KEEP_NOT_FOLDER},
        )
        self.assertTrue(target.is_dir())

    def test_a_malformed_manifest_is_kept_with_its_reason(self) -> None:
        run_id = self.made_run()
        self.undo()
        manifest = lr.run_folder(self.album, run_id) / lr.MANIFEST_NAME
        manifest.write_text("{", encoding="utf-8", newline="")
        row = self.prune().rows[0]
        self.assertIs(row.verdict.outcome, lp.Outcome.KEEP_UNREADABLE)
        self.assertIn("malformed manifest", row.verdict.detail or "")

    def test_a_dangling_manifest_link_is_read_refused_and_kept(self) -> None:
        folder = self.album / lr.RUN_FOLDER_NAME / "20261001T000000Z"
        folder.mkdir(parents=True)
        (folder / lr.MANIFEST_NAME).symlink_to(self.tmp / "missing.json")
        self.assertIs(self.prune().rows[0].verdict.outcome, lp.Outcome.KEEP_UNREADABLE)

    def test_an_undo_entry_that_is_not_a_plain_folder_counts_as_displaced(self) -> None:
        run_id = self.made_run()
        self.undo()
        (lr.run_folder(self.album, run_id) / "undo").write_text(
            "x", encoding="utf-8", newline=""
        )
        self.assertEqual(
            self.outcomes(self.prune()), {run_id: lp.Outcome.KEEP_DISPLACED}
        )

    def test_a_plain_file_under_a_run_id_is_kept_and_left_in_place(self) -> None:
        parent = self.album / lr.RUN_FOLDER_NAME
        parent.mkdir()
        entry = parent / "20261001T000000Z"
        entry.write_text("not a run\n", encoding="utf-8", newline="")
        self.assertEqual(
            self.outcomes(self.prune()),
            {entry.name: lp.Outcome.KEEP_NOT_FOLDER},
        )
        self.assertTrue(entry.is_file())

    def test_a_manifest_past_the_size_cap_is_kept_unread(self) -> None:
        run_id = self.made_run()
        self.undo()
        folder = lr.run_folder(self.album, run_id)
        (folder / lr.MANIFEST_NAME).write_bytes(
            b" " * (lyrics_manifest.MAX_MANIFEST_BYTES + 1)
        )
        row = self.prune().rows[0]
        self.assertIs(row.verdict.outcome, lp.Outcome.KEEP_UNREADABLE)
        self.assertIn("too large", row.verdict.detail or "")
        self.assertTrue(folder.is_dir())


class DeniedTest(PruneCase):
    """A folder prune is not allowed to read is kept, never taken for empty."""

    def deny(self, path: Path) -> None:
        """Remove all access to *path* until the test ends; skip as root."""
        # `geteuid` is POSIX-only; elsewhere a denied mode is not honored either.
        if getattr(os, "geteuid", lambda: 0)() == 0:
            self.skipTest(
                "root, or a host with no POSIX modes, reads past a denied mode"
            )
        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o700)

    def test_a_run_folder_that_cannot_be_searched_is_kept_not_unfinished(self) -> None:
        folder = self.album / lr.RUN_FOLDER_NAME / "20261001T000000Z"
        folder.mkdir(parents=True)
        (folder / lr.MANIFEST_NAME).write_text("{}", encoding="utf-8", newline="")
        self.deny(folder)
        row = self.prune().rows[0]
        self.assertIs(row.verdict.outcome, lp.Outcome.KEEP_UNREADABLE)
        self.assertTrue(row.verdict.detail)
        self.assertTrue(folder.is_dir())

    def test_an_undo_folder_that_cannot_be_listed_counts_as_displaced(self) -> None:
        run_id = self.made_run()
        self.undo()
        undo = lr.run_folder(self.album, run_id) / lyrics_undo.UNDO_DIR
        undo.mkdir()
        self.deny(undo)
        self.assertEqual(
            self.outcomes(self.prune()), {run_id: lp.Outcome.KEEP_DISPLACED}
        )


class UndoFolderTest(PruneCase):
    """A real ``undo`` folder: empty is nothing set aside, anything in it is."""

    def undone_run(self) -> Path:
        """A run undone, and the path its ``undo`` folder would have."""
        run_id = self.made_run()
        self.undo()
        return lr.run_folder(self.album, run_id) / lyrics_undo.UNDO_DIR

    def test_an_empty_undo_folder_holds_nothing(self) -> None:
        self.undone_run().mkdir()
        self.assertEqual(self.prune().removed, 1)

    def test_a_filled_undo_folder_keeps_the_run(self) -> None:
        undo = self.undone_run()
        undo.mkdir()
        (undo / "01.txt").write_text("set aside\n", encoding="utf-8", newline="")
        row = self.prune().rows[0]
        self.assertIs(row.verdict.outcome, lp.Outcome.KEEP_DISPLACED)


class NewestTest(PruneCase):
    """End to end: a plain undo after a prune never undoes anything."""

    def test_all_undone_removes_all_and_a_plain_undo_finds_no_run(self) -> None:
        self.made_run()
        self.undo()
        self.made_run(1)
        self.undo()
        self.prune()
        with self.assertRaises(lyrics_model.NoRunError):
            lyrics_backup.select_run(self.album, None)

    def test_an_older_kept_run_keeps_the_newest_and_a_plain_undo_does_nothing(
        self,
    ) -> None:
        kept = self.made_run()
        middle = self.made_run(1)
        self.undo(middle)
        newest = self.made_run(2)
        self.undo(newest)
        report = self.prune()
        self.assertEqual(
            self.outcomes(report),
            {
                kept: lp.Outcome.KEEP_NOT_UNDONE,
                middle: lp.Outcome.REMOVE_UNDONE,
                newest: lp.Outcome.KEEP_NEWEST,
            },
        )
        before = tree(self.album)
        self.assert_statuses(self.undo(), lr.UndoStatus.ALREADY_UNDONE)
        self.assertEqual(
            {k: v for k, v in tree(self.album).items() if k.endswith(".mp3")},
            {k: v for k, v in before.items() if k.endswith(".mp3")},
        )

    def test_an_unreadable_newest_folder_never_lets_a_plain_undo_reach_a_kept_run(
        self,
    ) -> None:
        kept = self.made_run()
        middle = self.made_run(1)
        self.undo(middle)
        # Newer than every run, with a manifest select_run skips: a dangling link.
        unreadable = self.album / lr.RUN_FOLDER_NAME / f"{middle}-2"
        unreadable.mkdir()
        (unreadable / lr.MANIFEST_NAME).symlink_to(self.tmp / "missing.json")
        report = self.prune()
        self.assertEqual(
            self.outcomes(report),
            {
                kept: lp.Outcome.KEEP_NOT_UNDONE,
                middle: lp.Outcome.KEEP_NEWEST,
                unreadable.name: lp.Outcome.KEEP_UNREADABLE,
            },
        )
        self.assertEqual(
            lyrics_backup.select_run(self.album, None),
            lr.run_folder(self.album, middle),
        )


class SafetyTest(PruneCase):
    """The lock, a dry run, an interrupted delete and a failed one."""

    def test_the_pass_holds_the_album_lock(self) -> None:
        self.made_run()
        self.prune()
        self.assertEqual(
            self.locked, [self.album / lr.RUN_FOLDER_NAME / lyrics_album.LOCK_NAME]
        )

    def test_a_held_lock_refuses_the_pass(self) -> None:
        self.made_run()
        with self.assertRaises(lyrics_album.LockHeldError):
            lp.prune_album(self.album, dry_run=False, acquire_lock=lambda path: None)

    def test_no_run_folder_is_no_rows_and_creates_nothing(self) -> None:
        self.assertEqual(self.prune().rows, ())
        self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())
        self.assertEqual(self.locked, [])

    def test_a_linked_run_folder_is_refused(self) -> None:
        target = self.tmp / "elsewhere"
        target.mkdir()
        (self.album / lr.RUN_FOLDER_NAME).symlink_to(target)
        with self.assertRaises(lyrics_album.RunFolderLinkError):
            self.prune()

    def test_a_dry_run_changes_no_byte(self) -> None:
        self.made_run()
        self.undo()
        before = tree(self.album)
        report = self.prune(dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertEqual(report.removed, 1)
        # The pass takes the lock, which creates its empty file; nothing else changes.
        after = tree(self.album)
        self.assertEqual(
            after.pop(f"{lr.RUN_FOLDER_NAME}/{lyrics_album.LOCK_NAME}", None),
            hashlib.sha256().hexdigest(),
        )
        self.assertEqual(after, before)

    def test_a_dry_run_with_the_lock_already_there_changes_no_byte(self) -> None:
        self.made_run()
        self.undo()
        self.prune(dry_run=True)
        before = tree(self.album)
        self.prune(dry_run=True)
        self.assertEqual(tree(self.album), before)

    def test_an_interrupted_delete_leaves_a_leftover_the_next_prune_removes(
        self,
    ) -> None:
        run_id = self.made_run()
        self.undo()

        def interrupted(path: Path) -> None:
            raise OSError("invented failure mid-delete")

        report = self.prune(delete=interrupted)
        self.assertEqual(report.errors, 1)
        self.assertEqual(self.runs_left(), [f"{run_id}{lp.PRUNING_SUFFIX}"])
        with self.assertRaises(lyrics_model.NoRunError):
            lyrics_backup.select_run(self.album, None)
        again = self.prune()
        self.assertEqual(
            self.outcomes(again),
            {f"{run_id}{lp.PRUNING_SUFFIX}": lp.Outcome.REMOVE_LEFTOVER},
        )
        self.assertEqual(self.runs_left(), [])

    def test_a_failed_removal_is_its_row_and_the_others_still_go(self) -> None:
        first = self.made_run()
        self.undo(first)
        second = self.made_run(1)
        self.undo(second)
        failing = f"{first}{lp.PRUNING_SUFFIX}"

        def delete(path: Path) -> None:
            if path.name == failing:
                raise OSError("invented failure")
            lp.shutil.rmtree(path)

        report = self.prune(delete=delete)
        errors = {r.verdict.name: r.error for r in report.rows if r.error}
        self.assertEqual(list(errors), [first])
        self.assertEqual(self.runs_left(), [failing])

    def test_a_leftover_goes_before_the_run_of_its_name_is_renamed(self) -> None:
        run_id = self.made_run()
        self.undo()
        leftover = self.album / lr.RUN_FOLDER_NAME / f"{run_id}{lp.PRUNING_SUFFIX}"
        (leftover / "frames").mkdir(parents=True)
        report = self.prune()
        self.assertEqual(report.errors, 0)
        self.assertEqual(self.runs_left(), [])

    def test_a_scan_reads_through_the_given_reader(self) -> None:
        run_id = self.made_run()
        self.undo()
        seen: list[Path] = []

        def read(run_dir: Path) -> lr.Manifest:
            seen.append(run_dir)
            return lyrics_manifest.read_manifest(run_dir)

        self.prune(read=read)
        self.assertEqual(seen, [lr.run_folder(self.album, run_id)])


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
