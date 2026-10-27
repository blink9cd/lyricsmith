"""The NetEase step: its search endpoint, our matcher's choice of hit, then that song's lyric.

NetEase (music.163.com) publishes no API documentation. The two endpoints and their
parameters are the ones long used by open-source lyrics clients: the search
(``/api/search/get``, ten song hits for "title artist") and the lyric
(``/api/song/lyric``, by song id). Both answer anonymous requests, so neither carries a
``Referer`` or a cookie: each call's session is `lyrics_http.cookie_free_session`, whose
jar refuses any cookie the search sets, so the lyric request cannot send one back. A
probe on 2026-10-23 got the same lyric with and without the search's ``Referer`` and
cookie.

Every reply's HTTP status is read by the status hook, and an OK body whose ``code`` is
not 200 is NetEase refusing the request, so both are errors, never "nothing found": a
throttled or changed endpoint then shows in the run's provider tally as asked and never
won, instead of as songs NetEase does not hold. A refusal's detail keeps its code
(``code -462``), as an error status's keeps the status. A body in any other shape than
the one below is a `ProviderError` naming NetEase.

NetEase answers in one LRC text, timed or not, so both steps get the same text and the
chain decides: a synced step accepts it only with a timed line. A song NetEase marks as
having no lyric (``nolyric``) or not yet collected (``uncollected``) is nothing found.

The table's step retries a transient refusal of either request (`lyrics_http.Retry`),
both sharing one call's deadline. A body's refusal code is not retried: risk control
answers the same way next time.

Nothing here prints.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import requests

from lyrics_answer import LrcAnswer
from lyrics_breakers import BreakerGuard
from lyrics_http import PROVIDER_DEADLINE_S, RetryPolicy, cookie_free_session
from lyrics_http import get_reply
from lyrics_match import Hit, has_artist, rank_hits
from lyrics_model import Source, TrackQuery
from lyrics_providers import ProviderError
from lyrics_reply import flag_field, number_field, unexpected_reply

#: The anonymous search: ``/api/search/pc`` refuses a request without a browser
#: cookie (code -462, risk control), while this one answers the same songs, cookie-free.
NETEASE_SEARCH_URL = "https://music.163.com/api/search/get"
NETEASE_LYRIC_URL = "https://music.163.com/api/song/lyric"
#: The search's fixed parameters: ten hits, of type 1 (songs), from the first.
SEARCH_PARAMS: Mapping[str, int] = {"limit": 10, "type": 1, "offset": 0}
#: The lyric's version parameter: 1 asks for the current original-language LRC.
LYRIC_VERSION = 1
#: The ``code`` an accepted request's body carries.
CODE_OK = 200
#: The most digits a refusing code is quoted with. NetEase's own codes are a few
#: digits; a reply can carry thousands, which would become the manifest's detail.
MAX_CODE_DIGITS = 9
#: The detail for a refusing code past `MAX_CODE_DIGITS`.
CODE_OUT_OF_RANGE = "code out of range"
#: Milliseconds in a second: NetEase gives a song's length in milliseconds.
_MS_PER_S = 1000


@dataclass(frozen=True)
class NeteaseSong:
    """One search hit: the song's id, and what the matcher reads."""

    song_id: int
    hit: Hit


def _unexpected(what: str) -> ProviderError:
    """The error for a NetEase reply whose *what* is not in the known shape."""
    return unexpected_reply(Source.NETEASE, what)


def _object(value: object, what: str) -> Mapping[str, object]:
    """*value* as a JSON object; raise naming *what* otherwise."""
    if not isinstance(value, dict):
        raise _unexpected(f"{what} is not an object")
    return value


def _code_detail(code: int) -> str:
    """A refusing *code*'s detail: the code, or a fixed one past `MAX_CODE_DIGITS`."""
    if abs(code) >= 10**MAX_CODE_DIGITS:
        return CODE_OUT_OF_RANGE
    return f"code {code}"


def _accepted(reply: Mapping[str, object]) -> Mapping[str, object]:
    """*reply* itself, after its ``code`` shows NetEase accepted the request."""
    code = reply.get("code", CODE_OK)
    if isinstance(code, bool) or not isinstance(code, int):
        raise _unexpected("code is not a number")
    if code != CODE_OK:
        detail = _code_detail(code)
        message = f"{Source.NETEASE} refused the request with {detail}"
        raise ProviderError(message, detail=detail)
    return reply


def _artists(song: Mapping[str, object]) -> tuple[str, ...]:
    """The song's credited artist names, in credit order."""
    artists = song.get("artists")
    if not isinstance(artists, list):
        raise _unexpected("artists is not a list")
    names: list[str] = []
    for artist in artists:
        name = _object(artist, "an artist").get("name")
        if not isinstance(name, str):
            raise _unexpected("an artist's name is not text")
        names.append(name)
    return tuple(names)


def _length(song: Mapping[str, object]) -> float | None:
    """The song's length in seconds, or None when absent or null."""
    ms = number_field(song, "duration", Source.NETEASE)
    return None if ms is None else ms / _MS_PER_S


def _song(value: object) -> NeteaseSong:
    """One search hit; raise `ProviderError` for any other shape."""
    song = _object(value, "a song")
    song_id, name = song.get("id"), song.get("name")
    if isinstance(song_id, bool) or not isinstance(song_id, int):
        raise _unexpected("a song's id is not a whole number")
    if not isinstance(name, str):
        raise _unexpected("a song's name is not text")
    return NeteaseSong(song_id, Hit(name, _artists(song), _length(song)))


# fuzz-target
def parse_songs(value: object) -> tuple[NeteaseSong, ...]:
    """The search hits *value* holds, none for no result; `ProviderError` otherwise."""
    reply = _accepted(_object(value, "the search answer"))
    result = reply.get("result")
    if result is None:
        return ()
    songs = _object(result, "result").get("songs")
    if songs is None:
        return ()
    if not isinstance(songs, list):
        raise _unexpected("songs is not a list")
    return tuple(_song(song) for song in songs)


# fuzz-target
def parse_lyric(value: object) -> str | None:
    """The LRC text *value* holds, None for no lyric; `ProviderError` otherwise."""
    reply = _accepted(_object(value, "the lyric answer"))
    if flag_field(reply, "nolyric", Source.NETEASE) or flag_field(
        reply, "uncollected", Source.NETEASE
    ):
        return None
    lrc = reply.get("lrc")
    if lrc is None:
        return None
    text = _object(lrc, "lrc").get("lyric")
    if text is not None and not isinstance(text, str):
        raise _unexpected("the lyric is not text")
    return text or None


@dataclass(frozen=True)
class NeteaseProvider:
    """The NetEase step, of either kind: the search, the matcher, then the lyric.

    With a *retry* policy, each request retries a transient refusal within
    *deadline_s* of the call's start, the watchdog's bound for it; with none, it is
    asked once.
    """

    session_factory: Callable[[], requests.Session] = cookie_free_session
    retry: RetryPolicy | None = None
    deadline_s: float = PROVIDER_DEADLINE_S
    #: The run's breaker for this provider, shared by its steps; the fetch layer asks
    #: it before each call (`lyrics_breakers`). None leaves the steps unguarded.
    breaker: BreakerGuard | None = field(
        default=None, kw_only=True, compare=False, repr=False
    )

    def asks(self, query: TrackQuery) -> bool:
        """False for a query with no artist, which the matcher cannot confirm a hit for.

        Such a query is a file stem standing in for a missing artist or title tag; the
        chain reads this to skip the step unrecorded
        (`lyrics_provider_kinds.AsksSelectively`).
        """
        return has_artist(query)

    def answer_all_kinds(self, query: TrackQuery) -> LrcAnswer:
        """NetEase's one text for *query*, the same for both kinds of step."""
        return LrcAnswer.same(self(query))

    def __call__(self, query: TrackQuery) -> str | None:
        """NetEase's LRC text for *query*, or None when no hit matches or it has none.

        A query it does not ask (`asks`) sends nothing and is None. A fresh cookie-free
        session per call, so a call the watchdog abandoned never shares one with the
        next, and the lyric request never sends back a cookie the search set.
        """
        if not self.asks(query):
            return None
        retry = None if self.retry is None else self.retry.within(self.deadline_s)
        with self.session_factory() as session:
            term = f"{query.title} {query.artist}"
            params: dict[str, str | int] = {**SEARCH_PARAMS, "s": term}
            searched = get_reply(
                session, NETEASE_SEARCH_URL, params, Source.NETEASE, retry=retry
            )
            ranked = rank_hits(query, parse_songs(searched.body), lambda song: song.hit)
            if not ranked:
                return None
            lyric_params = {"id": ranked[0].song_id, "lv": LYRIC_VERSION}
            reply = get_reply(
                session, NETEASE_LYRIC_URL, lyric_params, Source.NETEASE, retry=retry
            )
            return parse_lyric(reply.body)
