"""A `typing.Protocol` for each album stage function, and the `Stages` record.

Each stage function is bound to its protocol (in its own module, through `Stages`, or in
its tests), so the type checker holds every implementation to the one signature the
orchestrator calls, and a test can pass a conforming fake in its place. `Stages` is the
record of them all that the entry point fills with the real functions.

No stage takes the album lock. Every stage that changes a file runs while its caller
already holds it: the orchestrator for a run, and `undo_album` for an undo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from lyrics_model import (
    FetchResult,
    FrameRecord,
    Lyrics,
    ProviderTable,
    SidecarState,
    SidecarWrite,
    TrackQuery,
    TrackTags,
)
from lyrics_provider_states import BandcampState, CoolingState, SilenceCheck
from lyrics_records import (
    CheckResult,
    GeniusState,
    Manifest,
    StripResult,
    TrackBefore,
    UndoReport,
)
from lyrics_silence import Level

if TYPE_CHECKING:
    # For the annotation alone: lyrics_inventory reads tags through lyrics_tags, which
    # imports this module, so a runtime import here would be a cycle.
    from lyrics_inventory import EmbeddedLyrics


class ReadTags(Protocol):
    """Read a track's query tags, duration, ID3 version and every lyrics frame."""

    def __call__(self, path: Path) -> TrackTags:
        """Read only; a file with no ID3v2 header comes back with no version."""


class WriteLyrics(Protocol):
    """Embed lyrics, keeping the ID3 version, then confirm by reading back."""

    def __call__(self, path: Path, lyrics: Lyrics, lang: str) -> bool:
        """Return whether the file was saved; raise on ID3v2.2 and on a mismatch.

        The file is not saved when its lyrics frames already are the ones the write
        would leave. A file with no ID3v2 header is written as v2.4, mutagen's
        default: it has no version to keep. `TagVersionError` on ID3v2.2,
        `ReadBackError` on a mismatch. The caller holds the album lock; this does not
        take it.
        """


class CheckEncodable(Protocol):
    """Refuse an answer `WriteLyrics` could not encode, touching no file."""

    def __call__(self, lyrics: Lyrics, lang: str) -> None:
        """Raise on a time stamp, a character or a NUL the frames cannot hold as given.

        No I/O, so a caller can ask before any change at all.
        """


class CheckWritable(Protocol):
    """Refuse a track `WriteLyrics` foreseeably could not save, changing nothing."""

    def __call__(self, path: Path) -> None:
        """Raise on a tag version `WriteLyrics` cannot save or a file it cannot open.

        Read only, so a caller can ask before a change it could not finish.
        """


class StripLyrics(Protocol):
    """Remove every `SYLT` and `USLT` frame from a track; say whether any was there."""

    def __call__(self, path: Path) -> bool:
        """Save with the loaded ID3 version; a file with no ID3v2 header is left as is.

        Return True when a lyrics frame was removed, False when there was none to
        remove, in which case nothing was written.

        Also how undo restores a track whose manifest records no ID3 version: it had
        no lyrics frame before the run, so removing the run's frames is the restore.
        """


class StripTrack(Protocol):
    """Remove a track's lyrics frames and both its sidecars."""

    def __call__(
        self, mp3: Path, *, frames: bool = True, sidecars: bool = True
    ) -> StripResult:
        """Check both sidecar names, strip the frames, then remove each sidecar.

        *frames* False keeps the frames, and *sidecars* False the sidecars.

        Raise `SlotNotAFileError` when a sidecar name holds a folder, a pipe or a
        device, and `TagVersionError` on an ID3v2.2 tag holding lyrics, each before
        anything of the track changes. A link is removed itself, never its target.
        The caller holds the album lock; this does not take it.
        """


class RestoreFrames(Protocol):
    """Replace a track's lyrics frames with *frames*, verbatim."""

    def __call__(
        self, path: Path, frames: Sequence[FrameRecord], id3_version: tuple[int, int]
    ) -> None:
        """Save with *id3_version*; raise `ReadBackError` on a mismatch.

        *id3_version* is a recorded version, never None: a track recorded without one
        is restored through `StripLyrics` instead.
        """


class NormalizeSidecars(Protocol):
    """Normalize the sidecars beside an MP3 and read the survivor."""

    def __call__(self, mp3: Path, *, album: Path, apply: bool) -> SidecarState:
        """Raise `SidecarDecodeError` on a sidecar that is not UTF-8.

        *album* is the album *mp3* belongs to: a link under a sidecar name is read
        only when it leads to a file inside it.

        With *apply* False nothing on disk changes: the state is computed as though
        the renames and removals had happened, so a dry run reports which tracks a
        sidecar would serve. Required, not defaulted, so every call site says which
        of the two it means. With *apply* True the caller holds the album lock.
        """


class WriteSidecar(Protocol):
    """Write the accepted kind's sidecar atomically and remove the other kind."""

    def __call__(self, mp3: Path, lyrics: Lyrics) -> SidecarWrite | None:
        """Write by temp file and replace; None when nothing on disk changed.

        A sidecar file already holding the bytes to be written is left as it is. The
        caller holds the album lock; this does not take it.
        """


class BackUpAlbum(Protocol):
    """Create a run folder, back up every track, and write the manifest."""

    def __call__(
        self,
        album: Path,
        tracks: Sequence[Path],
        *,
        lang: str,
        genius: GeniusState,
        started: datetime,
        bandcamp: BandcampState = BandcampState.OFF,
        youtube_music: CoolingState = CoolingState.OFF,
        tekstowo: CoolingState = CoolingState.OFF,
        silence_check: SilenceCheck = SilenceCheck.OFF,
    ) -> Manifest:
        """Return the manifest as written, state `RunState.BACKED_UP`.

        The caller holds the album lock; this does not take it.
        """


class WriteManifest(Protocol):
    """Replace a run folder's manifest atomically."""

    def __call__(self, run_dir: Path, manifest: Manifest) -> None:
        """Write by temp file and replace.

        The caller holds the album lock; this does not take it.
        """


class FetchLyrics(Protocol):
    """Run the provider chain for one track, what it already holds in its place."""

    def __call__(
        self,
        query: TrackQuery,
        sidecar: SidecarState,
        providers: ProviderTable,
        *,
        deadline_s: float,
        embedded: EmbeddedLyrics,
        duration_s: float | None,
    ) -> FetchResult:
        """Bound each provider call by *deadline_s*.

        *embedded* is the worded lyrics the track's frames hold, each serving the
        track like a sidecar of its kind. *duration_s* is the track's, None when it
        cannot be read: an answer it rules out is declined.
        """


class CheckTrack(Protocol):
    """Judge embedded lyrics against the track's duration and its backup."""

    def __call__(
        self, embedded: Lyrics | None, backup: Lyrics | None, duration_s: float | None
    ) -> CheckResult:
        """Pure: never changes anything.

        *embedded* is None when a track the run wrote now holds no lyrics, which is
        a `CheckRule.NO_LYRICS` FLAG.
        """


class SelectRun(Protocol):
    """Choose the run folder undo and check read: the newest, or the one named."""

    def __call__(self, album: Path, run_id: str | None) -> Path:
        """The run folder for *run_id*, or the newest by run id when it is None.

        Raise `NoRunError` when the album holds no run folder, or none named *run_id*.
        """


class ReadManifest(Protocol):
    """Read and validate a run folder's manifest."""

    def __call__(self, run_dir: Path) -> Manifest:
        """Raise `ManifestSchemaError` on a schema other than the one this code knows.

        The manifest is read back from disk, where anything may have edited it, so
        the implementation is a parser of untrusted input and carries
        ``# fuzz-target``, with a property test that it never fails any other way.
        """


class LoadBeforeFrames(Protocol):
    """Rebuild a track's backed-up frames from its run folder's exports."""

    def __call__(self, run_dir: Path, before: TrackBefore) -> tuple[FrameRecord, ...]:
        """The frames in *before*, in manifest order, each from its proven export.

        An entry's ``exact`` JSON wins when present, else its ``.lrc`` / ``.txt``
        export is re-read; the backup wrote JSON wherever that export failed to read
        back as the frame, so SYLT entries return in their stored order either way.
        `lyrics_from_frames` turns the result into the check's backup lyrics.
        """


class LoadBeforeSidecar(Protocol):
    """Read a track's backed-up untimed sidecar from its run folder's copy."""

    def __call__(self, run_dir: Path, before: TrackBefore) -> Lyrics | None:
        """The copied ``.txt``, else a copied ``.lrc`` with no time stamp; else None.

        Never a sidecar recorded as a link. The check compares with it when the
        backed-up frames hold no word. Raise `SidecarDecodeError` or
        `SidecarTooLargeError` on a copy the sidecar stage could not read.
        """


class UndoRun(Protocol):
    """Restore an album, or one track, from a run's manifest."""

    def __call__(
        self,
        album: Path,
        run_dir: Path,
        manifest: Manifest,
        *,
        newest: bool,
        track: str | None,
    ) -> UndoReport:
        """Undo the run in *run_dir*, whose *manifest* the caller read.

        The run is chosen once, by the caller, through `SelectRun`, and its manifest
        read through `ReadManifest`, so a schema this code does not know is refused
        before undo begins. *newest* says whether that run is the album's newest.
        *track* is an album-relative path, as the check mode takes it.

        The caller holds the album lock; this does not take it: `undo_album` takes it
        after choosing the run and before reading its manifest.

        Raise `TrackNotInRunError` when *track* is not one the run lists, as the check
        mode's own *track* selection does (`Manifest.selected`).

        A manifest that cannot be written after a track (an OS error, or a write that
        does not read back) is not raised: undo stops and returns the tracks it
        reached, with the reason in the report's ``record_error``.
        """


class MeasureLevel(Protocol):
    """A track's whole-track peak through *ffmpeg* (lyrics_silence.measure_level)."""

    def __call__(self, track: Path, ffmpeg: str) -> Level:
        """Read only; a failure comes back as `MeasureFailed`, never raised."""


@dataclass(frozen=True)
class Stages:
    """Every stage function a run, a strip, a dry run, a check or an undo calls.

    Each is typed by its protocol above, so the entry point passes the real functions
    and a test passes fakes.
    """

    read_tags: ReadTags
    write_lyrics: WriteLyrics
    normalize_sidecars: NormalizeSidecars
    write_sidecar: WriteSidecar
    fetch: FetchLyrics
    check: CheckTrack
    back_up: BackUpAlbum
    write_manifest: WriteManifest
    select_run: SelectRun
    read_manifest: ReadManifest
    load_before: LoadBeforeFrames
    load_before_sidecar: LoadBeforeSidecar
    undo: UndoRun
    strip_track: StripTrack
    check_writable: CheckWritable
    check_encodable: CheckEncodable
    measure_level: MeasureLevel
