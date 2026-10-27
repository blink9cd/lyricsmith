"""The silence pass's HTML page: the whole list as one self-contained page, beside it.

`format_page` is pure: it takes the rows `sweep_silence_list.read_rows` reads back
from the whole list, every leg of a resumed one included, and lays them out through
the shared HTML page library (`html_page`), found through the entry point's
bootstrap: a summary of the count per verdict, each linked to its section, then a
section per verdict, the ones most worth a look first. Each section's rows are sorted
by album, then track path, and show the album, the track's file in it, track number,
title, length, peak and reason; an empty section shows its count of 0 and a short note.
Every value is shown as the terminal shows it (`stdio_utf8.spell_unsafe`), then
escaped for the page, so a direction mark reads the same in both. The header names
when the page was written and the list's file name, never its folder, so the page
carries no machine path. Its head carries `MARKER`, the line this pass knows its own
page by.

`write_page` reads the list, builds the page and replaces ``<list name>.html`` beside
it atomically, through the album tool's `lyrics_files.replace_atomically`, so a reader
never sees half a page, and a failed write leaves no temporary file behind. The page
is written readable by the user alone (0o600), as the list is: it names the same
tracks. It replaces only a page this pass wrote: a file there without `MARKER`, one
that is the list itself under a second name (another case on a case-insensitive
volume), or anything but a regular file (a FIFO pipe, read without waiting on it) is
refused with `PageRefusedError` and left as it is.
"""

from __future__ import annotations

import html
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import html_page
from html_page import Cell, Row, escaped
from lyrics_files import NotRegularFileError, read_regular, replace_atomically
from lyrics_state import STATE_FILE_MODE
from stdio_utf8 import ENCODING, spell_unsafe
from sweep_silence_list import ReadRow, read_rows, tsv_safe
from sweep_silence_verdict import GAP_MAX_S, Verdict

#: The page's title.
TITLE = "Silent tracks"
#: The footer's line.
READ_ONLY = "Read-only: nothing in the library was changed."
#: The page's suffix, in place of the list's.
PAGE_SUFFIX = ".html"
#: What a cell with no value shows: an untagged title, a length not read.
DASH = "—"
#: What an empty section shows in place of its table.
EMPTY_NOTE = "No track in this list has this verdict."
#: The table's columns, in order.
COLUMNS = (
    "Album",
    "File",
    "Track no.",
    "Title",
    "Length (s)",
    "Peak (dBFS)",
    "Reason",
)
#: The tool the page's generator meta element names.
GENERATOR = "lyricsmith sweep --mode silence"
#: The line in the page's head that marks it as this pass's own: only a file holding
#: it is replaced. It is the element `html_page.page` writes for `GENERATOR`.
MARKER = f'<meta name="generator" content="{html.escape(GENERATOR)}">'
#: How much of an existing file is read for `MARKER`, which sits in the first lines.
_MARKER_WINDOW = 4096


class PageRefusedError(ValueError):
    """The path holds a file this pass did not write, the list, or no regular file."""


@dataclass(frozen=True)
class Section:
    """One verdict's section: the verdict, its anchor, its heading and its note."""

    verdict: Verdict
    anchor: str
    heading: str
    note: str


#: Every section, in the page's order: the likely mistakes first, the meant last.
SECTIONS = (
    Section(
        Verdict.RIPPING_MISTAKE,
        "ripping-mistake",
        "Likely a ripping mistake",
        f"A real title, over {GAP_MAX_S:g} seconds, measured silent: most likely a"
        " song that came out silent when it was ripped.",
    ),
    Section(
        Verdict.UNSURE,
        "unsure",
        "Unsure",
        "Silent, but no rule fits, or the length could not be read: listen to decide.",
    ),
    Section(
        Verdict.NOT_MEASURED,
        "not-measured",
        "Not measured",
        "ffmpeg could not measure these; the reason is its own.",
    ),
    Section(
        Verdict.LIKELY_MEANT,
        "likely-meant",
        "Likely meant",
        f"A placeholder title, at most {GAP_MAX_S:g} seconds: a gap track, or a"
        " spacer run before a hidden track.",
    ),
)


# --- Rows ----------------------------------------------------------------------------


def _shown(value: str) -> str:
    """*value* as page text, each character spelled as the terminal's lines spell it."""
    return escaped(spell_unsafe(value))


def _text(value: str) -> Cell:
    """A text cell; an empty value reads as a dash."""
    return Cell(_shown(value)) if value else Cell(DASH)


def _number(value: str) -> Cell:
    """A number cell, in even-width figures; an empty value reads as a dash."""
    return Cell(_shown(value), numeric=True) if value else Cell(DASH, numeric=True)


def track_file(row: ReadRow) -> str:
    """*row*'s track relative to its album: its path less the album and ``/``.

    The path holds the album as the list writes it (`tsv_safe`), so that is what is
    taken off. A path that does not start with it, the library's own folder's (whose
    album is ``.``) or a row a person edited, is shown whole.
    """
    return row.path.removeprefix(tsv_safe(row.album) + "/")


def _row(row: ReadRow) -> Row:
    """One track's cells, in `COLUMNS` order."""
    return (
        _text(row.album),
        _text(track_file(row)),
        _number(row.track_no),
        _text(row.title),
        _number(row.length_s),
        _number(row.peak_dbfs),
        _text(row.reason),
    )


def _section(section: Section, rows: Sequence[ReadRow]) -> list[str]:
    """*section* with its rows, sorted by album then track path; a note when none."""
    mine = sorted(
        (row for row in rows if row.verdict is section.verdict),
        key=lambda row: (row.album, row.path),
    )
    body = (
        html_page.table(COLUMNS, [_row(row) for row in mine])
        if mine
        else [f'<p class="note">{EMPTY_NOTE}</p>']
    )
    return html_page.section(
        section.anchor, section.heading, len(mine), section.note, body
    )


# --- The page ------------------------------------------------------------------------


def _header(list_name: str, written_at: datetime) -> list[str]:
    """When the page was written, in UTC, and the list's file name."""
    written = written_at.astimezone(UTC)
    return [
        f'<p class="library">Written on {written:%Y-%m-%d at %H:%M} UTC'
        f" from the list {escaped(list_name)}.</p>"
    ]


def format_page(
    rows: Sequence[ReadRow], *, list_name: str, written_at: datetime
) -> str:
    """The page of *rows*, read from the list *list_name*, written at *written_at*.

    *written_at* is an aware time; the header shows it in UTC, to the minute.
    """
    summary = html_page.summary_section(
        [
            html_page.summary_row(
                s.heading, sum(row.verdict is s.verdict for row in rows), s.anchor
            )
            for s in SECTIONS
        ]
    )
    sections = [line for s in SECTIONS for line in _section(s, rows)]
    return html_page.page(
        title=TITLE,
        header=_header(list_name, written_at),
        main=[*summary, *sections],
        read_only=READ_ONLY,
        generator=GENERATOR,
    )


# --- Writing it ----------------------------------------------------------------------


def page_path(list_path: Path) -> Path:
    """The page beside *list_path*: its name with `PAGE_SUFFIX` for its suffix.

    A list that already ends in `PAGE_SUFFIX`, in any case, gets it added, so the
    page is never the list itself; `write_page` still refuses a page that is.
    """
    if list_path.suffix.casefold() == PAGE_SUFFIX:
        return list_path.with_name(list_path.name + PAGE_SUFFIX)
    return list_path.with_suffix(PAGE_SUFFIX)


def refuse_replacing(list_path: Path, path: Path) -> None:
    """Raise `PageRefusedError` unless the page at *path* may be replaced.

    Nothing there may be replaced; the list itself never may; any other file only
    when it holds `MARKER`, and anything but a regular file never: a FIFO pipe is
    refused at once, never waited on. Raises OSError when what is there cannot be
    read, a link included, which is never read through.
    """
    if not os.path.lexists(path):
        return
    if path.exists() and os.path.samefile(path, list_path):
        raise PageRefusedError("it is the list itself")
    try:
        head = read_regular(path, _MARKER_WINDOW, follow=False)
    except NotRegularFileError as error:
        raise PageRefusedError("it is not a regular file") from error
    if MARKER.encode(ENCODING) not in head:
        raise PageRefusedError("it exists and is not a page this pass wrote")


def write_page(list_path: Path, written_at: datetime) -> Path:
    """Write the page of the list at *list_path* beside it; return the page's path.

    Raises `sweep_silence_list.SilenceListError` when the list cannot be read back,
    `PageRefusedError` when the page may not be replaced (`refuse_replacing`), and
    OSError when the page cannot be written.
    """
    rows = read_rows(list_path)
    page = format_page(rows, list_name=list_path.name, written_at=written_at)
    path = page_path(list_path)
    refuse_replacing(list_path, path)
    replace_atomically(path, page.encode(ENCODING), mode=STATE_FILE_MODE)
    return path
