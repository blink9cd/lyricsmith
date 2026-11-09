"""The strata file: which group of a drawn sample each track was drawn into, and the seed.

The sample's draw writes it beside the track list it draws, and the hearing's report
reads it back to give each group its own rate and weigh the groups. The format is
UTF-8 text, every line ended by a linefeed: a header ``#seed<TAB><n>``, ``<n>`` the draw's
seed as a decimal integer written as Python writes one, then one
``path<TAB>group<TAB>album`` row a track. A path is absolute; a group is one of
`StrataGroup`; an album is any non-empty text. No field holds a tab, a linefeed, a
carriage return, a NUL or a lone surrogate, and no path is listed twice.

**Refused whole.** As `lyrics_changed.parse_changed_list` refuses a list, one line the
format does not allow (a blank one, a last one with no linefeed, a group not of the
six, a seed in another spelling) makes the whole file unreadable: a report weighing a
sample with one row silently gone would give its group a wrong rate. What is accepted
writes back byte for byte (`format_strata`), and the writer refuses any row the reader
would, before anything is written. A refusal names the line, never its content.

**Private and bounded.** The file names a library's tracks, so it is written through
`lyrics_state.write_state_file` (0o600, atomic, read back), and read no further than
`MAX_STRATA_BYTES` and one byte, never through a link or anything but a regular file
(`sweep_ledger.read_unlinked`). Nothing here prints.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_state import write_state_file
from sweep_ledger import read_unlinked

#: The largest strata file read: far past any sample's, small enough to hold in memory.
MAX_STRATA_BYTES: Final = 64 * 1024 * 1024
_ENCODING: Final = "utf-8"
_LINEFEED: Final = "\n"
_TAB: Final = "\t"
#: The header's first field, before the seed.
SEED_KEY: Final = "#seed"
#: A seed as Python writes an integer: an optional minus and digits.
_SEED: Final = re.compile(r"-?[0-9]+")
#: What no field may hold: the format's two separators, a CR (a CRLF ending) and a NUL.
_NOT_IN_FIELDS: Final = (_TAB, _LINEFEED, "\r", "\x00")
_ROW_FIELDS: Final = 3


class StrataError(ValueError):
    """A strata file, or rows for one, the format refuses; never quotes the content."""


class StrataGroup(StrEnum):
    """The groups a sample is drawn in, as the strata file spells them."""

    WRONG_TEXT_ALBUMS = "wrong-text-albums"
    NETEASE = "netease"
    YOUTUBE_MUSIC = "youtube-music"
    LRCLIB = "lrclib"
    OLDER_SIDECARS = "older-sidecars"
    WITHHELD = "withheld"


@dataclass(frozen=True)
class StrataRow:
    """One drawn track: its absolute path, its group and its album."""

    path: str
    group: StrataGroup
    album: str


@dataclass(frozen=True)
class Strata:
    """A strata file's seed and its rows, in file order."""

    seed: int
    rows: tuple[StrataRow, ...]


def _field_refusal(text: str) -> str | None:
    """Why *text* cannot be a field, or None when it can."""
    if not text:
        return "an empty field"
    if any(part in text for part in _NOT_IN_FIELDS):
        return "a field holding a separator, a carriage return or a NUL"
    if not text.isascii():
        # A lone surrogate (an undecodable file name) has no UTF-8 spelling, so the
        # file could not hold it: refuse it here, never as an encoding error at write.
        try:
            text.encode(_ENCODING)
        except UnicodeEncodeError:
            return "a field that is not UTF-8 text"
    return None


def _row_refusal(row: StrataRow) -> str | None:
    """Why *row* cannot be written or read, or None when it can."""
    if type(row.group) is not StrataGroup:
        return "a group not of the six"
    reason = _field_refusal(row.path) or _field_refusal(row.album)
    if reason is None and not Path(row.path).is_absolute():
        return "a path that is not absolute"
    return reason


def _checked_rows(rows: Iterable[StrataRow], first_line: int) -> tuple[StrataRow, ...]:
    """*rows*, unless one is refused or a path repeats; `StrataError` naming its line."""
    seen: set[str] = set()
    kept: list[StrataRow] = []
    for number, row in enumerate(rows, start=first_line):
        reason = _row_refusal(row)
        if reason is None and row.path in seen:
            reason = "a path listed twice"
        if reason is not None:
            raise StrataError(f"line {number}: {reason}")
        seen.add(row.path)
        kept.append(row)
    return tuple(kept)


def format_strata(seed: int, rows: Iterable[StrataRow]) -> bytes:
    """The strata file's bytes for *seed* and *rows*; `StrataError` for a refused row.

    A seed that is not an int (a bool included) is refused too. Each row is checked as
    the reader checks it, so every file written reads back as these rows.
    """
    if type(seed) is not int:
        raise StrataError("line 1: a seed that is not an integer")
    checked = _checked_rows(rows, first_line=2)
    lines = [f"{SEED_KEY}{_TAB}{seed}"]
    lines += [_TAB.join((row.path, row.group, row.album)) for row in checked]
    return "".join(line + _LINEFEED for line in lines).encode(_ENCODING)


def _parse_seed(line: str) -> int:
    """The seed the header *line* names; `StrataError` unless it is the header."""
    key, _, text = line.partition(_TAB)
    if key != SEED_KEY or not _SEED.fullmatch(text):
        raise StrataError("line 1: not the strata file's seed header")
    try:
        seed = int(text)
    except ValueError as error:  # past the interpreter's digit limit
        raise StrataError("line 1: a seed too long to read") from error
    if str(seed) != text:
        raise StrataError("line 1: a seed not written as an integer is")
    return seed


def _parse_row(line: str, number: int) -> StrataRow:
    """The row the line *line*, number *number*, holds; `StrataError` when refused."""
    fields = line.split(_TAB)
    if len(fields) != _ROW_FIELDS:
        raise StrataError(f"line {number}: not three fields")
    path, group, album = fields
    try:
        return StrataRow(path, StrataGroup(group), album)
    except ValueError as error:
        raise StrataError(f"line {number}: a group not of the six") from error


# fuzz-target
def parse_strata(data: bytes) -> Strata:
    """The seed and rows a strata file's bytes hold; `StrataError` when any line is refused.

    The format held strictly: UTF-8, every line ended by a linefeed, the seed header
    first, then rows of three fields, each checked as the writer checks it. Raises
    nothing else.
    """
    try:
        text = data.decode(_ENCODING)
    except UnicodeDecodeError as error:
        raise StrataError("the strata file is not UTF-8") from error
    *lines, tail = text.split(_LINEFEED)
    if tail:
        raise StrataError(f"line {len(lines) + 1}: not ended by a linefeed")
    if not lines:
        raise StrataError("line 1: not the strata file's seed header")
    seed = _parse_seed(lines[0])
    rows = (_parse_row(line, number) for number, line in enumerate(lines[1:], 2))
    return Strata(seed, _checked_rows(rows, first_line=2))


def write_strata(target: Path, seed: int, rows: Iterable[StrataRow]) -> None:
    """Write the strata file *target* for *seed* and *rows*: 0o600, atomic, read back.

    A refused row raises `StrataError` with nothing written; a failed write raises
    `OSError` or `lyrics_model.ReadBackError`, as `write_state_file` does.
    """
    write_state_file(target, format_strata(seed, rows))


def read_strata(target: Path) -> Strata:
    """The strata file *target*'s seed and rows; `StrataError` naming why it is unread.

    A missing file, a link, a FIFO pipe, a device, a file past `MAX_STRATA_BYTES` and
    one line the format refuses are each a `StrataError`, never a partial read.
    """
    try:
        data = read_unlinked(target, MAX_STRATA_BYTES + 1)
    except OSError as error:
        raise StrataError(f"{target}: cannot be read ({error})") from error
    if len(data) > MAX_STRATA_BYTES:
        raise StrataError(f"{target}: more than {MAX_STRATA_BYTES} bytes")
    try:
        return parse_strata(data)
    except StrataError as error:
        raise StrataError(f"{target}: {error}") from error
