"""The silence pass's work on one album: measure every track, judge each silent one.

Every MP3 the album tool would find in the album (`lyrics_album.walk_album`) is measured
with the album tool's own ffmpeg measurement (`lyrics_silence.measure_level`), whatever
lyrics it holds. A folder of the album that cannot be listed, a disc folder included,
makes the whole album unreadable rather than shorter, and each link the walk skipped is
returned by its list path, for the caller to name. Only once the whole album is measured
is each silent track judged (`sweep_silence_verdict.judge_silent_track`), since a spacer
run's rule needs the number of silent tracks the album holds. A track that could not be
measured is listed as such, with the measurement's reason. The title, track number and
length of a listed track are read through the album tool's tag reading; a track whose
tags cannot be read lists them empty, so a malformed file costs only its own fields, and
is named with its error's type for the caller to report. Nothing here writes or prints:
`measure_album` returns the album's rows, counts, unreadable tracks and links for the
caller to write.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_album import Discovery, walk_album
from lyrics_silence import Level, MeasureFailed, Runner, is_silent, measure_level
from lyrics_tags import read_tags, read_track_number
from sweep_silence_list import ListRow
from sweep_silence_verdict import Verdict, judge_silent_track

#: The name the list gives an album that is the library's own folder.
_ROOT_NAME = "."


@dataclass(frozen=True)
class TrackFacts:
    """What a listed track's tags say: its title, its number as written, its length."""

    title: str | None
    track_no: str | None
    length_s: float | None
    #: The type of the error that kept the tags from being read; None when they were.
    error: str | None = None


@dataclass(frozen=True)
class AlbumMeasure:
    """One album measured: its rows, in track order, and its counts.

    *measured* counts the tracks with a peak, *silent* those at or below the
    threshold, *not_measured* those with none. *unreadable* names each listed track
    whose tags could not be read, by its list path and its error's type, as the
    survey names its unreadable tracks. *links* names each linked MP3 or folder the
    walk skipped, by its list path.
    """

    rows: tuple[ListRow, ...]
    measured: int
    silent: int
    not_measured: int
    unreadable: tuple[str, ...] = ()
    links: tuple[str, ...] = ()


def album_name(album: Path, library: Path) -> str:
    """*album* relative to *library*, as the list names it; ``.`` for the library itself."""
    return album.relative_to(library).as_posix() or _ROOT_NAME


def track_facts(mp3: Path) -> TrackFacts:
    """*mp3*'s title, track number and length; all unknown when its tags cannot be read."""
    try:
        tags = read_tags(mp3)
        number = read_track_number(mp3)
    # The per-track boundary, as the survey's: a file mutagen cannot parse, or one that
    # vanished since it was measured, costs only this track's fields.
    except Exception as error:
        return TrackFacts(None, None, None, type(error).__name__)
    return TrackFacts(tags.title, number, tags.duration_s)


def _unlistable(error: OSError) -> None:
    """Raise *error*: a folder of the album that cannot be listed stops its walk."""
    raise error


def album_tracks(album: Path) -> Discovery:
    """Every MP3 of *album* in the album tool's order, and every link skipped.

    The album tool's discovery passes over a folder it cannot list without a word, so
    this walk raises the OSError instead, for the album's own folder and for any folder
    in it: an album missing a disc is named, not read as one holding fewer tracks. A
    folder holding no MP3 has no tracks.
    """
    return walk_album(album, onerror=_unlistable)


def _path(name: str, album: Path, mp3: Path) -> str:
    """The list's path for *mp3*: the album's *name*, ``/``, the track's path in it."""
    track = mp3.relative_to(album).as_posix()
    return track if name == _ROOT_NAME else f"{name}/{track}"


def _row(facts: TrackFacts, path: str, level: Level, silent: int) -> ListRow:
    """The row at *path*, silent or not measured, judged against *silent* in the album."""
    if isinstance(level, MeasureFailed):
        verdict, reason, peak = Verdict.NOT_MEASURED, level.reason, None
    else:
        judged = judge_silent_track(facts.title, facts.length_s, silent)
        verdict, reason, peak = judged.verdict, judged.reason, level.dbfs
    return ListRow(
        verdict,
        reason,
        peak,
        facts.length_s,
        facts.track_no,
        facts.title,
        path,
    )


def _listed(level: Level) -> bool:
    """Whether a track measured *level* is listed: silent, or not measured."""
    return isinstance(level, MeasureFailed) or is_silent(level)


def measure_album(
    album: Path, name: str, ffmpeg: str, *, run: Runner | None = None
) -> AlbumMeasure:
    """Measure every track of *album* (*name* in the list) with *ffmpeg*; list the silent.

    *run* is `measure_level`'s process seam. Raises OSError when a folder of the
    album cannot be listed (`album_tracks`).
    """
    found = album_tracks(album)
    levels = [(mp3, measure_level(mp3, ffmpeg, run=run)) for mp3 in found.tracks]
    failed = sum(isinstance(level, MeasureFailed) for _, level in levels)
    silent = sum(_listed(level) for _, level in levels) - failed
    listed = [
        (_path(name, album, mp3), track_facts(mp3), level)
        for mp3, level in levels
        if _listed(level)
    ]
    rows = tuple(_row(facts, path, level, silent) for path, facts, level in listed)
    unreadable = tuple(
        f"{path} ({facts.error})" for path, facts, _ in listed if facts.error
    )
    links = tuple(_path(name, album, link) for link in found.links)
    return AlbumMeasure(rows, len(levels) - failed, silent, failed, unreadable, links)
