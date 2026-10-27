"""`--mode accept` from the command line: its options, refusals, report and exit codes.

Each test drives `album.main` on the real wiring over an album built under the test's
own folder, with HOME and the XDG folders redirected there; every lyric is invented.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
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
import lyrics_accept_judge as laj  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_mode  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_lock  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

_HINT = "report only: nothing was removed; add --apply to remove it"
_NO_RUN = "no run under"


def tree_hashes(root: Path) -> dict[str, str]:
    """Every file under *root*, ``.lyricsmith`` included, by path, to its sha256."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


class AcceptCliCase(cli_fakes.RealWiringCliCase):
    """A real run of a one-track album, then accept from the command line."""

    def setUp(self) -> None:
        """A one-track album holding a sidecar, run once."""
        super().setUp()
        fixtures.build_album(
            self.album, (TrackSpec("01 Song.mp3", txt="An invented verse\n"),)
        )
        self.assertEqual(self.run_album().code, album.EXIT_OK)
        self.folder_path = self.album / lr.RUN_FOLDER_NAME
        (self.run_id,) = [
            p.name for p in self.folder_path.iterdir() if p.name != "lock"
        ]

    def accept(
        self, *extra: str, stdout: io.StringIO | None = None
    ) -> cli_fakes.Result:
        """Run accept on this test's album with *extra* options, *stdout* if given."""
        return self.main(
            "--album", str(self.album), "--mode", "accept", *extra, stdout=stdout
        )

    def records(self) -> list[Path]:
        """Each records folder under the redirected state folder."""
        root = self.xdg["XDG_STATE_HOME"] / "lyricsmith" / "accepted"
        return sorted(root.iterdir()) if root.is_dir() else []

    def assert_refused_untouched(self, *expected: str) -> None:
        """Accept --apply exits 2 naming *expected*, changing no byte, recording nothing."""
        before = tree_hashes(self.album)
        result = self.accept("--apply")
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN, result.stdout)
        for text in expected:
            self.assertIn(text, result.stdout + result.stderr)
        self.assertEqual(tree_hashes(self.album), before)
        self.assertEqual(self.records(), [])


class ReportAndApplyTest(AcceptCliCase):
    """The report-only pass changes nothing; --apply removes `.lyricsmith` alone."""

    def test_the_report_only_pass_changes_no_byte(self) -> None:
        before = tree_hashes(self.album)
        result = self.accept()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(tree_hashes(self.album), before)
        self.assertEqual(self.records(), [])
        self.assertIn("album accept: 1 run folder under album", result.stdout)
        self.assertTrue(result.stdout.endswith(f"{_HINT}\n"))

    def test_apply_removes_the_run_folder_and_records_each_manifest(self) -> None:
        before = tree_hashes(self.album)
        manifest = (self.folder_path / self.run_id / lr.MANIFEST_NAME).read_bytes()
        result = self.accept("--apply")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertFalse(os.path.lexists(self.folder_path))
        kept = {k: v for k, v in before.items() if not k.startswith(lr.RUN_FOLDER_NAME)}
        self.assertEqual(tree_hashes(self.album), kept)
        (folder,) = self.records()
        self.assertEqual(
            sorted(p.name for p in folder.iterdir()),
            sorted(["album.txt", f"{self.run_id}.json"]),
        )
        self.assertEqual((folder / f"{self.run_id}.json").read_bytes(), manifest)
        self.assertIn(f"records: {folder}", result.stdout)
        self.assertIn("removed 1 · at risk", result.stdout)

    def test_afterwards_undo_and_check_say_the_album_has_no_run(self) -> None:
        self.assertEqual(self.accept("--apply").code, album.EXIT_OK)
        for mode in ("undo", "check"):
            with self.subTest(mode=mode):
                result = self.main("--album", str(self.album), "--mode", mode)
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn(_NO_RUN, result.stderr)

    def test_an_album_with_no_run_says_so(self) -> None:
        shutil.rmtree(self.folder_path)
        result = self.accept("--apply")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(result.stdout, "album accept: album has no run\n")
        self.assertFalse(os.path.lexists(self.folder_path))

    def test_a_failed_removal_is_exit_1_and_the_next_accept_finishes_it(self) -> None:
        with mock.patch.object(shutil, "rmtree", side_effect=OSError("invented")):
            result = self.accept("--apply")
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stderr)
        self.assertIn("ERROR: invented", result.stdout)
        self.assertTrue((self.album / laj.ACCEPTING_NAME).is_dir())
        again = self.accept("--apply")
        self.assertEqual(again.code, album.EXIT_OK, again.stderr)
        self.assertIn("left by an earlier accept", again.stdout)
        self.assertFalse(os.path.lexists(self.album / laj.ACCEPTING_NAME))

    def test_records_that_cannot_be_written_are_exit_2(self) -> None:
        state = self.xdg["XDG_STATE_HOME"] / "lyricsmith"
        state.mkdir(parents=True, exist_ok=True)
        (state / "accepted").write_text("in the way", encoding="utf-8", newline="\n")
        before = tree_hashes(self.album)
        result = self.accept("--apply")
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN, result.stderr)
        self.assertIn("ERROR: the records could not be written", result.stdout)
        self.assertNotIn("partial records", result.stdout)
        self.assertEqual(tree_hashes(self.album), before)

    def test_a_write_failure_names_the_partial_records_folder(self) -> None:
        before = tree_hashes(self.album)
        failing = mock.patch.object(
            lyrics_accept_mode, "write_records", side_effect=OSError("invented")
        )
        with failing:
            result = self.accept("--apply")
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN, result.stderr)
        (folder,) = self.records()
        self.assertIn(f"partial records: {folder}", result.stdout)
        self.assertEqual(tree_hashes(self.album), before)

    def test_a_report_that_cannot_be_written_is_exit_3(self) -> None:
        class Closed(io.StringIO):
            def write(self, text: str) -> int:
                raise OSError("invented closed stdout")

        result = self.accept(stdout=Closed())
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)


class RefusalTest(AcceptCliCase):
    """Each refusal exits 2 and touches nothing."""

    def test_an_unfinished_run(self) -> None:
        path = self.folder_path / self.run_id / lr.MANIFEST_NAME
        data = json.loads(path.read_text(encoding="utf-8"))
        data["state"] = lr.RunState.BACKED_UP.value
        path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
        self.assert_refused_untouched("refused: unfinished run", "undo it")

    def test_an_unreadable_manifest(self) -> None:
        path = self.folder_path / self.run_id / lr.MANIFEST_NAME
        path.write_text("not json", encoding="utf-8", newline="\n")
        self.assert_refused_untouched("refused: unreadable manifest")

    def test_an_unknown_entry(self) -> None:
        (self.folder_path / "notes.txt").write_text(
            "invented", encoding="utf-8", newline="\n"
        )
        self.assert_refused_untouched("refused: unknown entry: notes.txt")

    def test_a_linked_run_folder(self) -> None:
        moved = self.tmp / "elsewhere"
        self.folder_path.rename(moved)
        self.folder_path.symlink_to(moved)
        self.assert_refused_untouched("link")
        self.assertTrue(self.folder_path.is_symlink())

    def test_the_lock_held(self) -> None:
        lock = run_lock.acquire(self.folder_path / "lock")
        assert lock is not None
        self.addCleanup(lock.release)
        self.assert_refused_untouched("held by another run")

    def test_an_unsafe_rmtree(self) -> None:
        with mock.patch.object(shutil.rmtree, "avoids_symlink_attacks", False):
            self.assert_refused_untouched("so accept will not run")


class OptionsTest(AcceptCliCase):
    """--apply belongs to accept alone; accept refuses what names a run or a track."""

    def test_apply_is_refused_in_every_other_mode(self) -> None:
        for mode in ("run", "check", "undo", "strip", "compare", "prune"):
            with self.subTest(mode=mode):
                result = self.main(
                    "--album", str(self.album), "--mode", mode, "--apply"
                )
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"--apply cannot be used with --mode {mode}", result.stderr
                )

    def test_every_option_accept_has_no_use_for_is_refused(self) -> None:
        for extra in (
            ("--run-id", self.run_id),
            ("--track", str(self.album / "01 Song.mp3")),
            ("--changed-list", str(self.tmp / "list.txt")),
            ("--dry-run",),
            ("--lang", "eng"),
            ("--config", str(self.tmp / "c.toml")),
            ("--offline",),
            ("--no-genius",),
            ("--no-netease",),
            ("--no-bandcamp",),
            ("--no-youtube-music",),
            ("--no-tekstowo",),
            ("--only-providers", "lrclib"),
            ("--fresh",),
            ("--refetch-untimed",),
            ("--refetch-implausible",),
        ):
            with self.subTest(extra=extra):
                result = self.accept(*extra)
                self.assertEqual(result.code, 2)
                self.assertIn("cannot be used with --mode accept", result.stderr)


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
