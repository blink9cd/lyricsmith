"""What the entry point's modes run with: the real child runner, sleep and clock, or a test's.

`real_wiring` binds the real ones; a test passes its own `Wiring` to the entry point's
`main` instead. Nothing here prints.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import sweep_accept
import sweep_child
import sweep_genius_seams
import sweep_paste_seams
import sweep_silence


@dataclass(frozen=True)
class Wiring:
    """What a run runs with: the album tool's child runner, the pause, the clock.

    *accept* is what accept runs the album tool's pass with, *silence* what the
    silence pass measures with, *paste* the terminal, clipboard and browser the
    paste walk uses, and *genius* what the Genius check reaches; None binds the real
    one.
    """

    run_child: sweep_child.ChildRunner
    sleep: Callable[[float], None]
    now: Callable[[], datetime]
    accept: sweep_accept.AcceptSeams | None = None
    silence: sweep_silence.SilenceSeams | None = None
    paste: sweep_paste_seams.PasteSeams | None = None
    genius: sweep_genius_seams.CheckSeams | None = None


def _utc_now() -> datetime:
    """Now, in UTC, which the ledger's time stamps are written in."""
    return datetime.now(UTC)


def real_wiring() -> Wiring:
    """The real child runner, the real sleep, the UTC clock."""
    return Wiring(sweep_child.run_child, time.sleep, _utc_now)
