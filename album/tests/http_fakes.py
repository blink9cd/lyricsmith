"""An HTTP transport that answers from memory, so a real client runs without a network.

`transport` replaces ``requests``' adapter ``send`` with a function of the request, so
the real library code, its session and any response hook on it all still run, and only
the socket is never reached. `response` and `json_body` build what the function answers.
A response's body is an unread stream, as a real one is, so a caller that streams the
reply reads it chunk by chunk and one that does not reads it whole.
`RecordingServer` is a provider's fake server built on it, `HeldTransport` one that never
answers until released, and `json_values` the property tests' strategy for replies in a
provider's vocabulary. `cookies_kept` hands a session's jar a stand-in reply setting
one cookie, as `requests` does after a send, and counts what the jar kept; `ld_script`
builds the JSON-LD script element a provider's page carries.

Not discovered as a suite (no ``test_`` prefix).
"""

from __future__ import annotations

import abc
import email.message
import io
import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import requests
import requests.adapters
import requests.cookies
from hypothesis import strategies as st

#: What a fake transport does with each request: the response to hand back.
Answer = Callable[[requests.PreparedRequest], requests.Response]


def response(
    request: requests.PreparedRequest,
    status: int,
    body: bytes = b"",
    headers: Mapping[str, str] | None = None,
) -> requests.Response:
    """A response to *request* with *status*, *headers* and *body*, built in memory."""
    built = requests.Response()
    built.status_code = status
    built.headers.update(headers or {})
    # An unread raw stream, as the transport hands back: requests reads it on demand.
    built.raw = io.BytesIO(body)
    built.request = request
    built.url = request.url or ""
    return built


def json_body(value: object) -> bytes:
    """*value* as the JSON bytes a response body carries."""
    return json.dumps(value).encode()


def transport(answer: Answer) -> AbstractContextManager[object]:
    """Replace the HTTP transport's ``send`` with *answer*, which never touches a socket."""

    def send(
        adapter: requests.adapters.HTTPAdapter,
        request: requests.PreparedRequest,
        **kwargs: object,
    ) -> requests.Response:
        return answer(request)

    return mock.patch.object(requests.adapters.HTTPAdapter, "send", send)


def always(status: int) -> Answer:
    """A transport answer giving every request *status*, with no body."""

    def answer(request: requests.PreparedRequest) -> requests.Response:
        return response(request, status)

    return answer


class HeldTransport:
    """A transport answer that holds every request until released, then gives *status*.

    It stands in for a server that never answers, so the watchdog abandons the call.
    The abandoned call goes on running, and may send a follow-up request once released,
    so `finish` must run while this transport is still installed: then that request is
    answered here, and never reaches the socket after the patch is gone.
    """

    def __init__(self, limit_s: float, status: int = 404) -> None:
        """Hold each request up to *limit_s*, then answer it with *status*."""
        self.limit_s = limit_s
        self.status = status
        self.released = threading.Event()
        #: The URL of every request sent, in order.
        self.sent: list[str] = []
        self._callers: list[threading.Thread] = []

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """Record *request* and the thread sending it, then hold it until released."""
        self.sent.append(request.url or "")
        self._callers.append(threading.current_thread())
        self.released.wait(timeout=self.limit_s)
        return response(request, self.status)

    def finish(self) -> bool:
        """Release every request and wait for each sending thread; whether all ended."""
        self.released.set()
        deadline = time.monotonic() + self.limit_s
        for caller in list(self._callers):
            caller.join(max(0.0, deadline - time.monotonic()))
        return not any(caller.is_alive() for caller in self._callers)


class RecordingServer(abc.ABC):
    """A provider's server from memory: records every request, then answers by its path.

    A subclass names the reply for each path (`reply`): an int is that status alone,
    bytes a 200 with that raw body, and anything else a 200 with it as the JSON body.
    """

    def __init__(self) -> None:
        """Start with no request recorded."""
        self.requests: list[requests.PreparedRequest] = []

    @abc.abstractmethod
    def reply(self, path: str) -> object:
        """What the server answers a request for *path* with."""

    def paths(self) -> list[str]:
        """The path of every request made, in order."""
        return [urlsplit(r.url or "").path for r in self.requests]

    def params(self, index: int) -> dict[str, list[str]]:
        """The query parameters of request *index*."""
        return parse_qs(urlsplit(self.requests[index].url or "").query)

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """Record *request*, then answer it by its endpoint."""
        self.requests.append(request)
        answer = self.reply(urlsplit(request.url or "").path)
        if isinstance(answer, int):
            return response(request, answer)
        if isinstance(answer, bytes):
            return response(request, 200, answer)
        return response(request, 200, json_body(answer))


def json_values(
    keys: Sequence[str], *, max_size: int, max_leaves: int
) -> st.SearchStrategy[object]:
    """JSON-like values whose objects are keyed by *keys*, a provider's own field names.

    *max_size* bounds each object's keys and *max_leaves* the whole value.
    """
    scalars = st.none() | st.booleans() | st.floats() | st.integers()
    return st.recursive(
        scalars | st.text(max_size=20),
        lambda inner: (
            st.lists(inner, max_size=4)
            | st.dictionaries(st.sampled_from(list(keys)), inner, max_size=max_size)
        ),
        max_leaves=max_leaves,
    )


def ld_script(value: object) -> str:
    """A JSON-LD script element holding *value*, as a provider's page carries one."""
    return f'<script type="application/ld+json">{json.dumps(value)}</script>'


class _CookieReply:
    """A reply stand-in: the headers `http.cookiejar` reads through ``info()``."""

    def __init__(self, set_cookie: str) -> None:
        """Hold one ``Set-Cookie`` header."""
        self._headers = email.message.Message()
        self._headers["Set-Cookie"] = set_cookie

    def info(self) -> email.message.Message:
        """The reply's headers."""
        return self._headers


def cookies_kept(
    session: requests.Session, *, method: str, url: str, set_cookie: str
) -> int:
    """How many cookies *session*'s jar holds after a reply to *url* sets *set_cookie*."""
    request = requests.cookies.MockRequest(requests.Request(method, url).prepare())
    # The ignore: a stand-in reply, not an HTTPResponse; the jar reads only its info().
    session.cookies.extract_cookies(_CookieReply(set_cookie), request)  # type: ignore[arg-type]
    return len(session.cookies)
