"""MusicBrainz and Bandcamp from memory, and invented MusicBrainz reply builders.

`BandcampServer` answers by host and path through `http_fakes.transport`, so the real
client, its session and its status hook all run and no socket is reached. It records
each request, and the fake clock's time at it when given one, so a suite can read the
pacing. The builders shape a release search and a lookup's URL relations as MusicBrainz
does; every id, title and name is invented.

Not discovered as a suite (no ``test_`` prefix).
"""

from __future__ import annotations

import email.message
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import requests

import http_fakes

#: Invented MusicBrainz ids, lowercase UUIDs as MusicBrainz gives them.
RELEASE_1 = "00000000-0000-4000-8000-000000000001"
RELEASE_2 = "00000000-0000-4000-8000-000000000002"
RELEASE_3 = "00000000-0000-4000-8000-000000000003"
RELEASE_4 = "00000000-0000-4000-8000-000000000004"
RELEASE_5 = "00000000-0000-4000-8000-000000000005"
ARTIST_1 = "00000000-0000-4000-8000-000000000009"
#: MusicBrainz's host, and the paths its search and lookups ask.
MB_HOST = "musicbrainz.org"
SEARCH_PATH = "/ws/2/release"


def release_path(release_id: str) -> str:
    """The path of a release lookup."""
    return f"{SEARCH_PATH}/{release_id}"


def artist_path(artist_id: str) -> str:
    """The path of an artist lookup."""
    return f"/ws/2/artist/{artist_id}"


@dataclass(frozen=True)
class Redirect:
    """A redirect answer: *location* with *status*, setting *set_cookie* when given."""

    location: str
    status: int = 301
    set_cookie: str | None = None


def with_cookie(built: requests.Response, set_cookie: str) -> requests.Response:
    """*built*, carrying *set_cookie* where ``requests`` reads a reply's cookies.

    A session stores cookies from its raw reply's ``_original_response.msg``, which
    an in-memory body has no such thing as, so one is put there.
    """
    headers = email.message.Message()
    headers["Set-Cookie"] = set_cookie
    original = type("OriginalResponse", (), {"msg": headers})()
    # The raw stream is a BytesIO, which takes the attribute requests looks for.
    built.raw._original_response = original
    return built


class BandcampServer:
    """MusicBrainz and Bandcamp from memory, by host and path; 404 for anything unrouted.

    A route's answer is an int (that status alone), a `Redirect`, a str (an HTML page),
    bytes (that raw body) or any other value (a JSON body). *clock*, when given, stamps
    each request's time.
    """

    def __init__(
        self,
        routes: Mapping[tuple[str, str], object],
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Answer *routes*, stamping each request with *clock* when given one."""
        self.routes = dict(routes)
        self.clock = clock
        #: Every request sent, in order, and the fake time of each when stamped.
        self.requests: list[requests.PreparedRequest] = []
        self.times: list[float] = []

    def paths(self) -> list[str]:
        """The path of every request made, in order."""
        return [urlsplit(r.url or "").path for r in self.requests]

    def params(self, index: int) -> dict[str, list[str]]:
        """The query parameters of request *index*."""
        return parse_qs(urlsplit(self.requests[index].url or "").query)

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """Record *request*, then answer it by its host and path."""
        self.requests.append(request)
        if self.clock is not None:
            self.times.append(self.clock())
        parts = urlsplit(request.url or "")
        answer = self.routes.get((parts.hostname or "", parts.path), 404)
        if isinstance(answer, int):
            return http_fakes.response(request, answer)
        if isinstance(answer, Redirect):
            built = http_fakes.response(
                request, answer.status, headers={"Location": answer.location}
            )
            if answer.set_cookie is None:
                return built
            return with_cookie(built, answer.set_cookie)
        if isinstance(answer, str):
            return http_fakes.response(request, 200, answer.encode())
        if isinstance(answer, bytes):
            return http_fakes.response(request, 200, answer)
        return http_fakes.response(request, 200, http_fakes.json_body(answer))


def release(
    release_id: str, title: str, artist: str, artist_id: str = ARTIST_1
) -> dict[str, object]:
    """One release of a search reply, credited to *artist* alone."""
    credit = {"name": artist, "joinphrase": "", "artist": {"id": artist_id}}
    return {"id": release_id, "title": title, "artist-credit": [credit]}


def search_reply(*releases: Mapping[str, object]) -> dict[str, object]:
    """A release search's reply holding *releases*, in order."""
    return {"count": len(releases), "offset": 0, "releases": list(releases)}


def relations_reply(*relations: tuple[str, str]) -> dict[str, object]:
    """A lookup's reply with one URL relation per ``(type, url)``."""
    return {
        "relations": [
            {"type": kind, "target-type": "url", "url": {"resource": url}}
            for kind, url in relations
        ]
    }
