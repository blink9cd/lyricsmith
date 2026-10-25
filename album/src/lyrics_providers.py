"""The providers' failure types, and the status hook that raises them on an HTTP error.

A provider must never answer a failure as though nothing were found: a rate limit read as
"no lyrics" looks, in the run's summary, exactly like a song the provider does not hold.
So every request a provider makes carries a ``requests`` response hook (`status_hook`),
which raises `HttpStatusError`, naming the source and the status, on any status of 400
or above. ``requests`` runs it inside the send, before the caller sees the reply.

The LRC adapters (`lyrics_lrclib`, `lyrics_netease`) pass it with each request, naming
the statuses a caller falls back on rather than fails (`absent`): LRCLIB's exact lookup
names its 404, no record matched, and any server error, and goes on to its search.
Every other error status raises. Their requests follow no redirect, so the hook raises
on a redirect status (300 to 399) too, whatever *absent* names: an unfollowed redirect
is never a reply. lyricsgenius answers a non-OK API status with a bare ``AssertionError`` whose
only record of the status is its message text, so `lyrics_genius` puts the hook on that
client's session instead (`hook_status_errors`); the error is not an exception type the
library catches, so it reaches the adapter unchanged. That hook leaves redirects alone:
the library follows them, and the hook sees each response on the way, so refusing a
redirect there would fail a Genius page that has merely moved. Which redirects that
client may follow is `lyrics_genius`'s own hook's to decide.

A failure keeps a short detail for the run's record of refusals (`error_detail`):
``HTTP <status>``, ``timeout``, or the few fixed words an adapter gives its
`ProviderError`, such as NetEase's ``code -462``. Never the error's message, which may
hold a request URL with the track's title in it, and never a reply's body. A request the
LRC adapters retried (`lyrics_http`) and that still failed is a `RetriedError`, whose
detail adds the retries made: ``HTTP 503 after 3 retries``.

Nothing here prints.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from typing import Protocol

from lyrics_model import Source

#: The statuses of a redirect: the reply is elsewhere, so it is not this one.
REDIRECT_STATUSES = range(300, 400)


#: The detail a call abandoned at its deadline records.
TIMEOUT_DETAIL = "timeout"


class ProviderTimeout(Exception):
    """A provider call outlived its deadline and was abandoned."""


class ProviderError(Exception):
    """A provider failed, or answered in a shape it is not known to answer in.

    *detail* is the few words a run records for it (`error_detail`): fixed text or a
    status or code, never the message, which may name the query, and never a body.
    """

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        """Carry *message*, and the short *detail*; None records the type's name."""
        super().__init__(message)
        self.detail = detail


class HttpStatusError(ProviderError):
    """A provider answered a request with an HTTP error status, such as a 429 rate limit.

    *retry_after_s* is the wait the reply's ``Retry-After`` header asked for, in
    seconds, or None when it gave none in that form (`retry_after_s`).
    """

    def __init__(
        self, source: Source, status: int, retry_after_s: float | None = None
    ) -> None:
        """Name *source* and *status*; the detail is ``HTTP <status>``."""
        super().__init__(f"{source} answered HTTP {status}", detail=f"HTTP {status}")
        self.source = source
        self.status = status
        self.retry_after_s = retry_after_s


def retries_phrase(count: int) -> str:
    """``1 retry`` or ``<count> retries``."""
    return "1 retry" if count == 1 else f"{count} retries"


class RetriedError(ProviderError):
    """A request still failed after *retries* retries; *last* is how it failed last.

    Its detail is *last*'s, with the retries added: ``HTTP 503 after 3 retries``, or
    ``ConnectionError after 1 retry`` when the call's deadline left room for one.
    """

    def __init__(self, last: Exception, retries: int) -> None:
        """Wrap *last*, the failure the final attempt met, after *retries* retries."""
        detail = f"{error_detail(last)} after {retries_phrase(retries)}"
        super().__init__(f"still failing: {detail}", detail=detail)
        self.last = last
        self.retries = retries


def http_status(error: Exception) -> int | None:
    """The HTTP status *error* names when a status hook raised it, else None."""
    return error.status if isinstance(error, HttpStatusError) else None


def error_detail(error: Exception) -> str:
    """The short detail a run records for a provider call that raised *error*.

    A `ProviderError`'s own detail, else the error's type alone. Never its message:
    one may carry a request URL, with the track's title or a provider token in it. A
    call abandoned at its deadline is a timeout, not an error, and records
    `TIMEOUT_DETAIL` instead.
    """
    if isinstance(error, ProviderError) and error.detail is not None:
        return error.detail
    return type(error).__name__


# --- The status hook ---------------------------------------------------------------------


class HttpResponse(Protocol):
    """A ``requests`` response, as far as the hook reads it."""

    @property
    def ok(self) -> bool:
        """True for a status below 400."""

    @property
    def status_code(self) -> int:
        """The HTTP status."""

    @property
    def headers(self) -> Mapping[str, str]:
        """The reply's headers, looked up without regard to case."""


#: The most digits a ``Retry-After`` may have and still be read: over 30 years of
#: seconds, far past any cap a caller holds it to. A longer run is garbled, and is
#: never handed to int() or float(), which raise on thousands of digits.
MAX_RETRY_AFTER_DIGITS = 9


def retry_after_s(value: str | None) -> float | None:
    """The seconds a ``Retry-After`` header's *value* asks to wait, else None.

    Only the whole-seconds form is read. The HTTP-date form, a negative number, a run
    past `MAX_RETRY_AFTER_DIGITS` and anything garbled are None, so the caller keeps
    its own schedule.
    """
    text = (value or "").strip()
    # isdecimal, not isdigit: a superscript two is a digit, but int() cannot read it.
    readable = text.isdecimal() and len(text) <= MAX_RETRY_AFTER_DIGITS
    return float(int(text)) if readable else None


#: A ``requests`` response hook: called with each response, its return value unused.
ResponseHook = Callable[..., object]


class HookedSession(Protocol):
    """A ``requests`` session, as far as a hook is put on it."""

    @property
    def hooks(self) -> MutableMapping[str, list[ResponseHook]]:
        """The session's hooks by event; every request it sends runs the "response" list."""


def status_hook(
    source: Source,
    absent: frozenset[int] = frozenset(),
    *,
    refuse_redirects: bool = True,
) -> ResponseHook:
    """A response hook raising `HttpStatusError` for *source* on any non-OK response.

    An error status in *absent* passes: it names a status the caller falls back on
    rather than fails, which it reads from the response itself. A redirect raises
    too, whatever *absent* names, unless *refuse_redirects* is off for a session that
    follows redirects itself.
    """

    def raise_on_error_status(
        response: HttpResponse, *args: object, **kwargs: object
    ) -> None:
        status = response.status_code
        if refuse_redirects and status in REDIRECT_STATUSES:
            raise HttpStatusError(source, status)
        if not response.ok and status not in absent:
            wait = retry_after_s(response.headers.get("Retry-After"))
            raise HttpStatusError(source, status, wait)

    return raise_on_error_status


def hook_status_errors(session: HookedSession, source: Source) -> None:
    """Make every request *session* sends raise `HttpStatusError` on an error status.

    Redirects pass: the session's library follows them, and the hook sees each one.
    """
    session.hooks["response"].append(status_hook(source, refuse_redirects=False))
