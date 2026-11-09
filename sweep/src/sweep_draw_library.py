"""The sample's library read: every track's timing, its album's runs, and the ledger's fixes.

`classify_library` walks the library as the sweep does (`contained_albums`, so an album
reached through a link is dropped and named, then `discover_tracks`), reads each
album's run manifests newest first (`read_album_runs`), and classifies every track
through `sweep_draw_classify.track_stratum`. The hearing's report imports it to count
each group's size in the library by the draw's own rule.

**A track's timing** (`track_timing`) is read from its sidecars as the album tool plans
them (`plan_sidecars`, which reads and never writes) and from its frames: a timed
``.lrc`` with no machine label is provider-timed, one labeled machine-timed, timed
frames with no timed ``.lrc`` frames-only. Placeholders (an instrumental note, stamps
alone) are no lyrics, as the album tool reads them (`unless_placeholder`). Any error
reading a track is that track's, as the survey's per-track boundary holds it: the track
is unreadable, and the read goes on.

**The ledger** is read by the caller, read-only, and handed in as the albums a
fix-text visit names (`fix_text_albums`): each is a wrong-text album, since a fix that
took a provider's untimed answer looks like any run's write in its manifest.

A run folder that cannot be listed, and a manifest that cannot be read, are passed over
and said through *notice*, by the run's folder and the error's type, never its
message; an album holding no MP3 is no album. Nothing here writes.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from album import DEFAULT_LANG
from lyrics_album import AlbumUnusableError, discover_tracks
from lyrics_backup import runs_newest_first
from lyrics_inventory import TrackLyricsKind, embedded_lyrics, lyrics_kind
from lyrics_manifest import read_manifest
from lyrics_model import ManifestSchemaError, SyncedLyrics
from lyrics_placeholder import unless_placeholder
from lyrics_records import ManifestTrack
from lyrics_sidecar import plan_sidecars
from lyrics_tags import read_tags
from sweep_draw_classify import (
    Stratum,
    Timing,
    marks_wrong_text,
    track_stratum,
    withheld_mark,
)
from sweep_ledger import LedgerRecord, LedgerEntry, SweepPass
from sweep_survey import contained_albums

#: What a notice is said through.
Notice = Callable[[str], None]


@dataclass(frozen=True)
class AlbumRuns:
    """An album's manifest entries by album-relative track path, newest first.

    *marked* is True when any entry of any run marks a wrong text (`marks_wrong_text`).
    """

    entries: Mapping[str, tuple[ManifestTrack, ...]]
    marked: bool


@dataclass(frozen=True)
class ClassifiedTrack:
    """One track of the library, classified.

    *path* is absolute, *album* library-relative; *stratum* is its group or why it has
    none, and *withheld* whether an entry not undone marks its removal withheld.
    """

    path: Path
    album: str
    timing: Timing
    stratum: Stratum
    withheld: bool


def _timing_of(mp3: Path, album: Path) -> Timing:
    """*mp3*'s timing; raises what reading its sidecars or its tag raises."""
    state = plan_sidecars(mp3, album)
    sidecar = unless_placeholder(state.lyrics)
    if (
        isinstance(sidecar, SyncedLyrics)
        and lyrics_kind(sidecar) is TrackLyricsKind.SYNCED
    ):
        return Timing.PROVIDER_TIMED if state.label is None else Timing.MACHINE_TIMED
    frames = embedded_lyrics(read_tags(mp3).frames, DEFAULT_LANG).without_placeholders()
    if frames.synced is not None:
        return Timing.FRAMES_ONLY
    if lyrics_kind(sidecar) is TrackLyricsKind.UNTIMED:
        return Timing.UNTIMED
    return Timing.NOT_TIMED


def track_timing(mp3: Path, album: Path) -> Timing:
    """*mp3*'s timing, read inside *album*; unreadable when anything reading it raises.

    The frames rank by the album tool's default language, which decides no timing:
    every millisecond SYLT is a timed candidate in any language (`embedded_lyrics`).
    """
    try:
        return _timing_of(mp3, album)
    # The per-track boundary, as the survey's: a sidecar or tag that cannot be read,
    # a file that cannot be opened, or anything a malformed file raises, is this
    # track's, and the library read goes on.
    except Exception:
        return Timing.UNREADABLE


def _run_folders(album: Path, notice: Notice) -> tuple[Path, ...]:
    """*album*'s run folders newest first; none, said, when they cannot be listed."""
    try:
        return runs_newest_first(album)
    except OSError as error:
        notice(f"passed over the runs of {album}: {type(error).__name__}")
        return ()


def read_album_runs(album: Path, notice: Notice) -> AlbumRuns:
    """Every readable manifest entry of *album*, by track, newest first, and its mark."""
    entries: dict[str, list[ManifestTrack]] = {}
    marked = False
    for run_dir in _run_folders(album, notice):
        try:
            manifest = read_manifest(run_dir)
        except (ManifestSchemaError, OSError) as error:
            notice(f"passed over run {run_dir}: {type(error).__name__}")
            continue
        for entry in manifest.tracks:
            entries.setdefault(entry.path, []).append(entry)
            marked = marked or marks_wrong_text(entry)
    return AlbumRuns({k: tuple(v) for k, v in entries.items()}, marked)


def fix_text_albums(records: Iterable[LedgerRecord]) -> frozenset[str]:
    """The library-relative albums a fix-text visit in *records* names."""
    return frozenset(
        record.album
        for record in records
        if isinstance(record, LedgerEntry) and record.pass_ is SweepPass.FIX_TEXT
    )


def _album_tracks(album: Path) -> tuple[Path, ...]:
    """*album*'s MP3s, as the album tool finds them; none when it holds no MP3."""
    try:
        return discover_tracks(album).tracks
    except AlbumUnusableError:
        return ()


def _classify_album(
    album: Path, name: str, fixed: Collection[str], notice: Notice
) -> list[ClassifiedTrack]:
    """Every track of *album*, named *name* in the library, classified."""
    tracks = _album_tracks(album)
    if not tracks:
        return []
    runs = read_album_runs(album, notice)
    wrong_text = runs.marked or name in fixed
    found: list[ClassifiedTrack] = []
    for mp3 in tracks:
        entries = runs.entries.get(mp3.relative_to(album).as_posix(), ())
        timing = track_timing(mp3, album)
        stratum = track_stratum(timing, entries, wrong_text_album=wrong_text)
        withheld = any(withheld_mark(entry) for entry in entries)
        found.append(ClassifiedTrack(mp3, name, timing, stratum, withheld))
    return found


def classify_library(
    library: Path, fixed: Collection[str], *, notice: Notice
) -> tuple[ClassifiedTrack, ...]:
    """Every track of *library*, resolved, classified, in the walk's order.

    *fixed* holds the library-relative albums the ledger names as fixed
    (`fix_text_albums`). Each artist folder the walk cannot list, each album reached
    through a link, and each run passed over is said through *notice*; a library that
    cannot be listed raises `sweep_survey.LibraryUnlistableError`.
    """
    albums = contained_albums(
        library,
        on_skip=lambda name: notice(
            f"skipped artist folder {name}: it cannot be listed"
        ),
        on_linked=lambda name: notice(
            f"skipped album {name}: its artist or album folder is a link, "
            "or it lies outside the library"
        ),
    )
    found: list[ClassifiedTrack] = []
    for album in albums:
        name = album.relative_to(library).as_posix()
        found += _classify_album(album, name, fixed, notice)
    return tuple(found)
