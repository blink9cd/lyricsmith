"""`--mode accept`: album accept's pass over every album of a library, then one summary.

`accept_library` walks the albums the entry point found with the shared walk (the
albums a run considers, linked ones already dropped), and gives each to the album
tool's own `accept_album`, in process, under album accept's rules: the sweep adds no
rule of its own. Each album is judged and acted on alone, so one refused or failing
album never stops the walk, as any bulk operation here must not: a judgment's refusal, and
the could-not-run errors `accept_album` raises (a held album lock, a linked
``.lyricsmith``, an `OSError` reading it), are that album's refusal, counted by the
plain reason `refusal_reason` gives it. Any refused or failing album makes the pass's
exit 1, as an album a run could not finish does.

The walk's albums were listed at its start, but a folder can be swapped for a link
during a long pass, and the album tool resolves the folder it is given: so, as a run
does, each album is checked again just before it is accepted, and one reached through
a link by then is skipped, named through *notice* and counted.

Every album holding a run prints its own report, album accept's `format_accept` under
a line naming the album relative to the library; an album with no run is only
counted. A report-only pass leaves out each album's hint and closes the summary with
it once. The entry point holds the ledger's lock for the whole walk and writes no
ledger line; nothing here touches the ledger. Nothing here prints: text goes out
through the *emit* and *notice* the entry point passes.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import lyrics_exit
import lyrics_wiring
from lyrics_accept_mode import HINT, AcceptReport, accept_album, format_accept
from lyrics_accept_records import UnsafeAcceptingError
from lyrics_album import AcquireLock, LockHeldError, RunFolderLinkError
from sweep_run import EXIT_ALBUM_ERRORS, EXIT_OK
from lyrics_model import LyricsError
from lyrics_prune import Delete
from lyrics_stages import Stages
from lyrics_summary import counts_line
from stdio_utf8 import spell_unsafe
from sweep_survey import is_linked, relinked_notice

#: What the summary calls the albums a pass clears, or would clear.
_CLEARED, _WOULD_CLEAR = "cleared", "would clear"
#: Each error `accept_album` raises before its judgment, by the plain words the report
#: and the summary give it, beside the judgment's own reasons. Checked in order, so a
#: subclass comes before the class it refines; `_OTHER` is any error not listed.
_RAISED_REASONS: tuple[tuple[type[Exception], str], ...] = (
    (LockHeldError, "album locked"),
    (RunFolderLinkError, "linked run folder"),
    (UnsafeAcceptingError, "unsafe leftover"),
    (OSError, "unreadable"),
)
_OTHER = "could not start"


def refusal_reason(error: Exception) -> str:
    """The plain reason an album is refused for, when *error* stopped its accept."""
    return next(
        (words for kind, words in _RAISED_REASONS if isinstance(error, kind)), _OTHER
    )


@dataclass(frozen=True)
class AcceptSeams:
    """The album tool's parts accept runs with: its lock, its stages, the environment.

    *environ* names the state folder the records go under; *delete* None is
    `shutil.rmtree`. A test passes its own; `real_seams` binds the album tool's.
    """

    acquire_lock: AcquireLock
    stages: Stages
    environ: Mapping[str, str]
    delete: Delete | None = None


def real_seams() -> AcceptSeams:
    """The album tool's real lock and stages, and this process's environment."""
    wiring = lyrics_wiring.real_wiring()
    return AcceptSeams(wiring.acquire_lock, wiring.stages, os.environ)


@dataclass
class AcceptTally:
    """The walk's counts, album by album: the summary is made from these alone."""

    apply: bool
    albums: int = 0
    cleared: int = 0
    at_risk: int = 0
    no_run: int = 0
    skipped: int = 0
    errors: int = 0
    refused: Counter[str] = field(default_factory=Counter)
    refused_albums: int = 0

    def add_report(self, report: AcceptReport) -> None:
        """Count one album's report."""
        self.albums += 1
        if not report.found:
            self.no_run += 1
        elif report.refused:
            self.add_refusal({str(r.kind) for r in report.refusals}, counted=False)
        else:
            self.errors += bool(report.errors)
            self.cleared += not report.errors
            # Albums, not tracks: each album's own report counts its tracks. A failed
            # album counts too, since its at-risk tracks are still at risk.
            self.at_risk += bool(report.tracks_at_risk)

    def add_refusal(self, reasons: set[str], *, counted: bool = True) -> None:
        """Count one refused album, once under each distinct reason in *reasons*."""
        self.albums += counted
        self.refused_albums += 1
        self.refused.update(reasons)

    @property
    def failed(self) -> bool:
        """Whether an album was refused, or its removal or records failed."""
        return bool(self.refused_albums or self.errors)


def format_summary(tally: AcceptTally) -> str:
    """The closing summary: the counts, the refusals by reason, and any hint."""
    lines = [
        counts_line(
            ("albums", tally.albums),
            (_CLEARED if tally.apply else _WOULD_CLEAR, tally.cleared),
            ("refused", tally.refused_albums),
            ("holding at-risk tracks", tally.at_risk),
            ("no run", tally.no_run),
            ("skipped", tally.skipped),
            ("errors", tally.errors),
        )
    ]
    if tally.refused:
        reasons = counts_line(*sorted(tally.refused.items()))
        lines.append(f"refused by reason: {reasons}")
    if not tally.apply:
        lines.append(HINT)
    return "".join(f"{line}\n" for line in lines)


@dataclass(frozen=True)
class AcceptRun:
    """One library pass: what it is given, and where its text goes."""

    library: Path
    apply: bool
    seams: AcceptSeams
    now: Callable[[], datetime]
    prog: str
    emit: Callable[[str], None]
    notice: Callable[[str], None]


def _section(run: AcceptRun, album: Path, report: AcceptReport) -> str:
    """One album's report under its name; a report-only pass's hint left for the end."""
    text = format_accept(report, album=album, prog=run.prog, hint=False)
    return f"{_name(run, album)}\n{text}\n"


def _name(run: AcceptRun, album: Path) -> str:
    """*album* relative to the library, its control characters made visible."""
    return spell_unsafe(album.relative_to(run.library).as_posix())


def _skipped_as_linked(run: AcceptRun, album: Path, tally: AcceptTally) -> bool:
    """True, said through *notice* and counted, when *album* is now reached by a link.

    The walk dropped every album reached through one, but a folder can be swapped for
    a link since, and `accept_album` would follow it.
    """
    if not is_linked(album, run.library):
        return False
    tally.albums += 1
    tally.skipped += 1
    run.notice(relinked_notice(album, run.library))
    return True


def _accept_one(run: AcceptRun, album: Path, tally: AcceptTally) -> None:
    """Accept *album*, count it, and emit its section, if it has a run."""
    if _skipped_as_linked(run, album, tally):
        return
    try:
        report = accept_album(
            album,
            apply=run.apply,
            acquire_lock=run.seams.acquire_lock,
            stages=run.seams.stages,
            environ=run.seams.environ,
            now=run.now,
            delete=run.seams.delete,
        )
    # The album's refusal alone. Almost all of these are raised before anything of the
    # album changed; a `.lyricsmith.accepting` made by someone else while the lock is
    # held is raised at the rename, after the records were written, so it too deletes
    # nothing.
    except (LyricsError, OSError) as error:
        reason = refusal_reason(error)
        tally.add_refusal({reason})
        line = spell_unsafe(f"refused: {reason}: {error}")
        run.emit(f"{_name(run, album)}\n{line}\n\n")
        return
    tally.add_report(report)
    if report.found:
        run.emit(_section(run, album, report))


def accept_library(run: AcceptRun, albums: Sequence[Path]) -> int:
    """Accept each of *albums*, then emit the summary; return the exit code.

    0 finished, every album cleared, holding no run, or skipped; 1 finished, but an
    album was refused, or its removal or records failed. A report that cannot be
    written raises `ReportNotWrittenError` and stops the walk, since nothing would
    say what the rest of it did.
    """
    tally = AcceptTally(run.apply)
    for album in albums:
        with lyrics_exit.report_writes():
            _accept_one(run, album, tally)
    with lyrics_exit.report_writes():
        run.emit(format_summary(tally))
    return EXIT_ALBUM_ERRORS if tally.failed else EXIT_OK
