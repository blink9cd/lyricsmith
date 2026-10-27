"""A run's ``manifest.json`` that is a FIFO pipe is refused at once, never waited on.

`lyrics_run_folders.manifest_present` tests the manifest's own entry without following
it, so a FIFO under that name makes its folder a run. Opening a FIFO for reading blocks
until a writer comes, which would hang undo, check, prune and sweep; the reader must
instead open it without blocking and refuse anything that is not a regular file. Each
read here runs on a worker thread with a deadline (`fifo_fakes.within_deadline`), and a
reader still blocked at it is freed by opening the pipe's other end, so a regression
fails the test rather than hanging the suite. The album is built from silent MP3s under
the test's own directory; every title and lyric is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_judge as laj  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_records as lar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run_folders as rf  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fifo_fakes import make_pipe, within_deadline  # noqa: E402 - follows the bootstrap
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

TRACKS = (
    TrackSpec("01 A.mp3", title="The Lantern", txt="A lamp on the sill\nthe moth\n"),
    TrackSpec("02 B.mp3", title="The Weir", txt="Water over stone\nthe eel\n"),
)
FIRST, SECOND = run_fakes.RUN_ID, f"{run_fakes.RUN_ID}-2"
#: What the refusal of a manifest that is no regular file says.
_NOT_REGULAR = "not a regular file"


class ReadManifestTest(fixtures.IsolatedCase):
    """`read_manifest` refuses a FIFO manifest at once, as malformed."""

    def setUp(self) -> None:
        """An empty run folder."""
        super().setUp()
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.manifest = self.run_dir / rf.MANIFEST_NAME

    def assert_refused(self, pipe: Path) -> None:
        """Assert reading the run folder's manifest is refused, before the deadline."""
        with self.assertRaisesRegex(lm.ManifestSchemaError, _NOT_REGULAR):
            within_deadline(
                self, pipe, lambda: lyrics_manifest.read_manifest(self.run_dir)
            )

    def test_a_fifo_manifest_is_refused_at_once(self) -> None:
        make_pipe(self.manifest)
        self.assert_refused(self.manifest)

    def test_a_fifo_reached_through_a_link_inside_the_run_folder_is_refused(
        self,
    ) -> None:
        pipe = self.run_dir / "pipe"
        make_pipe(pipe)
        self.manifest.symlink_to(pipe)
        self.assert_refused(pipe)

    def test_a_folder_manifest_is_refused_as_not_a_regular_file(self) -> None:
        self.manifest.mkdir()
        with self.assertRaisesRegex(lm.ManifestSchemaError, _NOT_REGULAR):
            lyrics_manifest.read_manifest(self.run_dir)


class AcceptRecordsTest(rb.UndoCase):
    """Accept's verbatim copy of a manifest refuses a FIFO too, never waiting on it."""

    def test_a_fifo_manifest_is_an_os_error_at_once(self) -> None:
        mp3 = self.track("01.mp3", txt="A verse the album held\n")
        self.back_up(mp3)
        manifest = rf.run_folder(self.album, run_fakes.RUN_ID) / rf.MANIFEST_NAME
        manifest.unlink()
        make_pipe(manifest)
        kind = lp.Kind.UNREADABLE
        entry = laj.RunEntry(run_fakes.RUN_ID, ("", 0), kind, error="pipe")
        records = self.tmp / "records"
        records.mkdir()
        with self.assertRaisesRegex(OSError, _NOT_REGULAR):
            within_deadline(
                self,
                manifest,
                lambda: lar.write_records(records, self.album, (entry,)),
            )


class ModesFailClosedTest(cli_fakes.RealWiringCliCase):
    """Undo, check and prune on an album whose newest run's manifest is a FIFO."""

    def setUp(self) -> None:
        """Two strip runs of the album; the newer run's manifest swapped for a FIFO."""
        super().setUp()
        self.paths = fixtures.build_album(self.album, TRACKS)
        self.strip()
        self.paths[0].with_suffix(".txt").write_bytes(b"A verse put back\n")
        self.strip()
        self.manifest = rf.run_folder(self.album, SECOND) / rf.MANIFEST_NAME
        self.manifest.unlink()
        make_pipe(self.manifest)

    def strip(self) -> None:
        """Strip the album as a new run."""
        result = self.main("--album", str(self.album), "--mode", "strip")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)

    def mode(self, *extra: str) -> cli_fakes.Result:
        """Run the album tool on the album with *extra* options, within the deadline."""
        return within_deadline(
            self, self.manifest, lambda: self.main("--album", str(self.album), *extra)
        )

    def assert_refused(self, result: cli_fakes.Result) -> None:
        """Assert *result* is a could-not-run naming the newer run, with no traceback."""
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN, result.stderr)
        self.assertIn(f"run {SECOND!r}: its manifest could not be read", result.stderr)
        self.assertIn(_NOT_REGULAR, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_plain_undo_refuses_and_touches_no_track(self) -> None:
        before = rb.snapshots(self.paths)
        self.assert_refused(self.mode("--mode", "undo"))
        self.assertEqual(rb.snapshots(self.paths), before)

    def test_a_plain_check_refuses(self) -> None:
        self.assert_refused(self.mode("--mode", "check"))

    def test_prune_keeps_the_run_as_unreadable(self) -> None:
        parent = self.album / rf.RUN_FOLDER_NAME
        found = within_deadline(
            self,
            self.manifest,
            lambda: lp.scan(parent, lyrics_manifest.read_manifest),
        )
        kinds = {entry.name: entry.kind for entry in found}
        self.assertIs(kinds[SECOND], lp.Kind.UNREADABLE)
        verdicts = {v.name: v.outcome for v in lp.judge(found)}
        self.assertIs(verdicts[SECOND], lp.Outcome.KEEP_UNREADABLE)


class ReadRegularTest(fixtures.IsolatedCase):
    """`lyrics_files.read_regular`: a regular file's bytes, up to a cap; nothing else."""

    def test_a_regular_file_is_read_whole(self) -> None:
        path = self.tmp / "plain.json"
        path.write_bytes(b"{}\n")
        self.assertEqual(lyrics_files.read_regular(path), b"{}\n")

    def test_a_limit_reads_no_further(self) -> None:
        path = self.tmp / "plain.json"
        path.write_bytes(b"0123456789")
        self.assertEqual(lyrics_files.read_regular(path, 4), b"0123")

    def test_a_folder_is_refused(self) -> None:
        with self.assertRaisesRegex(lyrics_files.NotRegularFileError, _NOT_REGULAR):
            lyrics_files.read_regular(self.tmp)

    def test_a_fifo_is_refused_at_once(self) -> None:
        pipe = self.tmp / "pipe"
        make_pipe(pipe)
        with self.assertRaisesRegex(lyrics_files.NotRegularFileError, _NOT_REGULAR):
            within_deadline(self, pipe, lambda: lyrics_files.read_regular(pipe))

    def test_the_refusal_is_an_os_error(self) -> None:
        self.assertTrue(issubclass(lyrics_files.NotRegularFileError, OSError))


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
