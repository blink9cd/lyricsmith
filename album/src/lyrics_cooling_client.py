"""The base of a client that stops for the run at a refusal and cools down for a day.

YouTube Music's and Tekstowo's clients share it (`CoolingClient`): one request at a
time under a lock, paced by a `lyrics_pace.Pacer` from when each request begins, and
stopped once a request is refused. Its two seams are where the rules for refusals
attach:

- `CoolingClient.available` is the one readiness point: each provider's ``asks``
  reads it, and so does `CoolingClient.turn` before any request; ``provider_stop``
  reports the `stop` behind it.
- `CoolingClient.refuse` is the one policy point every refusal goes through, classed as
  a `Refusal`. Here every class stops the client for the run and writes the 24-hour
  cool-down (`lyrics_cooldown.record_stop`); a subclass may take a class aside, as
  Tekstowo counts its unrecognized pages before stopping. It takes the refusal's
  `RefusalFacts`, and the error it gives (a `StoppedError`) carries them: the class and,
  where the reply gave them, its status, ``Retry-After`` wait and ``Server`` header. So
  does every later call's, so a run records each refusal with its class
  (`lyrics_refusal.classify`).

`Refusal` and `status_refusal` live in `lyrics_refusal` and are re-exported here.

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
from pathlib import Path
from typing import ClassVar

import requests

from lyrics_cooldown import Stop, record_stop, utc_now
from lyrics_http import cookie_free_session
from lyrics_pace import Pacer, held_until
from lyrics_providers import ProviderError
from lyrics_refusal import ClassedError, RefusalFacts
from lyrics_refusal import Refusal as Refusal
from lyrics_refusal import status_refusal as status_refusal


class StoppedError(ClassedError):
    """A cooling source stopped for the run, or had already; its detail is *what*.

    A subclass names its source (`SOURCE`). *facts* are the stopping refusal's.
    """

    SOURCE: ClassVar[str]

    def __init__(self, what: str, facts: RefusalFacts) -> None:
        """Carry *what* as both the message's tail and the recorded detail."""
        super().__init__(f"{self.SOURCE} stopped: {what}", detail=what, facts=facts)


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
    stopped_error: ClassVar[type[StoppedError]]

    state_path: Path
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = utc_now  # aware UTC
    session_factory: Callable[[], requests.Session] = cookie_free_session
    stop: Stop | None = field(default=None, init=False)
    #: The stopping refusal's facts, which every later call's error carries too.
    stop_facts: RefusalFacts = field(
        default=RefusalFacts(Refusal.UNRECOGNIZED), init=False
    )
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

        The one readiness point: the provider's ``asks`` and `turn` both read it.
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
                # Only a stop makes the client unavailable (`available`), so one is
                # set, and its error is the one raised.
                assert self.stop is not None
                raise self.stopped_error(self.stop.what, self.stop_facts)
            self.pacer.take_turn(give_up_at)
            yield

    def refuse(self, facts: RefusalFacts, what: str) -> ProviderError:
        """The error to raise for a refusal of *facts*, recorded as *what*.

        The one policy point every refusal goes through, where a policy per class (its
        ``reason``) attaches: a subclass overrides it. Here every class stops the client
        for the run; the error carries *facts*: the class, and the refusing status, asked
        wait and server where the reply gave them.
        """
        return self._stopped(what, facts)

    def _stopped(self, what: str, facts: RefusalFacts) -> ProviderError:
        """Stop for the run on *what*, of *facts*, writing the cool-down; the error.

        A cool-down that cannot be written still stops the run (`record_stop`), the
        failure kept in `Stop.write_error` for the caller to report.
        """
        refused_at = self.now().replace(microsecond=0)  # to the second, as written
        self.stop = record_stop(self.state_path, refused_at, what)
        self.stop_facts = facts
        return self.stopped_error(what, facts)
