"""Runs in .lyricsmith, read as one list, newest by run id.

Each album is a folder under the test's own temporary directory, and each run in it is
a run-id folder holding a placeholder manifest: these cases read only which folders
exist, never what a manifest says.
"""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run_folders as rf  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_album import RunFolderLinkError  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_model import NoRunError  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_records import MANIFEST_NAME  # noqa: E402 - import follows the sys.path bootstrap above

OLD = "20261001T000000Z"
NEW = "20261002T000000Z"


def _run(album: Path, folder: str, run_id: str) -> Path:
    """A run *run_id* in *album*'s run folder *folder*, holding a placeholder manifest."""
    path = album / folder / run_id
    path.mkdir(parents=True)
    (path / MANIFEST_NAME).write_text("{}", encoding="utf-8", newline="\n")
    return path


class RunFoldersTest(fixtures.IsolatedCase):
    """The newest run, a named run, and a new run's id, in the run folder."""

    def setUp(self) -> None:
        """An empty album folder for this test."""
        super().setUp()
        self.album = self.tmp / "album"
        self.album.mkdir()

    def test_the_name_is_lyricsmith(self) -> None:
        self.assertEqual(rf.RUN_FOLDER_NAME, ".lyricsmith")
        self.assertEqual(lyrics_records.RUN_FOLDER_NAME, rf.RUN_FOLDER_NAME)

    def test_the_newest_run_is_found(self) -> None:
        _run(self.album, rf.RUN_FOLDER_NAME, OLD)
        new = _run(self.album, rf.RUN_FOLDER_NAME, NEW)
        self.assertEqual(rf.select_run(self.album, None), new)

    def test_a_named_run_is_selected_where_it_lives(self) -> None:
        old = _run(self.album, rf.RUN_FOLDER_NAME, OLD)
        _run(self.album, rf.RUN_FOLDER_NAME, NEW)
        self.assertEqual(rf.select_run(self.album, OLD), old)

    def test_an_album_with_no_run_folder_has_no_run(self) -> None:
        with self.assertRaises(NoRunError):
            rf.select_run(self.album, None)
        with self.assertRaises(NoRunError):
            rf.select_run(self.album, OLD)

    def test_runs_newest_first(self) -> None:
        old = _run(self.album, rf.RUN_FOLDER_NAME, OLD)
        new = _run(self.album, rf.RUN_FOLDER_NAME, NEW)
        self.assertEqual(rf.runs_newest_first(self.album), (new, old))

    def test_newer_runs_are_oldest_first(self) -> None:
        _run(self.album, rf.RUN_FOLDER_NAME, OLD)
        _run(self.album, rf.RUN_FOLDER_NAME, NEW)
        _run(self.album, rf.RUN_FOLDER_NAME, f"{OLD}-2")
        self.assertEqual(rf.newer_runs(self.album, OLD), (f"{OLD}-2", NEW))

    def test_a_run_not_yet_made_is_placed_under_lyricsmith(self) -> None:
        self.assertEqual(
            rf.run_folder(self.album, NEW), self.album / ".lyricsmith" / NEW
        )
        self.assertEqual(
            lyrics_records.run_folder(self.album, NEW), self.album / ".lyricsmith" / NEW
        )

    def test_the_run_parent_is_the_folder_when_it_exists(self) -> None:
        self.assertIsNone(rf.run_parent(self.album))
        _run(self.album, rf.RUN_FOLDER_NAME, NEW)
        self.assertEqual(rf.run_parent(self.album), self.album / ".lyricsmith")

    def test_a_linked_run_folder_is_no_run_parent(self) -> None:
        elsewhere = self.tmp / "elsewhere"
        _run(elsewhere, rf.RUN_FOLDER_NAME, OLD)
        (self.album / rf.RUN_FOLDER_NAME).symlink_to(elsewhere / rf.RUN_FOLDER_NAME)
        self.assertIsNone(rf.run_parent(self.album))
        self.assertEqual(rf.runs_newest_first(self.album), ())

    def test_a_link_under_a_run_id_is_no_run(self) -> None:
        old = _run(self.album, rf.RUN_FOLDER_NAME, OLD)
        outside = _run(self.tmp, "outside", NEW)
        (self.album / rf.RUN_FOLDER_NAME / NEW).symlink_to(
            outside, target_is_directory=True
        )
        self.assertEqual(rf.select_run(self.album, None), old)
        self.assertEqual(rf.runs_newest_first(self.album), (old,))
        self.assertEqual(rf.newer_runs(self.album, OLD), ())
        with self.assertRaises(NoRunError):
            rf.select_run(self.album, NEW)

    def test_run_ids_name_each_run_and_nothing_else(self) -> None:
        _run(self.album, rf.RUN_FOLDER_NAME, OLD)
        _run(self.album, rf.RUN_FOLDER_NAME, f"{OLD}-2")
        (self.album / rf.RUN_FOLDER_NAME / NEW).mkdir()
        _run(self.album, rf.RUN_FOLDER_NAME, "not-a-run-id")
        outside = _run(self.tmp, "outside", "20261003T000000Z")
        (self.album / rf.RUN_FOLDER_NAME / outside.name).symlink_to(
            outside, target_is_directory=True
        )
        self.assertEqual(rf.run_ids(self.album), frozenset({OLD, f"{OLD}-2"}))

    def test_run_ids_of_an_album_with_no_or_a_linked_run_folder_are_none(
        self,
    ) -> None:
        self.assertEqual(rf.run_ids(self.album), frozenset())
        elsewhere = self.tmp / "elsewhere"
        _run(elsewhere, rf.RUN_FOLDER_NAME, OLD)
        (self.album / rf.RUN_FOLDER_NAME).symlink_to(elsewhere / rf.RUN_FOLDER_NAME)
        self.assertEqual(rf.run_ids(self.album), frozenset())

    def test_a_folder_without_a_manifest_is_no_run(self) -> None:
        old = _run(self.album, rf.RUN_FOLDER_NAME, OLD)
        (self.album / rf.RUN_FOLDER_NAME / NEW).mkdir()
        self.assertEqual(rf.select_run(self.album, None), old)

    def test_newer_runs_of_an_unknown_id_are_none(self) -> None:
        _run(self.album, rf.RUN_FOLDER_NAME, OLD)
        self.assertEqual(rf.newer_runs(self.album, NEW), ())

    def test_a_new_run_is_written_under_lyricsmith(self) -> None:
        made = lyrics_backup.create_run_dir(
            self.album, datetime(2026, 10, 3, tzinfo=UTC)
        )
        self.assertEqual(made.parent, self.album / ".lyricsmith")

    def test_a_new_run_never_reuses_an_id_taken(self) -> None:
        _run(self.album, rf.RUN_FOLDER_NAME, OLD)
        started = datetime(2026, 10, 1, tzinfo=UTC)
        made = lyrics_backup.create_run_dir(self.album, started)
        self.assertEqual(made, self.album / ".lyricsmith" / f"{OLD}-2")

    def test_a_link_at_the_run_folder_is_refused(self) -> None:
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (self.album / rf.RUN_FOLDER_NAME).symlink_to(elsewhere)
        with self.assertRaises(RunFolderLinkError):
            rf.select_run(self.album, None)
        with self.assertRaises(RunFolderLinkError):
            lyrics_backup.create_run_dir(self.album, datetime(2026, 10, 3, tzinfo=UTC))
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_link_at_the_lock_is_refused(self) -> None:
        (self.album / rf.RUN_FOLDER_NAME).mkdir()
        (self.album / rf.RUN_FOLDER_NAME / rf.LOCK_NAME).symlink_to(self.tmp / "x")
        with self.assertRaises(RunFolderLinkError):
            rf.select_run(self.album, None)


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
