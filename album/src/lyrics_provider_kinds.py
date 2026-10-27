"""What a provider may say of itself, which the fetch chain reads before and after a call.

Each is a protocol a provider implements by carrying the method, read with
``isinstance``: its own whole-call bound (`OwnsDeadline`), the queries it sends no
request for (`AsksSelectively`, read by `skips`), one call settling every kind
(`AnswersEveryKind`), and what it kept from an earlier call (`KeepsAnswers`). The
chain is `lyrics_fetch`'s, which re-exports every name here. Nothing here prints.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lyrics_answer import LrcAnswer
from lyrics_model import Provider, TrackQuery


@runtime_checkable
class OwnsDeadline(Protocol):
    """A provider that retries inside one call, and so sets its own whole-call bound.

    The watchdog uses that bound as given, with no input of the chain's, so it cannot
    disagree with the moment the provider itself gives up.
    """

    def step_deadline_s(self) -> float:
        """The bound for one whole call."""


@runtime_checkable
class AsksSelectively(Protocol):
    """A provider that sends no request for some queries, and says which beforehand.

    The chain skips it for those, recording no attempt: an attempt would count the
    provider as asked in the run's tally (`lyrics_tally`) for a track it never asked
    about, and a provider that had stopped working would hide behind those counts.
    """

    def asks(self, query: TrackQuery) -> bool:
        """False when a call for *query* would send no request."""


def skips(provider: Provider, query: TrackQuery) -> bool:
    """True when *provider* says it would send no request for *query*."""
    return isinstance(provider, AsksSelectively) and not provider.asks(query)


@runtime_checkable
class AnswersEveryKind(Protocol):
    """A provider whose one call settles every kind of step for a query.

    The chain asks it once per query within a track's fetch and keeps the answer, or the
    failure, for its other kind's step (`lyrics_fetch._ask`), so a plain pass sends
    nothing a synced pass already sent. The method's name is one no single-kind provider
    carries, since the protocol check reads only that the attribute exists.
    """

    def answer_all_kinds(self, query: TrackQuery) -> LrcAnswer:
        """The provider's text of each kind for *query*."""


@runtime_checkable
class KeepsAnswers(Protocol):
    """A provider that keeps what one call heard, and serves later queries from it.

    A call it serves from what it kept sends nothing, so it is no evidence of the
    provider's health: the chain neither asks the breaker before it nor tells the
    breaker how it ended, as for an every-kind provider's `lyrics_fetch.Heard` re-read.
    """

    def kept(self, query: TrackQuery) -> bool:
        """Whether a call for *query* would read what an earlier call kept."""
