"""A refusal's class and facts, read once from the error that ended a provider step.

A provider that refused or failed is classed once, here, from the exception itself, so
every later decision reads the class rather than the detail text (`classify`). The five
classes (`Refusal`) are a rate limit, an overloaded server, a server never reached, a
block, and an answer this tool does not recognize. The facts kept beside the class
(`RefusalFacts`) are the refusing status, the retries a retried request made, the wait
the reply's ``Retry-After`` asked for before any cap, and the reply's ``Server`` header,
so a CDN's ``503`` can be told from the origin's, and whether the step's request went
unsent because the provider's breaker was open (`lyrics_breakers`), so a count can tell
a skipped lookup from a refusal heard. Never a reply body.

`classify` reads only types and attributes:

- `lyrics_providers.HttpStatusError`: its status's class (`status_refusal`); a redirect
  status is a refused redirect, a reply of the wrong shape, so unrecognized.
- `lyrics_providers.RetriedError`: how it failed last, with its retries.
- `ClassedError`: the facts it was raised with. The cooling clients' stopped and
  unrecognized errors and Genius's challenge after its retries are such errors.
- A connection error or a timeout, `ProviderTimeout` included: unreachable.
- Anything else, such as a reply that is not JSON, too large, or of the wrong shape
  (NetEase's ``code N``, ``unexpected reply``): unrecognized.

The one reader of a detail, for a manifest written before the class was kept, is
`lyrics_refusal_detail.reason_from_detail`, which also reads the provider. The record a
track's outcome keeps of one refusal, `ProviderRefusal`, lives here too;
`lyrics_records` re-exports it. Nothing here prints.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import StrEnum

import requests

from lyrics_model import LyricsKind, Source
from lyrics_providers import REDIRECT_STATUSES
from lyrics_providers import HttpStatusError, ProviderError, ProviderTimeout
from lyrics_providers import RetriedError

#: The status a service answers with when asked too often.
_TOO_MANY_REQUESTS = 429
#: The client errors (4xx). Most say one request was bad, not that the client is barred.
_CLIENT_ERRORS = range(400, 500)
#: The client errors that do bar the client: unauthorized, forbidden, proxy
#: authentication required, and unavailable for legal reasons.
_BARRING_STATUSES = frozenset({401, 403, 407, 451})
#: The server errors (5xx): a server or gateway that failed to answer this time, read
#: as overloaded whatever its number, since any of them may answer next time.
_SERVER_ERRORS = range(500, 600)
#: The statuses a request may be tried again after, a rate limit or any server error:
#: exactly those `status_refusal` classes as rate limited or overloaded. The HTTP
#: layer's retries read this set (`lyrics_http.RETRY_STATUSES`).
TRANSIENT_STATUSES = frozenset({_TOO_MANY_REQUESTS, *_SERVER_ERRORS})
#: The errors of a server never reached: a connection that failed or dropped, or a
#: request or a whole call that ran out of time. A connection dropped while the body is
#: read raises ``ChunkedEncodingError`` from the read, not a ``ConnectionError``, and is
#: the same server that never finished answering. Their names are what an old manifest's
#: detail holds for them (`lyrics_providers.error_detail`), so its reader
#: (`lyrics_refusal_detail`) reads the same list.
UNREACHABLE_ERRORS: tuple[type[Exception], ...] = (
    ProviderTimeout,
    requests.ConnectionError,
    requests.ConnectTimeout,
    requests.ReadTimeout,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.SSLError,
    requests.exceptions.ProxyError,
    ConnectionError,
    ConnectionAbortedError,
    ConnectionRefusedError,
    ConnectionResetError,
    BrokenPipeError,
    TimeoutError,
)
#: The most characters a refusal's detail, or its server, keeps: room for a status, a
#: provider's refusal code or a short reason, never a reply body.
MAX_REFUSAL_DETAIL_CHARS = 80


class Refusal(StrEnum):
    """The class of a refusal, for a policy per class to tell them apart.

    Stored in a manifest by its words.
    """

    RATE_LIMITED = "rate limited"
    OVERLOADED = "overloaded"
    UNREACHABLE = "unreachable"
    BLOCKED = "blocked"
    UNRECOGNIZED = "unrecognized"


def status_refusal(status: int) -> Refusal:
    """The class of a refusing *status*.

    429 is rate limited and any 5xx overloaded. A 4xx that bars the client
    (`_BARRING_STATUSES`) is blocked; any other 4xx, such as a 400 or a 404, is that
    request's own error, unrecognized. Every other status is blocked too, for a
    provider that takes any reply but 200 as a refusal.
    """
    if status == _TOO_MANY_REQUESTS:
        return Refusal.RATE_LIMITED
    if status in _SERVER_ERRORS:
        return Refusal.OVERLOADED
    if status in _CLIENT_ERRORS and status not in _BARRING_STATUSES:
        return Refusal.UNRECOGNIZED
    return Refusal.BLOCKED


def stopping_status_refusal(status: int) -> Refusal:
    """The class of a refusing *status* that stops its provider for the run.

    As `status_refusal`, but a client error it calls that request's own (unrecognized)
    is blocked: the stop leaves every later track unasked, so it is never one request's
    fault, and a blocked refusal keeps the album owing the provider.
    """
    refusal = status_refusal(status)
    return Refusal.BLOCKED if refusal is Refusal.UNRECOGNIZED else refusal


@dataclass(frozen=True)
class RefusalFacts:
    """What one refusal was: its class, and what the failed reply said of itself.

    *status* is the refusing HTTP status, *retries* the retries made before the last
    failure, *retry_after_s* the wait the reply's ``Retry-After`` asked for, before
    any cap a retry holds it to, and *server* its ``Server`` header. Each is None, or
    no retries, where the error does not know it. *unsent* is True only for a step an
    open breaker skipped: no request went out, and *reason* is the class that opened
    the breaker, so the reports count it apart from a refusal heard. `classify` never
    sets it, since every error it reads came from a request sent.
    """

    reason: Refusal
    status: int | None = None
    retries: int = 0
    retry_after_s: float | None = None
    server: str | None = None
    unsent: bool = False


class ClassedError(ProviderError):
    """A provider error raised knowing its refusal's class and facts (`classify`)."""

    def __init__(self, message: str, *, detail: str, facts: RefusalFacts) -> None:
        """Carry *message*, the short *detail* and the refusal's *facts*."""
        super().__init__(message, detail=detail)
        self.facts = facts


def _status_facts(error: HttpStatusError) -> RefusalFacts:
    """*error*'s facts: a redirect status is a refused redirect, so unrecognized."""
    if error.status in REDIRECT_STATUSES:
        reason = Refusal.UNRECOGNIZED
    else:
        reason = status_refusal(error.status)
    return RefusalFacts(
        reason, error.status, retry_after_s=error.retry_after_s, server=error.server
    )


def classify(error: BaseException) -> RefusalFacts:
    """The class and facts of the refusal *error* records, by its type and attributes.

    Never by its detail or message text, which is the one thing a later reword
    changes without anything failing.
    """
    if isinstance(error, RetriedError):
        return dataclasses.replace(classify(error.last), retries=error.retries)
    if isinstance(error, HttpStatusError):
        return _status_facts(error)
    if isinstance(error, ClassedError):
        return error.facts
    if isinstance(error, UNREACHABLE_ERRORS):
        return RefusalFacts(Refusal.UNREACHABLE)
    return RefusalFacts(Refusal.UNRECOGNIZED)


def one_capped_line(text: str) -> str:
    """*text* as one line, whitespace runs single spaces, cut to the detail's cap."""
    return " ".join(text.split())[:MAX_REFUSAL_DETAIL_CHARS].rstrip()


@dataclass(frozen=True)
class ProviderRefusal:
    """One provider call that refused or failed instead of answering.

    *provider* and *step* name the chain step as `lyrics_fetch.CHAIN` does: the
    source, and the kind of lyrics it was asked for. *detail* is a short reason such
    as ``HTTP 503``, ``timeout`` or a provider's refusal code. It is held to one line,
    whitespace runs collapsed to single spaces, and cut to `MAX_REFUSAL_DETAIL_CHARS`,
    so a reply body passed by mistake cannot fill the manifest. *reason* and the rest
    are the refusal's facts (`RefusalFacts`); *server* is held as *detail* is, and a
    blank one is None.
    """

    provider: Source
    step: LyricsKind
    detail: str
    reason: Refusal
    status: int | None = None
    retries: int = 0
    retry_after_s: float | None = None
    server: str | None = None
    unsent: bool = False

    def __post_init__(self) -> None:
        """Collapse the detail and server to capped lines; idempotent, so a reread is equal."""
        # Frozen, so each normalized value is set the one way a frozen dataclass allows.
        object.__setattr__(self, "detail", one_capped_line(self.detail))
        server = None if self.server is None else one_capped_line(self.server)
        object.__setattr__(self, "server", server or None)

    @classmethod
    def of(
        cls, provider: Source, step: LyricsKind, detail: str, facts: RefusalFacts
    ) -> ProviderRefusal:
        """The refusal of *provider*'s *step*, recorded as *detail*, with its *facts*."""
        return cls(
            provider,
            step,
            detail,
            facts.reason,
            facts.status,
            facts.retries,
            facts.retry_after_s,
            facts.server,
            facts.unsent,
        )
