"""The sweep: the album tool run on each album to visit, one at a time, paced and stoppable.

For each album to visit, in the walk's order and skipping those the ledger holds as
done (`plan_visits`, which the survey uses too), `sweep` pauses (only after an album it
ran), drops the album if it is now reached through a link (`linked_now`), runs
the album tool on the album as a child process (`sweep_visits`), reads the run's manifest,
and appends the album's entry to the ledger.
It stops when the albums run out, at ``--limit`` albums run, at Ctrl-C, after ``--stop-after``
consecutive refused albums, or when the ledger cannot be read or written: the albums
visited so far are still reported, the last one included.

**The stop rule.** An album whose providers only refused (`sweep_tally`) adds one to a
run of refused albums; a done or owed album, one where a provider answered, ends that
run (`sweep_outcome.next_streak`). Every other outcome neither adds to the run nor ends it: it says
nothing about the providers.

**A done fix** (`sweep_fix_text`) is passed over only while its album's records in the
transcript store are as its visit left them; any change makes it an album to visit.

**An owed album** (`sweep_owed`) is visited again by the next sweep of its pass and
language, naming only the providers it owes; that visit's outcome replaces the last.
One whose last visit was challenged by a provider it still owes is visited after every
other album (`sweep_challenged`), said once before the first visit.
One whose whole debt is held by a cool-down or a breaker (`sweep_waiting`) is passed
over: not run, no ledger line, no ``--limit`` place, no pause owed and no change to the
stop rule's run; the report names it apart. The holds are read fresh for each owed
album, before the pause owed ahead of it, so a cool-down that ends during that pause
still passes the album over, and the next sweep visits it.
Every `sweep_totals.PROGRESS_EVERY_ALBUMS` albums visited, a progress line says how
many were visited, done and owed, and the refusals so far.

**Ctrl-C.** The album tool shares the terminal, so Ctrl-C reaches it too: it stops at
once, letting only a write in hand finish, records the track in hand as interrupted and
exits 130, and the album is recorded as interrupted, not done. A second Ctrl-C while the
sweep waits for it kills it; either way the album is recorded and the sweep stops.
Ctrl-C after the album tool ended, while its run is read, leaves the run unread and
records the album the same way, not done. Ctrl-C after the run was read, while the
album's entry is made or appended, lets both finish (`sweep_ctrl_c`), with
the outcome the run read gives, then stops. Ctrl-C while the next album's holds are
read, during the pause, while the next album is checked for a link, or while its album
tool is set up (its runs listed, its command and ``--changed-list`` made) stops before
that album.
Nothing here prints: progress goes to the caller's *progress*.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from sweep_ledger import DEFAULT_LANG as DEFAULT_LANG  # re-exported for the suites
from sweep_ledger import (
    LedgerRecord,
    Outcome,
    RestartMark,
    SweepPass,
    append_record,
    append_unless_last,
    newest_entries,
    read_records,
)
from sweep_earlier import LedgerPlan, plan_ledger
from lyrics_model import ReadBackError, Source
from sweep_challenged import last_notices
from sweep_fix_text import unchanged_done
from sweep_ctrl_c import finished_despite_ctrl_c
from sweep_owed import turned_off
from sweep_survey import AlbumSurvey, VisitPlan, linked_now, needing_visit, plan_visits
from sweep_outcome import next_streak
from sweep_visits import InterruptedBeforeStartError
from sweep_visits import SweepOptions, SweepSeams, Visit, lang_of, pass_of, visit_one
from sweep_waiting import HoldCheck, PassedOver, passed_over_line
from sweep_totals import progress_due, progress_line

#: The sweep finished, every album it visited done (or offline) with the album tool
#: exiting 0.
EXIT_OK = 0
#: The sweep finished, but an album it visited did not end done (or offline) with
#: the album tool exiting 0: `sweep_exit_code` decides it from the report.
EXIT_ALBUM_ERRORS = 1
#: A run, accept or silence pass that could not start or go on; the help's exit codes
#: list each case.
EXIT_USAGE = 2
#: Stopped after --stop-after consecutive refused albums.
EXIT_REFUSED = 3
#: Interrupted by Ctrl-C, the shell's 128 + SIGINT.
EXIT_INTERRUPTED = 130

#: Each owed album, by its name in the ledger, and the providers it owes.
OwedMap = dict[str, tuple[Source, ...]]

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
class SweepReport:
    """The albums visited, those skipped as done, those left, and why it stopped.

    *dropped* names, relative to the library, each album found reached through a link
    just before its visit, and so never run; *passed_over* each owed album whose whole
    debt was held, in visit order (`sweep_waiting`).
    """

    visited: tuple[Visit, ...]
    skipped_done: int
    remaining: int
    stop: StopReason
    dropped: tuple[str, ...] = ()
    passed_over: tuple[PassedOver, ...] = ()


def ledger_plan(
    records: Sequence[LedgerRecord],
    library: Path,
    surveys: Sequence[AlbumSurvey],
    options: SweepOptions,
    progress: Callable[[str], None],
) -> LedgerPlan:
    """The done albums and debts *records* hold of *surveys*, for survey and run alike.

    In the language and pass of *options*, by `sweep_earlier.plan_ledger`. A provider
    the sweep turns off challenges nothing: an album it alone challenged keeps its place.
    In the fix pass a done album stays done only while its records are as its last
    visit left them (`sweep_fix_text.unchanged_done`).
    """
    read = plan_ledger(
        records,
        library,
        needing_visit(library, surveys),
        lang=lang_of(options),
        pass_=pass_of(options),
        owe_earlier=options.owe_earlier_runs,
        progress=progress,
    )
    off = turned_off(options.skip)
    kept = {
        n: tuple(p for p in by if p not in off) for n, by in read.challenged_by.items()
    }
    challenged_by = {name: by for name, by in kept.items() if by}
    read = dataclasses.replace(read, challenged_by=challenged_by)
    if pass_of(options) is not SweepPass.FIX_TEXT:
        return read
    digests = {
        survey.album.relative_to(library).as_posix(): survey.records_digest
        for survey in surveys
    }
    newest = newest_entries(records, lang_of(options), SweepPass.FIX_TEXT)
    return dataclasses.replace(read, done=unchanged_done(read.done, newest, digests))


def _plan(
    library: Path,
    surveys: Sequence[AlbumSurvey],
    ledger: Path,
    options: SweepOptions,
    seams: SweepSeams,
) -> tuple[VisitPlan, OwedMap, list[str]]:
    """The albums to visit and passed over as done, the debts, and the challenge notices.

    The notices name each album put last for its challenge, in visit order. A restart
    is recorded first, and forgets every debt and challenge as it forgets every done.
    """
    records = read_records(ledger, seams.progress)
    read = ledger_plan(records, library, surveys, options, seams.progress)
    if options.restart:
        read = LedgerPlan(frozenset(), {}, {})
        append_record(ledger, RestartMark(seams.now().isoformat(), pass_of(options)))
    last, restart = read.challenged, options.restart
    plan = plan_visits(library, surveys, read.done, restart=restart, last=last)
    return plan, read.owed, last_notices(library, plan.to_visit, read.challenged_by)


def _ledger_failed(ledger: Path, error: Exception, seams: SweepSeams) -> None:
    """Say through *progress* that the ledger at *ledger* failed, and how."""
    seams.progress(f"the ledger cannot be read or written: {ledger}: {error}")


def _pause(options: SweepOptions, seams: SweepSeams, *, owed: bool) -> None:
    """Pause ``--pause-seconds`` before the next album when the last one ran (*owed*)."""
    if owed:
        seams.sleep(options.pause_s)


def _visit_all(
    library: Path,
    pending: Sequence[Path],
    ledger: Path,
    options: SweepOptions,
    seams: SweepSeams,
    owed: OwedMap,
    passed: list[PassedOver],
) -> tuple[list[Visit], list[str], StopReason]:
    """Visit *pending* in order until one of the stops; *owed* says what each owes.

    The visits, the albums dropped just before their visit (`linked_now`), and the
    stop; each owed album passed over, its whole debt held, goes into *passed*. Only
    a visit counts toward ``--limit`` and owes the pause before the next album: a
    dropped or passed-over album was never run, so it takes neither.
    """
    visits: list[Visit] = []
    dropped: list[str] = []
    streak = 0
    pause_owed = False
    check = HoldCheck(seams.holds, seams.progress)
    for index, album in enumerate(pending):
        if options.limit is not None and len(visits) >= options.limit:
            return visits, dropped, StopReason.LIMIT
        name = album.relative_to(library).as_posix()
        label = f"[{index + 1}/{len(pending)}] {name}"
        linked = False
        # Ctrl-C while the holds are read, during the pause or while the album is
        # checked for a link stops before the album, as nothing of it has run yet.
        try:
            waits = check.waits(
                owed.get(name, ()), options.skip, offline=options.offline
            )
            if not waits:
                _pause(options, seams, owed=pause_owed)
                pause_owed = False
                seams.progress(label)
                linked = linked_now(library, album, seams.progress)
        except KeyboardInterrupt:
            return visits, dropped, StopReason.INTERRUPTED
        if waits:
            seams.progress(label)
            seams.progress(passed_over_line(name, waits))
            passed.append(PassedOver(name, waits))
            continue
        if linked:
            dropped.append(name)
            continue
        # Ctrl-C while its album tool is set up also stops before the album.
        try:
            visit, interrupted = visit_one(
                library, album, options, seams, owed.get(name, ())
            )
        except InterruptedBeforeStartError:
            return visits, dropped, StopReason.INTERRUPTED
        entry = visit.entry
        visits.append(visit)
        pause_owed = True
        # Ctrl-C during the write finishes it, then stops: the album is never left
        # changed but unrecorded.
        try:
            _, late = finished_despite_ctrl_c(lambda: append_unless_last(ledger, entry))
        except LEDGER_ERRORS as error:
            _ledger_failed(ledger, error, seams)
            return visits, dropped, StopReason.LEDGER
        if progress_due(len(visits)):
            seams.progress(progress_line([v.entry for v in visits]))
        if late or interrupted or entry.outcome is Outcome.INTERRUPTED:
            return visits, dropped, StopReason.INTERRUPTED
        streak = next_streak(streak, entry.outcome)
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
    the first visit, then a notice for each album put last for its challenge.
    """
    try:
        plan, owed, notices = _plan(library, surveys, ledger, options, seams)
    except LEDGER_ERRORS as error:
        _ledger_failed(ledger, error, seams)
        needing = sum(survey.needs_visit for survey in surveys)
        return SweepReport((), 0, needing, StopReason.LEDGER)
    seams.progress(f"{len(plan.to_visit)} of {len(surveys)} albums to visit")
    for line in notices:
        seams.progress(line)
    pending = plan.to_visit
    passed: list[PassedOver] = []
    visits, dropped, stop = _visit_all(
        library, pending, ledger, options, seams, owed, passed
    )
    return SweepReport(
        visited=tuple(visits),
        skipped_done=len(plan.done),
        remaining=len(pending) - len(visits) - len(dropped) - len(passed),
        stop=stop,
        dropped=tuple(dropped),
        passed_over=tuple(passed),
    )


def sweep_exit_code(report: SweepReport) -> int:
    """The sweep's exit code: interrupted, stopped on refusals, album errors, or clean.

    An album passed over is still owed, so it is an album error too.
    """
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
    return EXIT_OK if clean and not report.passed_over else EXIT_ALBUM_ERRORS
