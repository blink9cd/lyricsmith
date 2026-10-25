"""The base of a client that stops for the run at a refusal and cools down for a day.

YouTube Music's and Tekstowo's clients share it (`CoolingClient`): one request at a
time under a lock, paced by a `lyrics_pace.Pacer` from when each request begins, and
stopped once a request is refused. Its two seams are where the rules for refusals
attach:

- `CoolingClient.available` is the one readiness point: each provider's ``asks``
  reads it, and so does `CoolingClient.turn` before any request; ``provider_stop``
  reports the `stop` behind it.
- `CoolingClient.refuse` is the one policy point every refusal goes through, classed
  as a `Refusal`. Today every class stops the client for the run and writes the
  24-hour cool-down (`lyrics_cooldown.record_stop`); a subclass may take a class
  aside, as Tekstowo counts its unrecognized pages before stopping.

A call's order is fixed by `CoolingClient.turn`: the deadline first, then the stop,
then the pace, so a call past its deadline is a timeout even once stopped, and a
stopped client sends nothing and sleeps nothing. Nothing here prints.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import ClassVar

import requests

from lyrics_cooldown import Stop, record_stop, utc_now
from lyrics_http import cookie_free_session
from lyrics_pace import Pacer, held_until
from lyrics_providers import ProviderError

#: The status a service answers with when asked too often.
_TOO_MANY_REQUESTS = 429
#: The statuses of a server or gateway too busy to answer.
_OVERLOADED_STATUSES = frozenset({502, 503, 504})


class Refusal(Enum):
    """The class of a refusal, for a policy per class to tell them apart.

    Today every class but Tekstowo's unrecognized pages takes the same policy: a stop
    for the run, with the 24-hour cool-down.
    """

    RATE_LIMITED = "rate limited"
    OVERLOADED = "overloaded"
    UNREACHABLE = "unreachable"
    BLOCKED = "blocked"
    UNRECOGNIZED = "unrecognized"


def status_refusal(status: int) -> Refusal:
    """The class of a refusing *status*: too many requests, overloaded, else blocked."""
    if status == _TOO_MANY_REQUESTS:
        return Refusal.RATE_LIMITED
    if status in _OVERLOADED_STATUSES:
        return Refusal.OVERLOADED
    return Refusal.BLOCKED


@dataclass
class CoolingClient:
    """A source's one client for a run: paced, locked, and stoppable once.

    *state_path* is the cool-down file a stop writes. The clock, sleeper, now and
    session factory are injected so tests run with no time passing and no network. A
    subclass names its source (`NAME`), its pace (`MIN_INTERVAL_S`) and the error a
    stopped call raises (`stopped_error`, built from the stop's words).
    """

    NAME: ClassVar[str]
    MIN_INTERVAL_S: ClassVar[float]
    stopped_error: ClassVar[type[ProviderError]]

    state_path: Path
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = utc_now  # aware UTC
    session_factory: Callable[[], requests.Session] = cookie_free_session
    stop: Stop | None = field(default=None, init=False)
    lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )
    #: Built from the clock and sleeper given, so the pace runs on the same fakes.
    pacer: Pacer = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Pace this source at its `MIN_INTERVAL_S`, on the injected clock and sleeper."""
        self.pacer = Pacer(
            self.MIN_INTERVAL_S, self.NAME, clock=self.clock, sleeper=self.sleeper
        )

    def available(self) -> bool:
        """Whether the client still asks its source: True until it stopped.

        The one readiness point, where a breaker answering per run would answer too.
        """
        return self.stop is None

    @contextmanager
    def turn(self, give_up_at: float) -> Iterator[None]:
        """One request's turn: under the lock, not stopped, and paced; by *give_up_at*.

        Past *give_up_at* it raises `ProviderTimeout` unsent; once stopped, the
        `stopped_error` unsent and unslept; a pace that would outlast *give_up_at*,
        `ProviderTimeout`. The lock is held over the body, so no two requests overlap
        and an abandoned call's request ends before the next begins.
        """
        with held_until(self.lock, self.clock, give_up_at, self.NAME):
            if not self.available():
                # Only a stop makes the client unavailable today; a breaker would be
                # told apart here, with an error of its own.
                assert self.stop is not None
                raise self.stopped_error(self.stop.what)
            self.pacer.take_turn(give_up_at)
            yield

    def refuse(self, refusal: Refusal, what: str) -> ProviderError:
        """The error to raise for a *refusal* of class *refusal*, recorded as *what*.

        The one policy point every refusal goes through, where a policy per class and
        a breaker would attach. Today every class stops the client for the run.
        """
        del refusal  # every class takes the same policy today
        return self._stopped(what)

    def _stopped(self, what: str) -> ProviderError:
        """Stop for the run on *what*, writing the cool-down; the error to raise.

        A cool-down that cannot be written still stops the run (`record_stop`), the
        failure kept in `Stop.write_error` for the caller to report.
        """
        refused_at = self.now().replace(microsecond=0)  # to the second, as written
        self.stop = record_stop(self.state_path, refused_at, what)
        return self.stopped_error(what)
