"""The sweep: the album tool run on each album to visit, one at a time, paced and stoppable.

For each album to visit, in the walk's order and skipping those the ledger holds as
done (`plan_visits`, which the survey uses too), `sweep` pauses (only after an album it
ran), drops the album if it is now reached through a link (`_linked_now`), runs
the album tool on the album as a child process (`visit_album`), reads the run's manifest,
and appends the album's entry to the ledger.
It stops when the albums run out, at ``--limit`` albums run, at Ctrl-C, after ``--stop-after``
consecutive refused albums, or when the ledger cannot be read or written: the albums
visited so far are still reported, the last one included.

**The stop rule.** An album whose providers only refused (`sweep_tally`) adds one to a
run of refused albums; a done album, one where the providers answered, ends that run
(`_next_streak`). Every other outcome neither adds to the run nor ends it: it says
nothing about the providers.

**Ctrl-C.** The album tool shares the terminal, so Ctrl-C reaches it too: it stops at
once, letting only a write in hand finish, records the track in hand as interrupted and
exits 130, and the album is recorded as interrupted, not done. A second Ctrl-C while the
sweep waits for it kills it; either way the album is recorded and the sweep stops.
Ctrl-C after the album tool ended, while its run is read, leaves the run unread and
records the album the same way, not done. Ctrl-C after the run was read, while the
album's entry is made or appended, lets both finish (`_finished_despite_ctrl_c`), with
the outcome the run read gives, then stops. Ctrl-C during the pause stops before the next
album. Nothing here prints: progress goes to the caller's *progress*.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

import sweep_child
from sweep_changed import ChangedUnion
from sweep_ledger import (
    DEFAULT_LANG,
    LedgerEntry,
    Outcome,
    RestartMark,
    SweepPass,
    append_record,
    append_unless_last,
    done_albums,
    read_ledger,
)
from lyrics_model import ReadBackError
from sweep_survey import AlbumSurvey, VisitPlan, is_linked, plan_visits
from sweep_made_run import MadeRun, tally_made_run
from sweep_outcome import outcome_of

#: The sweep finished, every album it visited done (or offline) with the album tool
#: exiting 0.
EXIT_OK = 0
#: The sweep finished, but an album it visited did not end done (or offline) with
#: the album tool exiting 0: `sweep_exit_code` decides it from the report.
EXIT_ALBUM_ERRORS = 1
#: The sweep could not go on, or never started: a ledger that cannot be read or
#: written (`sweep_exit_code`), and in the CLI a usage problem, a library that is
#: missing or cannot be listed, the library's lock not taken, or a run on Windows.
EXIT_USAGE = 2
#: Stopped after --stop-after consecutive refused albums.
EXIT_REFUSED = 3
#: Interrupted by Ctrl-C, the shell's 128 + SIGINT.
EXIT_INTERRUPTED = 130

_T = TypeVar("_T")

#: The outcomes of an album that finished cleanly, given exit 0: done, or run offline.
_CLEAN = frozenset({Outcome.DONE, Outcome.OFFLINE})


class StopReason(StrEnum):
    """Why a sweep stopped."""

    FINISHED = "finished"
    LIMIT = "limit"
    REFUSALS = "refusals"
    INTERRUPTED = "interrupted"
    LEDGER = "ledger"


#: What reading or writing the ledger may raise: the file system's own errors, or a
#: write that did not read back as written.
LEDGER_ERRORS = (OSError, ReadBackError)


@dataclass(frozen=True)
class SweepOptions:
    """What shapes a sweep: the album tool's options, the pace, the stop and the limit.

    *refetch_untimed* makes the sweep its refetch pass (`pass_of`);
    *refetch_implausible* is handed to every child in either pass. *skip* is the
    `sweep_child.SKIP_OPTIONS` each child is given, in their order.
    """

    lang: str | None
    offline: bool
    pause_s: float
    stop_after: int
    limit: int | None
    restart: bool
    timeout_s: float = sweep_child.ALBUM_TIMEOUT_S
    refetch_untimed: bool = False
    refetch_implausible: bool = False
    skip: tuple[str, ...] = ()


@dataclass(frozen=True)
class SweepSeams:
    """What a sweep runs with: the child runner, the pause, the clock, the progress line.

    *entry* is the album tool's entry point. *changed* is the ``--changed-list`` union,
    None when no list was asked for.
    """

    run_child: sweep_child.ChildRunner
    sleep: Callable[[float], None]
    now: Callable[[], datetime]
    progress: Callable[[str], None]
    entry: Path
    changed: ChangedUnion | None = None


@dataclass(frozen=True)
class Visit:
    """One album visited: its folder, its ledger entry, and why it could not run.

    *error* is the text of the file system error that kept the sweep from running the
    album tool on it (`_visit`); None otherwise.
    """

    album: Path
    entry: LedgerEntry
    error: str | None = None


@dataclass(frozen=True)
class SweepReport:
    """The albums visited, those skipped as done, those left, and why it stopped.

    *dropped* names, relative to the library, each album found reached through a link
    just before its visit, and so never run.
    """

    visited: tuple[Visit, ...]
    skipped_done: int
    remaining: int
    stop: StopReason
    dropped: tuple[str, ...] = ()


def lang_of(options: SweepOptions) -> str:
    """The language the album tool runs in: the one given, else the album tool's default."""
    return options.lang or DEFAULT_LANG


def pass_of(options: SweepOptions) -> SweepPass:
    """The sweep's pass: the refetch pass with ``--refetch-untimed``, else the normal."""
    return SweepPass.REFETCH_UNTIMED if options.refetch_untimed else SweepPass.NORMAL


def _finished_despite_ctrl_c(action: Callable[[], _T]) -> tuple[_T, bool]:
    """Run *action* to its end even when Ctrl-C cuts it short; its value, and if one did.

    Each Ctrl-C starts *action* again, so it must be safe to repeat: making an entry,
    or an append that skips a record already written.
    """
    interrupted = False
    while True:
        try:
            return action(), interrupted
        except KeyboardInterrupt:
            interrupted = True


def _entry_of(
    name: str, made: MadeRun, options: SweepOptions, seams: SweepSeams
) -> LedgerEntry:
    """The ledger entry of album *name*, from the run its child made."""
    tally = made.tally
    return LedgerEntry(
        album=name,
        run_id=made.run_id,
        exit_code=made.result.exit_code,
        outcome=outcome_of(made.result, tally, offline=options.offline),
        time=seams.now().isoformat(),
        counts=None if tally is None else tally.counts,
        refusing_tracks=0 if tally is None else tally.refusing_tracks,
        refusing_providers=() if tally is None else tally.refusing_providers,
        lang=lang_of(options),
        pass_=pass_of(options),
    )


def visit_album(
    library: Path, album: Path, options: SweepOptions, seams: SweepSeams
) -> tuple[LedgerEntry, sweep_child.ChildResult]:
    """Run the album tool on *album*; its ledger entry, and how the child ended.

    A child left unreaped is named by pid through *progress* (`tally_made_run` says
    which runs are read). The child's ``--changed-list``, when the sweep keeps one,
    is merged into the union once the child has ended (`sweep_changed`). Ctrl-C
    while the entry is made still makes it, as the run read says it ended, and marks
    the result interrupted so the sweep stops after it.
    """
    name = album.relative_to(library).as_posix()
    before = sweep_child.run_ids(album)
    changed = seams.changed
    argv = sweep_child.album_argv(
        seams.entry,
        album,
        lang=options.lang,
        offline=options.offline,
        refetch_untimed=options.refetch_untimed,
        refetch_implausible=options.refetch_implausible,
        changed_list=None if changed is None else changed.child_list(album, name),
        skip=options.skip,
    )
    result = seams.run_child(argv, options.timeout_s)
    if changed is not None:
        # The child has ended, so its list is final: absorbed even through a Ctrl-C.
        _, late = _finished_despite_ctrl_c(lambda: changed.absorb(album))
        result = dataclasses.replace(result, interrupted=result.interrupted or late)
    if result.exit_code is None:
        seams.progress(
            f"the album tool on {name} (pid {result.pid}) did not stop even when "
            "killed and may still be running"
        )
    netease = sweep_child.NO_NETEASE_OPTION not in options.skip
    made = tally_made_run(
        album, name, before, result, netease=netease, progress=seams.progress
    )
    entry, interrupted = _finished_despite_ctrl_c(
        lambda: _entry_of(name, made, options, seams)
    )
    if interrupted:
        return entry, dataclasses.replace(made.result, interrupted=True)
    return entry, made.result


def _could_not_run(name: str, options: SweepOptions, seams: SweepSeams) -> LedgerEntry:
    """The ledger entry of album *name* the sweep could not run: never done, so retried."""
    return LedgerEntry(
        album=name,
        run_id=None,
        exit_code=None,
        outcome=Outcome.COULD_NOT_RUN,
        time=seams.now().isoformat(),
        lang=lang_of(options),
        pass_=pass_of(options),
    )


def _visit(
    library: Path, album: Path, options: SweepOptions, seams: SweepSeams
) -> tuple[Visit, bool]:
    """*album* visited (`visit_album`), and whether Ctrl-C reached its album tool.

    A file system error on the album's own side before its album tool ran (its run
    folder that cannot be listed, an album tool that cannot be started) is that
    album's: it is recorded as could not run, its error said through *progress* and
    kept in the visit, and the sweep goes on. A ``--changed-list`` handed out for it
    is absorbed at once, in visit order, saying the album tool could not start.
    """
    try:
        entry, result = visit_album(library, album, options, seams)
    except OSError as error:
        name = album.relative_to(library).as_posix()
        seams.progress(f"{name}: could not run: {error}")
        late = _absorb_unstarted(album, seams)
        return Visit(album, _could_not_run(name, options, seams), str(error)), late
    return Visit(album, entry), result.interrupted


def _absorb_unstarted(album: Path, seams: SweepSeams) -> bool:
    """Absorb *album*'s list, if one was handed out, as an album tool never started.

    True when Ctrl-C reached the sweep meanwhile: the absorb still finished.
    """
    changed = seams.changed
    if changed is None:
        return False
    _, late = _finished_despite_ctrl_c(lambda: changed.absorb(album, started=False))
    return late


def read_done(
    ledger: Path, lang: str, pass_: SweepPass, progress: Callable[[str], None]
) -> frozenset[str]:
    """The albums the ledger at *ledger* holds as done in *lang* by *pass_*.

    An empty set when the ledger is missing. Only reads: a missing ledger is not
    made. Each line that cannot be read is reported through *progress* and skipped;
    a file that cannot be read raises `OSError`.
    """
    read = read_ledger(ledger)
    for problem in read.problems:
        progress(f"ledger {problem}; skipped")
    return done_albums(read.records, lang, pass_)


def _plan(
    library: Path,
    surveys: Sequence[AlbumSurvey],
    ledger: Path,
    options: SweepOptions,
    seams: SweepSeams,
) -> VisitPlan:
    """The albums to visit and those passed over as done; a restart is recorded first."""
    done = read_done(ledger, lang_of(options), pass_of(options), seams.progress)
    if options.restart:
        append_record(ledger, RestartMark(seams.now().isoformat(), pass_of(options)))
    return plan_visits(library, surveys, done, restart=options.restart)


def _ledger_failed(ledger: Path, error: Exception, seams: SweepSeams) -> None:
    """Say through *progress* that the ledger at *ledger* failed, and how."""
    seams.progress(f"the ledger cannot be read or written: {ledger}: {error}")


def _next_streak(streak: int, outcome: Outcome) -> int:
    """The run of consecutive refused albums after one that ended as *outcome*."""
    if outcome is Outcome.REFUSED:
        return streak + 1
    return 0 if outcome is Outcome.DONE else streak


def _linked_now(library: Path, album: Path, seams: SweepSeams) -> bool:
    """True, said through *progress*, when *album* is now reached through a link.

    The survey dropped every album reached through one, but a folder can be swapped
    for a link while a long sweep runs, and the album tool would follow it: it resolves
    the album folder it is given. So the check is made again just before each visit.
    """
    if not is_linked(album, library):
        return False
    seams.progress(
        f"skipped album {album.relative_to(library).as_posix()}: since the survey, "
        "its artist or album folder became a link, or it moved outside the library; "
        "not run"
    )
    return True


def _visit_all(
    library: Path,
    pending: Sequence[Path],
    ledger: Path,
    options: SweepOptions,
    seams: SweepSeams,
) -> tuple[list[Visit], list[str], StopReason]:
    """Visit *pending* in order until one of the stops.

    The visits, the albums dropped just before their visit (`_linked_now`), and the
    stop. Only a visit counts toward ``--limit`` and owes the pause before the next
    album: a dropped album was never run, so it takes neither.
    """
    visits: list[Visit] = []
    dropped: list[str] = []
    streak = 0
    pause_owed = False
    for index, album in enumerate(pending):
        if options.limit is not None and len(visits) >= options.limit:
            return visits, dropped, StopReason.LIMIT
        if pause_owed:
            try:
                seams.sleep(options.pause_s)
            except KeyboardInterrupt:
                return visits, dropped, StopReason.INTERRUPTED
            pause_owed = False
        seams.progress(
            f"[{index + 1}/{len(pending)}] {album.relative_to(library).as_posix()}"
        )
        if _linked_now(library, album, seams):
            dropped.append(album.relative_to(library).as_posix())
            continue
        visit, interrupted = _visit(library, album, options, seams)
        entry = visit.entry
        visits.append(visit)
        pause_owed = True
        # Ctrl-C during the write finishes it, then stops: the album is never left
        # changed but unrecorded.
        try:
            _, late = _finished_despite_ctrl_c(
                lambda: append_unless_last(ledger, entry)
            )
        except LEDGER_ERRORS as error:
            _ledger_failed(ledger, error, seams)
            return visits, dropped, StopReason.LEDGER
        if late or interrupted or entry.outcome is Outcome.INTERRUPTED:
            return visits, dropped, StopReason.INTERRUPTED
        streak = _next_streak(streak, entry.outcome)
        if streak >= options.stop_after:
            return visits, dropped, StopReason.REFUSALS
    return visits, dropped, StopReason.FINISHED


def sweep(
    library: Path,
    surveys: Sequence[AlbumSurvey],
    ledger: Path,
    options: SweepOptions,
    seams: SweepSeams,
) -> SweepReport:
    """Visit each album of *surveys* that needs it and is not yet done, into *ledger*.

    Which albums those are is `plan_visits`' answer, said through *progress* before
    the first visit.
    """
    try:
        plan = _plan(library, surveys, ledger, options, seams)
    except LEDGER_ERRORS as error:
        _ledger_failed(ledger, error, seams)
        needing = sum(survey.needs_visit for survey in surveys)
        return SweepReport((), 0, needing, StopReason.LEDGER)
    seams.progress(f"{len(plan.to_visit)} of {len(surveys)} albums to visit")
    pending = plan.to_visit
    visits, dropped, stop = _visit_all(library, pending, ledger, options, seams)
    return SweepReport(
        visited=tuple(visits),
        skipped_done=len(plan.done),
        remaining=len(pending) - len(visits) - len(dropped),
        stop=stop,
        dropped=tuple(dropped),
    )


def sweep_exit_code(report: SweepReport) -> int:
    """The sweep's exit code: interrupted, stopped on refusals, album errors, or clean."""
    if report.stop is StopReason.INTERRUPTED:
        return EXIT_INTERRUPTED
    if report.stop is StopReason.REFUSALS:
        return EXIT_REFUSED
    if report.stop is StopReason.LEDGER:
        return EXIT_USAGE
    clean = all(
        visit.entry.outcome in _CLEAN and visit.entry.exit_code == 0
        for visit in report.visited
    )
    return EXIT_OK if clean else EXIT_ALBUM_ERRORS
