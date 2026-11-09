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
import functools
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import sweep_child
from sweep_changed import ChangedUnion, made_something
from sweep_ledger import DEFAULT_LANG, LedgerEntry, Outcome, SweepPass
from lyrics_exit import EXIT_OK
from lyrics_model import Source
from lyrics_options import MODE_FIX_TEXT, MODE_RUN
from sweep_ctrl_c import finished_despite_ctrl_c
from sweep_owed import dropped_debt, dropped_notice, revisit_only
from sweep_owed import synced_asked, without_turned_off
from sweep_fix_text import album_digest
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
    *heard_dir*, the transcript store, makes the sweep its fix pass (`pass_of`):
    each child runs the album tool's ``--mode fix-text`` on it. *hold_for_genius* is
    the fix pass's ``--hold-for-genius`` file, which the resume command carries.
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
    heard_dir: Path | None = None
    hold_for_genius: Path | None = None


@dataclass(frozen=True)
class SweepSeams:
    """What a sweep runs with: the child runner, the pause, the clock, the progress line.

    *entry* is the album tool's entry point. *changed* is the ``--changed-list`` union,
    None when no list was asked for, and *held* the ``--hold-for-genius`` one.
    *holds* reads every provider's hold afresh (`sweep_waiting.read_holds`); the
    default holds none, so passes nothing over.
    """

    run_child: sweep_child.ChildRunner
    sleep: Callable[[float], None]
    now: Callable[[], datetime]
    progress: Callable[[str], None]
    entry: Path
    changed: ChangedUnion | None = None
    holds: Callable[[], HoldsRead] = no_holds
    held: ChangedUnion | None = None

    def unions(self) -> tuple[ChangedUnion, ...]:
        """The lists the sweep keeps: the changed one, then the held one, when asked."""
        return tuple(u for u in (self.changed, self.held) if u is not None)


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
    """The sweep's pass: the fix with a store, the refetch with its option, or normal."""
    if options.heard_dir is not None:
        return SweepPass.FIX_TEXT
    return SweepPass.REFETCH_UNTIMED if options.refetch_untimed else SweepPass.NORMAL


def album_mode(options: SweepOptions) -> str:
    """The album tool's mode each child runs: the fix in the fix pass, else a run."""
    return MODE_RUN if options.heard_dir is None else MODE_FIX_TEXT


def _digest_of(album: Path, outcome: Outcome, options: SweepOptions) -> str | None:
    """A done fix's digest of *album*'s records as it left them; None for any other."""
    if options.heard_dir is None or outcome is not Outcome.DONE:
        return None
    return album_digest(album, options.heard_dir)


def _entry_of(
    album: Path, name: str, made: MadeRun, options: SweepOptions, seams: SweepSeams
) -> LedgerEntry:
    """The ledger entry of *album*, named *name*, from the run its child made."""
    tally = made.tally
    outcome = outcome_of(
        made.result,
        tally,
        offline=options.offline,
        fix_text=options.heard_dir is not None,
        no_run=made.no_run,
    )
    return LedgerEntry(
        album=name,
        run_id=made.run_id,
        exit_code=made.result.exit_code,
        outcome=outcome,
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
        records_digest=_digest_of(album, outcome, options),
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
    ``--changed-list`` and a ``--hold-for-genius`` list are handed out for the album
    while its argv is built.
    """
    dropped = dropped_debt(owed, options.skip)
    if dropped:
        seams.progress(dropped_notice(name, dropped))
    before = sweep_child.run_ids(album)
    changed, held = seams.changed, seams.held
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
        mode=album_mode(options),
        heard_dir=options.heard_dir,
        hold_list=None if held is None else held.child_list(album, name),
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
    which runs are read). The child's ``--changed-list`` and ``--hold-for-genius``
    lists, when the sweep keeps them, are each merged into their union once the
    child has ended (`sweep_changed`). Ctrl-C
    while the entry is made still makes it, as the run read says it ended, and marks
    the result interrupted so the sweep stops after it.
    """
    name = album.relative_to(library).as_posix()
    try:
        before, only, argv = _set_up(album, name, options, seams, owed)
    except KeyboardInterrupt:
        _absorb(album, seams, started=False)
        raise InterruptedBeforeStartError from None
    result = seams.run_child(argv, options.timeout_s)
    made_nothing = functools.partial(_made_nothing, album, before, result)
    # The child has ended, so its lists are final: absorbed even through a Ctrl-C.
    if _absorb(album, seams, started=True, made_nothing=made_nothing):
        result = dataclasses.replace(result, interrupted=True)
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
        lambda: _entry_of(album, name, made, options, seams)
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
        late = _absorb(album, seams, started=False)
        return Visit(album, _could_not_run(name, options, seams), str(error)), late
    return Visit(album, entry), result.interrupted


def _made_nothing(
    album: Path, before: frozenset[str], result: sweep_child.ChildResult
) -> bool:
    """True when *album*'s child exited 0 and made no run since *before*.

    That is a fix with nothing to fix, which writes no list. A run folder that cannot
    be listed tells nothing, so it is False: `tally_made_run` says why.
    """
    if result.exit_code != EXIT_OK:
        return False
    try:
        return not sweep_child.new_run_ids(album, before)
    except OSError:
        return False


def _absorb(
    album: Path,
    seams: SweepSeams,
    *,
    started: bool,
    made_nothing: Callable[[], bool] = made_something,
) -> bool:
    """Absorb each list handed out for *album*; *started* False for a tool never run.

    *made_nothing* tells whether the child exited 0 having made no run
    (`ChangedUnion.absorb`). True when Ctrl-C reached the sweep meanwhile: every
    absorb still finished.
    """
    late = False
    for union in seams.unions():
        _, cut = finished_despite_ctrl_c(
            functools.partial(
                union.absorb, album, started=started, made_nothing=made_nothing
            )
        )
        late = late or cut
    return late
