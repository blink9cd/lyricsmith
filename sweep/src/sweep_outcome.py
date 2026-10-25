"""How an album's visit ended: its `Outcome`, from the child's result and its run's tally.

The sweep (`sweep_run`) records each visit's outcome in the ledger; the rule deciding it
is kept here, apart, so it reads on its own. An album the sweep could not run the album
tool on at all is `could not run` without a result, which `sweep_run` decides itself.
Nothing here prints.
"""

from __future__ import annotations

import lyrics_exit
import sweep_child
from sweep_ledger import Outcome
from sweep_tally import AlbumTally


def _finished_codes() -> frozenset[int]:
    """The album tool's codes for a run that finished: with no track error, or some.

    Each of the album tool's codes is read from `lyrics_exit` when it is asked, never
    copied here, so the sweep reads the codes the album tool it runs gives.
    """
    return frozenset({lyrics_exit.EXIT_OK, lyrics_exit.EXIT_TRACK_ERRORS})


def outcome_of(
    result: sweep_child.ChildResult, tally: AlbumTally | None, *, offline: bool
) -> Outcome:
    """How a visit ended, from the child's result and its run's tally, if read.

    A run made *offline* asked no provider, so it is never done.
    """
    if result.exit_code is None:
        return Outcome.ABANDONED
    # The bound stops a child with Ctrl-C's own signal, so a timed-out child exits
    # 130 too: the bound is asked first.
    if result.timed_out:
        return Outcome.TIMED_OUT
    if result.interrupted or result.exit_code == lyrics_exit.EXIT_INTERRUPTED:
        return Outcome.INTERRUPTED
    if result.exit_code == lyrics_exit.EXIT_CANNOT_RUN:
        return Outcome.COULD_NOT_RUN
    # Asked before the tally: the child said its record failed, whatever was read.
    if result.exit_code == lyrics_exit.EXIT_RECORD_NOT_WRITTEN:
        return Outcome.RECORD_NOT_WRITTEN
    if result.exit_code not in _finished_codes():
        return Outcome.FAILED
    if tally is None:
        return Outcome.NO_RECORD
    if offline:
        return Outcome.OFFLINE
    return Outcome.REFUSED if tally.refused else Outcome.DONE
