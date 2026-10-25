"""The one pacer every paced provider keeps, and the deadline-bounded lock they share.

A `Pacer` holds a provider's requests at least `Pacer.interval_s` apart on an injected
clock. Where its interval counts from is the provider's own choice: from when a
request begins (`Pacer.take_turn`, as YouTube Music, Tekstowo and Genius do), or from
when it ends (`Pacer.wait` before it, `Pacer.mark` after it, as MusicBrainz does). A
wait that would end past the call's deadline is not begun: `ProviderTimeout`, with
nothing slept and the pacing left as it was.

`held_until` is the guard a provider with a lock takes before pacing: the lock is
waited for only until the call's deadline, the moment the watchdog abandons the call,
so an abandoned call's request ends before the next begins and a late one sends
nothing. `sleep_within` is the same rule for any other wait a call makes, such as a
retry's: a wait that would end at or past the deadline is not begun. Nothing here prints.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

from lyrics_providers import ProviderTimeout


class Lock(Protocol):
    """A lock waited for with a timeout, as `threading.Lock` is."""

    def acquire(self, blocking: bool = ..., timeout: float = ...) -> bool:
        """Wait up to *timeout* seconds for the lock; whether it was got."""

    def release(self) -> None:
        """Let the lock go."""


@dataclass
class Pacer:
    """At least *interval_s* between a provider's requests, never waiting past a deadline.

    *name* is the provider's, for the timeout's message, and *late_message* replaces
    that message where a provider's own wording predates the shared pacer.
    """

    interval_s: float
    name: str
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    late_message: str | None = None
    #: The clock's reading at the last `mark`; None before the first.
    last: float | None = field(default=None, init=False)

    def wait(self, give_up_at: float) -> None:
        """Sleep out what is left of the interval since the last mark; never mark.

        A turn that could not start by *give_up_at* raises `ProviderTimeout`, with
        nothing slept and `last` unchanged. The clock is read once, so the check and
        the sleep agree.
        """
        start = now = self.clock()
        if self.last is not None:
            start = max(now, self.last + self.interval_s)
        if start > give_up_at:
            raise ProviderTimeout(self._late())
        if start > now:
            self.sleeper(start - now)

    def _late(self) -> str:
        """The message of a turn refused at its deadline: `late_message`, else the standard."""
        if self.late_message is not None:
            return self.late_message
        return f"the {self.name} pace outlasts the deadline"

    def mark(self) -> None:
        """Count the next interval from now."""
        self.last = self.clock()

    def take_turn(self, give_up_at: float) -> None:
        """`wait`, then `mark`: the interval counts from when this request begins."""
        self.wait(give_up_at)
        self.mark()


@contextmanager
def held_until(
    lock: Lock, clock: Callable[[], float], give_up_at: float, name: str
) -> Iterator[None]:
    """Hold *lock* for the body, waiting for it only until *give_up_at* on *clock*.

    A deadline already reached, or a lock still held when it comes, raises
    `ProviderTimeout` naming *name*'s step, with nothing held and the body never run.
    """
    remaining = give_up_at - clock()
    if remaining <= 0 or not lock.acquire(timeout=remaining):
        raise ProviderTimeout(f"the {name} step's deadline passed")
    try:
        yield
    finally:
        lock.release()


def sleep_within(
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
    wait_s: float,
    give_up_at: float,
) -> bool:
    """Sleep *wait_s* unless it would end at or past *give_up_at*; whether it was slept.

    Unlike a pace wait (`Pacer.wait`), which the request follows at once, a wait ending
    exactly at the deadline is not begun: what follows it, such as a retry's
    `held_until`, refuses at the deadline, so the wait would only turn a refusal into
    a timeout.
    """
    if clock() + wait_s >= give_up_at:
        return False
    sleeper(wait_s)
    return True
