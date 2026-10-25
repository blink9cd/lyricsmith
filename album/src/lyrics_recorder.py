"""A run's record as it stands: the manifest rewritten after each track, and why it stopped.

`RunRecorder` holds the manifest a run is filling in. Each finished track's outcome
goes into it and the whole manifest is written at once, with Ctrl-C held for the
write, so a run that stops part-way still records what it changed; the progress line
for the track follows the write. A write that fails, or does not read back as
written, ends the run: the manifest is undo's record, so no further track may change.
Nothing here prints: progress goes to a callback, and a line it cannot send ends the
run as a lost report.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lyrics_album import deferred_interrupt
from lyrics_exit import report_not_written
from lyrics_files import RECORD_ERRORS as _RECORD_ERRORS
from lyrics_outcomes import describe_outcome, printable
from lyrics_provider_states import CoolingState
from lyrics_records import Manifest, RunState, TrackOutcome
from lyrics_stages import Stages


@dataclass
class RunRecorder:
    """The run's manifest as it stands, written after each track, and why it stopped.

    *youtube_music*, when given, is read before every write, and its state replaces the
    manifest's, so the write after a track that stopped YouTube Music records the stop;
    *tekstowo* is read and written alike.
    """

    run_dir: Path
    manifest: Manifest
    stages: Stages
    progress: Callable[[str], None]
    interrupted: bool = False
    record_error: str | None = None
    youtube_music: Callable[[], CoolingState] | None = None
    tekstowo: Callable[[], CoolingState] | None = None

    def record(self, index: int, outcome: TrackOutcome) -> bool:
        """Record track *index*'s *outcome* and report it; True when the run must stop.

        The update and the write run with the interrupt deferred, so an interrupt
        honored as they end still finds the outcome on disk; the progress line follows.
        """
        tracks = list(self.manifest.tracks)
        track = dataclasses.replace(tracks[index], after=outcome)
        tracks[index] = track
        verdict = describe_outcome(outcome)
        line = printable(f"[{index + 1}/{len(tracks)}] {track.path}: {verdict}")
        return self._write(
            dataclasses.replace(self.manifest, tracks=tuple(tracks)), line
        )

    def complete(self) -> None:
        """Mark the run complete, every track recorded."""
        self._write(dataclasses.replace(self.manifest, state=RunState.COMPLETE), None)

    def _write(self, manifest: Manifest, line: str | None) -> bool:
        """Write *manifest*, then send *line*; True when an interrupt or failed record hit.

        A failed record is an OS error, or a write that does not read back; its line is
        not sent. A line that cannot be sent raises `lyrics_exit.ReportNotWrittenError`.

        A write that fails while a Ctrl-C is held surfaces as the interrupt, raised as
        the hold ends, with the failure as its context: both are kept, the failure as
        the reason the run could not be recorded.
        """
        if self.youtube_music is not None:
            manifest = dataclasses.replace(manifest, youtube_music=self.youtube_music())
        if self.tekstowo is not None:
            manifest = dataclasses.replace(manifest, tekstowo=self.tekstowo())
        try:
            with deferred_interrupt():
                self.manifest = manifest
                self.stages.write_manifest(self.run_dir, manifest)
        except KeyboardInterrupt as interrupt:
            self.interrupted = True
            if isinstance(interrupt.__context__, _RECORD_ERRORS):
                self._failed(interrupt.__context__)
        # The manifest is the run's record for undo: once it cannot be written, or does
        # not read back as written, no further track may change, so the run ends here
        # with the summary.
        except _RECORD_ERRORS as error:
            self._failed(error)
        if line is not None and self.record_error is None:
            self._send(line)
        return self.interrupted or self.record_error is not None

    def _send(self, line: str) -> None:
        """Send *line*; one that cannot be sent (a closed stderr) ends the run.

        Outside the record's error scope: the track is already recorded, so this is
        a lost report, never a manifest that could not be written.
        """
        try:
            self.progress(line)
        except OSError as error:
            raise report_not_written(error) from error

    def _failed(self, error: Exception) -> None:
        """Keep why the manifest could not be written."""
        self.record_error = f"could not record the run in {self.run_dir}: {error}"

    def recorded(self, index: int, outcome: TrackOutcome) -> bool:
        """True when track *index*'s record already holds *outcome*."""
        return self.manifest.tracks[index].after == outcome
