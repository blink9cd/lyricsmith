"""A check, a compare or a dry run whose report cannot be flushed exits 3, as a run does.

A report printed to a pipe sits in the stream's buffer until it is flushed. Left to the
interpreter's own flush at exit, a closed pipe makes that flush fail after `main` has
returned, and the interpreter replaces the code with 120, which the help does not list.
So every mode flushes both streams inside `main`, where a failure is the lost-report
exit, `3`, with its one notice line. Each stream here is an in-process fake whose
flush raises the broken pipe a closed reader gives; every lyric is invented.
"""

from __future__ import annotations

import errno
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_exit  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above

#: Two tracks: the first finds synced lyrics, the second nothing.
TRACKS = (
    fixtures.TrackSpec("01 A.mp3", title="The Keeper"),
    fixtures.TrackSpec("02 B.mp3", title="The Mill"),
)
#: What the one notice line says, after the tool's name.
REPORT_LOST = f"album.py: {lyrics_exit.REPORT_NOT_WRITTEN}"
#: The entry point, run as a child by the end-to-end case.
ENTRY_POINT = _HERE.parent / "album.py"
#: How long the end-to-end child may take; it asks no provider.
CHILD_TIMEOUT_S = 60


class UnflushableStream(io.StringIO):
    """A stream that takes every write and refuses every flush, as a closed pipe does."""

    def flush(self) -> None:
        """Raise the broken pipe a closed reader gives."""
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")


class ReportFlushCase(cli_fakes.CliCase):
    """A two-track album, its first track answered."""

    def setUp(self) -> None:
        """Build the album and answer its first track."""
        super().setUp()
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        fixtures.build_album(self.album, TRACKS)

    def assert_report_lost(self, result: cli_fakes.Result) -> None:
        """*result* is the lost-report exit, said in one stderr line, no traceback."""
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertIn(REPORT_LOST, result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class StdoutFlushTest(ReportFlushCase):
    """A stdout whose flush fails ends each read-only mode with exit 3."""

    def test_a_check(self) -> None:
        self.run_album()
        result = self.main(
            "--album", str(self.album), "--mode", "check", stdout=UnflushableStream()
        )
        self.assert_report_lost(result)
        self.assertIn("01 A.mp3  PASS", result.stdout)

    def test_a_compare(self) -> None:
        result = self.main(
            "--album", str(self.album), "--mode", "compare", stdout=UnflushableStream()
        )
        self.assert_report_lost(result)

    def test_a_dry_run(self) -> None:
        result = self.main(
            "--album", str(self.album), "--dry-run", stdout=UnflushableStream()
        )
        self.assert_report_lost(result)
        self.assertFalse(self.folder.backed_up)


class StderrFlushTest(ReportFlushCase):
    """A stderr whose flush fails is the same exit, its notice lost with it."""

    def test_a_check(self) -> None:
        self.run_album()
        stderr = UnflushableStream()
        result = self.main("--album", str(self.album), "--mode", "check", stderr=stderr)
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)


class FlushedReportTest(ReportFlushCase):
    """Streams that flush leave each read-only mode's exit as it was."""

    def test_a_check_after_a_clean_run(self) -> None:
        self.run_album()
        result = self.main("--album", str(self.album), "--mode", "check")
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertNotIn(REPORT_LOST, result.stderr)


class ClosedPipeDryRunTest(fixtures.IsolatedCase):
    """The entry point as a child, its dry-run plan sent to a pipe with no reader."""

    def test_the_plan_lost_is_exit_3_not_the_interpreter_s_120(self) -> None:
        album_dir = self.tmp / "album"
        fixtures.build_album(album_dir, TRACKS[:1])
        empty = self.tmp / "empty-path"
        empty.mkdir()
        log = self.tmp / "stderr.log"
        reader, writer = os.pipe()
        os.close(reader)
        argv = [sys.executable, str(ENTRY_POINT), "--album", str(album_dir)]
        try:
            with log.open("wb") as sink:
                # Not a captured pipe: nothing is read back once the timeout fires.
                child = subprocess.run(  # reliability: ignore[RL005] both streams are sinks, a readerless pipe and a file; nothing is read back
                    [*argv, "--dry-run", "--offline"],
                    stdin=subprocess.DEVNULL,
                    env={**os.environ, "PATH": str(empty)},
                    stdout=writer,
                    stderr=sink,
                    timeout=CHILD_TIMEOUT_S,
                    check=False,
                )
        finally:
            os.close(writer)
        stderr = log.read_text(encoding="utf-8")
        self.assertEqual(child.returncode, album.EXIT_RECORD_NOT_WRITTEN, stderr)
        self.assertIn(REPORT_LOST, stderr)
        self.assertNotIn("Exception ignored", stderr)


if __name__ == "__main__":  # pragma: no cover - run directly; the gate uses test_album
    unittest.main()
