"""``--mode silence``: measure every track of a library and list the silent ones.

A read-only pass. It walks the albums the survey walks (`sweep_survey.contained_albums`,
an album reached through a link dropped and named), measures every track of each with
the album tool's ffmpeg measurement, whatever its lyrics (`sweep_silence_album`), and
appends each album's silent and unmeasured tracks, judged, to a list under the state
directory, then a marker naming the album done (`sweep_silence_list`). Each album's
lines go out in one write and a flush, so a crash loses at most the album in hand.
Nothing in the library is written.

The order of its refusals is the order of what each would waste: no ffmpeg, then a
``--resume`` list that cannot be read, both before the library is walked; then the walk,
whose unlistable library `sweep.main` turns into exit 2; then a resumed list that marks
albums done but none of this library's, a list another library began; and only then the
list is made, or the resumed one cut back to its last finished album. An album with a
folder that cannot be listed is named and skipped, unmarked, so it is left for a resume,
and a track that cannot be measured is listed as such: either makes the exit 1. A linked
track or folder in an album is named and passed over. Whenever any album is left, the
summary ends with the command that resumes. Ctrl-C keeps every album already written,
prints the summary so far and the command that resumes, and exits as a run does.

However it ends, once the list is closed the pass writes the whole list's HTML page
beside it (`sweep_silence_page`), so a ``--resume`` on a list with nothing left
measures nothing and only writes the page again. A page that cannot be written is named
on stderr and makes the exit 1; the list, which is what matters, is kept. Ctrl-C
while the page is written ends the run as interrupted: the page is named as not
written, and the summary and the command that resumes, and so writes it, are printed.

Progress and notices go to stderr, the summary to stdout, as the sweep's other modes.
"""

from __future__ import annotations

import argparse
import os
import shlex
import stat
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Protocol

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import lyrics_silence
import sweep_report
import sweep_run
from lyrics_files import NotRegularFileError
from lyrics_state import STATE_FILE_MODE, make_state_folder
from stdio_utf8 import ENCODING, spell_unsafe
from sweep_silence_album import AlbumMeasure, album_name, measure_album
from sweep_silence_list import (
    ResumePoint,
    SilenceListError,
    album_block,
    done_key,
    header_line,
    list_path,
    read_resume,
)
from sweep_silence_page import PageRefusedError, page_path, write_page
from sweep_silence_verdict import Verdict
from sweep_survey import contained_albums

#: The option naming the list a pass resumes, and its help: the entry point registers
#: it in its own file, where the CLI-to-doc check can see it.
RESUME_OPTION = "--resume"
RESUME_HELP = (
    "silence only: append to this list, skipping the albums it marks done, after "
    "cutting off the lines of an album it did not finish; a new list under the state "
    "directory when omitted."
)
#: The open flags every list descriptor shares; a flag a platform lacks is 0 there.
#: O_BINARY exists only on Windows, where a descriptor opened without it is in text
#: mode and each LF lands as CRLF, a line the list's own parser then refuses.
_SHARED_FLAGS = ("O_CLOEXEC", "O_BINARY")
#: What a resumed list adds: never through a link, never blocking on a FIFO pipe.
_RESUME_ONLY_FLAGS = ("O_NOFOLLOW", "O_NONBLOCK")
#: The verdicts the summary counts, in the order it prints them.
_JUDGED = (Verdict.LIKELY_MEANT, Verdict.RIPPING_MISTAKE, Verdict.UNSURE)


@dataclass(frozen=True)
class SilenceSeams:
    """What the pass measures with: ffmpeg's lookup, and `measure_level`'s process seam.

    The defaults are the real ones; a test passes a lookup and a fake ffmpeg.
    """

    find_ffmpeg: Callable[[], str | None] = lyrics_silence.find_ffmpeg
    run: lyrics_silence.Runner | None = None


class Wired(Protocol):
    """The part of the sweep's wiring the pass uses: the clock and its own seams."""

    @property
    def now(self) -> Callable[[], datetime]:
        """The clock naming a new list."""

    @property
    def silence(self) -> SilenceSeams | None:
        """The pass's seams; None binds the real ones."""


@dataclass(frozen=True)
class SilencePlan:
    """The albums this pass measures, as ``(folder, name)`` in walk order, and the rest.

    *done* counts the albums a resumed list marks done; *beyond* those still to do past
    ``--limit``.
    """

    albums: tuple[tuple[Path, str], ...]
    done: int
    beyond: int


@dataclass
class SilenceTally:
    """The pass's counts so far."""

    albums: int = 0
    skipped: int = 0
    tracks: int = 0
    silent: int = 0
    not_measured: int = 0
    verdicts: Counter[Verdict] = field(default_factory=Counter)

    def add(self, measured: AlbumMeasure) -> None:
        """Count one written album."""
        self.albums += 1
        self.tracks += measured.measured
        self.silent += measured.silent
        self.not_measured += measured.not_measured
        self.verdicts.update(row.verdict for row in measured.rows)


def notice(text: str) -> None:
    """One line of progress or notice, on stderr, its control characters made visible."""
    print(spell_unsafe(text), file=sys.stderr)


def _skipped(name: str) -> None:
    """The notice for an artist folder the walk could not list."""
    notice(f"skipped artist folder {name}: it cannot be listed")


def _linked(name: str) -> None:
    """The notice for an album reached through a link, which the pass drops."""
    notice(
        f"skipped album {name}: its artist or album folder is a link, "
        "or it lies outside the library"
    )


def plan_albums(
    library: Path, albums: Sequence[Path], point: ResumePoint | None, limit: int | None
) -> SilencePlan:
    """Of *albums*, those not marked done in *point*, the first *limit* of them."""
    done = point.done if point is not None else frozenset()
    named = [(album, album_name(album, library)) for album in albums]
    to_do = [(album, name) for album, name in named if done_key(name) not in done]
    measured = to_do if limit is None else to_do[:limit]
    return SilencePlan(
        tuple(measured), len(named) - len(to_do), len(to_do) - len(measured)
    )


def _platform_flags(names: tuple[str, ...]) -> int:
    """The flags *names* name, each 0 where the platform lacks it.

    Read at each open, never once at import, so a test can stand in Windows'
    O_BINARY on any host.
    """
    flags = 0
    for name in names:
        flags |= getattr(os, name, 0)
    return flags


def _new_list_flags() -> int:
    """How a new list is opened: made here, never over a name already taken, binary."""
    return os.O_WRONLY | os.O_CREAT | os.O_EXCL | _platform_flags(_SHARED_FLAGS)


def _resume_flags() -> int:
    """How a resumed list is opened: appended to, never through a link or blocking."""
    return (
        os.O_WRONLY
        | os.O_APPEND
        | _platform_flags(_RESUME_ONLY_FLAGS)
        | _platform_flags(_SHARED_FLAGS)
    )


def _new_list(path: Path) -> int:
    """A descriptor on a new list at *path*, 0o600, its state folders made 0o700.

    Exclusive, so a file or a link already under the name is an error, never
    written through.
    """
    make_state_folder(path.parent)
    return os.open(path, _new_list_flags(), STATE_FILE_MODE)


def _resumed_list(path: Path, keep: int) -> int:
    """A descriptor appending to the list at *path*, cut back to *keep* bytes.

    Opened without following a link and without blocking, then told by the opened
    descriptor itself, so a link, a FIFO pipe or a device swapped in since the list
    was read is an error at once, and nothing is cut or written through it.
    """
    descriptor = os.open(path, _resume_flags())
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise NotRegularFileError(f"{path.name} is not a regular file")
        os.ftruncate(descriptor, keep)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def open_list(path: Path, point: ResumePoint | None) -> IO[str]:
    """The list at *path* opened to append: made new, or cut back to *point*'s keep.

    A new list is made 0o600 in its state folder (`_new_list`), and must not exist
    yet; a resumed one is never reached through a link (`_resumed_list`). Either way
    the header is written first when the list keeps none. Raises OSError.
    """
    if point is None:
        descriptor = _new_list(path)
    else:
        descriptor = _resumed_list(path, point.keep)
    out = os.fdopen(descriptor, "a", encoding=ENCODING, newline="")
    if point is None or point.keep == 0:
        out.write(header_line())
        out.flush()
    return out


def _measure_albums(
    plan: SilencePlan,
    ffmpeg: str,
    seams: SilenceSeams,
    out: IO[str],
    tally: SilenceTally,
) -> None:
    """Measure each album of *plan*, writing its block, counting into *tally*."""
    total = len(plan.albums)
    for number, (album, name) in enumerate(plan.albums, start=1):
        notice(f"[{number}/{total}] {name}")
        try:
            measured = measure_album(album, name, ffmpeg, run=seams.run)
        except OSError as error:
            notice(f"skipped album {name}: it cannot be read ({type(error).__name__})")
            tally.skipped += 1
            continue
        for track in measured.unreadable:
            notice(f"unreadable tags: {track}")
        for link in measured.links:
            notice(f"skipped link {link}: a link is never followed")
        out.write(album_block(measured.rows, name))
        out.flush()
        tally.add(measured)


def albums_left(tally: SilenceTally, plan: SilencePlan) -> int:
    """The albums a resume would still measure: not reached, or skipped as unreadable.

    A skipped album gets no marker, so a resume retries it; it is left, not done.
    """
    return plan.beyond + len(plan.albums) - tally.albums


def format_summary(
    tally: SilenceTally,
    plan: SilencePlan,
    path: Path,
    stop: str,
    page: Path | None = None,
) -> str:
    """The summary: what was done before, measured, found, left, the list and its page.

    *page* is None when the page could not be written: then no line names it.
    """
    left = albums_left(tally, plan)
    lines = [
        f"already done: {plan.done}",
        f"albums measured: {tally.albums}",
        f"tracks measured: {tally.tracks}",
        f"silent: {tally.silent}",
        *(f"  {verdict}: {tally.verdicts[verdict]}" for verdict in _JUDGED),
        f"not measured: {tally.not_measured}",
        *([f"albums skipped: {tally.skipped}"] if tally.skipped else []),
        f"{stop}; albums left: {left}",
        f"list: {path}",
        *([f"page: {page}"] if page is not None else []),
    ]
    return "".join(f"{spell_unsafe(line)}\n" for line in lines)


def resume_command(prog: Sequence[str], library: Path, path: Path) -> str:
    """The command that resumes this pass in *path*, named by *prog*'s words."""
    return shlex.join(
        [
            *prog,
            "--mode",
            "silence",
            "--library",
            str(library),
            RESUME_OPTION,
            str(path),
        ]
    )


def _stop_word(plan: SilencePlan, interrupted: bool) -> str:
    """Why the pass ended."""
    if interrupted:
        return "interrupted"
    return "limit reached" if plan.beyond else "finished"


@dataclass(frozen=True)
class _Ending:
    """What the pass's ending names: the library, the list, the command's words."""

    library: Path
    path: Path
    prog: Sequence[str]
    name: str
    #: The page written beside the list; None when it could not be written.
    page: Path | None


def _finish(
    tally: SilenceTally, plan: SilencePlan, ending: _Ending, interrupted: bool
) -> int:
    """Print the summary, and the resume command when albums are left; the exit code."""
    stop = _stop_word(plan, interrupted)
    summary = format_summary(tally, plan, ending.path, stop, ending.page)
    # The summary is this mode's report, stdout's as the other modes' reports are; the
    # stream is named so the report's destination is a choice, not an accident.
    print(summary, end="", file=sys.stdout)
    # With no page, the resume command is also how to write it: it measures nothing more.
    if albums_left(tally, plan) or ending.page is None:
        command = resume_command(ending.prog, ending.library, ending.path)
        # Escaped like the run's: a control character prints as a visible escape.
        print(spell_unsafe(f"resume: {command}"), file=sys.stdout)
    if interrupted:
        notice(f"{ending.name}: interrupted")
        return sweep_run.EXIT_INTERRUPTED
    clean = tally.not_measured == 0 and tally.skipped == 0 and ending.page is not None
    return sweep_run.EXIT_OK if clean else sweep_run.EXIT_ALBUM_ERRORS


def _dropped(point: ResumePoint | None, path: Path) -> None:
    """Name the lines a resume cut off *path*, if it cut any.

    A list whose header has no line feed keeps nothing: what was cut is the header
    alone, which `open_list` wrote again, so that is what is named.
    """
    if point is None or not point.dropped:
        return
    if point.keep == 0:
        notice(f"the list's header had no line ending; it was written again: {path}")
        return
    lines = "line" if point.dropped == 1 else "lines"
    notice(f"dropped {point.dropped} {lines} of an unfinished album from {path}")


def _foreign(point: ResumePoint | None, plan: SilencePlan) -> int:
    """How many albums a resumed list marks done when none is this library's; else 0.

    Such a list was begun on another library: resuming it would measure this whole
    library into a list of another's.
    """
    return len(point.done) if point is not None and plan.done == 0 else 0


def _foreign_notice(name: str, path: Path, done: int, library: Path) -> None:
    """Name the list another library began, and how to resume it."""
    albums = "album" if done == 1 else "albums"
    notice(
        f"{name}: {path} marks {done} {albums} done, and none of them is an album "
        f"of {library}; resume it with the --library of the pass that began it"
    )


def _write_page(path: Path, now: Callable[[], datetime], name: str) -> Path | None:
    """Write the page of the list at *path*; its path, or None, named, when it fails."""
    try:
        return write_page(path, now())
    except (OSError, SilenceListError, PageRefusedError) as error:
        notice(
            f"{name}: the page cannot be written: {page_path(path)} ({error}); "
            "the list is kept"
        )
        return None


def silence_mode(
    args: argparse.Namespace,
    library: Path,
    wiring: Wired,
    words: sweep_report.Words,
    *,
    prog_file: str,
) -> int:
    """Run the silence pass over *library*; the exit code.

    *words* name the sweep as the user ran it, and *prog_file* is its entry file's own
    name, for the resume command of a sweep run alone (`sweep_report.resume_prog`).
    """
    name = " ".join(words)
    seams = wiring.silence or SilenceSeams()
    ffmpeg = seams.find_ffmpeg()
    if ffmpeg is None:
        notice(f"{name}: --mode silence needs ffmpeg on PATH; nothing was measured")
        return sweep_run.EXIT_USAGE
    try:
        point = None if args.resume is None else read_resume(args.resume)
    except SilenceListError as error:
        notice(f"{name}: {error}")
        return sweep_run.EXIT_USAGE
    albums = contained_albums(library, on_skip=_skipped, on_linked=_linked)
    plan = plan_albums(library, albums, point, args.limit)
    if foreign := _foreign(point, plan):
        _foreign_notice(name, args.resume, foreign, library)
        return sweep_run.EXIT_USAGE
    path: Path = args.resume or list_path(os.environ, wiring.now())
    try:
        out = open_list(path, point)
    except OSError as error:
        notice(f"{name}: the list cannot be opened: {path} ({error})")
        return sweep_run.EXIT_USAGE
    _dropped(point, path)
    tally, interrupted = SilenceTally(), False
    with out:
        try:
            _measure_albums(plan, ffmpeg, seams, out, tally)
        except KeyboardInterrupt:
            interrupted = True
    try:
        page = _write_page(path, wiring.now, name)
    except KeyboardInterrupt:
        # Ctrl-C mid-write: the replace already removed its temporary file, so the
        # run ends as any interrupted one does, its summary and resume command printed.
        page, interrupted = None, True
        notice(f"{name}: the page was not written: {page_path(path)}")
    prog = sweep_report.resume_prog(words, prog_file)
    ending = _Ending(library, path, prog, name, page)
    return _finish(tally, plan, ending, interrupted)
