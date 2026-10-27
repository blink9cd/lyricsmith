"""One album's visit: the album tool run on it as a child process, and its ledger entry.

`visit_album` runs the album tool on one album, reads the run it made and turns it into
the album's ledger entry; `visit_one` wraps it so a file system error on the album's own
side is that album's, recorded as could not run, and the sweep goes on. The options and
seams a sweep runs with are declared here, since every visit reads them; the sweep
itself (`sweep_run`) walks the albums, pauses and stops. Nothing here prints: progress
goes to the caller's *progress*.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import sweep_child
from sweep_changed import ChangedUnion
from sweep_ledger import DEFAULT_LANG, LedgerEntry, Outcome, SweepPass
from lyrics_model import Source
from sweep_ctrl_c import finished_despite_ctrl_c
from sweep_owed import dropped_debt, dropped_notice, revisit_only
from sweep_owed import synced_asked, without_turned_off
from sweep_made_run import MadeRun, tally_made_run
from sweep_outcome import outcome_of
from sweep_waiting import HoldsRead, no_holds


@dataclass(frozen=True)
class SweepOptions:
    """What shapes a sweep: the album tool's options, the pace, the stop and the limit.

    *refetch_untimed* makes the sweep its refetch pass (`pass_of`);
    *refetch_implausible* is handed to every child in either pass. *skip* is the
    `sweep_child.SKIP_OPTIONS` each child is given, in their order.
    *owe_earlier_runs* owes the debts of earlier runs too (`sweep_earlier`).
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
    owe_earlier_runs: bool = False


@dataclass(frozen=True)
class SweepSeams:
    """What a sweep runs with: the child runner, the pause, the clock, the progress line.

    *entry* is the album tool's entry point. *changed* is the ``--changed-list`` union,
    None when no list was asked for. *holds* reads every provider's hold afresh
    (`sweep_waiting.read_holds`); the default holds none, so passes nothing over.
    """

    run_child: sweep_child.ChildRunner
    sleep: Callable[[float], None]
    now: Callable[[], datetime]
    progress: Callable[[str], None]
    entry: Path
    changed: ChangedUnion | None = None
    holds: Callable[[], HoldsRead] = no_holds


@dataclass(frozen=True)
class Visit:
    """One album visited: its folder, its ledger entry, and why it could not run.

    *error* is the text of the file system error that kept the sweep from running the
    album tool on it (`visit_one`); None otherwise.
    """

    album: Path
    entry: LedgerEntry
    error: str | None = None


def lang_of(options: SweepOptions) -> str:
    """The language the album tool runs in: the one given, else the album tool's default."""
    return options.lang or DEFAULT_LANG


def pass_of(options: SweepOptions) -> SweepPass:
    """The sweep's pass: the refetch pass with ``--refetch-untimed``, else the normal."""
    return SweepPass.REFETCH_UNTIMED if options.refetch_untimed else SweepPass.NORMAL


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
        owed_providers=() if tally is None else tally.owed,
        challenged_providers=() if tally is None else tally.challenged,
        refusals={} if tally is None else tally.refusals,
        retried={} if tally is None else tally.retried,
    )


class InterruptedBeforeStartError(KeyboardInterrupt):
    """Ctrl-C reached the sweep while an album's album tool was set up, before it ran.

    Nothing of the album ran, so the sweep stops before it with no ledger entry, as
    Ctrl-C during the pause does (`sweep_run`).
    """


def _set_up(
    album: Path,
    name: str,
    options: SweepOptions,
    seams: SweepSeams,
    owed: Sequence[Source],
) -> tuple[frozenset[str], tuple[Source, ...], list[str]]:
    """What *album*'s album tool runs with: the runs before it, whom it asks, its argv.

    A debt the sweep's own ``--no-*`` options drop is said through *progress*. A
    ``--changed-list`` is handed out for the album while its argv is built.
    """
    dropped = dropped_debt(owed, options.skip)
    if dropped:
        seams.progress(dropped_notice(name, dropped))
    before = sweep_child.run_ids(album)
    changed = seams.changed
    only = revisit_only(owed, offline=options.offline)
    argv = sweep_child.album_argv(
        seams.entry,
        album,
        lang=options.lang,
        offline=options.offline,
        refetch_untimed=options.refetch_untimed,
        refetch_implausible=options.refetch_implausible,
        changed_list=None if changed is None else changed.child_list(album, name),
        only=only,
        skip=options.skip,
    )
    return before, only, argv


def visit_album(
    library: Path,
    album: Path,
    options: SweepOptions,
    seams: SweepSeams,
    *,
    owed: Sequence[Source] = (),
) -> tuple[LedgerEntry, sweep_child.ChildResult]:
    """Run the album tool on *album*; its ledger entry, and how the child ended.

    *owed*, the providers the album owes from its last visit, are the only ones the
    album tool asks (`sweep_owed.revisit_only`); none named asks every provider. A
    debt the sweep's own ``--no-*`` options drop is said through *progress*. Ctrl-C
    while the album tool is set up, before it runs, absorbs any ``--changed-list``
    already handed out as an album tool never started, and raises
    `InterruptedBeforeStartError`.

    A child left unreaped is named by pid through *progress* (`tally_made_run` says
    which runs are read). The child's ``--changed-list``, when the sweep keeps one,
    is merged into the union once the child has ended (`sweep_changed`). Ctrl-C
    while the entry is made still makes it, as the run read says it ended, and marks
    the result interrupted so the sweep stops after it.
    """
    name = album.relative_to(library).as_posix()
    try:
        before, only, argv = _set_up(album, name, options, seams, owed)
    except KeyboardInterrupt:
        _absorb_unstarted(album, seams)
        raise InterruptedBeforeStartError from None
    changed = seams.changed
    result = seams.run_child(argv, options.timeout_s)
    if changed is not None:
        # The child has ended, so its list is final: absorbed even through a Ctrl-C.
        _, late = finished_despite_ctrl_c(lambda: changed.absorb(album))
        result = dataclasses.replace(result, interrupted=result.interrupted or late)
    if result.exit_code is None:
        seams.progress(
            f"the album tool on {name} (pid {result.pid}) did not stop even when "
            "killed and may still be running"
        )
    synced = synced_asked(only, options.skip)
    made = tally_made_run(
        album, name, before, result, synced=synced, progress=seams.progress
    )
    if made.tally is not None:
        made = dataclasses.replace(
            made, tally=without_turned_off(made.tally, options.skip)
        )
    entry, interrupted = finished_despite_ctrl_c(
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


def visit_one(
    library: Path,
    album: Path,
    options: SweepOptions,
    seams: SweepSeams,
    owed: Sequence[Source],
) -> tuple[Visit, bool]:
    """*album* visited (`visit_album`), and whether Ctrl-C reached its album tool.

    A file system error on the album's own side before its album tool ran (its run
    folder that cannot be listed, an album tool that cannot be started) is that
    album's: it is recorded as could not run, its error said through *progress* and
    kept in the visit, and the sweep goes on. A ``--changed-list`` handed out for it
    is absorbed at once, in visit order, saying the album tool could not start.
    *owed* is what the album owes (`visit_album`).
    """
    try:
        entry, result = visit_album(library, album, options, seams, owed=owed)
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
    _, late = finished_despite_ctrl_c(lambda: changed.absorb(album, started=False))
    return late
