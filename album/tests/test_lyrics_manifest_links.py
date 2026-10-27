"""One rule for a run's ``manifest.json`` that is a link: it is present, read and refused.

`lyrics_run_folders.manifest_present` tests the manifest's own entry without following
it, so a link there, dangling or leading out of the run folder, makes its folder a run
for every listing (`run_ids`, `select_run`, `newer_runs`, `runs_newest_first`) and for
prune alike. Reading it then fails closed through the containment check or the read's
own error: undo and check refuse to run, naming the run and the way to another, and an
undo of an older run warns that a newer run's changes were overwritten. Such a run is
never hidden, so a plain undo cannot pass over it to an older run. The album is built
from silent MP3s under the test's own directory; every title and lyric is invented.
"""

from __future__ import annotations

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
import lyrics_run_folders as rf  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

TRACKS = (
    TrackSpec("01 A.mp3", title="The Lantern", txt="A lamp on the sill\nthe moth\n"),
    TrackSpec("02 B.mp3", title="The Weir", txt="Water over stone\nthe eel\n"),
)
FIRST, SECOND = run_fakes.RUN_ID, f"{run_fakes.RUN_ID}-2"
#: The undo warning given when a newer run's manifest cannot be read.
GENERAL = (
    f"warning: run {FIRST} is not the newest; a newer run's changes to the same "
    "tracks were overwritten"
)
#: What the containment check says of a manifest linked out of its run folder.
OUTSIDE = "outside the run folder"


def _deny(case: unittest.TestCase, path: Path) -> None:
    """Remove all access to *path* until *case* ends; skip it as root."""
    # `geteuid` is POSIX-only; elsewhere a denied mode is not honored either.
    if getattr(os, "geteuid", lambda: 0)() == 0:
        case.skipTest("root, or a host with no POSIX modes, reads past a denied mode")
    path.chmod(0o000)
    case.addCleanup(path.chmod, 0o700)


class ManifestPresentTest(fixtures.IsolatedCase):
    """The predicate: the manifest's own entry, tested without following it."""

    def setUp(self) -> None:
        """An empty run folder."""
        super().setUp()
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.manifest = self.run_dir / rf.MANIFEST_NAME

    def test_a_file_is_present(self) -> None:
        self.manifest.write_text("{}", encoding="utf-8", newline="")
        self.assertTrue(rf.manifest_present(self.run_dir))

    def test_no_entry_is_absent(self) -> None:
        self.assertFalse(rf.manifest_present(self.run_dir))

    def test_a_dangling_link_is_present(self) -> None:
        self.manifest.symlink_to(self.tmp / "missing.json")
        self.assertTrue(rf.manifest_present(self.run_dir))

    def test_a_link_out_of_the_run_folder_is_present(self) -> None:
        outside = self.tmp / "outside.json"
        outside.write_text("{}", encoding="utf-8", newline="")
        self.manifest.symlink_to(outside)
        self.assertTrue(rf.manifest_present(self.run_dir))

    def test_a_folder_that_cannot_be_searched_raises(self) -> None:
        _deny(self, self.run_dir)
        with self.assertRaises(PermissionError):
            rf.manifest_present(self.run_dir)


class ListingTest(fixtures.IsolatedCase):
    """Every listing takes a folder whose manifest is a link, or cannot be told, as a run."""

    def setUp(self) -> None:
        """An album with an older run holding a real manifest."""
        super().setUp()
        self.album = self.tmp / "album"
        self.old = rf.run_folder(self.album, FIRST)
        self.old.mkdir(parents=True)
        (self.old / rf.MANIFEST_NAME).write_text("{}", encoding="utf-8", newline="")
        self.new = rf.run_folder(self.album, SECOND)
        self.new.mkdir()

    def assert_newer_run_listed(self) -> None:
        """Assert every listing takes the newer folder for the newest run."""
        self.assertEqual(rf.run_ids(self.album), frozenset({FIRST, SECOND}))
        self.assertEqual(rf.select_run(self.album, None), self.new)
        self.assertEqual(rf.select_run(self.album, SECOND), self.new)
        self.assertEqual(rf.newer_runs(self.album, FIRST), (SECOND,))
        self.assertEqual(rf.runs_newest_first(self.album), (self.new, self.old))

    def test_a_dangling_manifest_link_is_a_run(self) -> None:
        (self.new / rf.MANIFEST_NAME).symlink_to(self.tmp / "missing.json")
        self.assert_newer_run_listed()

    def test_a_manifest_linked_out_of_the_run_folder_is_a_run(self) -> None:
        outside = self.tmp / "outside.json"
        outside.write_text("{}", encoding="utf-8", newline="")
        (self.new / rf.MANIFEST_NAME).symlink_to(outside)
        self.assert_newer_run_listed()

    def test_a_folder_whose_manifest_cannot_be_told_is_a_run(self) -> None:
        refused = PermissionError("not open to search")
        with mock.patch.object(rf, "manifest_present", side_effect=refused):
            self.assert_newer_run_listed()

    def test_a_folder_that_cannot_be_searched_is_a_run(self) -> None:
        _deny(self, self.new)
        self.assertEqual(rf.select_run(self.album, None), self.new)

    def test_a_plain_file_under_a_run_id_is_no_run(self) -> None:
        self.new.rmdir()
        self.new.write_text("not a run\n", encoding="utf-8", newline="")
        self.assertEqual(rf.run_ids(self.album), frozenset({FIRST}))


class ModesFailClosedTest(cli_fakes.RealWiringCliCase):
    """Undo and check on a run whose manifest is a link refuse, naming why."""

    def setUp(self) -> None:
        """Two strip runs of the album on the real wiring, with a fixed clock."""
        super().setUp()
        self.paths = fixtures.build_album(self.album, TRACKS)
        self.strip()
        self.paths[0].with_suffix(".txt").write_bytes(b"A verse put back\n")
        self.strip()
        self.manifest = rf.run_folder(self.album, SECOND) / rf.MANIFEST_NAME

    def strip(self) -> None:
        """Strip the album as a new run."""
        result = self.main("--album", str(self.album), "--mode", "strip")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)

    def dangle(self) -> None:
        """Make the newer run's manifest a link to a name that does not exist."""
        self.manifest.unlink()
        self.manifest.symlink_to(self.manifest.parent / "missing.json")

    def link_out(self) -> None:
        """Move the newer run's manifest out of its run folder; link to it there."""
        outside = self.tmp / "outside.json"
        shutil.move(self.manifest, outside)
        self.manifest.symlink_to(outside)

    def mode(self, *extra: str) -> cli_fakes.Result:
        """Run the album tool on the album with *extra* options."""
        return self.main("--album", str(self.album), *extra)

    def assert_refused(self, result: cli_fakes.Result, reason: str) -> None:
        """Assert *result* is a could-not-run naming *reason*, with no traceback."""
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN, result.stderr)
        self.assertIn(reason, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_plain_undo_never_passes_a_dangling_manifest_for_an_older_run(
        self,
    ) -> None:
        self.dangle()
        before = rb.snapshots(self.paths)
        self.assert_refused(self.mode("--mode", "undo"), "missing.json")
        self.assertEqual(rb.snapshots(self.paths), before)

    def test_a_plain_undo_refuses_a_manifest_linked_out(self) -> None:
        self.link_out()
        self.assert_refused(self.mode("--mode", "undo"), OUTSIDE)

    def test_a_check_refuses_a_dangling_manifest(self) -> None:
        self.dangle()
        self.assert_refused(self.mode("--mode", "check"), "missing.json")

    def test_a_check_by_id_refuses_a_manifest_linked_out(self) -> None:
        self.link_out()
        self.assert_refused(self.mode("--mode", "check", "--run-id", SECOND), OUTSIDE)

    def test_an_undo_of_the_older_run_warns_of_a_newer_dangling_manifest(
        self,
    ) -> None:
        self.dangle()
        result = self.mode("--mode", "undo", "--run-id", FIRST)
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        warnings = [x for x in result.stderr.splitlines() if x.startswith("warning:")]
        self.assertEqual(warnings, [GENERAL])

    def test_the_lines_say_which_run_was_refused_and_the_way_forward(self) -> None:
        self.dangle()
        result = self.mode("--mode", "undo")
        self.assert_refused(result, f"run {SECOND!r}: its manifest could not be read")
        self.assertIn("--run-id <older id>", result.stderr)
        self.assertIn("mend or remove its run folder", result.stderr)

    def test_a_manifest_that_is_a_folder_is_a_run_refused(self) -> None:
        self.manifest.unlink()
        self.manifest.mkdir()
        before = rb.snapshots(self.paths)
        result = self.mode("--mode", "undo")
        self.assert_refused(result, f"run {SECOND!r}: its manifest could not be read")
        self.assertEqual(rb.snapshots(self.paths), before)

    def test_a_check_names_the_run_whose_manifest_leads_out(self) -> None:
        self.link_out()
        result = self.mode("--mode", "check")
        self.assert_refused(result, f"run {SECOND!r}: its manifest could not be read")
        self.assertIn("--run-id <older id>", result.stderr)


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
