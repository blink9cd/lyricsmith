"""`--mode prune` from the command line: its options, report, exit codes and delete.

Each test drives `album.main` on the real wiring over an album built under the
test's own folder; every lyric is invented.
"""

from __future__ import annotations

import io
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above


class PruneModeTest(cli_fakes.RealWiringCliCase):
    """A real run and undo of a one-track album, then prune from the command line."""

    def setUp(self) -> None:
        """A one-track album run and undone, so its one run folder can go."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))
        self.assertEqual(self.run_album().code, album.EXIT_OK)
        self.assertEqual(
            self.main("--album", str(self.album), "--mode", "undo").code,
            album.EXIT_OK,
        )

    def prune(self, *extra: str, stdout: io.StringIO | None = None) -> cli_fakes.Result:
        """Run prune on this test's album with *extra* options, *stdout* if given."""
        return self.main(
            "--album", str(self.album), "--mode", "prune", *extra, stdout=stdout
        )

    def runs_left(self) -> list[str]:
        """The entries of `.lyricsmith` other than the lock."""
        parent = self.album / lr.RUN_FOLDER_NAME
        return sorted(p.name for p in parent.iterdir() if p.name != "lock")

    def test_it_removes_the_undone_run_and_reports_it(self) -> None:
        result = self.prune()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.runs_left(), [])
        self.assertIn("album prune: 1 run folder under album", result.stdout)
        self.assertIn("removed: undone", result.stdout)
        self.assertTrue(result.stdout.endswith("removed 1 · kept 0 · errors 0\n"))

    def test_a_dry_run_says_would_remove_and_keeps_the_folder(self) -> None:
        result = self.prune("--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(len(self.runs_left()), 1)
        self.assertIn("would remove: undone", result.stdout)
        self.assertTrue(
            result.stdout.endswith(
                "would remove 1 · kept 0 · errors 0\ndry run: nothing was removed\n"
            )
        )

    def test_a_failed_removal_is_exit_1(self) -> None:
        with mock.patch.object(shutil, "rmtree", side_effect=OSError("invented")):
            result = self.prune()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn("errors 1", result.stdout)
        self.assertIn("ERROR: invented", result.stdout)

    def test_a_kept_folder_shows_its_reason(self) -> None:
        # A second, newer run that is not undone keeps itself and, by the newest-run
        # rule, shows the kept outcome with its state.
        self.assertEqual(self.run_album("--fresh").code, album.EXIT_OK)
        result = self.prune()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertIn("kept: not undone", result.stdout)

    def test_an_unreadable_manifest_row_shows_its_detail(self) -> None:
        run = self.album / lr.RUN_FOLDER_NAME / self.runs_left()[0]
        (run / lr.MANIFEST_NAME).write_text("not json", encoding="utf-8", newline="\n")
        result = self.prune()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertIn("kept: unreadable manifest (", result.stdout)

    def test_an_album_with_no_runs_says_so(self) -> None:
        shutil.rmtree(self.album / lr.RUN_FOLDER_NAME)
        result = self.prune()
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertIn("album prune: no run folders under album", result.stdout)

    def test_an_rmtree_that_can_follow_a_link_prunes_through_the_walk(self) -> None:
        # Every Windows Python's: the walk deletes instead, so prune still runs.
        with mock.patch.object(shutil.rmtree, "avoids_symlink_attacks", False):
            result = self.prune()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.runs_left(), [])
        self.assertIn("removed: undone", result.stdout)

    def test_the_walk_removes_a_link_in_a_run_as_the_link(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_bytes(b"not the album's\n")
        run = self.album / lr.RUN_FOLDER_NAME / self.runs_left()[0]
        (run / "out").symlink_to(outside, target_is_directory=True)
        with mock.patch.object(shutil.rmtree, "avoids_symlink_attacks", False):
            result = self.prune()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.runs_left(), [])
        self.assertEqual((outside / "keep.txt").read_bytes(), b"not the album's\n")

    def test_a_report_that_cannot_be_written_is_exit_3(self) -> None:
        class Closed(io.StringIO):
            def write(self, text: str) -> int:
                raise OSError("invented closed stdout")

        result = self.prune(stdout=Closed())
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)

    def test_every_option_prune_has_no_use_for_is_refused(self) -> None:
        for extra in (
            ("--run-id", "20261001T000000Z"),
            ("--track", str(self.album / "01 Song.mp3")),
            ("--changed-list", str(self.tmp / "list.txt")),
            ("--lang", "eng"),
            ("--config", str(self.tmp / "c.toml")),
            ("--offline",),
            ("--no-genius",),
            ("--no-netease",),
            ("--no-bandcamp",),
            ("--no-youtube-music",),
            ("--no-tekstowo",),
            ("--fresh",),
            ("--refetch-untimed",),
        ):
            with self.subTest(extra=extra):
                result = self.prune(*extra)
                self.assertEqual(result.code, 2)
                self.assertIn("cannot be used with --mode prune", result.stderr)


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
