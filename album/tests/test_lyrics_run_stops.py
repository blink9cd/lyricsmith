"""How a run stops: an interrupt as the manifest is written, and an album-level OS error.

An interrupt raised while a finished track is being recorded waits for the record, then
ends the run with the summary; so does one landing between steps, outside every held
write, and a computed outcome is recorded first. A manifest write that fails, or does
not read back, ends the run after the summary, even behind a held interrupt; a track's
write failing behind one is that track's error. An OS error at an album-level step (the
lock, the backup, the run folder, a manifest read) is exit 2 with one line on stderr and
no traceback; one raised recording a track ends the run after the summary, with one line
on stderr and exit 3, track errors or not: undo depends on the record.
A run folder removed after its run was chosen is no run at all, and a progress line that
cannot be sent raises as a lost report, never as a failed record. A track whose tags
cannot be read shows why. Every lyric is invented.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import shutil
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import host_shapes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_exit  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_recorder  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_summary  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above

TrackSpec = fixtures.TrackSpec

#: Three tracks: the first finds synced lyrics, the others nothing.
TRACKS = (
    TrackSpec("01 A.mp3", title="The Keeper"),
    TrackSpec("02 B.mp3", title="The Mill"),
    TrackSpec("03 C.mp3", title="The Bell"),
)
#: The stage's write_manifest call that records the first track. The fake backup
#: writes its own manifest without going through the stage.
FIRST_TRACK_RECORD = 1
#: How undo and check open their refusal of a run whose manifest cannot be read.
_UNREADABLE = f"run {run_fakes.RUN_ID!r}: its manifest could not be read"


class StopCase(cli_fakes.CliCase):
    """A three-track album, its first track answered."""

    def setUp(self) -> None:
        """Build the album and answer its first track."""
        super().setUp()
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        self.paths = fixtures.build_album(self.album, TRACKS)

    def failing_record(self, act: object) -> None:
        """Make the record of the first track run *act* before writing the manifest.

        *act* is an exception to raise; anything else raises a real SIGINT.
        """
        real, calls = self.folder.write_manifest, []

        def write_manifest(run_dir: Path, manifest: lr.Manifest) -> None:
            calls.append(manifest)
            if len(calls) == FIRST_TRACK_RECORD:
                if isinstance(act, BaseException):
                    raise act
                signal.raise_signal(signal.SIGINT)
            real(run_dir, manifest)

        self.overrides["write_manifest"] = write_manifest

    def assert_one_line_error(self, result: cli_fakes.Result, text: str) -> None:
        """Exit 2, the error on one stderr line, no traceback."""
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertIn(f"album.py: {text}", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def assert_record_not_written(self, result: cli_fakes.Result) -> None:
        """Exit 3, the run's record named on one stderr line, no traceback."""
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertIn("album.py: could not record the run", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class InterruptWhileRecordingTest(StopCase):
    """Ctrl-C while a track's outcome is recorded waits for the record."""

    def test_the_record_completes_then_the_run_ends_with_the_summary(self) -> None:
        self.failing_record(signal.SIGINT)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertEqual(
            self.folder.afters_on_disk(self.album), ["written", None, None]
        )
        self.assertIn("01 A.mp3", result.stdout)
        self.assertEqual(result.stdout.count(lyrics_summary.NOT_REACHED), 2)
        self.assertFalse(run_fakes.lock_held(self.album))


class AlbumLevelOSErrorTest(StopCase):
    """An OS error at an album-level step is exit 2, with one line on stderr."""

    def test_an_album_that_cannot_hold_its_run_folder_exits_2(self) -> None:
        (self.album / lr.RUN_FOLDER_NAME).write_bytes(b"")
        # The host's own words for a folder made where a file is: "[Errno 17] File
        # exists" on POSIX, "[WinError 183] Cannot create a file when that file
        # already exists" on Windows. One error; only its wording is the host's.
        blocker = self.tmp / "blocker"
        blocker.write_bytes(b"")
        with self.assertRaises(FileExistsError) as made:
            blocker.mkdir(exist_ok=True)
        result = self.run_album()
        self.assert_one_line_error(result, host_shapes.os_error_text(made.exception))
        self.assertIn(lr.RUN_FOLDER_NAME, result.stderr)
        self.assertEqual((result.stdout, self.folder.backed_up), ("", []))

    def test_a_backup_raising_an_os_error_exits_2(self) -> None:
        def back_up(
            album: Path,
            tracks: Sequence[Path],
            *,
            lang: str,
            genius: lr.GeniusState,
            started: dt.datetime,
            bandcamp: lr.BandcampState = lr.BandcampState.OFF,
            youtube_music: lr.CoolingState = lr.CoolingState.OFF,
            tekstowo: lr.CoolingState = lr.CoolingState.OFF,
            silence_check: lr.SilenceCheck = lr.SilenceCheck.OFF,
        ) -> lr.Manifest:
            raise PermissionError("read-only album")

        self.overrides["back_up"] = back_up
        result = self.run_album()
        self.assert_one_line_error(result, "read-only album")
        self.assertEqual(result.stdout, "")
        self.assertFalse(run_fakes.lock_held(self.album))

    def test_a_manifest_unreadable_in_check_exits_2(self) -> None:
        (self.album / lr.RUN_FOLDER_NAME / run_fakes.RUN_ID).mkdir(parents=True)

        def read_manifest(run_dir: Path) -> lr.Manifest:
            raise OSError("manifest unreadable")

        self.overrides["read_manifest"] = read_manifest
        result = self.run_album("--mode", "check")
        self.assert_one_line_error(result, _UNREADABLE)
        self.assertIn("(manifest unreadable)", result.stderr)

    def test_a_manifest_unreadable_in_undo_exits_2(self) -> None:
        (self.album / lr.RUN_FOLDER_NAME / run_fakes.RUN_ID).mkdir(parents=True)

        # Undo reads the chosen run's manifest through the stage, holding the lock.
        def read_manifest(run_dir: Path) -> lr.Manifest:
            raise OSError("manifest unreadable")

        self.overrides["read_manifest"] = read_manifest
        result = self.run_album("--mode", "undo")
        self.assert_one_line_error(result, _UNREADABLE)
        self.assertIn("(manifest unreadable)", result.stderr)
        self.assertFalse(run_fakes.lock_held(self.album))

    def test_a_run_folder_gone_before_the_lock_is_no_run_to_undo(self) -> None:
        run_dir = self.album / lr.RUN_FOLDER_NAME / run_fakes.RUN_ID
        run_dir.mkdir(parents=True)

        # Another process removes the chosen run between its selection and the lock.
        def acquire(path: Path) -> lyrics_album.HeldLock | None:
            shutil.rmtree(run_dir)
            return run_fakes.acquire(path)

        self.acquire_lock = acquire
        self.overrides["read_manifest"] = lyrics_manifest.read_manifest
        result = self.run_album("--mode", "undo")
        self.assert_one_line_error(result, f"no run {run_fakes.RUN_ID!r} under")
        self.assertFalse(run_fakes.lock_held(self.album))

    def test_a_run_folder_gone_before_its_check_is_no_run_to_check(self) -> None:
        run_dir = self.album / lr.RUN_FOLDER_NAME / run_fakes.RUN_ID
        run_dir.mkdir(parents=True)
        real_select = self.folder.select_run

        def select_run(album: Path, run_id: str | None) -> Path:
            chosen = real_select(album, run_id)
            shutil.rmtree(chosen)
            return chosen

        self.overrides["select_run"] = select_run
        self.overrides["read_manifest"] = lyrics_manifest.read_manifest
        result = self.run_album("--mode", "check")
        self.assert_one_line_error(result, f"no run {run_fakes.RUN_ID!r} under")

    def test_a_manifest_missing_from_a_run_folder_is_an_unreadable_run(self) -> None:
        # The run is still there, so this is a damaged run, not a missing one.
        run_dir = self.album / lr.RUN_FOLDER_NAME / run_fakes.RUN_ID
        run_dir.mkdir(parents=True)
        # The host's own words for the missing file: "[Errno 2] No such file or
        # directory" on POSIX, "[WinError 2] The system cannot find the file
        # specified" on Windows. One error; only its wording is the host's.
        with self.assertRaises(FileNotFoundError) as missing:
            lyrics_manifest.read_manifest(run_dir)
        self.overrides["read_manifest"] = lyrics_manifest.read_manifest
        result = self.run_album("--mode", "undo")
        self.assert_one_line_error(result, _UNREADABLE)
        self.assertIn(host_shapes.os_error_text(missing.exception), result.stderr)
        self.assertIn(lr.MANIFEST_NAME, result.stderr)


class ProgressOSErrorTest(StopCase):
    """A progress line that cannot be written is a lost report, not a lost record."""

    def test_the_report_is_lost_and_the_track_stays_recorded(self) -> None:
        def progress(line: str) -> None:
            if line.startswith("[1/"):
                raise BrokenPipeError("stderr is closed")

        with self.assertRaisesRegex(
            lyrics_exit.ReportNotWrittenError, "stderr is closed"
        ) as raised:
            lyrics_run.run_album(
                self.album,
                self.paths,
                self.providers,
                run_fakes.options(),
                run_fakes.make_stages(self.folder),
                acquire_lock=run_fakes.acquire,
                started=run_fakes.STARTED,
                progress=progress,
            )
        self.assertIsInstance(raised.exception.__cause__, BrokenPipeError)
        self.assertEqual(
            self.folder.afters_on_disk(self.album), ["written", None, None]
        )
        self.assertFalse(run_fakes.lock_held(self.album))


class RecordOSErrorTest(StopCase):
    """An OS error recording a written track ends the run after the summary."""

    def test_the_summary_prints_and_the_run_exits_3(self) -> None:
        self.failing_record(OSError("disk full"))
        result = self.run_album()
        self.assert_record_not_written(result)
        self.assertIn("disk full", result.stderr)
        self.assertEqual(result.stdout.count(lyrics_summary.NOT_REACHED), 2)
        self.assertIn("01 A.mp3", result.stdout)
        self.assertEqual(self.folder.state_on_disk(self.album), "backed_up")
        self.assertFalse(run_fakes.lock_held(self.album))

    def test_a_failure_marking_the_run_complete_ends_it_the_same_way(self) -> None:
        real, calls = self.folder.write_manifest, []

        def write_manifest(run_dir: Path, manifest: lr.Manifest) -> None:
            calls.append(manifest)
            if manifest.state is lr.RunState.COMPLETE:
                raise OSError("disk full")
            real(run_dir, manifest)

        self.overrides["write_manifest"] = write_manifest
        result = self.run_album()
        self.assert_record_not_written(result)
        self.assertNotIn(lyrics_summary.NOT_REACHED, result.stdout)

    def test_a_record_failure_beside_track_errors_is_still_exit_3(self) -> None:
        def read_tags(path: Path) -> lm.TrackTags:
            if path.name.startswith("02"):
                raise ValueError("can't sync to MPEG frame")
            return lyrics_tags.read_tags(path)

        real = self.folder.write_manifest

        def write_manifest(run_dir: Path, manifest: lr.Manifest) -> None:
            if manifest.state is lr.RunState.COMPLETE:
                raise OSError("disk full")
            real(run_dir, manifest)

        self.overrides["read_tags"] = read_tags
        self.overrides["write_manifest"] = write_manifest
        result = self.run_album()
        self.assertIn("ERROR fetch: can't sync to MPEG frame", result.stdout)
        self.assert_record_not_written(result)


class RecordFailureTest(StopCase):
    """A record that does not read back, or fails under a held Ctrl-C, still ends at 3."""

    def test_a_manifest_that_does_not_read_back_ends_the_run_after_the_summary(
        self,
    ) -> None:
        self.failing_record(lm.ReadBackError("manifest.json did not read back"))
        result = self.run_album()
        self.assert_record_not_written(result)
        self.assertIn("did not read back", result.stderr)
        self.assertEqual(result.stdout.count(lyrics_summary.NOT_REACHED), 2)
        self.assertFalse(run_fakes.lock_held(self.album))

    def test_an_os_error_behind_a_held_interrupt_is_still_reported(self) -> None:
        real, calls = self.folder.write_manifest, []

        def write_manifest(run_dir: Path, manifest: lr.Manifest) -> None:
            calls.append(manifest)
            if len(calls) == FIRST_TRACK_RECORD:
                signal.raise_signal(signal.SIGINT)
                raise OSError("disk full")
            real(run_dir, manifest)

        self.overrides["write_manifest"] = write_manifest
        result = self.run_album()
        self.assert_record_not_written(result)
        self.assertIn("disk full", result.stderr)
        self.assertEqual(result.stdout.count(lyrics_summary.NOT_REACHED), 2)


class HeldWriteErrorTest(StopCase):
    """A track's write that fails under a held Ctrl-C records that error, then stops."""

    def test_a_sidecar_write_failing_under_a_held_interrupt_is_recorded(
        self,
    ) -> None:
        def write_sidecar(mp3: Path, lyrics: lm.Lyrics) -> lm.SidecarWrite:
            signal.raise_signal(signal.SIGINT)
            raise OSError("sidecar disk full")

        self.overrides["write_sidecar"] = write_sidecar
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertIn("ERROR embed: sidecar disk full", result.stdout)
        self.assertNotIn(lyrics_outcomes.INTERRUPTED, result.stdout)
        self.assertEqual(self.folder.afters_on_disk(self.album), ["error", None, None])
        self.assertEqual(result.stdout.count(lyrics_summary.NOT_REACHED), 2)
        self.assertFalse(run_fakes.lock_held(self.album))

    def test_a_fetch_interrupt_carrying_a_context_is_a_plain_interrupt(self) -> None:
        def fetch(
            query: lm.TrackQuery,
            sidecar: lm.SidecarState,
            providers: lm.ProviderTable,
            *,
            deadline_s: float,
            embedded: lyrics_inventory.EmbeddedLyrics,
            duration_s: float | None,
        ) -> lm.FetchResult:
            # A Ctrl-C landing while the fetch handles an error of its own carries
            # that error as its context, though no write was held.
            try:
                raise ValueError("a provider's own retry")
            except ValueError:
                raise KeyboardInterrupt

        self.overrides["fetch"] = fetch
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertIn(f"ERROR fetch: {lyrics_outcomes.INTERRUPTED}", result.stdout)
        self.assertNotIn("retry", result.stdout)
        self.assertEqual(result.stdout.count(lyrics_summary.NOT_REACHED), 2)


class InterruptBetweenStepsTest(StopCase):
    """Ctrl-C outside every held write still ends with the summary and the record."""

    def test_an_interrupt_before_the_record_is_held_still_records_the_track(
        self,
    ) -> None:
        real = lyrics_recorder.describe_outcome
        raised: list[lr.TrackOutcome] = []

        def describe(outcome: lr.TrackOutcome) -> str:
            if not raised:
                raised.append(outcome)
                raise KeyboardInterrupt
            return real(outcome)

        with mock.patch.object(lyrics_recorder, "describe_outcome", describe):
            result = self.run_album()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertEqual(
            self.folder.afters_on_disk(self.album), ["written", None, None]
        )
        self.assertIn("01 A.mp3", result.stdout)
        self.assertEqual(result.stdout.count(lyrics_summary.NOT_REACHED), 2)
        self.assertFalse(run_fakes.lock_held(self.album))

    def test_an_outcome_the_manifest_already_holds_is_not_recorded_again(
        self,
    ) -> None:
        # A failed backup records its track's error itself, so the run's outcome for
        # that track is the one already on disk.
        failed = lr.TrackOutcome(
            lr.OutcomeStatus.ERROR,
            error=lr.StageError(lr.ErrorStage.BACKUP, "OSError", "unreadable"),
        )
        self.folder.fail = frozenset({"01 A.mp3"})
        real_back_up, writes = self.folder.back_up_album, []

        def back_up(
            album: Path,
            tracks: Sequence[Path],
            *,
            lang: str,
            genius: lr.GeniusState,
            started: dt.datetime,
            bandcamp: lr.BandcampState = lr.BandcampState.OFF,
            youtube_music: lr.CoolingState = lr.CoolingState.OFF,
            tekstowo: lr.CoolingState = lr.CoolingState.OFF,
            silence_check: lr.SilenceCheck = lr.SilenceCheck.OFF,
        ) -> lr.Manifest:
            manifest = real_back_up(
                album,
                tracks,
                lang=lang,
                genius=genius,
                started=started,
                bandcamp=bandcamp,
                youtube_music=youtube_music,
                tekstowo=tekstowo,
                silence_check=silence_check,
            )
            first = dataclasses.replace(manifest.tracks[0], after=failed)
            return dataclasses.replace(manifest, tracks=(first, *manifest.tracks[1:]))

        def write_manifest(run_dir: Path, manifest: lr.Manifest) -> None:
            writes.append(manifest)

        def describe(outcome: lr.TrackOutcome) -> str:
            raise KeyboardInterrupt

        self.overrides.update(back_up=back_up, write_manifest=write_manifest)
        with mock.patch.object(lyrics_recorder, "describe_outcome", describe):
            result = self.run_album()
        self.assertEqual((result.code, writes), (album.EXIT_INTERRUPTED, []))
        self.assertIn("ERROR backup: unreadable", result.stdout)

    def test_an_interrupt_before_a_tracks_boundary_ends_with_the_summary(
        self,
    ) -> None:
        real = lyrics_run.TrackState
        started: list[bool] = []

        def state() -> lyrics_run.TrackState:
            started.append(True)
            if len(started) == len(TRACKS) - 1:
                raise KeyboardInterrupt
            return real()

        with mock.patch.object(lyrics_run, "TrackState", state):
            result = self.run_album()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertEqual(
            self.folder.afters_on_disk(self.album), ["written", None, None]
        )
        self.assertEqual(result.stdout.count(lyrics_summary.NOT_REACHED), 2)

    def test_an_interrupt_marking_the_run_complete_ends_with_the_summary(
        self,
    ) -> None:
        real = lyrics_recorder.dataclasses.replace

        def replace(record: lr.Manifest, **changes: object) -> lr.Manifest:
            if changes.get("state") is lr.RunState.COMPLETE:
                raise KeyboardInterrupt
            return real(record, **changes)

        with mock.patch.object(lyrics_recorder.dataclasses, "replace", replace):
            result = self.run_album()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertIn("03 C.mp3", result.stdout)
        self.assertEqual(self.folder.state_on_disk(self.album), "backed_up")


class TagReadErrorTest(StopCase):
    """A track whose tags cannot be read is an error that shows its message."""

    def test_the_message_is_shown(self) -> None:
        def read_tags(path: Path) -> lm.TrackTags:
            if path.name.startswith("02"):
                raise ValueError("can't sync to MPEG frame")
            return lyrics_tags.read_tags(path)

        self.overrides["read_tags"] = read_tags
        result = self.run_album()
        self.assertIn("ERROR fetch: can't sync to MPEG frame", result.stdout)
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
