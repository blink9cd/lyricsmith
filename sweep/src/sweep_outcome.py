"""How an album's visit ended: its `Outcome`, from the child's result and its run's tally.

The sweep (`sweep_run`) records each visit's outcome in the ledger; the rule deciding it
is kept here, apart, so it reads on its own. An album the sweep could not run the album
tool on at all is `could not run` without a result, which `sweep_run` decides itself.
The stop rule's run of refused albums is counted from each outcome here too
(`next_streak`). Nothing here prints.
"""

from __future__ import annotations

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import lyrics_exit
import sweep_child
from sweep_ledger import Outcome
from sweep_tally import AlbumTally

#: The outcomes of an album some provider answered, which end a run of refused ones.
_ANSWERED = frozenset({Outcome.DONE, Outcome.OWED})


def _finished_codes() -> frozenset[int]:
    """The album tool's codes for a run that finished: with no track error, or some.

    Each of the album tool's codes is read from `lyrics_exit` when it is asked, never
    copied here, so the sweep reads the codes the album tool it runs gives.
    """
    return frozenset({lyrics_exit.EXIT_OK, lyrics_exit.EXIT_TRACK_ERRORS})


def outcome_of(
    result: sweep_child.ChildResult,
    tally: AlbumTally | None,
    *,
    offline: bool,
    fix_text: bool = False,
    no_run: bool = False,
) -> Outcome:
    """How a visit ended, from the child's result and its run's tally, if read.

    A run made *offline* asked no provider, so it is never done. Of a finished run,
    one whose providers only refused is refused, one still owing a provider answers
    is owed, and only one owing none is done. A *fix_text* visit ends past its
    refusals by its fix's own rule (`_fix_outcome`); *no_run* says its child made
    no run at all.
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
    if fix_text:
        return _fix_outcome(result, tally, no_run=no_run)
    if tally is None:
        return Outcome.NO_RECORD
    if offline:
        return Outcome.OFFLINE
    if tally.refused:
        return Outcome.REFUSED
    return Outcome.OWED if tally.owed else Outcome.DONE


def _fix_outcome(
    result: sweep_child.ChildResult, tally: AlbumTally | None, *, no_run: bool
) -> Outcome:
    """How a fix that finished ended: done only when no guard kept a wrong text.

    A fix exiting 0 that made no run had nothing to fix, and is done. A fix whose
    providers only refused is refused, as a run is. A fix is never owed: an owed
    album is revisited naming only its debt, which would leave every other provider
    out on purpose, and the album tool removes a wrong text when no provider it meant
    to ask failed, so such a revisit could remove a text those others never answered
    for. A fix exiting 1 kept a wrong text, so it is retried whole; one exiting 0 is
    done, whatever its tally owes, since each of its texts was written or removed, or
    its removal withheld on an album that looks spoken, which no retry would change.
    """
    if tally is None:
        if no_run and result.exit_code == lyrics_exit.EXIT_OK:
            return Outcome.DONE
        return Outcome.NO_RECORD
    if tally.refused:
        return Outcome.REFUSED
    if result.exit_code == lyrics_exit.EXIT_OK:
        return Outcome.DONE
    return Outcome.TRACK_ERRORS


def next_streak(streak: int, outcome: Outcome) -> int:
    """The run of consecutive refused albums after one that ended as *outcome*.

    A refused album adds one; a done or owed one, where a provider answered, ends the
    run; any other outcome says nothing about the providers, so leaves it as it was.
    """
    if outcome is Outcome.REFUSED:
        return streak + 1
    return 0 if outcome in _ANSWERED else streak
