"""A run, a strip or an undo whose report cannot be written, once the work began, exits 3.

A progress line that cannot be sent after its track is recorded (a closed stderr) ends
the run there, with no summary; a summary that cannot be printed (a closed stdout) is
the same. Either is exit 3, as a record that could not be written is: the run was made,
and a caller reading the code must say so and print its undo. The one notice line is
tried, and when stderr is the stream that is gone, the exit code says it alone. A line
that fails before the backup is still exit 2: nothing has changed. A real pipe holds the
summary in its buffer until the interpreter's own flush at exit, which would replace the
code with 120, so the streams are flushed first and a failed one pointed at the null
device. Every stream in-process is a fake; the end-to-end cases run the entry point as a
child with its stdout or its stderr on a pipe that has no reader. Every lyric is invented.
"""

from __future__ import annotations

import errno
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import IO
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_exit  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above

TrackSpec = fixtures.TrackSpec

#: Three tracks: the first finds synced lyrics, the others nothing.
TRACKS = (
    TrackSpec("01 A.mp3", title="The Keeper"),
    TrackSpec("02 B.mp3", title="The Mill"),
    TrackSpec("03 C.mp3", title="The Bell"),
)
#: The first track's progress line begins so.
FIRST_PROGRESS = "[1/"
#: What the one notice line says, after the tool's name.
REPORT_LOST = f"album.py: {lyrics_exit.REPORT_NOT_WRITTEN}"
#: The entry point, run as a child by the end-to-end case.
ENTRY_POINT = _HERE.parent / "album.py"
#: How long the end-to-end child may take; it asks no provider.
CHILD_TIMEOUT_S = 60


class ClosingStream(io.StringIO):
    """A stream whose pipe closes at the first write holding *closes_on*.

    With *stays_closed*, every write after it fails too, as a closed pipe does;
    without, only that one write fails.
    """

    def __init__(self, closes_on: str, *, stays_closed: bool = True) -> None:
        """A stream that takes every write until one holds *closes_on*."""
        super().__init__()
        self.closes_on = closes_on
        self.stays_closed = stays_closed
        self.closed_pipe = False

    def write(self, text: str, /) -> int:
        """Take *text*, or raise the broken pipe once it has closed."""
        if self.closed_pipe or self.closes_on in text:
            self.closed_pipe = self.stays_closed
            raise BrokenPipeError(errno.EPIPE, "Broken pipe")
        return super().write(text)


class ReportLostCase(cli_fakes.CliCase):
    """A three-track album, its first track answered."""

    def setUp(self) -> None:
        """Build the album and answer its first track."""
        super().setUp()
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        fixtures.build_album(self.album, TRACKS)


class ProgressLineLostTest(ReportLostCase):
    """A progress line that cannot be sent after its track is recorded is exit 3."""

    def test_a_run_stops_with_its_track_recorded_and_no_summary(self) -> None:
        stderr = ClosingStream(FIRST_PROGRESS)
        result = self.main("--album", str(self.album), stderr=stderr)
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            self.folder.afters_on_disk(self.album), ["written", None, None]
        )
        self.assertFalse(run_fakes.lock_held(self.album))

    def test_a_strip_stops_the_same_way(self) -> None:
        stderr = ClosingStream(FIRST_PROGRESS)
        result = self.main("--album", str(self.album), "--mode", "strip", stderr=stderr)
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertEqual(result.stdout, "")
        self.assertFalse(run_fakes.lock_held(self.album))

    def test_one_failed_line_still_leaves_the_notice(self) -> None:
        stderr = ClosingStream(FIRST_PROGRESS, stays_closed=False)
        result = self.main("--album", str(self.album), stderr=stderr)
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertIn(REPORT_LOST, result.stderr)
        self.assertIn("Broken pipe", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_line_lost_before_the_backup_is_still_exit_2(self) -> None:
        stderr = ClosingStream("backing up", stays_closed=False)
        result = self.main("--album", str(self.album), stderr=stderr)
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertIn("album.py: [Errno 32] Broken pipe", result.stderr)
        self.assertEqual(self.folder.backed_up, [])

    def test_a_stderr_gone_before_the_backup_is_exit_2_with_nothing_raised(
        self,
    ) -> None:
        stderr = ClosingStream("backing up")
        result = self.main("--album", str(self.album), stderr=stderr)
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertEqual(self.folder.backed_up, [])


class SummaryLostTest(ReportLostCase):
    """A summary that cannot be printed, once the work is done, is exit 3."""

    def test_a_run_whose_summary_cannot_be_printed_exits_3(self) -> None:
        stdout = ClosingStream("")
        result = self.main("--album", str(self.album), stdout=stdout)
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertIn(REPORT_LOST, result.stderr)
        self.assertEqual(
            self.folder.afters_on_disk(self.album),
            ["written", "not found", "not found"],
        )

    def test_an_undo_whose_summary_cannot_be_printed_exits_3(self) -> None:
        self.main("--album", str(self.album))
        self.folder.undo_report = lr.UndoReport(
            run_fakes.RUN_ID,
            True,
            (lr.UndoTrackResult("01 A.mp3", lr.UndoStatus.UNDONE),),
        )
        stdout = ClosingStream("")
        result = self.main("--album", str(self.album), "--mode", "undo", stdout=stdout)
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertIn(REPORT_LOST, result.stderr)

    def test_both_streams_gone_is_exit_3_with_nothing_raised(self) -> None:
        stdout, stderr = ClosingStream(""), ClosingStream("could not write")
        result = self.main("--album", str(self.album), stdout=stdout, stderr=stderr)
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertNotIn(REPORT_LOST, result.stderr)

    def test_a_closing_notice_that_cannot_be_written_exits_3(self) -> None:
        def write_manifest(run_dir: Path, manifest: lr.Manifest) -> None:
            raise OSError("disk full")

        self.overrides["write_manifest"] = write_manifest
        stderr = ClosingStream("could not record", stays_closed=False)
        result = self.main("--album", str(self.album), stderr=stderr)
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertIn(REPORT_LOST, result.stderr)


class UndoWarningLostTest(cli_fakes.RealWiringCliCase):
    """An undo whose warning of a newer run cannot be written exits 3."""

    def test_the_warning_lost_is_a_lost_report(self) -> None:
        track = fixtures.build_album(
            self.album, (TrackSpec("01 A.mp3", title="A", txt="A verse\n"),)
        )[0]
        strip = ("--album", str(self.album), "--mode", "strip")
        self.assertEqual(self.main(*strip).code, album.EXIT_OK)
        track.with_suffix(".txt").write_bytes(b"A new verse\n")
        self.assertEqual(self.main(*strip).code, album.EXIT_OK)
        undo = ("--album", str(self.album), "--mode", "undo")
        stderr = ClosingStream("warning:", stays_closed=False)
        result = self.main(*undo, "--run-id", run_fakes.RUN_ID, stderr=stderr)
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertIn(REPORT_LOST, result.stderr)


class FlushedStream(io.StringIO):
    """A stream whose flush fails, on the descriptor *fd*."""

    def __init__(self, fd: int) -> None:
        """A stream on *fd* that cannot be flushed."""
        super().__init__()
        self.fd = fd

    def flush(self) -> None:
        """Fail, as a buffer left for a closed pipe does."""
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")

    def fileno(self) -> int:
        """The descriptor this stream stands for."""
        return self.fd


class QuietStreamsTest(fixtures.IsolatedCase):
    """A stream that cannot be flushed is pointed at the null device; others are left."""

    def test_a_stream_that_will_not_flush_is_moved_to_the_null_device(self) -> None:
        fd = os.open(self.tmp / "stream", os.O_WRONLY | os.O_CREAT)
        self.addCleanup(os.close, fd)
        with (
            mock.patch.object(sys, "stdout", FlushedStream(fd)),
            mock.patch.object(sys, "stderr", io.StringIO()),
        ):
            lyrics_exit.quiet_failed_streams()
        moved, null = os.fstat(fd), os.stat(os.devnull)
        self.assertEqual((moved.st_dev, moved.st_ino), (null.st_dev, null.st_ino))

    def test_a_stream_with_no_descriptor_is_left_as_it_is(self) -> None:
        stream = FlushedStream(-1)
        with mock.patch.object(stream, "fileno", side_effect=io.UnsupportedOperation):
            lyrics_exit.to_null_device(stream)

    def test_a_stream_closed_at_startup_is_left_out(self) -> None:
        with mock.patch.object(sys, "stdout", None):
            self.assertEqual(lyrics_exit._standard_streams(), (sys.stderr,))


class ClosedPipeEndToEndTest(fixtures.IsolatedCase):
    """The entry point, run as a child on a pipe with no reader, keeps its exit code."""

    def setUp(self) -> None:
        """A one-track album, and a file to take whichever stream is not the pipe."""
        super().setUp()
        self.album = self.tmp / "album"
        fixtures.build_album(self.album, TRACKS[:1])
        self.log = self.tmp / "stream.log"

    def child_env(self) -> dict[str, str]:
        """The environment with a PATH of one empty folder, so no host ffmpeg is found.

        The child runs on ``sys.executable`` by absolute path and needs nothing from PATH.
        """
        empty = self.tmp / "empty-path"
        empty.mkdir(exist_ok=True)
        return {**os.environ, "PATH": str(empty)}

    def run_child(self, *, closed: str) -> int:
        """Run an offline album with the *closed* stream on a readerless pipe."""
        reader, writer = os.pipe()
        os.close(reader)
        try:
            with self.log.open("wb") as sink:
                streams: dict[str, int | IO[bytes]] = {"stdout": sink, "stderr": sink}
                streams[closed] = writer
                argv = [sys.executable, str(ENTRY_POINT), "--album", str(self.album)]
                # Not a captured pipe: nothing is read back once the timeout fires,
                # so the bound holds even if the child left a descendant.
                child = subprocess.run(  # reliability: ignore[RL005] both streams are sinks, a readerless pipe and a file; nothing is read back
                    [*argv, "--offline"],
                    stdin=subprocess.DEVNULL,
                    env=self.child_env(),
                    stdout=streams["stdout"],
                    stderr=streams["stderr"],
                    timeout=CHILD_TIMEOUT_S,
                    check=False,
                )
        finally:
            os.close(writer)
        return child.returncode

    def test_the_child_finds_no_ffmpeg(self) -> None:
        path = self.child_env()["PATH"]
        self.assertIsNone(shutil.which("ffmpeg", path=path))

    def test_a_summary_to_a_closed_pipe_exits_3_with_no_flush_error(self) -> None:
        code = self.run_child(closed="stdout")
        stderr = self.log.read_text(encoding="utf-8")
        self.assertEqual(code, album.EXIT_RECORD_NOT_WRITTEN, stderr)
        self.assertIn(REPORT_LOST, stderr)
        self.assertNotIn("Exception ignored", stderr)
        self.assertTrue((self.album / lr.RUN_FOLDER_NAME).is_dir())

    def test_a_stderr_closed_from_the_start_exits_2(self) -> None:
        code = self.run_child(closed="stderr")
        self.assertEqual(code, album.EXIT_CANNOT_RUN)
        # Exit 2 is argparse's too: the child got past its options to the handler.
        self.assertNotIn("usage:", self.log.read_text(encoding="utf-8"))
        # The lock is taken before the line, so its folder may exist, holding no run.
        runs = self.album / lr.RUN_FOLDER_NAME
        self.assertEqual([path for path in runs.glob("*") if path.is_dir()], [])
