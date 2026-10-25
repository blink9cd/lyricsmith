"""Test-side helpers for the backup and undo suites: frames, snapshots, a stand-in run.

`simulate_run` does to an album what a run does once its backup is written: it
normalizes each track's sidecars, embeds lyrics, writes the sidecar, and records the
``after`` block, through the real stage functions, so an undo test starts from the
state a real run leaves. `snapshot` captures everything an undo must put back, so a
test compares the album before the run with the album after the undo. Every lyric here
is invented.

`UndoCase` is the base of the undo suites: it builds an album, backs it up with the
real backup stage, and undoes it. Not discovered as a suite (no ``test_`` prefix).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

import fixtures
import lyrics_backup
import lyrics_manifest
import lyrics_model as lm
import lyrics_outcomes
import lyrics_records as lr
import lyrics_sidecar
import lyrics_slots
import lyrics_tags
import lyrics_undo
import run_fakes
import tag_builders

#: Invented synced lyrics a stand-in run embeds.
RUN_SYNCED = lm.SyncedLyrics(
    (
        lm.SyncedLine(1500, "The run wrote this line"),
        lm.SyncedLine(6000, "and this one after it"),
    )
)
#: Invented plain lyrics a stand-in run embeds.
RUN_PLAIN = lm.PlainLyrics("A plain verse the run wrote\nover whatever was there\n")


def lyrics_frames(path: Path) -> Counter[lm.FrameRecord]:
    """*path*'s lyrics frames, every field compared, the stored order ignored.

    A save orders frames by size, so a restore puts the same frames back in another
    order; a multiset compares what the frames hold, byte for byte.
    """
    return Counter(lyrics_tags.read_tags(path).frames)


def synced_frame_lines(path: Path) -> tuple[lm.SyncedLine, ...]:
    """The timed lines *path*'s ``SYLT`` frames hold, each entry as a line in order."""
    return tuple(
        lm.SyncedLine(entry.time, entry.text)
        for frame in lyrics_tags.read_tags(path).frames
        if isinstance(frame.payload, tuple)
        for entry in frame.payload
    )


@dataclasses.dataclass(frozen=True)
class LinkTo:
    """A sidecar name holding a symbolic link, by the target the link stores."""

    target: str


def sidecars(mp3: Path) -> dict[str, bytes | LinkTo]:
    """The sidecars beside *mp3*, by name: a file's bytes, or a link as `LinkTo`.

    Read as the stages read a slot name (`lyrics_slots.sidecar_entry`), so a link is
    never mistaken for the file it leads to, and a folder there is no sidecar.
    """
    found: dict[str, bytes | LinkTo] = {}
    for path in lyrics_slots.sidecar_paths(mp3):
        entry = lyrics_slots.sidecar_entry(path)
        if entry is lyrics_slots.Slot.LINK:
            found[path.name] = LinkTo(os.readlink(path))
        elif entry is lyrics_slots.Slot.FILE:
            found[path.name] = path.read_bytes()
    return found


def undo_error(result: lr.UndoTrackResult) -> str:
    """*result*'s undo error as ``<type>: <message>``; empty when it has none.

    Every undo error is recorded against the undo stage, so that is asserted here.
    """
    if result.error is None:
        return ""
    assert result.error.stage is lr.ErrorStage.UNDO, result.error
    return f"{result.error.exc_type}: {result.error.message}"


@dataclasses.dataclass(frozen=True)
class TrackSnapshot:
    """What an undo must put back for one track."""

    version: tuple[int, int] | None
    frames: Counter[lm.FrameRecord]
    others: list[str]
    tail: bytes
    sidecars: Mapping[str, bytes | LinkTo]


def snapshot(mp3: Path) -> TrackSnapshot:
    """*mp3*'s ID3 version, lyrics frames, other frames, last 128 bytes and sidecars."""
    tags = lyrics_tags.read_tags(mp3)
    has_v2 = tags.id3_version is not None
    return TrackSnapshot(
        tags.id3_version,
        Counter(tags.frames),
        tag_builders.other_frames(mp3) if has_v2 else [],
        mp3.read_bytes()[-tag_builders.V1_TAG_BYTES :],
        sidecars(mp3),
    )


def snapshots(paths: Iterable[Path]) -> dict[Path, TrackSnapshot]:
    """A snapshot of each of *paths*."""
    return {path: snapshot(path) for path in paths}


def _run_track(album: Path, mp3: Path, lyrics: lm.Lyrics, lang: str) -> lr.TrackOutcome:
    """Do to *mp3* what a run does to a track; return the ``after`` block it records.

    The hash of each sidecar it leaves is recorded as the run records it, by the
    run's own `TrackState.hashes`.
    """
    state = lyrics_sidecar.normalize_sidecars(mp3, album=album, apply=True)
    lyrics_tags.write_lyrics(mp3, lyrics, lang)
    written = lyrics_sidecar.write_sidecar(mp3, lyrics)
    # A stand-in run's lyrics are its own invention, so no sidecar already holds them.
    assert written is not None
    hashes = lyrics_outcomes.TrackState(sidecar=state, written=written).hashes()
    return lr.TrackOutcome(
        lr.OutcomeStatus.WRITTEN,
        lm.Source.LRCLIB,
        lyrics.kind,
        sidecar_written=written.written,
        sidecars_removed=state.removed + written.removed,
        sidecars_renamed=state.renamed,
        frames_written=True,
        sidecar_hashes=hashes,
    )


def simulate_run(
    album: Path,
    manifest: lr.Manifest,
    lyrics: lm.Lyrics = RUN_SYNCED,
    *,
    record: bool = True,
    lang: str = "eng",
) -> lr.Manifest:
    """Run *manifest*'s backed-up tracks as a run would, embedding *lyrics* in each.

    With *record*, each track's ``after`` block is written and the run marked
    complete, as a finished run leaves its manifest. Without it the manifest is left
    as the backup wrote it, as a run killed after its backup leaves it.
    """
    tracks: list[lr.ManifestTrack] = []
    for track in manifest.tracks:
        if track.before is not None:
            after = _run_track(album, album / track.path, lyrics, lang)
            track = dataclasses.replace(track, after=after if record else None)
        tracks.append(track)
    if not record:
        return manifest
    finished = dataclasses.replace(
        manifest, tracks=tuple(tracks), state=lr.RunState.COMPLETE
    )
    lyrics_manifest.write_manifest(lr.run_folder(album, manifest.run_id), finished)
    return finished


class UndoCase(fixtures.IsolatedCase):
    """Builds an album, backs it up, and undoes it."""

    def setUp(self) -> None:
        """An empty album folder for this test."""
        super().setUp()
        self.album = self.tmp / "album"
        self.album.mkdir()

    def track(
        self,
        relpath: str,
        frames: tuple[lm.FrameRecord, ...] = (),
        *,
        minor: int | None = 4,
        txt: str | None = None,
        lrc: str | None = None,
    ) -> Path:
        """A track at *relpath* holding *frames* and the given sidecars.

        A v2.3 or v2.4 track carries the frames a write must leave alone; a None
        *minor* builds a file with no ID3 tag at all.
        """
        path = self.album / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if minor is None:
            fixtures.build_silent_mp3(path, tag_builders.SECONDS, id3_version=None)
        else:
            tag_builders.build_rich_mp3(path, minor)
        if frames:
            tag_builders.plant_frames(path, frames)
        for suffix, text in ((".txt", txt), (".lrc", lrc)):
            if text is not None:
                path.with_suffix(suffix).write_text(text, encoding="utf-8", newline="")
        return path

    def back_up(
        self, *tracks: Path, started: dt.datetime = run_fakes.STARTED
    ) -> lr.Manifest:
        """Back up *tracks* as a run started at *started*."""
        return lyrics_backup.back_up_album(
            self.album,
            tracks,
            lang="eng",
            genius=lr.GeniusState.ABSENT,
            started=started,
        )

    def undo(
        self, run_id: str | None = None, track: str | None = None
    ) -> lr.UndoReport:
        """Undo *run_id* (the newest when None), for album-relative *track* or all.

        The run is chosen, and its manifest read, as `undo_album` does it, through the
        real backup and manifest stages.
        """
        run_dir = lyrics_backup.select_run(self.album, run_id)
        newest = lyrics_backup.select_run(self.album, None) == run_dir
        manifest = lyrics_manifest.read_manifest(run_dir)
        return lyrics_undo.undo_run(
            self.album, run_dir, manifest, newest=newest, track=track
        )

    def manifest(self, run_id: str) -> lr.Manifest:
        """Run *run_id*'s manifest as it now stands on disk."""
        return lyrics_manifest.read_manifest(lr.run_folder(self.album, run_id))

    def assert_statuses(self, report: lr.UndoReport, *statuses: lr.UndoStatus) -> None:
        """Assert *report*'s tracks ended with *statuses*, in manifest order."""
        self.assertEqual(tuple(track.status for track in report.tracks), statuses)
