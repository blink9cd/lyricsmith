"""Every provider's shared HTTP: one session per call, a named User-Agent, a capped GET.

A GET given a `Retry` tries a transient refusal again (`RetryPolicy`): an HTTP 429 or any
5xx, or a connection that dropped or was refused, waits 2, 5 and then 10 seconds
between attempts, or what the reply's ``Retry-After`` asks in whole seconds, up to
`RETRY_AFTER_CAP_S`. A wait that would end past the call's own deadline is not begun, so
the retries never outlast the watchdog's bound for the call (`PROVIDER_DEADLINE_S` by
default), and a call the watchdog abandoned stops retrying on its own. Every other
failure is raised at once: another client error, a redirect, a request timeout (which
has already spent much of the call) and a certificate failure. Only the final failure
is raised, as a `RetriedError` naming the retries made when there were any. A refusal a
retry got past is not raised, but a policy given a hook (`RetryPolicy.on_retry`) is told
of each retry made, by the provider asked and the refusal's class
(`lyrics_refusal.classify`), so a run can count them (`lyrics_retried`). A status the
caller falls back on (*absent*) is no failure, so it is never retried.

Each provider call opens its own session (`new_session`), so a call the fetch chain's
watchdog abandoned never shares a connection or a cookie with the next one. Every request
carries a per-request timeout, because the watchdog bounds the whole call but cannot
interrupt a read already in flight, and the status hook (`lyrics_providers`), so an error
status, or a redirect, raises instead of reading as nothing found. A redirect is never
followed, as no API asked through it is known to answer one and following it would send the
request to a host nobody chose; only `get_text` follows, at most `MAX_REDIRECTS`.

A reply is streamed and read only up to `MAX_REPLY_BYTES`, refused past it before it is
decoded, so a hostile or broken server cannot make the run hold an unbounded body, or
the several times larger structure decoding it would build.

A session never sends a login from a netrc file: ``requests`` reads one for any host
that has no other authentication, and a credential the user keeps for another tool has
no business in a lyrics request. Only that is turned off; the proxy and certificate
bundle settings from the environment still apply (`block_netrc`).

The User-Agent names this tool and the project's version, as LRCLIB's API documentation
asks of every client, with no URL and nothing about the machine or its user in it. The
name is a constant (`USER_AGENT_NAME`), so a move or rename of the folder never changes
it; the version is read at run time from the project's ``pyproject.toml``, so it never
goes stale on a release. Each half keeps only ASCII letters, digits, ``.``, ``_`` and
``-``, since a version string could hold a newline or a space that would corrupt the
header; a half left empty becomes `FALLBACK_TOOL_NAME` or `UNKNOWN_VERSION`. The Genius
client's session sends the same header (`lyrics_genius.build_genius_client`).

Nothing here prints.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias, overload
from urllib.parse import urljoin

import requests

# The project's manifest reader, which reads the version the User-Agent carries, is on
# sys.path through the album tool's located module.
import lyrics_located  # noqa: F401 - puts the manifest reader on sys.path
import lyricsmith_project
from lyrics_model import Source
from lyrics_pace import ends_before
from lyrics_providers import HttpStatusError, ProviderError, RetriedError, status_hook
from lyrics_providers import REDIRECT_STATUSES
from lyrics_refusal import TRANSIENT_STATUSES, Refusal, classify

#: The project configuration whose ``[project]`` version the User-Agent carries.
PROJECT_CONFIG = lyricsmith_project.PROJECT_CONFIG
#: What the User-Agent says when the project's version cannot be read, or cleans to nothing.
UNKNOWN_VERSION = lyricsmith_project.UNKNOWN_VERSION
#: What the User-Agent names the tool when the name it is given cleans to nothing. Generic
#: on purpose, so a bad name never stands in for the tool's own.
FALLBACK_TOOL_NAME = "lyrics-tool"
#: Every character a User-Agent half may not keep: all but ASCII letters, digits, . _ -
_UNSAFE_TOKEN_CHARS = re.compile(r"[^A-Za-z0-9._-]")
#: A request's timeout: seconds to connect, then seconds between bytes of the reply.
RequestTimeout: TypeAlias = tuple[float, float]
#: Seconds to connect, then seconds between bytes of the reply. The watchdog bounds the
#: whole call; this bounds each request inside it, so a stalled socket fails on its own.
REQUEST_TIMEOUT_S: RequestTimeout = (5.0, 10.0)
#: The most bytes a reply may hold. LRCLIB's search answers at most 20 records and any
#: lyrics text over 64 KiB is refused anyway, so a real reply is far below this.
MAX_REPLY_BYTES = 2 * 1024 * 1024
#: The digits `MAX_REPLY_BYTES` takes: a declared length with more is over it unread.
_CAP_DIGITS = len(str(MAX_REPLY_BYTES))
#: How many bytes of a reply are read at a time, so the cap is checked as it streams in.
READ_CHUNK_BYTES = 64 * 1024
#: The details a run records for a reply refused unread, and one that is not JSON.
REPLY_TOO_LARGE = "reply too large"
REPLY_NOT_JSON = "reply not JSON"
#: A text GET's redirect limit, and the details: refused, one too many, body not UTF-8.
MAX_REDIRECTS = 2
REDIRECT_REFUSED = "redirect refused"
TOO_MANY_REDIRECTS = "too many redirects"
REPLY_NOT_TEXT = "reply not text"
#: A text GET's detail for a starting URL its own check refuses, before anything is sent.
#: Not a refused redirect: no server was asked, so nothing a provider stops for.
URL_REFUSED = "URL refused"
#: The whole-call bound each provider call runs under by default: the chain's watchdog
#: abandons a call past it, and an LRC call's retries never wait past it.
PROVIDER_DEADLINE_S = 30.0
#: The waits before each retry of a transiently refused request; then it is an error.
RETRY_WAITS_S: tuple[float, ...] = (2.0, 5.0, 10.0)
#: The longest wait a reply's Retry-After may ask for in place of the scheduled one.
RETRY_AFTER_CAP_S = 10.0
#: The statuses of an overloaded or rate-limiting server, which may answer next time:
#: the refusal classes' own set, so a retry and a class never disagree.
RETRY_STATUSES = TRANSIENT_STATUSES
#: Connection failures that are not transient: a request timeout has already spent much
#: of the call, and a certificate failure answers the same way every time.
_LASTING_CONNECTION_ERRORS = (requests.Timeout, requests.exceptions.SSLError)


@dataclass(frozen=True)
class Reply:
    """A decoded JSON reply, and the URL that was asked, query string included."""

    url: str
    body: object


@dataclass(frozen=True)
class TextReply:
    """A text reply, and the URL that answered it, after any redirect followed."""

    url: str
    text: str


class RedirectRefused(ProviderError):
    """A text GET's redirect *allowed* refused: `REDIRECT_REFUSED`, naming its target.

    *location* is the refused ``Location`` resolved against the page that answered it,
    or None when the redirect named none, so a caller can read one refused target as
    an answer of its own (`lyrics_bandcamp`); every other caller reads a `ProviderError`.
    """

    def __init__(self, source: Source, location: str | None) -> None:
        """Refuse *source*'s redirect to *location*, None when it named no target."""
        super().__init__(f"{source} redirect refused", detail=REDIRECT_REFUSED)
        self.location = location


#: What a policy tells of each retry it makes: the provider asked, the refusal's class.
OnRetry = Callable[[Source, Refusal], None]


@dataclass(frozen=True)
class RetryPolicy:
    """How a call's requests retry: the waits between attempts, on an injected clock.

    *on_retry*, when given, is told of each retry made, before its wait.
    """

    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    waits_s: tuple[float, ...] = RETRY_WAITS_S
    on_retry: OnRetry | None = None

    def within(self, bound_s: float) -> Retry:
        """The retry for one call starting now, which waits only within *bound_s*."""
        return Retry(self, self.clock() + bound_s)


@dataclass(frozen=True)
class Retry:
    """One call's retry: its policy, and the moment past which it waits no more."""

    policy: RetryPolicy
    give_up_at: float

    def pause_s(self, error: Exception, scheduled_s: float) -> float | None:
        """The wait before trying again after *error*, or None to raise it now.

        None for a failure that is not transient, and for a wait that would end at or
        past `give_up_at` (`lyrics_pace.ends_before`). A transient status's Retry-After
        replaces *scheduled_s*, capped.
        """
        if isinstance(error, HttpStatusError):
            if error.status not in RETRY_STATUSES:
                return None
            asked = error.retry_after_s
            wait = scheduled_s if asked is None else min(asked, RETRY_AFTER_CAP_S)
        elif isinstance(error, requests.ConnectionError) and not isinstance(
            error, _LASTING_CONNECTION_ERRORS
        ):
            wait = scheduled_s
        else:
            return None
        return wait if ends_before(self.policy.clock(), wait, self.give_up_at) else None


def _clean_token(text: str, fallback: str) -> str:
    """*text* keeping only ``[A-Za-z0-9._-]``, or *fallback* when nothing is left."""
    return _UNSAFE_TOKEN_CHARS.sub("", text) or fallback


#: The name every request carries. A constant, not the folder's name: a move or rename of the
#: folder must not change what a service sees.
USER_AGENT_NAME: Final = "lyricsmith"


def user_agent(config: Path = PROJECT_CONFIG, tool_name: str = USER_AGENT_NAME) -> str:
    """``<tool>/<version>``: *tool_name* and *config*'s project version, each cleaned."""
    name = _clean_token(tool_name, FALLBACK_TOOL_NAME)
    return f"{name}/{_clean_token(lyricsmith_project.project_version(config), UNKNOWN_VERSION)}"


def send_unchanged(request: requests.PreparedRequest) -> requests.PreparedRequest:
    """An authentication that adds nothing: *request* as it is."""
    return request


def block_netrc(session: requests.Session) -> None:
    """Stop *session* from sending a login read from a netrc file.

    ``requests`` consults netrc only for a request whose session and call name no
    authentication, so a do-nothing one turns that off and nothing else: headers a
    caller sets, such as a bearer key, pass unchanged, and the environment's proxy and
    certificate settings still apply, which turning off ``trust_env`` would lose.
    """
    session.auth = send_unchanged


def new_session() -> requests.Session:
    """A fresh session naming this tool in its User-Agent, that sends no netrc login."""
    session = requests.Session()
    session.headers["User-Agent"] = user_agent()
    block_netrc(session)
    return session


def cookie_free_session() -> requests.Session:
    """A `new_session` whose cookie jar refuses every cookie a reply sets.

    An empty ``allowed_domains`` makes `http.cookiejar.DefaultCookiePolicy` refuse a
    cookie from any domain, so the session's jar stays empty whatever ``Set-Cookie`` a
    reply carries. That holds only for a request sent with ``allow_redirects=False``, as
    this module's own `_send` sends: when ``requests`` follows a redirect itself, it
    copies a 3xx reply's cookies into a per-request jar of the default policy and sends
    them on the next hop, past this jar's policy.
    """
    session = new_session()
    session.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
    return session


def _send(
    session: requests.Session,
    url: str,
    params: Mapping[str, str | int],
    source: Source,
    absent: frozenset[int],
    *,
    redirects: bool = False,
    timeout_s: RequestTimeout = REQUEST_TIMEOUT_S,
) -> requests.Response:
    """GET *url* with *params* within *timeout_s*, its body not yet read.

    An error status not in *absent*, or a redirect unless *redirects*, raises in it.
    """
    hook = status_hook(source, absent, refuse_redirects=not redirects)
    return session.get(
        url,
        params=dict(params),
        timeout=timeout_s,
        hooks={"response": [hook]},
        # Never followed here, so `get_text` sees each hop: requests' own following would
        # carry a 3xx reply's cookie to the next hop past the refusing jar.
        allow_redirects=False,
        stream=True,
    )


def _send_retrying(
    session: requests.Session,
    url: str,
    params: Mapping[str, str | int],
    source: Source,
    absent: frozenset[int],
    retry: Retry | None,
    timeout_s: RequestTimeout = REQUEST_TIMEOUT_S,
) -> requests.Response:
    """`_send`, tried again after each transient failure *retry* has room to wait for.

    With no *retry*, one attempt. Each attempt has *timeout_s*. The final failure is
    raised as it is when no retry was made, else as a `RetriedError` counting them.
    """
    if retry is None:
        return _send(session, url, params, source, absent, timeout_s=timeout_s)
    retries = 0
    while True:
        try:
            return _send(session, url, params, source, absent, timeout_s=timeout_s)
        except (HttpStatusError, requests.RequestException) as error:
            schedule = retry.policy.waits_s
            pause = None
            if retries < len(schedule):
                pause = retry.pause_s(error, schedule[retries])
            if pause is None:
                if retries:
                    raise RetriedError(error, retries) from error
                raise
            if retry.policy.on_retry is not None:
                retry.policy.on_retry(source, classify(error).reason)
        retry.policy.sleeper(pause)
        retries += 1


def _too_large(source: Source) -> ProviderError:
    """The error for a reply from *source* over the cap, in fixed words."""
    message = f"{source} answered a reply over {MAX_REPLY_BYTES} bytes"
    return ProviderError(message, detail=REPLY_TOO_LARGE)


def _over_cap(digits: str) -> bool:
    """Whether the decimal *digits*, with no leading zero, exceed `MAX_REPLY_BYTES`."""
    if len(digits) != _CAP_DIGITS:
        return len(digits) > _CAP_DIGITS
    return int(digits) > MAX_REPLY_BYTES


def capped_body(response: requests.Response, source: Source) -> bytes:
    """*response*'s body, refused past `MAX_REPLY_BYTES` before the rest is read.

    A declared length over the cap is refused with nothing read; one with more digits
    than the cap, leading zeros aside, is over it without being handed to int(), which
    raises on thousands of digits. A length that is missing, or not a number, is no
    promise, so the bytes are counted as they arrive.
    A compressed reply is counted as it inflates: urllib3, from 2.6, inflates no more
    than each read asks for, which is why the project pins it there.
    """
    declared = response.headers.get("Content-Length", "")
    # isdecimal, not isdigit: a superscript two is a digit, but int() cannot read it.
    if declared.isdecimal() and _over_cap(declared.lstrip("0")):
        raise _too_large(source)
    body = bytearray()
    for chunk in response.iter_content(chunk_size=READ_CHUNK_BYTES):
        body += chunk
        if len(body) > MAX_REPLY_BYTES:
            raise _too_large(source)
    return bytes(body)


def _decoded(response: requests.Response, source: Source) -> Reply:
    """*response*'s JSON body and URL; a body too large or not JSON raises naming *source*."""
    raw = capped_body(response, source)
    try:
        body: object = json.loads(raw)
    # A JSONDecodeError, and bytes undecodable as text, are both ValueErrors.
    except ValueError as error:
        message = f"{source} answered a reply that is not JSON"
        raise ProviderError(message, detail=REPLY_NOT_JSON) from error
    return Reply(response.url, body)


def get_json(
    session: requests.Session,
    url: str,
    params: Mapping[str, str | int],
    source: Source,
    *,
    absent: frozenset[int] = frozenset(),
    retry: Retry | None = None,
) -> Reply | None:
    """GET *url* with *params*; its JSON reply, or None for a status in *absent*.

    A redirect, or any other error status, raises `HttpStatusError` from inside the
    send, and a body over `MAX_REPLY_BYTES` or not JSON raises `ProviderError` naming
    *source*. With a *retry*, a transient refusal is tried again first (`Retry`). Once
    the send returns a response, it is closed on every path from there.
    """
    with _send_retrying(session, url, params, source, absent, retry) as response:
        if response.status_code in absent:
            return None
        return _decoded(response, source)


def get_reply(
    session: requests.Session,
    url: str,
    params: Mapping[str, str | int],
    source: Source,
    *,
    retry: Retry | None = None,
    timeout_s: RequestTimeout = REQUEST_TIMEOUT_S,
) -> Reply:
    """GET *url* with *params*; its JSON reply, every error status raised.

    With a *retry*, a transient refusal is tried again first (`Retry`). Each request
    has *timeout_s*, which a caller with less of its call left than the default lowers.
    """
    sent = _send_retrying(session, url, params, source, frozenset(), retry, timeout_s)
    with sent as response:
        return _decoded(response, source)


def _text_body(response: requests.Response, source: Source) -> str:
    """*response*'s body, capped, as strict UTF-8; anything else is ``reply not text``."""
    try:
        return capped_body(response, source).decode("utf-8")
    except UnicodeDecodeError as error:
        message = f"{source} answered a reply that is not UTF-8 text"
        raise ProviderError(message, detail=REPLY_NOT_TEXT) from error


@overload
def get_text(
    session: requests.Session,
    url: str,
    source: Source,
    *,
    allowed: Callable[[str], bool],
    max_redirects: int = MAX_REDIRECTS,
    absent: None = None,
) -> TextReply: ...


@overload
def get_text(
    session: requests.Session,
    url: str,
    source: Source,
    *,
    allowed: Callable[[str], bool],
    max_redirects: int = MAX_REDIRECTS,
    absent: frozenset[int],
) -> TextReply | None: ...


def get_text(
    session: requests.Session,
    url: str,
    source: Source,
    *,
    allowed: Callable[[str], bool],
    max_redirects: int = MAX_REDIRECTS,
    absent: frozenset[int] | None = None,
) -> TextReply | None:
    """GET *url* as text, following at most *max_redirects* redirects *allowed* accepts.

    *url* and each ``Location`` must pass *allowed* before it is asked. Never retried.
    A *url* it refuses is `URL_REFUSED`; a ``Location`` it refuses, or none given, is
    `RedirectRefused` (`REDIRECT_REFUSED`), naming it. A status in *absent*, on
    whichever hop answers it, is None, as `get_json`'s is; with no *absent* every error
    status raises.
    """
    if not allowed(url):
        raise ProviderError(f"{source} URL refused", detail=URL_REFUSED)
    passes = absent or frozenset()
    current: str | None = url
    for _hop in range(max_redirects + 1):
        if current is None or not allowed(current):
            raise RedirectRefused(source, current)
        with _send(session, current, {}, source, passes, redirects=True) as reply:
            if reply.status_code in passes:
                return None
            if reply.status_code not in REDIRECT_STATUSES:
                return TextReply(current, _text_body(reply, source))
            location = reply.headers.get("Location")
        current = urljoin(current, location) if location else None
    raise ProviderError(f"{source} redirected too often", detail=TOO_MANY_REDIRECTS)
