r"""The silence pass's list: where it lives, how its lines read, and its resume reader.

**Where.** ``$XDG_STATE_HOME/lyricsmith/silence/silent-<UTC start>.tsv`` when that
variable holds an absolute path, else under ``~/.local/state``: the same state root the
ledger uses (`sweep_ledger.state_root`), never the library.

**What.** A header naming `COLUMNS`, then a row per silent track and per track that
could not be measured, and after each album's rows a marker line, ``#album-done``, a
tab and the album's key (`album_block`). An album with no silent track is its marker
alone, so a resumed pass knows it was measured. Every field is made TSV-safe: a tab,
carriage return or line feed becomes a space, so one line is one row. An unknown value
is an empty field. No row starts with ``#``: its first field is a verdict.

**Keys.** A marker names its album by `done_key`, an escaped form that is one to one:
a backslash, tab, line feed and carriage return are written ``\\``, ``\t``, ``\n`` and
``\r``, so two albums never share a key. A file name that is not UTF-8 reaches Python
holding lone surrogates, which no UTF-8 file can hold; the key and every row field
write each as its ``\udcXX`` escape (`stdio_utf8.printable`), so writing the list never fails on
one, and the backslash already escaped keeps that key distinct too.

**Read back.** `parse_resume` reads a list's bytes for ``--resume``: the albums its
markers name done, and how much of it to keep. A pass appends each album as one write,
so a crash can leave only the last album's lines unfinished: rows after the last
marker, or a last line with no line feed. Those are an unfinished album: the resume
cuts the file back to just past the last marker and measures that album again. The
list is a file a person may edit, so it is a parser of untrusted input: any complete
line before the cut that it does not expect raises `SilenceListError`, naming the
line, and nothing else. `parse_rows` reads the same bytes for the page, by the same
rules: each finished album's rows, with the album its marker names (`album_of_key`
undoes the key, which is one to one); the cut tail, an album the resume would measure
again, is ignored. A row's fields come back as written: making them TSV-safe lost what
a tab or line break was, so they are already safe text and are not undone. Both read
the file through the album tool's `lyrics_files.read_regular`, never following a link
and never past `MAX_LIST_BYTES`: a FIFO pipe, a device, a link or a larger file is a
`SilenceListError` at once, never a wait or a read of somewhere else. Nothing here
writes or prints.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_backup import RUN_ID_FORMAT
from stdio_utf8 import ENCODING, printable
from sweep_ledger import read_unlinked, state_root
from sweep_silence_verdict import Verdict

#: The largest list a resume or the page reads. A row is one silent or unmeasured
#: track and a marker one album, each well under a kilobyte, so a library of a
#: million tracks lists in a few MiB: this is far past any real list, and still a
#: bound on what a file swapped in can make the pass hold in memory.
MAX_LIST_BYTES = 64 * 1024 * 1024
#: The folder under the state directory that holds every silence list.
SILENCE_DIR_NAME = Path("lyricsmith") / "silence"
#: The list's columns, in order, as its header names them.
COLUMNS = ("verdict", "reason", "peak_dbfs", "length_s", "track_no", "title", "path")
#: The first field of the line closing an album's rows.
DONE_MARKER = "#album-done"
#: A marker line's fields: the marker and the album.
_MARKER_FIELDS = 2
_SEP = "\t"
_UNSAFE = str.maketrans({"\t": " ", "\r": " ", "\n": " "})
#: The escapes `done_key` writes; the backslash first, so an escape is never doubled.
_KEY_ESCAPES = (("\\", "\\\\"), ("\t", "\\t"), ("\n", "\\n"), ("\r", "\\r"))
#: Each escape a key holds, as `done_key` writes it: the backslash pairs, then a lone
#: surrogate's ``\udcXX`` (`printable`, lowercase hex as ``backslashreplace`` writes).
_KEY_ESCAPE = re.compile(r"\\(?:([\\tnr])|u(d[89a-f][0-9a-f]{2}))")
_KEY_UNESCAPES = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}
_HEADER = _SEP.join(COLUMNS)
_HEADER_BYTES = _HEADER.encode()
_VERDICTS = frozenset(str(verdict) for verdict in Verdict)


class SilenceListError(ValueError):
    """A silence list that cannot be read, or holds a line this tool does not write."""


@dataclass(frozen=True)
class ResumePoint:
    """What a list says for a resume: its done albums, and the bytes of it to keep.

    *keep* ends just past the last marker's line feed, or past the header's when no
    album is done; 0 when even the header has no line feed, so the resume writes the
    header again. *dropped* counts the lines after it, an unfinished album's.
    """

    done: frozenset[str]
    keep: int
    dropped: int


@dataclass(frozen=True)
class ListRow:
    """One track's row: a judged silent track, or one that could not be measured."""

    verdict: Verdict
    reason: str
    #: The whole-track peak in dBFS; None when not measured.
    peak_dbfs: float | None
    #: The track's length in seconds; None when unknown.
    length_s: float | None
    #: The track number as its tag writes it (``3/12``); None when untagged.
    track_no: str | None
    title: str | None
    #: The album relative to the library, ``/``, the track relative to the album.
    path: str


@dataclass(frozen=True)
class ReadRow:
    """One row read back for the page: the album its marker names, then its fields.

    Every field but the verdict is as the list writes it: a number to one decimal
    place, and an empty field for an unknown value.
    """

    album: str
    verdict: Verdict
    reason: str
    peak_dbfs: str
    length_s: str
    track_no: str
    title: str
    path: str


# --- Where -----------------------------------------------------------------------------


def list_dir(environ: Mapping[str, str]) -> Path:
    """The folder of every silence list, under the state directory *environ* names."""
    return state_root(environ) / SILENCE_DIR_NAME


def list_path(environ: Mapping[str, str], started: datetime) -> Path:
    """A new list's path, named by *started* in UTC (an aware time)."""
    stamp = started.astimezone(UTC).strftime(RUN_ID_FORMAT)
    return list_dir(environ) / f"silent-{stamp}.tsv"


# --- Lines -----------------------------------------------------------------------------


def tsv_safe(text: str) -> str:
    """*text* as one writable field of one line: each tab and line break a space."""
    return printable(text.translate(_UNSAFE))


def _number(value: float | None) -> str:
    """*value* to one decimal place, or empty when unknown: none, or not finite."""
    return "" if value is None or not math.isfinite(value) else f"{value:.1f}"


def header_line() -> str:
    """The list's header line, with its newline."""
    return _HEADER + "\n"


def format_row(row: ListRow) -> str:
    """*row* as one TSV line, without its newline."""
    fields = (
        str(row.verdict),
        row.reason,
        _number(row.peak_dbfs),
        _number(row.length_s),
        row.track_no or "",
        row.title or "",
        row.path,
    )
    return _SEP.join(tsv_safe(field) for field in fields)


def done_key(album: str) -> str:
    """*album* as a marker names it and `parse_resume` returns it: escaped, one to one."""
    for raw, escaped in _KEY_ESCAPES:
        album = album.replace(raw, escaped)
    return printable(album)


def marker_line(album: str) -> str:
    """The line marking *album* (relative to the library) done, with its newline."""
    return f"{DONE_MARKER}{_SEP}{done_key(album)}\n"


def album_block(rows: Iterable[ListRow], album: str) -> str:
    """*album*'s rows and its marker, each with its newline: appended as one write."""
    return "".join(format_row(row) + "\n" for row in rows) + marker_line(album)


# --- Reading back ----------------------------------------------------------------------


def _checked_fields(line: str) -> list[str]:
    """The fields of a marker or row *line*; raise on anything else."""
    if "\r" in line:
        raise SilenceListError("a carriage return")
    fields = line.split(_SEP)
    if fields[0] == DONE_MARKER:
        if len(fields) != _MARKER_FIELDS or not fields[1]:
            raise SilenceListError("a done marker without exactly one album")
        return fields
    if len(fields) != len(COLUMNS):
        raise SilenceListError(f"not {len(COLUMNS)} fields")
    if fields[0] not in _VERDICTS:
        raise SilenceListError("not a verdict this tool writes")
    return fields


def _line_fields(raw: bytes, number: int) -> list[str]:
    """`_checked_fields` of complete line *number*, which must be UTF-8; errors name it."""
    try:
        return _checked_fields(raw.decode(ENCODING))
    except UnicodeDecodeError as error:
        raise SilenceListError(f"line {number}: not UTF-8") from error
    except SilenceListError as error:
        raise SilenceListError(f"line {number}: {error}") from error


def _split_lines(data: bytes) -> tuple[list[bytes], bytes]:
    """*data*'s complete lines and its tail; raise unless the first line is the header.

    The header may be the tail: a list holding only a header with no line feed.
    """
    *complete, tail = data.split(b"\n")
    if (complete[0] if complete else tail) != _HEADER_BYTES:
        raise SilenceListError("line 1: not the silence list's header")
    return complete, tail


# fuzz-target
def parse_resume(data: bytes) -> ResumePoint:
    """Where a resume of the list *data* stands; raise `SilenceListError` on a bad line.

    Every complete line is checked; a last line with no line feed may hold anything,
    and is never read as a marker.
    """
    complete, tail = _split_lines(data)
    if not complete:
        return ResumePoint(frozenset(), 0, 1)
    done: set[str] = set()
    keep, offset, kept_lines = len(complete[0]) + 1, 0, 1
    for number, raw in enumerate(complete, start=1):
        offset += len(raw) + 1
        if number > 1 and (fields := _line_fields(raw, number))[0] == DONE_MARKER:
            done.add(fields[1])
            keep, kept_lines = offset, number
    dropped = len(complete) - kept_lines + (1 if tail else 0)
    return ResumePoint(frozenset(done), keep, dropped)


def album_of_key(key: str) -> str:
    """The album a marker's *key* names: each escape `done_key` writes undone.

    A backslash the writer never makes (a key a person edited) is left as written.
    """

    def undo(match: re.Match[str]) -> str:
        """The character one escape stands for."""
        pair, surrogate = match.groups()
        return _KEY_UNESCAPES[pair] if pair else chr(int(surrogate, 16))

    return _KEY_ESCAPE.sub(undo, key)


# fuzz-target
def parse_rows(data: bytes) -> tuple[ReadRow, ...]:
    """Every finished album's rows in the list *data*, in list order, with the album.

    Raise `SilenceListError` on a line `parse_resume` would refuse. Rows after the last
    marker, and a last line with no line feed, are an unfinished album: ignored.
    """
    complete, _ = _split_lines(data)
    rows: list[ReadRow] = []
    pending: list[list[str]] = []
    for number, raw in enumerate(complete[1:], start=2):
        fields = _line_fields(raw, number)
        if fields[0] != DONE_MARKER:
            pending.append(fields)
            continue
        album = album_of_key(fields[1])
        rows += (ReadRow(album, Verdict(row[0]), *row[1:]) for row in pending)
        pending = []
    return tuple(rows)


def read_list(path: Path) -> bytes:
    """The bytes of the list at *path*; raise `SilenceListError` naming it.

    A link, a FIFO pipe, a device, or a file past `MAX_LIST_BYTES` is refused
    before more than the cap is read; a link is named as one
    (`sweep_ledger.read_unlinked`).
    """
    try:
        data = read_unlinked(path, MAX_LIST_BYTES + 1)
    except OSError as error:
        raise SilenceListError(f"{path}: cannot be read ({error})") from error
    if len(data) > MAX_LIST_BYTES:
        raise SilenceListError(f"{path}: more than {MAX_LIST_BYTES} bytes")
    return data


def read_rows(path: Path) -> tuple[ReadRow, ...]:
    """`parse_rows` of the list at *path* (`read_list`); raise `SilenceListError`."""
    data = read_list(path)
    try:
        return parse_rows(data)
    except SilenceListError as error:
        raise SilenceListError(f"{path}: {error}") from error


def read_resume(path: Path) -> ResumePoint:
    """Where a resume of the list at *path* stands (`read_list`); raise `SilenceListError`."""
    data = read_list(path)
    try:
        return parse_resume(data)
    except SilenceListError as error:
        raise SilenceListError(f"{path}: {error}") from error
