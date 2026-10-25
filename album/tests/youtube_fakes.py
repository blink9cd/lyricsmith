"""A scripted YouTube Music server from memory, and a client built on fakes to ask it.

`ScriptedServer` answers each request with the next ``(status, body, headers)`` of its
script, recording the request, its decoded JSON body and the fake clock's time at it.
`client` builds the real `YouTubeMusicClient` on a `genius_fakes.FakeClock`, a fixed
`NOW` and a state path inside the test's temporary directory, so its pacing takes no
time and its cool-down file never reaches the real state folder. Every reply is invented.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``, never from another suite, so each suite still imports alone.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import requests

import http_fakes
import lyrics_youtube_music as lym
from genius_fakes import FakeClock

#: One scripted answer: its status, its raw body and its headers.
Answer = tuple[int, bytes, Mapping[str, str]]
#: The fixed moment every faked client reads as now, aware UTC.
NOW = datetime(2026, 10, 21, 9, 30, tzinfo=UTC)
#: The cool-down file's name inside a test's state folder.
STATE_NAME = "youtube_music.json"


def ok(value: object) -> Answer:
    """A ``200`` holding *value* as its JSON body."""
    return (200, http_fakes.json_body(value), {})


class ScriptedServer:
    """Answers each request with the next scripted answer; records what was asked, and when."""

    def __init__(self, script: Sequence[Answer], clock: FakeClock) -> None:
        """Answer *script* in order, timing each request on *clock*."""
        self.script = list(script)
        self.clock = clock
        self.requests: list[requests.PreparedRequest] = []
        self.bodies: list[object] = []
        self.times: list[float] = []

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """Record *request*, its JSON body and the time, then give the next answer."""
        self.requests.append(request)
        raw = request.body
        assert isinstance(raw, bytes), "the client sends its body as encoded JSON"
        self.bodies.append(json.loads(raw))
        self.times.append(self.clock())
        status, body, headers = self.script[len(self.requests) - 1]
        return http_fakes.response(request, status, body, headers)


def state_path(tmp: Path) -> Path:
    """Where a test's cool-down file is written: a folder not yet made, under *tmp*."""
    return tmp / "state" / STATE_NAME


def client(tmp: Path, server: object, clock: FakeClock) -> lym.YouTubeMusicClient:
    """The real client on *clock*, the fixed `NOW` and a state path under *tmp*.

    *server* is not used here: the suite installs it as the transport. It is taken so
    a call names the server and the clock it records on together.
    """
    del server
    return lym.YouTubeMusicClient(
        state_path(tmp), clock=clock, sleeper=clock.sleep, now=lambda: NOW
    )
