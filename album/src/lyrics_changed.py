"""The ``--changed-list`` file: every MP3 a run, a strip or an undo saved, one per line.

A tool that makes a music player reread tags reads it; nothing here knows of that tool
or of any player, so the album tool stays free of the machine it runs on. The format,
the contract that reader holds, is UTF-8, one absolute path per line, each line ended by
a linefeed, so a file a writer stopped in the middle of is told apart from a whole one.

`format_changed_list` writes only lines that contract accepts: a path holding a
linefeed or a NUL, one ending in a carriage return, one that is not strict UTF-8 (a
name decoded from other bytes through the surrogate escape) or one that is not
absolute is left out, and handed back with the reason. `parse_changed_list` is the
strict read-back the sweep uses on each child's list: any line the contract refuses
makes the whole list unreadable, since the sweep then lists the album whole instead.

Which tracks are listed is `run_changed` and `undo_changed`: each MP3 whose file was
saved, and each whose save may have reached the disk before the step failed. Listing
an unchanged track costs a reread of its tags; missing a changed one leaves the player
showing stale tags, so an unsure track is listed. Each path is under the album folder
as the album tool resolved it, the track's own path never resolved: the reader matches
files by device and inode.

`save_changed_list` writes the file atomically with the interrupt held, and hands back
why it could not, for the caller to report after its summary. Nothing here prints.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import lyrics_files
from lyrics_album import deferred_interrupt
from lyrics_records import ErrorStage, ManifestTrack, UndoTrackResult

#: Why a path is left out of the list, one per rule of the reader's contract.
REASON_LINEFEED = "holds a linefeed, which would split it into two lines"
REASON_CR = "ends in a carriage return, which reads as a CRLF line ending"
REASON_NUL = "holds a NUL"
REASON_NOT_UTF8 = "is not UTF-8"
REASON_RELATIVE = "is not an absolute path"

#: The one line terminator, as text and as a byte.
_LINEFEED = "\n"
_LINEFEED_BYTE = b"\n"
_CR = "\r"
_NUL = "\x00"
_NUL_BYTE = b"\x00"
_ENCODING = "utf-8"
#: The stages whose step saves the MP3 itself: a track whose error was raised in one
#: may have been saved before it (a save whose read-back then failed, say).
_SAVING_STAGES = frozenset({ErrorStage.EMBED, ErrorStage.STRIP})


@dataclass(frozen=True)
class LeftOut:
    """A path the list cannot hold, as it was given, and why."""

    path: str
    reason: str


def refusal(path: str) -> str | None:
    """Why the reader could not read *path* back from a list line, or None when it can."""
    if _LINEFEED in path:
        return REASON_LINEFEED
    if _NUL in path:
        return REASON_NUL
    if path.endswith(_CR):
        return REASON_CR
    try:
        path.encode(_ENCODING)
    except UnicodeEncodeError:
        return REASON_NOT_UTF8
    if not Path(path).is_absolute():
        return REASON_RELATIVE
    return None


def format_changed_list(paths: Iterable[str]) -> tuple[bytes, tuple[LeftOut, ...]]:
    """The list file's bytes for *paths*, and each path left out of it, with why.

    Each path is normalized lexically, as the reader normalizes what it reads, so
    every written line reads back as itself; it is written once, where first named.
    A path left out is named once too. No paths make an empty file.
    """
    lines: dict[str, None] = {}
    left: dict[str, LeftOut] = {}
    for path in paths:
        normal = os.path.normpath(path)
        reason = refusal(normal)
        if reason is None:
            lines[normal] = None
        else:
            left.setdefault(path, LeftOut(path, reason))
    data = "".join(f"{line}{_LINEFEED}" for line in lines).encode(_ENCODING)
    return data, tuple(left.values())


def _strict_line(raw: bytes) -> str | None:
    """One terminated line's path, normalized; "" for a blank line, None when refused."""
    if _NUL_BYTE in raw:
        return None
    try:
        text = raw.decode(_ENCODING)
    except UnicodeDecodeError:
        return None
    if not text.strip():
        return ""
    if text.endswith(_CR) or not Path(text).is_absolute():
        return None
    return os.path.normpath(text)


# fuzz-target
def parse_changed_list(data: bytes) -> tuple[str, ...] | None:
    """Every path in a list file's bytes, in order; None when any line is refused.

    The reader's rules, held strictly: each line ended by a linefeed, UTF-8, no NUL,
    no carriage return ending it, absolute; blank lines skipped. A list with one line
    the contract refuses, a last line with no linefeed among them, is no list at all.
    Never raises.
    """
    *terminated, tail = data.split(_LINEFEED_BYTE)
    if tail:
        return None
    paths: list[str] = []
    for raw in terminated:
        path = _strict_line(raw)
        if path is None:
            return None
        if path:
            paths.append(path)
    return tuple(paths)


def _listed(album: Path, relative: str) -> str:
    """The absolute path a list names a track of *album* by; the track's never resolved."""
    return os.path.abspath(album / relative)


def run_changed(album: Path, tracks: Iterable[ManifestTrack]) -> tuple[str, ...]:
    """The MP3s of a run's or a strip's *tracks* it saved, in run order.

    A track whose frames were written or stripped is listed, and so is one whose
    error was raised while its MP3 was being saved (`_SAVING_STAGES`): a save whose
    read-back then failed changed the file, though no frames are recorded as written.
    A track never reached is not.
    """
    return tuple(
        _listed(album, track.path)
        for track in tracks
        if track.after is not None
        and (
            track.after.frames_written
            or (
                track.after.error is not None
                and track.after.error.stage in _SAVING_STAGES
            )
        )
    )


def undo_changed(album: Path, results: Iterable[UndoTrackResult]) -> tuple[str, ...]:
    """The MP3s an undo saved, or may have saved before failing, in undo order."""
    return tuple(_listed(album, result.path) for result in results if result.saved)


def left_out_notice(left: LeftOut) -> str:
    """The stderr line naming a path left out of the list, escaped as Python shows it.

    The escaped form shows a linefeed, a control character or an undecodable byte as
    a visible escape, and can always be printed.
    """
    return f"--changed-list: left out {left.path!r}: {left.reason}"


def said_once(notice: Callable[[str], None]) -> Callable[[str], None]:
    """*notice*, passing each distinct line on once: a retried write says nothing twice."""
    said: set[str] = set()

    def _once(line: str) -> None:
        if line not in said:
            said.add(line)
            notice(line)

    return _once


def save_changed_list(
    target: Path | None, paths: Iterable[str], notice: Callable[[str], None]
) -> str | None:
    """Write the list of *paths* to *target*; why it could not be written, or None.

    Nothing is written when *target* is None. Each path left out is said once through
    *notice*. The file is replaced atomically, so a reader finds the old list or the
    whole new one, and Ctrl-C is held from choosing the lines until the replace has
    landed or failed; a held Ctrl-C is then raised, the failure, if any, lost with it,
    so a caller that wants both tries again (`write_changed_list`).
    """
    if target is None:
        return None
    failure = None
    with deferred_interrupt():
        data, left = format_changed_list(paths)
        for item in left:
            notice(left_out_notice(item))
        try:
            lyrics_files.replace_atomically(target, data)
        except OSError as error:
            failure = f"could not write --changed-list {target}: {error}"
    return failure


def write_changed_list(
    path: Path | None,
    listed: Callable[[], Sequence[str]],
    notice: Callable[[str], None],
) -> tuple[str | None, bool]:
    """Write the ``--changed-list`` *path*: why it failed, and whether Ctrl-C came.

    A Ctrl-C landing while the list is chosen or written, before the write holds it or
    as the hold ends, would leave no list, or lose a write's failure: the pair is run
    once more, so the list stands and its failure is still known, each notice still
    said once. A second Ctrl-C ends the tool as any other does.
    """
    once = said_once(notice)
    try:
        return save_changed_list(path, listed(), once), False
    except KeyboardInterrupt:
        return save_changed_list(path, listed(), once), True
