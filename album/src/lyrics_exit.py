"""The tool's exit codes, and how a run, a strip or an undo turns its report into one.

The entry point re-exports the codes, so callers and tests reach them through it.
`report_exit` and its helper take a *notice* parameter, the entry point's printer: every
notice goes to it, and nothing here prints.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, Iterator
from typing import IO, Protocol

#: Every track processed with no error; not found and a check FLAG are not errors.
EXIT_OK = 0
#: At least one track errored.
EXIT_TRACK_ERRORS = 1
#: The run could not start (bad options, no album, the lock held, nothing to undo), or
#: an album-level step before any MP3 changed failed.
EXIT_CANNOT_RUN = 2
#: The work finished, or stopped part-way, but the run's record (its manifest), the
#: --changed-list or its report (a progress line, the summary) could not be written; it
#: outranks 1 and 130: undo needs the record.
EXIT_RECORD_NOT_WRITTEN = 3
#: A paste only (`lyrics_paste_mode`): the track refused by its guards, nothing written.
EXIT_PASTE_REFUSED = 4
#: A paste only: its run was made, but its one track errored.
EXIT_PASTE_ERROR = 5
#: A paste only: not written, the paste not sung or not lyrics to write. A code of its
#: own, so no crash or other failure (exit 1 among them) is ever read as not sung.
EXIT_PASTE_NOT_SUNG = 6
#: Interrupted by Ctrl-C, the shell's 128 + SIGINT.
EXIT_INTERRUPTED = 130


#: What a lost report says on stderr, before why.
REPORT_NOT_WRITTEN = "could not write the report"


class ReportNotWrittenError(Exception):
    """A progress line, the summary or a notice that could not be written, once the work began.

    Deliberately neither an `OSError` nor a `LyricsError`: the entry point reads
    both as could-not-run (exit 2), and this run was made. It is exit 3.
    """


def report_not_written(error: OSError) -> ReportNotWrittenError:
    """The lost report *error* caused; raise it ``from`` *error*."""
    return ReportNotWrittenError(f"{REPORT_NOT_WRITTEN}: {error}")


@contextlib.contextmanager
def report_writes() -> Iterator[None]:
    """Hold a mode's report, once its work is done: an `OSError` in it is a lost report.

    Only the streams are meant to fail here; a file the report writes, such as the
    --changed-list, says its own failure instead of raising. A closed stdout or stderr
    is no reason to read the run as never made. Both streams are flushed before it
    ends: stdout to a pipe holds the summary in its buffer, and a failure left for the
    interpreter's own flush at exit would never reach this.
    """
    try:
        yield
        for stream in _standard_streams():
            stream.flush()
    except OSError as error:
        raise report_not_written(error) from error


def lost_report_exit(
    error: ReportNotWrittenError, prog: str, notice: Callable[[str], None]
) -> int:
    """Exit 3 for a report that could not be written, said on stderr while it can be."""
    last_notice(f"{prog}: {error}", notice)
    return EXIT_RECORD_NOT_WRITTEN


def last_notice(text: str, notice: Callable[[str], None]) -> None:
    """Send the notice the exit code goes with, then quiet each stream that failed.

    A stream that still cannot be flushed is pointed at the null device: otherwise the
    interpreter's flush at exit fails on the bytes it holds and replaces the code with
    120.
    """
    # stderr may be the stream that is gone; the exit code then says it alone, and
    # nothing is left to write a second failure to.
    with contextlib.suppress(OSError):
        notice(text)
    quiet_failed_streams()


def quiet_failed_streams() -> None:
    """Point each standard stream that cannot be flushed at the null device."""
    for stream in _standard_streams():
        try:
            stream.flush()
        except OSError:
            to_null_device(stream)


def to_null_device(stream: IO[str]) -> None:
    """Point *stream*'s descriptor at the null device, so what it still holds is dropped."""
    # A stream with no descriptor of its own (an in-process stand-in) leaves nothing
    # for the interpreter's flush at exit to fail on.
    try:
        fd = stream.fileno()
    except (OSError, ValueError):
        return
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, fd)
    finally:
        os.close(null)


def _standard_streams() -> tuple[IO[str], ...]:
    """The standard output and error streams as they are now, any closed at startup left out."""
    # Python sets a stream to None when its descriptor was closed at startup.
    streams: tuple[IO[str] | None, ...] = (sys.stdout, sys.stderr)
    return tuple(stream for stream in streams if stream is not None)


class FinishedReport(Protocol):
    """What a run's, a strip's and an undo's report each say about how they ended."""

    @property
    def record_error(self) -> str | None:
        """Why the manifest could not be written, when that is what stopped it."""

    @property
    def interrupted(self) -> bool:
        """True when a Ctrl-C stopped it."""

    @property
    def has_errors(self) -> bool:
        """True when any track errored."""


def report_exit(
    report: FinishedReport,
    prog: str,
    written: tuple[str | None, bool],
    notice: Callable[[str], None],
    *,
    recorded: str = "the tracks that finished",
) -> int:
    """The exit code of a run, a strip or an undo, with the notice when it was cut short.

    *recorded* names what the manifest of an interrupted one holds. *written* is
    `lyrics_changed.write_changed_list`'s answer: a list that could not be written is
    said last, and is exit 3; a Ctrl-C while it was written is exit 130.
    """
    list_error, late = written
    code = _finished_exit(report, prog, recorded, notice)
    if list_error is not None:
        notice(f"{prog}: {list_error}")
        return EXIT_RECORD_NOT_WRITTEN
    if late and code != EXIT_INTERRUPTED:
        notice(f"{prog}: interrupted")
    return EXIT_INTERRUPTED if late else code


def _finished_exit(
    report: FinishedReport,
    prog: str,
    recorded: str,
    notice: Callable[[str], None],
) -> int:
    """`report_exit`'s answer for the lyrics work alone."""
    if report.record_error is not None:
        notice(f"{prog}: {report.record_error}")
        return EXIT_RECORD_NOT_WRITTEN
    if report.interrupted:
        notice(f"{prog}: interrupted; the manifest records {recorded}")
        return EXIT_INTERRUPTED
    return EXIT_TRACK_ERRORS if report.has_errors else EXIT_OK
