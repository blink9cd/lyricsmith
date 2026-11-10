"""The Genius check's three lists: what Genius has, what it lacks, and what is unsure.

Each list holds every checked track of its finding (`sweep_genius_judge.Finding`), in
the track list's order, and is written into ``--out`` through
`lyrics_state.write_state_file` (0o600, atomic, read back), so a rerun replaces it:

- `HAS_IT_NAME`, a TSV under the header `HAS_IT_COLUMNS`: the track's path, the
  Genius song's URL, its title and its artist (its credits joined by ``&``).
- `NOT_ON_GENIUS_NAME`, a plain track list in the ``--changed-list`` format
  (`lyrics_changed.format_changed_list`), so it can feed a later step such as
  ``sync --tracks-from``.
- `UNSURE_NAME`, a TSV under the header `UNSURE_COLUMNS`: the track's path, why it is
  unsure, and the closest Genius song's URL, empty when there is none.

Every TSV field is made safe (`sweep_silence_list.tsv_safe`: each tab and line break a
space, a lone surrogate escaped). A has-it track whose path that would change is
written to the unsure list instead, its reason `UNLISTABLE_PATH`: the walker would read
the changed path back as no track and pass the row over, silently. A Genius title or
artist is cut to
`MAX_NAME_CHARS`, since Genius wrote it. A URL is written only when it is https on a
Genius host (`lyrics_genius.genius_url_allowed`), checked again here as it is written,
so no other URL reaches a list whatever a hit held. `read_has_it` reads a has-it list
back, strictly, for ``sweep --mode paste`` (`sweep_paste`). Nothing here prints.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_changed import format_changed_list
from lyrics_genius import genius_url_allowed, song_hit
from lyrics_genius_search import SearchHit
from lyrics_match import MAX_NAME_CHARS as MAX_NAME_CHARS
from lyrics_state import write_state_file
from stdio_utf8 import ENCODING
from sweep_genius_judge import Finding, Judged
from sweep_ledger import read_unlinked
from sweep_silence_list import tsv_safe

#: The three files written into --out.
HAS_IT_NAME: Final = "has-it.tsv"
NOT_ON_GENIUS_NAME: Final = "not-on-genius.txt"
UNSURE_NAME: Final = "unsure.tsv"
#: Each TSV's header, its columns in order.
HAS_IT_COLUMNS: Final = ("track", "genius url", "genius title", "genius artist")
UNSURE_COLUMNS: Final = ("track", "reason", "closest genius url")
_SEP: Final = "\t"
_LINEFEED: Final = "\n"
#: How a joint credit is written: as the matcher joins one.
_CREDIT_JOIN: Final = " & "
#: Why a track Genius has is listed unsure: its path cannot be written to has-it.tsv.
UNLISTABLE_PATH: Final = (
    "its path holds a tab, a line break or a lone surrogate, which has-it.tsv cannot "
    "hold, so the walker could not paste it"
)
#: The character no path can hold, refused in a has-it row as the changed list does.
_NUL: Final = "\0"
#: The largest has-it list read back: far past any real list, small enough to hold.
MAX_HAS_IT_BYTES: Final = 64 * 1024 * 1024


@dataclass(frozen=True)
class CheckedTrack:
    """One track of the list, by its path as listed, and what the check found.

    *unanswered* is set when Genius did not answer its search: refused, or never
    asked because the breaker was open.
    """

    path: str
    judged: Judged
    unanswered: bool = False


def _url(hit: SearchHit | None) -> str:
    """*hit*'s URL when it is a Genius one, else empty."""
    url = None if hit is None else hit.url
    return url if url is not None and genius_url_allowed(url) else ""


def _name(text: str) -> str:
    """A name Genius wrote, cut to the cap."""
    return text[:MAX_NAME_CHARS]


def _tsv(header: Sequence[str], rows: Iterable[Sequence[str]]) -> bytes:
    """*header* and *rows* as TSV bytes, each field made safe, each line ended."""
    lines = [header, *rows]
    text = "".join(
        _SEP.join(tsv_safe(field) for field in line) + _LINEFEED for line in lines
    )
    return text.encode(ENCODING)


def listed_finding(track: CheckedTrack) -> Judged:
    """*track*'s finding as the lists write it: unsure when had but unlistable.

    A summary counting the lists' rows counts through this, so it agrees with them.
    """
    judged = track.judged
    if judged.finding is Finding.HAS_IT and tsv_safe(track.path) != track.path:
        return Judged(Finding.UNSURE, UNLISTABLE_PATH, judged.hit)
    return judged


def _of(tracks: Iterable[CheckedTrack], finding: Finding) -> list[CheckedTrack]:
    """The *tracks* listed as found *finding* (`listed_finding`), in order."""
    listed = (replace(track, judged=listed_finding(track)) for track in tracks)
    return [track for track in listed if track.judged.finding is finding]


def _has_it_row(track: CheckedTrack) -> tuple[str, ...]:
    """*track*'s has-it row: its path, then the song's URL, title and credits."""
    hit = track.judged.hit
    # The judge never finds a track had without the hit that has its lyrics.
    if hit is None:
        raise ValueError(f"a has-it finding names no hit: {track.path}")
    credits = _CREDIT_JOIN.join(song_hit(hit).artists)
    return (track.path, _url(hit), _name(hit.title), _name(credits))


def format_has_it(tracks: Iterable[CheckedTrack]) -> bytes:
    """The has-it list's bytes: its header, then a row for each such track."""
    rows = (_has_it_row(track) for track in _of(tracks, Finding.HAS_IT))
    return _tsv(HAS_IT_COLUMNS, rows)


def format_unsure(tracks: Iterable[CheckedTrack]) -> bytes:
    """The unsure list's bytes: its header, then a row for each such track."""
    rows = (
        (track.path, track.judged.reason or "", _url(track.judged.hit))
        for track in _of(tracks, Finding.UNSURE)
    )
    return _tsv(UNSURE_COLUMNS, rows)


def format_not_on_genius(tracks: Iterable[CheckedTrack]) -> bytes:
    """The not-on-Genius list's bytes, in the ``--changed-list`` format.

    Every path came from a list the same format's reader accepted, so none is left
    out of it.
    """
    data, _ = format_changed_list(
        track.path for track in _of(tracks, Finding.NOT_ON_GENIUS)
    )
    return data


@dataclass(frozen=True)
class HasItRow:
    """One has-it row read back: the track, the Genius song's URL, title and artist."""

    track: Path
    url: str
    title: str
    artist: str


class HasItError(Exception):
    """A has-it list that cannot be read, or is not one this module wrote."""


def read_has_it(path: Path) -> tuple[HasItRow, ...]:
    """Every row of the has-it list at *path*, in order; `HasItError` if unusable.

    Strict, as `format_has_it` writes it: UTF-8, every line ended by a linefeed, the
    header `HAS_IT_COLUMNS`, then rows of exactly its four fields, each track an
    absolute path, and no NUL anywhere. Read no further than `MAX_HAS_IT_BYTES` and one byte, never
    through a link (`sweep_ledger.read_unlinked`). The URL is not judged here: the
    caller checks each before it is used.
    """
    try:
        data = read_unlinked(path, MAX_HAS_IT_BYTES + 1)
    except OSError as error:
        raise HasItError(f"cannot be read: {path}: {error}") from error
    if len(data) > MAX_HAS_IT_BYTES:
        raise HasItError(f"is more than {MAX_HAS_IT_BYTES} bytes: {path}")
    try:
        text = data.decode(ENCODING)
    except UnicodeDecodeError as error:
        raise HasItError(f"is not UTF-8: {path}") from error
    if not text.endswith(_LINEFEED):
        raise HasItError(f"is not a has-it list (its last line is not ended): {path}")
    header, *rows = text[: -len(_LINEFEED)].split(_LINEFEED)
    if header != _SEP.join(HAS_IT_COLUMNS):
        raise HasItError(
            f"is not a has-it list (its header is not the check's): {path}"
        )
    return tuple(
        _parsed_has_it_row(line, number, path) for number, line in enumerate(rows, 2)
    )


def _parsed_has_it_row(line: str, number: int, path: Path) -> HasItRow:
    """Line *number* of *path* as a row; `HasItError` when it is not one."""
    # No path holds NUL, and a check on one would fail far from the list, mid-walk.
    if _NUL in line:
        raise HasItError(f"is not a has-it list (line {number} holds a NUL): {path}")
    fields = line.split(_SEP)
    if len(fields) != len(HAS_IT_COLUMNS) or not os.path.isabs(fields[0]):
        raise HasItError(
            f"is not a has-it list (line {number} is not an absolute track and "
            f"{len(HAS_IT_COLUMNS) - 1} fields): {path}"
        )
    track, url, title, artist = fields
    return HasItRow(Path(track), url, title, artist)


def write_lists(out: Path, tracks: Sequence[CheckedTrack]) -> tuple[Path, ...]:
    """Write the three lists for *tracks* into *out*; their paths, in order.

    Raises `OSError` or `lyrics_model.ReadBackError` when one cannot be written, as
    `write_state_file` does; a list written before it stands.
    """
    written = (
        (out / HAS_IT_NAME, format_has_it(tracks)),
        (out / NOT_ON_GENIUS_NAME, format_not_on_genius(tracks)),
        (out / UNSURE_NAME, format_unsure(tracks)),
    )
    for path, data in written:
        write_state_file(path, data)
    return tuple(path for path, _ in written)
