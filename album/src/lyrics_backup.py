"""The backup stage: a run folder holding every track's lyrics as they were, and its index.

`back_up_album` creates ``<album>/.lyricsmith/<run-id>/``, exports each track's
`SYLT` / `USLT` frames and copies its sidecars there, and writes the manifest with
state `RunState.BACKED_UP`. Only after that may a run change any track.

- **The run id** is the start time in UTC, ``YYYYMMDDTHHMMSSZ``, with ``-2``, ``-3``
  and so on when that folder already exists. The folder is created exclusively, so
  two runs started in one second never share one.
- **Exports mirror the album.** A track ``CD1/01 Song.mp3`` exports to
  ``frames/CD1/01 Song.sylt.1.lrc`` and ``frames/CD1/01 Song.uslt.1.txt``, numbered
  per kind in stored order, and its sidecars copy to ``sidecars/CD1/01 Song.lrc`` /
  ``.txt``, so a person finds a track's lyrics by eye. A name too long to leave room
  for those endings keeps its head and a short hash of the whole
  (`lyrics_files.fit_name`); the manifest records every name, so undo and the check
  never derive one. A sidecar that is a symbolic
  link is not copied: the manifest records the link's target, and undo puts the link
  itself back.
- **The round-trip proof.** Each export is read back from disk and compared with the
  frame it came from. A `USLT` survives by construction (UTF-8, newlines as stored),
  unless its text holds what UTF-8 cannot encode. An LRC cannot carry every `SYLT`: a
  frame timed in MPEG frames rather than milliseconds, entries out of time order, an
  entry holding a line break or surrounding spaces, or opening with a bracket.
  Wherever the export differs, the frame is also written verbatim as JSON beside it
  (``frames/CD1/01 Song.sylt.1.frames.json``), which undo prefers. Nothing is
  restored from an export that was not proven. A stamp past ``99:59.999`` is carried:
  the LRC reader takes minutes of any width.
- **A failed track is excluded.** Any error while backing a track up (an unreadable
  tag, an I/O error, a second track whose export stem an earlier one took, as
  ``Song.MP3`` after ``Song.mp3``, a sidecar past the lyrics cap, refused unread) is
  recorded as that track's ``backup`` error with no ``before`` block, and a run never
  writes such a track. The other tracks proceed.

`select_run` (re-exported from `lyrics_run_folders`) and
`load_before_frames` are the reading half, used by undo and the check;
`load_before_sidecar` reads a backed-up untimed sidecar for the check.
No function here takes the album lock, and nothing here prints.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import lyrics_files
import lyrics_manifest
from lyrics_model import (
    SYLT_MILLISECONDS,
    FrameKind,
    FrameRecord,
    Lyrics,
    ManifestSchemaError,
    PlainLyrics,
    ReadBackError,
    SyltEntry,
    SyncedLine,
    SyncedLyrics,
    format_lrc,
    parse_lrc,
)
from lyrics_provider_states import BandcampState, CoolingState, SilenceCheck
from lyrics_records import (
    ErrorStage,
    FrameExport,
    GeniusState,
    Manifest,
    ManifestTrack,
    OutcomeStatus,
    RunState,
    SidecarCopy,
    StageError,
    TrackBefore,
    TrackOutcome,
)
from lyrics_sidecar import read_bounded, read_sidecar_text
from lyrics_slots import LRC_SUFFIX, TXT_SUFFIX, Slot, sidecar_entry, sidecar_paths
from lyrics_stages import BackUpAlbum, LoadBeforeFrames, LoadBeforeSidecar, SelectRun
from lyrics_run_folders import (
    NO_SUFFIX,
    RUN_FOLDER_NAME,
    refuse_linked_run_folder,
)
from lyrics_run_folders import newer_runs as newer_runs
from lyrics_run_folders import run_folder as run_folder
from lyrics_run_folders import run_order as run_order
from lyrics_run_folders import runs_newest_first as runs_newest_first
from lyrics_run_folders import select_run as select_run
from lyrics_tags import read_tags

#: The run id's form: the run's start time in UTC.
RUN_ID_FORMAT = "%Y%m%dT%H%M%SZ"
#: Where a run folder keeps its frame exports and its sidecar copies.
_FRAMES_DIR = "frames"
_SIDECARS_DIR = "sidecars"
#: The suffix of a frame's exact JSON copy.
_EXACT_SUFFIX = ".frames.json"
#: Each frame kind's export suffix: LRC for timed lyrics, plain text for the rest.
_EXPORT_SUFFIX = {FrameKind.SYLT: ".lrc", FrameKind.USLT: ".txt"}
_ENCODING = "utf-8"
#: The room a backup keeps after a stem for the endings it adds: the longest,
#: ``.sylt.<k>.frames.json``, takes 21 bytes with a three-digit frame number, and this
#: leaves room for a number of up to fourteen digits.
_ENDING_RESERVE = 32


# --- Run ids and folders -------------------------------------------------------------


def run_id_base(started: datetime) -> str:
    """The run id for a run started at *started*, before any collision suffix."""
    return started.astimezone(UTC).strftime(RUN_ID_FORMAT)


def create_run_dir(album: Path, started: datetime) -> Path:
    """Create and return a new run folder under *album* for a run started at *started*.

    The folder is created exclusively in ``.lyricsmith``: when the plain run id is
    taken there, ``-2``, ``-3`` and so on are tried in turn, so a concurrent run can
    never land in it too and no id names two runs. A link at the run folder raises
    `RunFolderLinkError` before anything is created.
    """
    refuse_linked_run_folder(album)
    parent = album / RUN_FOLDER_NAME
    parent.mkdir(exist_ok=True)
    base, suffix = run_id_base(started), NO_SUFFIX
    while True:
        run_id = base if suffix == NO_SUFFIX else f"{base}-{suffix}"
        run_dir = parent / run_id
        try:
            run_dir.mkdir()
        except FileExistsError:
            suffix += 1
            continue
        return run_dir


# --- Exports and their proof ---------------------------------------------------------


def _export_stem(track_path: str) -> str:
    """The run-folder name stem for a track: its album path without the suffix.

    The last part is shortened by `lyrics_files.fit_name` when it would leave too
    little room for the longest ending a backup gives it, so a track named in nearly
    the whole limit still backs up. A folder on the way is the album's own, so it fits.
    """
    stem = PurePosixPath(track_path).with_suffix("")
    short = lyrics_files.fit_name(stem.name, "", _ENDING_RESERVE)
    return stem.with_name(short).as_posix()


def _export_text(frame: FrameRecord) -> str:
    """The readable export of *frame*: its text, or its entries as millisecond LRC."""
    if isinstance(frame.payload, str):
        return frame.payload
    return format_lrc([SyncedLine(entry.time, entry.text) for entry in frame.payload])


def _sylt_entries(text: str) -> tuple[SyltEntry, ...] | None:
    """The SYLT entries an LRC export reads back as, or None when it is not timed."""
    lyrics = parse_lrc(text)
    if not isinstance(lyrics, SyncedLyrics):
        return None
    return tuple(SyltEntry(line.text, line.ms) for line in lyrics.lines)


def _reads_back(frame: FrameRecord, text: str) -> bool:
    """Whether export *text*, re-parsed, is exactly *frame*'s payload.

    An LRC stamp is milliseconds, so a SYLT timed in anything else never counts as
    carried, even when its numbers would survive: the export would misstate them.
    """
    if isinstance(frame.payload, str):
        return text == frame.payload
    if frame.format != SYLT_MILLISECONDS:
        return False
    return _sylt_entries(text) == frame.payload


def _read_text(path: Path) -> str:
    """*path*'s bytes as UTF-8, newlines as stored."""
    return path.read_bytes().decode(_ENCODING)


def _read_capped(path: Path, name: str) -> bytes:
    """*path*'s bytes, the run-folder file *name*; refused past the manifest's cap.

    One byte more than `lyrics_manifest.MAX_MANIFEST_BYTES` is read, so an oversized
    file is known by that byte alone and never read whole: `ManifestSchemaError`.
    """
    with path.open("rb") as handle:
        data = handle.read(lyrics_manifest.MAX_MANIFEST_BYTES + 1)
    if len(data) > lyrics_manifest.MAX_MANIFEST_BYTES:
        raise ManifestSchemaError(
            f"{name} is too large: over {lyrics_manifest.MAX_MANIFEST_BYTES} bytes"
        )
    return data


def _write_exact(run_dir: Path, name: str, frame: FrameRecord) -> str:
    """Write *frame*'s exact JSON copy as *name*, proving it reads back; return *name*."""
    target = run_dir / name
    lyrics_files.write_confirmed(target, lyrics_manifest.frame_bytes(frame))
    if lyrics_manifest.parse_frame(target.read_bytes()) != frame:
        raise ReadBackError(f"the exact copy {target.name} does not read back")
    return name


def _export_frame(run_dir: Path, stem: str, frame: FrameRecord, k: int) -> FrameExport:
    """Export *frame*, the *k*-th of its kind, prove it, and return its manifest entry.

    Text UTF-8 cannot encode (a lone surrogate) is written as `?`, so the export stays
    readable and the proof sends the frame to its exact copy.
    """
    name = f"{_FRAMES_DIR}/{stem}.{frame.kind.value.lower()}.{k}"
    file = name + _EXPORT_SUFFIX[frame.kind]
    target = run_dir / file
    lyrics_files.write_confirmed(
        target, _export_text(frame).encode(_ENCODING, "replace")
    )
    exact = None
    if not _reads_back(frame, _read_text(target)):
        exact = _write_exact(run_dir, name + _EXACT_SUFFIX, frame)
    return FrameExport(
        frame.kind,
        frame.lang,
        frame.desc,
        frame.encoding,
        frame.format,
        frame.type,
        file,
        exact,
    )


def _numbered(frames: Sequence[FrameRecord]) -> Iterator[tuple[FrameRecord, int]]:
    """Each of *frames* with its number among frames of its kind, from 1."""
    counts = dict.fromkeys(FrameKind, 0)
    for frame in frames:
        counts[frame.kind] += 1
        yield frame, counts[frame.kind]


def _back_up_sidecar(run_dir: Path, stem: str, sidecar: Path) -> SidecarCopy | None:
    """Back up what the slot name *sidecar* holds; return its manifest entry, if any.

    A file is copied into the run folder byte for byte. A link is recorded by its
    target and nothing is copied, so undo can put the link itself back, whether it
    leads to a file, to a folder or nowhere. Nothing, or a folder, a pipe or a device,
    has no entry.
    """
    entry = sidecar_entry(sidecar)
    if entry is Slot.ABSENT:
        return None
    if entry is Slot.LINK:
        return SidecarCopy(sidecar.name, None, os.readlink(sidecar))
    file = f"{_SIDECARS_DIR}/{stem}{sidecar.suffix}"
    # Read as the sidecar stage reads one, bounded: the backup meets every sidecar
    # first, so one past the lyrics cap is refused here, unread, as the track's error.
    data = read_bounded(sidecar, sidecar.name)
    lyrics_files.write_confirmed(run_dir / file, data)
    return SidecarCopy(sidecar.name, file)


def _claim_stem(path: str, taken: set[str]) -> str:
    """*path*'s export stem, added to *taken*; a stem already taken is refused.

    ``Song.mp3`` and ``Song.MP3`` share the stem ``Song``, so the second would
    overwrite the first's exports: it raises `FileExistsError` instead.
    """
    stem = _export_stem(path)
    if stem in taken:
        raise FileExistsError(f"{path} backs up as {stem!r}, as an earlier track does")
    taken.add(stem)
    return stem


def _back_up(run_dir: Path, mp3: Path, path: str, taken: set[str]) -> ManifestTrack:
    """Back up one track: its frames, proven, and its sidecars."""
    stem = _claim_stem(path, taken)
    tags = read_tags(mp3)
    frames = tuple(
        _export_frame(run_dir, stem, frame, k) for frame, k in _numbered(tags.frames)
    )
    # The names in their slot order, which is the order they are backed up in.
    found = (_back_up_sidecar(run_dir, stem, p) for p in sidecar_paths(mp3))
    sidecars = tuple(copy for copy in found if copy is not None)
    return ManifestTrack(path, tags.id3_version, TrackBefore(frames, sidecars))


def back_up_track(
    album: Path, run_dir: Path, mp3: Path, taken: set[str]
) -> ManifestTrack:
    """Back up *mp3* into *run_dir*; a failure is the track's backup error.

    The track's manifest path is *mp3* relative to *album*, in POSIX form. *taken*
    holds the export stems earlier tracks of this run claimed; this track's is added,
    and a track whose stem is already there fails rather than overwrite their exports.
    """
    path = mp3.relative_to(album).as_posix()
    try:
        return _back_up(run_dir, mp3, path, taken)
    except Exception as error:
        # The per-track boundary: whatever stopped this backup, the track now has no
        # undo source, and the one safe response is to exclude it from every write.
        failure = StageError(ErrorStage.BACKUP, type(error).__name__, str(error))
        outcome = TrackOutcome(OutcomeStatus.ERROR, error=failure)
        return ManifestTrack(path, None, None, outcome)


def back_up_album(
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
    """Create a run folder, back up every one of *tracks*, and write the manifest.

    Return the manifest as written, state `RunState.BACKED_UP`; its run folder is
    ``run_folder(album, manifest.run_id)``. The caller holds the album lock. The
    manifest records *bandcamp*, *youtube_music* and *tekstowo*, whether the run asks
    each, and *silence_check*, whether it measures its missed tracks.
    """
    run_dir = create_run_dir(album, started)
    taken: set[str] = set()
    entries = tuple(back_up_track(album, run_dir, mp3, taken) for mp3 in tracks)
    kept = Manifest(run_dir.name, lang, RunState.BACKED_UP, genius, entries)
    manifest = replace(
        kept,
        bandcamp=bandcamp,
        youtube_music=youtube_music,
        tekstowo=tekstowo,
        silence_check=silence_check,
    )
    lyrics_manifest.write_manifest(run_dir, manifest)
    return manifest


# --- Reading a backup back -----------------------------------------------------------


def _restored_payload(
    run_dir: Path, export: FrameExport
) -> str | tuple[SyltEntry, ...]:
    """The payload *export*'s readable file holds."""
    try:
        source = lyrics_files.inside_run_folder(run_dir, export.file)
        text = _read_capped(source, export.file).decode(_ENCODING)
    except UnicodeDecodeError as error:
        raise ManifestSchemaError(f"{export.file} is not UTF-8") from error
    if export.kind is FrameKind.USLT:
        return text
    entries = _sylt_entries(text)
    if entries is None:
        raise ManifestSchemaError(f"{export.file} no longer holds timed lyrics")
    return entries


def _load_frame(run_dir: Path, export: FrameExport) -> FrameRecord:
    """The frame *export* backed up: its exact copy when it has one, else its export."""
    if export.exact is not None:
        exact = lyrics_files.inside_run_folder(run_dir, export.exact)
        return lyrics_manifest.parse_frame(_read_capped(exact, export.exact))
    payload = _restored_payload(run_dir, export)
    try:
        return FrameRecord(
            export.kind,
            export.lang,
            export.desc,
            export.encoding,
            export.format,
            export.type,
            payload,
        )
    except ValueError as error:
        # FrameRecord refuses fields that contradict the kind: an edited manifest.
        raise ManifestSchemaError(f"{export.file}: {error}") from error


def load_before_frames(run_dir: Path, before: TrackBefore) -> tuple[FrameRecord, ...]:
    """The frames *before* records, in manifest order, each from its proven source.

    Raise `ManifestSchemaError` when the run folder no longer holds what the backup
    wrote (an export edited to hold no time stamp, or not UTF-8, or an export or exact
    copy grown past `lyrics_manifest.MAX_MANIFEST_BYTES`, read no further),
    `OutsideRunFolderError` when an export or exact copy is reached through a link out
    of the run folder, and the `OSError` reading it when an export is gone.
    """
    return tuple(_load_frame(run_dir, export) for export in before.frames)


def _copied_sidecar(before: TrackBefore, suffix: str) -> str | None:
    """The run-folder copy of *before*'s sidecar ending in *suffix*; never a link's."""
    copies = (copy.file for copy in before.sidecars if copy.name.endswith(suffix))
    return next((file for file in copies if file is not None), None)


def _read_copy(run_dir: Path, file: str) -> str:
    """The backed-up sidecar copy *file*, read as the sidecar stage reads a sidecar."""
    return read_sidecar_text(lyrics_files.inside_run_folder(run_dir, file))


def load_before_sidecar(run_dir: Path, before: TrackBefore) -> Lyrics | None:
    """The untimed lyrics *before*'s sidecars held, read in the sidecar stage's order.

    The ``.lrc`` copy first, as the run reads it: its words when it holds no time
    stamp, and None when it is timed with words or blank with none, since the run
    then never read the ``.txt``. The ``.txt`` copy only beside no ``.lrc`` copy or
    one of time stamps alone, which gives way to it in the run too. Read from the
    copies in *run_dir* (`read_sidecar_text`); a sidecar recorded as a link was never
    copied and is never read. None when the backup copied neither.
    Raise `SidecarDecodeError` on a copy that is not UTF-8, `SidecarTooLargeError` on
    one past the lyrics cap, `OutsideRunFolderError` on one reached through a link out
    of the run folder, and the `OSError` reading it when it is gone.
    """
    lrc = _copied_sidecar(before, LRC_SUFFIX)
    if lrc is not None:
        lyrics = parse_lrc(_read_copy(run_dir, lrc))
        if isinstance(lyrics, PlainLyrics):
            return lyrics if lyrics.text.strip() else None
        if any(line.text.strip() for line in lyrics.lines):
            return None
    txt = _copied_sidecar(before, TXT_SUFFIX)
    return None if txt is None else PlainLyrics(_read_copy(run_dir, txt))


#: Each stage function bound to its protocol, so the type checker holds the signatures.
STAGES: tuple[BackUpAlbum, SelectRun, LoadBeforeFrames, LoadBeforeSidecar] = (
    back_up_album,
    select_run,
    load_before_frames,
    load_before_sidecar,
)
