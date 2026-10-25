"""Tekstowo from memory, and the real client built on fakes to ask it.

`TekstowoServer` is `bandcamp_fakes.BandcampServer` (routes by host and path: an int
status, a `Redirect`, a str page or raw bytes; 404 unrouted) that also sets a cookie on
every answer, as the site does, so a suite can show none is ever sent back. It keeps the
requests and the fake clock's time at each under the names `BandcampServer` uses.
`search_path` is the path the client's own `search_url` asks, so a route keys on the
client's encoding. `client` builds the real `TekstowoClient` on a `genius_fakes.FakeClock`,
a fixed `NOW` and a state path inside the test's temporary directory. Every page and
name is invented.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``, never from another suite, so each suite still imports alone.
"""

from __future__ import annotations

import email.message
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

import lyrics_tekstowo as lt
from bandcamp_fakes import BandcampServer
from genius_fakes import FakeClock

#: The cookie every answer sets: invented, in the shape of the site's own.
SET_COOKIE = "_nss=invented"
#: The fixed moment every faked client reads as now, aware UTC.
NOW = datetime(2026, 10, 21, 9, 30, tzinfo=UTC)
#: The invented track every suite asks for.
ARTIST, TITLE = "Invented Artist", "The Keeper Counts"


def _set_cookie(built: requests.Response) -> requests.Response:
    """*built*, setting `SET_COOKIE` both in its headers and where a session reads it.

    A session stores cookies from its raw reply's ``_original_response.msg``, which an
    in-memory body lacks, so one is put there; the header is set for a reader too.
    """
    built.headers["Set-Cookie"] = SET_COOKIE
    headers = email.message.Message()
    headers["Set-Cookie"] = SET_COOKIE
    original = type("OriginalResponse", (), {"msg": headers})()
    # The raw stream is a BytesIO, which takes the attribute requests looks for.
    built.raw._original_response = original
    return built


class TekstowoServer(BandcampServer):
    """`BandcampServer`, with a cookie set on every answer it gives."""

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """Record and answer *request* as `BandcampServer` does, then set the cookie."""
        return _set_cookie(super().__call__(request))


def search_path(artist: str, title: str) -> str:
    """The path the client's search for *artist* and *title* asks."""
    return urlsplit(lt.search_url(artist, title)).path


def state_path(tmp: Path) -> Path:
    """Where a test's cool-down file is written: a folder not yet made, under *tmp*."""
    return tmp / "state" / "tekstowo.json"


def client(tmp: Path, clock: FakeClock) -> lt.TekstowoClient:
    """The real client on *clock*, the fixed `NOW` and a state path under *tmp*."""
    return lt.TekstowoClient(
        state_path(tmp), clock=clock, sleeper=clock.sleep, now=lambda: NOW
    )
