"""Each audio file's planned action, judged from what sits at its output location.

The planned-actions rule: a file with no sidecar is transcribed; one with a
sidecar is skipped unless --overwrite, and then replaced only when its .lrc is ours,
that is, when its header block holds the machine by-line. A .txt alone is never
ours. A sidecar is present when its name exists at all, a dangling symlink
included, so a link is skipped or refused and never silently replaced. Two audio
files that would write one .lrc keep only the first claimant (`claim_outputs`).
Reading a .lrc and probing a folder's case rule are the only I/O; nothing here writes.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Sequence
from pathlib import Path

import at_discover
from at_types import (
    MACHINE_BY_LINE,
    TIMESTAMP_LINE,
    Action,
    FileResult,
    Outcome,
    Planned,
    shown_path,
    split_lines,
)

#: How much of a .lrc is read to judge it: ours keeps its by-line in the first few
#: lines, so this holds any header block a person writes, and a planted multi-gigabyte
#: sidecar costs no more than this. A by-line past it is not judged ours.
LRC_READ_CAP_BYTES = 64 * 1024


# fuzz-target
def is_ours(lrc_text: str) -> bool:
    """Whether the header block, the lines before the first timed line, holds our by-line."""
    for line in split_lines(lrc_text):
        if TIMESTAMP_LINE.match(line):
            return False
        if line.strip() == MACHINE_BY_LINE:
            return True
    return False


def read_flags() -> int:
    """How a .lrc is opened: read-only, never blocking, binary.

    Never blocking, so a FIFO pipe with no writer opens at once; binary, so the bytes
    are the file's own. A flag a platform lacks is 0 there, and each is read at the
    open, never once at import. The album tool reads with these same flags (its
    `lyrics_files.read_flags`); a test where both tools are on the path pins the two
    together.
    """
    return (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )


def read_lrc(path: Path) -> str | None:
    """A .lrc's text, None when there is none, '' when it cannot be read (not ours).

    A dangling symlink, or anything else at the name that is not a regular file, is
    present but not ours, and is never read: a FIFO blocks in a plain open() and a
    link to a device such as /dev/zero reads without end, which would hang even a dry
    run. The name is checked first, then opened without blocking (`read_flags`) and
    told again by the opened descriptor's own fstat, so a FIFO or device swapped in
    between the two is refused there too, never waited on.

    Only the first `LRC_READ_CAP_BYTES` are read, all `is_ours` needs, so a huge or
    sparse sidecar is never read whole: a header block running past the cap is
    present but not ours. Read as UTF-8 with an optional byte-order mark, undecodable
    bytes replaced (a character cut at the cap among them), so a hand-made file in
    another encoding is still judged rather than failing the run.
    """
    # is_file first: on 3.12 it raises under an unsearchable folder, which the
    # caller turns into this file's failed row; lexists alone would answer False.
    if not path.is_file():
        return "" if os.path.lexists(path) else None
    try:
        head = _read_head(path)
    except OSError:
        return ""
    return "" if head is None else head.decode("utf-8-sig", errors="replace")


def _read_head(path: Path) -> bytes | None:
    """The first `LRC_READ_CAP_BYTES` of *path*, None when it opens as no regular file."""
    descriptor = os.open(path, read_flags())
    # Told before fdopen, which itself refuses a folder with an error of its own.
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        return None
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read(LRC_READ_CAP_BYTES)


def choose_action(lrc_text: str | None, txt_exists: bool, overwrite: bool) -> Action:
    """The planned-actions rule; a .txt alone is never ours."""
    if lrc_text is None and not txt_exists:
        return Action.TRANSCRIBE
    if not overwrite:
        return Action.SKIPPED
    if lrc_text is not None and is_ours(lrc_text):
        return Action.OVERWRITE
    return Action.REFUSED


def plan_files(
    audio_files: Sequence[Path], root: Path, output_dir: Path | None, overwrite: bool
) -> list[Planned]:
    """Each file's action, judged only at its own output location."""
    planned = []
    for audio in audio_files:
        lrc, txt = at_discover.output_paths(audio, root, output_dir)
        txt_present = txt.exists() or os.path.lexists(txt)
        action = choose_action(read_lrc(lrc), txt_present, overwrite)
        planned.append(Planned(audio, lrc, txt, action))
    return planned


#: os.path.samefile's shape, so a test can answer for a disk it does not have.
SameFile = Callable[[Path, Path], bool]


def is_case_insensitive(folder: Path, samefile: SameFile = os.path.samefile) -> bool:
    """Whether names in *folder*'s filesystem match without regard to case.

    Asked of the nearest existing folder at or above *folder* whose name has a case
    to swap: an --output-dir mirror folder is created only later, and "2024" has no
    other case. That folder and its case-swapped twin are the same folder only on a
    case-insensitive filesystem; a missing or distinct twin means case-sensitive.
    Any other answer, or no cased folder at all, folds: the fold can only fail a
    file, never let one pair replace another.
    """
    for candidate in (folder, *folder.parents):
        twin = candidate.name.swapcase()
        if twin == candidate.name or not os.path.isdir(candidate):
            continue
        try:
            return samefile(candidate, candidate.with_name(twin))
        except FileNotFoundError:
            return False
        except OSError:
            return True
    return True


def _first_claimant(
    lrc: Path,
    exact: dict[str, Path],
    folded: dict[str, Path],
    case_insensitive: Callable[[Path], bool],
) -> Path | None:
    """The audio file already writing *lrc*; None when *lrc* is unclaimed.

    Matched by its exact name, or by its case-folded one where case is ignored.
    """
    if str(lrc) in exact:
        return exact[str(lrc)]
    other = folded.get(str(lrc).casefold())
    if other is not None and case_insensitive(lrc.parent):
        return other
    return None


def claim_outputs(
    items: Sequence[Planned | FileResult],
    root: Path,
    case_insensitive: Callable[[Path], bool] = is_case_insensitive,
) -> list[Planned | FileResult]:
    """Keep the first claimant of each .lrc; every later one becomes a failed row.

    one.mp3 and one.m4a both map to one.lrc, and the second rename would replace the
    first's pair without --overwrite. a.mp3 and A.mp3 name one .lrc only where the
    destination ignores case, so a case-only twin is probed there, at run time.
    Items arrive in sorted order, so the first claimant is the first in sorted order;
    failed rows pass through.
    """
    exact: dict[str, Path] = {}
    folded: dict[str, Path] = {}
    kept: list[Planned | FileResult] = []
    for item in items:
        if isinstance(item, FileResult):
            kept.append(item)
            continue
        first = _first_claimant(item.lrc, exact, folded, case_insensitive)
        if first is None:
            exact[str(item.lrc)] = item.audio
            folded.setdefault(str(item.lrc).casefold(), item.audio)
            kept.append(item)
            continue
        detail = (
            f"another audio file, {shown_path(first, root)}, already writes this .lrc"
        )
        kept.append(FileResult(item.audio, Outcome.FAILED, (detail,)))
    return kept
