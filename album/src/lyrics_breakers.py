"""A run's breakers: one guard per guarded provider, shared by its steps, kept on disk.

A run reads the state file of each guarded provider it asks
(`lyrics_choices.asked_guarded`) once, as it starts (`breaker_setups`), and leaves
every other provider's file unread; the table gives each asked provider's steps the one
`BreakerGuard` built from it (`lyrics_table`). The fetch layer asks the guard before a
step (`BreakerGuard.shut`) and tells it how the step ended (`BreakerGuard.settle`),
by the policy in `lyrics_breaker`. A skipped step records a refusal of the breaker's
last class, marked unsent (`RefusalFacts.unsent`), so the summary's ``refusals:`` and
``providers:`` lines count it as ``breaker open``, never as one more refusal of that
class. A guard writes its file on each change of state,
never otherwise; a write that fails is kept (`BreakerGuard.write_error`) for the run to
report, and the run goes on with the state in memory.

One failure counts once. A provider that keeps a failure and raises the same error
again, as Bandcamp does for each track of an album whose lookup failed, would
otherwise count one refused request once per track; the guard remembers the errors it
has counted (`_SEEN`) and ignores one met again. A provider that says what it kept
(`lyrics_provider_kinds.KeepsAnswers`) is not asked about at all for such a call. A lock guards
the guard's state, its counted errors and its file write together, so a read of the state
never sees one change half made, nor two writes of the file interleave, whichever thread
asks.

A provider carries its guard as ``breaker`` (`GuardedByBreaker`): the real LRCLIB,
NetEase, Genius and Bandcamp steps do; YouTube Music and Tekstowo keep their own
24-hour stop (`lyrics_cooling_client`) and carry none. Nothing here prints.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from lyrics_breaker import (
    GUARDED_SOURCES,
    BreakerState,
    after_answer,
    after_refusal,
    breaker_path,
    open_detail,
    read_breaker,
    write_breaker,
)
from lyrics_cooldown import utc_now
from lyrics_model import ProviderTable, ReadBackError, Source
from lyrics_refusal import RefusalFacts

#: How many counted errors a guard remembers, so one raised again counts once. A
#: provider raises a kept error again for the tracks that follow, so the latest few
#: are enough.
_SEEN = 32


@dataclass(frozen=True)
class BreakerSetup:
    """What a guard starts from: its file, the state read from it, and the clock."""

    path: Path
    state: BreakerState
    now: Callable[[], datetime] = utc_now  # aware UTC


@dataclass(frozen=True)
class Shut:
    """Why a step was not asked: the detail and facts its refused attempt records."""

    detail: str
    facts: RefusalFacts


class BreakerGuard:
    """One provider's breaker for a run: asked before each step, told how it ended."""

    def __init__(self, setup: BreakerSetup) -> None:
        """Start from *setup*'s state, writing each change to its file."""
        self.setup = setup
        self.state = setup.state
        #: Why the last write failed, when one did; the run reports it.
        self.write_error: str | None = None
        self._lock = threading.Lock()
        self._counted: deque[BaseException] = deque(maxlen=_SEEN)

    def shut(self) -> Shut | None:
        """Why a step is skipped now, or None when it may ask (closed, or half-open).

        The skip records a refusal of the class that opened the breaker, marked unsent.
        """
        with self._lock:
            state = self.state
            if not state.is_open(self.setup.now()):
                return None
        assert state.reason is not None  # an open breaker always holds one
        # Unsent is the recorded fact the reports count apart; the detail is only words.
        return Shut(open_detail(state), RefusalFacts(state.reason, unsent=True))

    def settle(self, error: BaseException | None, facts: RefusalFacts | None) -> None:
        """Tell the guard how a step ended: an answer (no *error*), or a refusal.

        A refusal's *facts* give its class. An *error* already counted is a failure
        kept and raised again, so it changes nothing.
        """
        with self._lock:
            if error is None or facts is None:
                self._change(after_answer(self.state))
                return
            if any(error is seen for seen in self._counted):
                return
            self._counted.append(error)
            self._change(after_refusal(self.state, facts.reason, self.setup.now()))

    def _change(self, state: BreakerState) -> None:
        """Take *state*, writing it when it differs; a failed write is kept."""
        if state == self.state:
            return
        self.state = state
        try:
            write_breaker(self.setup.path, state)
        except (OSError, ReadBackError) as error:
            self.write_error = str(error)


@runtime_checkable
class GuardedByBreaker(Protocol):
    """A provider whose steps a breaker guards: it carries the guard, or None."""

    @property
    def breaker(self) -> BreakerGuard | None:
        """The guard its steps share."""


def guard_of(provider: object) -> BreakerGuard | None:
    """The guard *provider* carries, or None when it carries none."""
    return provider.breaker if isinstance(provider, GuardedByBreaker) else None


def table_guards(table: ProviderTable) -> dict[Source, BreakerGuard]:
    """Each guard *table*'s providers carry, by their source."""
    guards: dict[Source, BreakerGuard] = {}
    for key, provider in table.items():
        guard = guard_of(provider)
        if guard is not None:
            guards[key.source] = guard
    return guards


def breaker_setups(
    environ: Mapping[str, str],
    clock: Callable[[], datetime],
    notice: Callable[[str], None],
    *,
    sources: Iterable[Source] = GUARDED_SOURCES,
) -> dict[Source, BreakerSetup]:
    """Each guarded provider's setup of *sources*, its file read now on *clock*.

    A file that cannot be used is a notice under the provider's name, and starts it
    closed; the file is replaced the first time its breaker changes. A guarded
    provider not in *sources* has no setup, and its file is not read.
    """
    setups: dict[Source, BreakerSetup] = {}
    for source in sources:
        path = breaker_path(environ, source)
        read = read_breaker(path, clock())
        if read.warning is not None:
            notice(f"{source.label.lower()}: {read.warning}")
        setups[source] = BreakerSetup(path, read.state, clock)
    return setups


def report_write_errors(table: ProviderTable, notice: Callable[[str], None]) -> None:
    """Notice each guard in *table* whose file could not be written, once each."""
    for source, guard in table_guards(table).items():
        if guard.write_error is not None:
            prefix = source.label.lower()
            notice(f"{prefix}: could not write the breaker file: {guard.write_error}")
