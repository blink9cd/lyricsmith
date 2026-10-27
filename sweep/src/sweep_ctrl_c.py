"""Finishing a step through Ctrl-C: an action the sweep must not leave half done.

The sweep's run (`sweep_run`) makes and appends an album's ledger entry, and merges a
child's ``--changed-list``, through `finished_despite_ctrl_c`, so a Ctrl-C landing in
the middle never leaves an album changed but unrecorded: the action is run again to its
end, and the caller learns a Ctrl-C came, to stop after it. Nothing here prints.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")


def finished_despite_ctrl_c(action: Callable[[], _T]) -> tuple[_T, bool]:
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
