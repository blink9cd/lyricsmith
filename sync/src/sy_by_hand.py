"""The ``--by-hand-list`` file: each track left for a person, so a resumed run skips it.

**The format.** UTF-8, one row per line, each line ended by a linefeed, three
tab-separated fields: the track's absolute path as the walk names it, the reason, and
the detail. Four reasons: ``low confidence``, the track got no ``.lrc`` and its detail
is the median line confidence as its summary row prints it (`UNMEASURED` for a result
that carries none, so the track is never left off unsaid); ``not sung``, the track got
no ``.lrc`` because whisper-cli did not hear its text in the vocals, and its detail is
the share of what was heard that is the text's (`UNMEASURED` likewise); ``first line``,
the track's ``.lrc`` was written but its first line's stamp, the detail, wants checking
by ear; and ``little heard``, the track's ``.lrc`` was written but too few word pairs
were heard to check its text, the detail their count. A path the format cannot hold (a
tab, a linefeed, a carriage return or a NUL in it, not strict UTF-8, or not absolute) is
left out, and the track's row says so (`path_refusal`, the album tool's changed-list
rules with the tab and any carriage return added). Blank lines are skipped, so a person
may tidy the file by hand.

**The read.** `read_by_hand_list` reads the whole file at the start of a run, before
any track is timed, never through a link, never from a FIFO pipe or a device, and
never past `MAX_LIST_BYTES` (the sweep's `read_unlinked`). A missing file is an empty
list. It is a file a person edits, so it is a parser of untrusted input: any line it
does not expect raises `ByHandListError` naming the line, and nothing else
(`parse_by_hand_list`). A ``low confidence`` or ``not sung`` row skips its track
(`sy_plan.listed_by_hand`); removing the row is how the track is timed again, and so,
for a ``not sung`` row whose transcript `--heard-dir` kept, is changing its text.

**The write.** `Recorder` keeps every row it read, rows for tracks outside this walk
included, and saves the whole file again each time a finished track changes it: a
low-confidence or not-sung track adds its row, and a track that wrote its ``.lrc``
replaces its earlier ``first line`` and ``little heard`` rows, if any, with its new
ones, or drops each that no longer holds, since it was about the replaced file; its
earlier ``not sung`` or ``low confidence`` row, from before it was timed, goes too. A
track left low-confidence, or with a text longer than the track, was heard sung, so its
earlier ``not sung`` row goes as well. Each
save is an atomic replace made with Ctrl-C held (the album tool's `deferred_interrupt`),
not an append: a reader, or the next run, sees the old file or the new one whole, never
a torn last line, and an interrupted run keeps every row it reached. It stays cheap,
since a row is added only after a track's minutes of timing, and a file of a whole
library's rows is a few hundred kilobytes. The file is written as the state files are
(`lyrics_state.write_state_file`): 0o600, each missing folder above it made 0o700, and
confirmed by a read-back. A link, FIFO or anything but a regular file at its name is
refused rather than replaced. A failed save is said in the track's row, and the run then
exits 1. Nothing here prints.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Final

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
import lyrics_files
from lyrics_album import deferred_interrupt
from lyrics_changed import refusal
from lyrics_state import write_state_file
from sweep_ledger import read_unlinked
from sy_types import WRITTEN, Action, Outcome, TrackResult

#: The reason of a track that got no .lrc and is left to be timed by hand.
REASON_LOW: Final = "low confidence"
#: The reason of a written track whose flagged first line wants checking by ear.
REASON_FIRST: Final = "first line"
#: The reason of a track that got no .lrc: whisper-cli did not hear its text sung.
REASON_NOT_SUNG: Final = "not sung"
#: The reason of a written track whose text too few heard pairs could check.
REASON_LITTLE: Final = "little heard"
REASONS: Final = (REASON_LOW, REASON_FIRST, REASON_NOT_SUNG, REASON_LITTLE)
#: The reasons whose track a run skips, and the skip each plans as: the track got no
#: .lrc, and timing it again would give the same answer.
SKIPPED_REASONS: Final[Mapping[str, Action]] = MappingProxyType(
    {REASON_LOW: Action.SKIP_BY_HAND, REASON_NOT_SUNG: Action.SKIP_NOT_SUNG}
)
#: The detail of a low-confidence or not-sung row whose result carries no figure: the
#: track is still listed, and so still skipped, rather than left out without a word.
UNMEASURED: Final = "not measured"
#: Why a path is left out, beyond the album tool's changed-list rules.
REASON_TAB: Final = "holds a tab, which would split it into more fields"
REASON_CR: Final = "holds a carriage return"
#: The largest list a run reads. A row is one track, well under a kilobyte, so a
#: library of a million tracks lists in well under this; it still bounds what a file
#: swapped in can make a run hold in memory.
MAX_LIST_BYTES = 64 * 1024 * 1024
#: The option's name, as each line it adds to a track's row starts.
OPTION: Final = "--by-hand-list"
#: Each outcome, and the reasons its track's earlier rows stop being true with it.
_STALE: Final = MappingProxyType(
    {
        # A written .lrc ends every row about the track's earlier state: the old
        # file's notes, and a not-sung or low-confidence row from before it was timed.
        **{outcome: frozenset(REASONS) for outcome in WRITTEN},
        # A track heard and aligned, however poorly, was sung: its not-sung row, from
        # an earlier text, no longer holds.
        Outcome.LOW_CONFIDENCE: frozenset({REASON_LOW, REASON_NOT_SUNG}),
        Outcome.TOO_LONG: frozenset({REASON_NOT_SUNG}),
        Outcome.NOT_SUNG: frozenset({REASON_NOT_SUNG}),
    }
)
_SEP = "\t"
_FIELDS = 3
_LINEFEED = b"\n"
_ENCODING = "utf-8"


class ByHandListError(ValueError):
    """A list that cannot be read, or holds a line this tool does not write."""


@dataclass(frozen=True)
class Row:
    """One listed track: its absolute path, the reason, and the detail."""

    path: str
    reason: str
    detail: str


def path_refusal(path: str) -> str | None:
    """Why a row cannot hold *path*, or None when it can.

    The album tool's changed-list rules (`lyrics_changed.refusal`), and two more: a
    tab would split the row's fields, and a carriage return anywhere is refused, not
    only at the end, so no row can be read as another.
    """
    if _SEP in path:
        return REASON_TAB
    if "\r" in path:
        return REASON_CR
    return refusal(path)


def format_rows(rows: Iterable[Row]) -> bytes:
    """The file's bytes for *rows*, each a line; each path is one `path_refusal` passes."""
    lines = (_SEP.join((row.path, row.reason, row.detail)) + "\n" for row in rows)
    return "".join(lines).encode(_ENCODING)


def _checked_row(raw: bytes) -> Row | None:
    """One terminated line's row; None for a blank line; raise on anything else."""
    if b"\x00" in raw:
        raise ByHandListError("a NUL")
    try:
        text = raw.decode(_ENCODING)
    except UnicodeDecodeError as error:
        raise ByHandListError("not UTF-8") from error
    if "\r" in text:
        raise ByHandListError("a carriage return")
    if not text.strip():
        return None
    fields = text.split(_SEP)
    if len(fields) != _FIELDS:
        raise ByHandListError(f"not {_FIELDS} tab-separated fields")
    path, reason, detail = fields
    if reason not in REASONS:
        raise ByHandListError("not a reason this tool writes")
    refused = path_refusal(path)
    if refused is not None:
        raise ByHandListError(f"the path {refused}")
    return Row(path, reason, detail)


def _line_row(raw: bytes, number: int) -> Row | None:
    """`_checked_row` of line *number*, its error naming the line."""
    try:
        return _checked_row(raw)
    except ByHandListError as error:
        raise ByHandListError(f"line {number}: {error}") from error


# fuzz-target
def parse_by_hand_list(data: bytes) -> tuple[Row, ...]:
    """Every row in a list file's bytes, in order; raise `ByHandListError` on a bad line.

    Each line must end with a linefeed: a last line without one is a file a writer
    stopped in, or a person's edit, and is refused rather than guessed at.
    """
    *complete, tail = data.split(_LINEFEED)
    if tail:
        raise ByHandListError(f"line {len(complete) + 1}: no linefeed ends it")
    rows = (_line_row(raw, number) for number, raw in enumerate(complete, start=1))
    return tuple(row for row in rows if row is not None)


def read_by_hand_list(path: Path) -> tuple[Row, ...]:
    """The rows of the list at *path*; none when it is missing.

    Raise `ByHandListError` naming *path*: a link, FIFO pipe or device, a file that
    cannot be read or is over `MAX_LIST_BYTES`, or a line `parse_by_hand_list` refuses.
    """
    try:
        data = read_unlinked(path, MAX_LIST_BYTES + 1)
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise ByHandListError(f"{path}: cannot be read ({error})") from error
    if len(data) > MAX_LIST_BYTES:
        raise ByHandListError(f"{path}: more than {MAX_LIST_BYTES} bytes")
    try:
        return parse_by_hand_list(data)
    except ByHandListError as error:
        raise ByHandListError(f"{path}: {error}") from error


def skipped_paths(rows: Iterable[Row]) -> dict[str, Action]:
    """Each track a run skips, by the path of its ``low confidence`` or ``not sung`` row.

    Each maps to the skip its reason plans as; a track with rows of both reasons takes
    the later row's.
    """
    return {
        row.path: SKIPPED_REASONS[row.reason]
        for row in rows
        if row.reason in SKIPPED_REASONS
    }


def _new_rows(result: TrackResult) -> tuple[Row, ...]:
    """The rows *result* adds to the list: none, its one skip, or a written track's notes."""
    path = str(result.audio)
    if result.outcome is Outcome.LOW_CONFIDENCE:
        return (Row(path, REASON_LOW, result.confidence or UNMEASURED),)
    if result.outcome is Outcome.NOT_SUNG:
        return (Row(path, REASON_NOT_SUNG, result.share or UNMEASURED),)
    if result.outcome not in WRITTEN:
        return ()
    first = (
        ()
        if result.first_line is None
        else (Row(path, REASON_FIRST, result.first_line),)
    )
    little = result.little_heard
    heard = () if little is None else (Row(path, REASON_LITTLE, f"{little} pairs"),)
    return (*first, *heard)


def _noted(result: TrackResult, note: str) -> TrackResult:
    """*result* with *note* as one more detail line beneath its row."""
    return replace(result, detail=(*result.detail, note))


def _save(target: Path, rows: Sequence[Row]) -> None:
    """Replace the list at *target* with *rows*; refuse anything but a regular file.

    A missing name is made; one holding a link, folder or FIFO raises
    `lyrics_files.NotRegularFileError` and is left as it is.
    """
    try:
        entry = os.lstat(target)
    except FileNotFoundError:
        entry = None
    if entry is not None and not stat.S_ISREG(entry.st_mode):
        raise lyrics_files.NotRegularFileError(f"{target.name} is not a regular file")
    write_state_file(target, format_rows(rows))


class Recorder:
    """The list as this run keeps it: the rows read, then each finished track's."""

    def __init__(self, target: Path | None, rows: Sequence[Row]) -> None:
        """Keep *rows*, read from *target*; with no *target*, record nothing."""
        self.target = target
        self.rows = tuple(rows)
        #: How many rows this run added, and whether any save failed.
        self.added = 0
        self.failed = False

    def record(self, result: TrackResult) -> TrackResult:
        """Save the list as *result* changes it; *result*, any note for its row added.

        A path the format cannot hold, and a save that failed, are each said beneath
        the track's row; nothing else changes it.
        """
        if self.target is None:
            return result
        new = _new_rows(result)
        stale = _STALE.get(result.outcome, frozenset())
        path = str(result.audio)
        kept = tuple(r for r in self.rows if not (r.path == path and r.reason in stale))
        if not new and kept == self.rows:
            return result
        refused = path_refusal(path) if new else None
        if refused is not None:
            return _noted(result, f"{OPTION}: left out, the path {refused}")
        failure = self._commit(self.target, (*kept, *new), len(new))
        return result if failure is None else _noted(result, failure)

    def _commit(self, target: Path, rows: tuple[Row, ...], new: int) -> str | None:
        """Save *rows* to *target*, *new* of them added, with Ctrl-C held; why it failed.

        The kept rows and the count change only once the save has landed, inside the
        hold, so a Ctrl-C raised as the hold ends never parts them from the file.
        """
        with deferred_interrupt():
            try:
                _save(target, rows)
            except lyrics_files.RECORD_ERRORS as error:
                self.failed = True
                return f"{OPTION}: could not write {target}: {error}"
            self.rows = rows
            self.added += new
        return None
