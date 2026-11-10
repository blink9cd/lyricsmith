"""Genius's keyed search: whether Genius has a song, asked without fetching its lyrics.

The Genius step (`lyrics_genius`) asks lyricsgenius's ``search_song``, which searches
through Genius's unkeyed site and then scrapes the song's page; Cloudflare now
challenges both for a script. The keyed API, ``api.genius.com``, still answers a
script holding a key, but never with lyrics. Its search does say which songs Genius
has, and whether their lyrics are complete, so `GeniusSearcher` asks it alone:
lyricsgenius's ``API.search_songs``, one ``GET /search`` with the key as the bearer
header and `SEARCH_PER_PAGE` results. Nothing here calls the library's ``search``,
``search_all``, ``search_song``, ``song`` or ``lyrics``: each goes to the unkeyed site,
or fetches a page.

The client is the Genius step's own (`lyrics_genius.build_genius_client`), so its
session's rules hold here too: a host allowlist, refused foreign redirects, capped
reads, and an error status raised as `HttpStatusError`. Its redirects are held tighter
still (`refuse_off_api_redirect`): only to the keyed API's own host, never to Genius's
site, which a search has no reason to reach. Each search is paced at
`lyrics_genius.GENIUS_MIN_INTERVAL_S` and a 403 / 429 challenge retried on
`lyrics_genius.GENIUS_RETRY_WAITS_S`, exactly as the step does, under the same
whole-call bound (`lyrics_genius.genius_step_deadline_s`). `guarded_search` asks
Genius's breaker first (`lyrics_breakers.BreakerGuard`) and tells it how the search
ended, so a run that keeps being refused stops asking.

A reply is read by `parse_search_reply` into a `SearchPage`: its `SearchHit` records,
which the matcher reads as it reads the Genius step's songs
(`lyrics_genius.song_matches`), and how many hits the reply listed, so a reader can
tell a hit skipped as unreadable, or a page as full as one search gives, from a clear
answer that nothing more was found. A URL in a
hit is kept only when it is https on a Genius host (`lyrics_genius.genius_url_allowed`).
Nothing here prints, and the key is never in an error, a detail or a hit.
"""

from __future__ import annotations

import threading
import time
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Protocol
from urllib.parse import urljoin, urlsplit

import lyricsgenius
import requests

from lyrics_breakers import BreakerGuard
from lyrics_fetch import run_with_deadline
from lyrics_genius import (
    GENIUS_CHALLENGE_STATUSES,
    GENIUS_MIN_INTERVAL_S,
    GENIUS_RETRY_WAITS_S,
    build_genius_client,
    genius_step_deadline_s,
    genius_url_allowed,
    still_challenged,
)

# Re-exported, so the check's callers and tests read the refusal's detail from here.
from lyrics_http import REDIRECT_REFUSED as REDIRECT_REFUSED
from lyrics_pace import Pacer, held_until, sleep_within
from lyrics_providers import (
    REDIRECT_STATUSES,
    TIMEOUT_DETAIL,
    HttpStatusError,
    ProviderError,
    ProviderTimeout,
    error_detail,
)
from lyrics_records import Secret
from lyrics_refusal import classify

#: The results one search asks for: the most the keyed search gives one page.
SEARCH_PER_PAGE: Final = 5
#: The lyrics state of a song whose lyrics Genius holds whole.
COMPLETE_STATE: Final = "complete"
#: The hit type of a song: the only kind the song search is read for.
_SONG_TYPE: Final = "song"
#: The detail of a reply not of a search's shape: no mapping, or no hit list in it.
REPLY_NOT_SEARCH: Final = "reply not a search"
#: The detail of a reply listing hits of which none could be read as a song.
HITS_UNREADABLE: Final = "hits unreadable"
#: The one host a search's redirect may lead to: the keyed API's own.
KEYED_API_HOST: Final = "api.genius.com"


class GeniusSearchClient(Protocol):
    """``lyricsgenius.Genius``, as far as the keyed search calls it."""

    def search_songs(
        self, search_term: str, per_page: int | None = ..., page: int | None = ...
    ) -> Mapping[str, object]:
        """The keyed API's song search for *search_term*, its ``response`` parsed."""


def refuse_off_api_redirect(
    response: requests.Response, *args: object, **kwargs: object
) -> None:
    """A response hook: raise `ProviderError` on a redirect off the keyed API's host.

    The session's own hook already refuses a redirect off Genius; this one refuses
    Genius's site too, and a redirect naming no ``Location``, before either is read.
    """
    if response.status_code not in REDIRECT_STATUSES:
        return
    location = response.headers.get("Location")
    target = urljoin(response.url, location) if location else ""
    if not genius_url_allowed(target) or urlsplit(target).hostname != KEYED_API_HOST:
        raise ProviderError("Genius search redirect refused", detail=REDIRECT_REFUSED)


def build_search_client(secret: Secret) -> GeniusSearchClient:
    """The real client for the keyed search: the Genius step's own, held to the API.

    Its session's hooks gain `refuse_off_api_redirect`, first, so a refused redirect
    is never read. The cast names the one method used here; the object is
    ``lyricsgenius.Genius``, which the step's own protocol types by ``search_song``.
    """
    genius = typing.cast(lyricsgenius.Genius, build_genius_client(secret))
    # The library's private session, which `build_genius_client` replaced with its own.
    genius._session.hooks["response"].insert(0, refuse_off_api_redirect)
    return typing.cast(GeniusSearchClient, genius)


@dataclass(frozen=True)
class SearchHit:
    """One song a search found, as far as a check reads it.

    *artist* is ``primary_artist``'s name, empty when the reply has none;
    *primary_artists* the credit records in order, each holding a text ``name``. *url*
    is the song's page, None unless it is a Genius https URL. *complete* is whether
    Genius holds its lyrics whole, and *instrumental* whether it marks the song as
    having none. *lyrics* is always empty: the keyed search gives none, and the field
    is there so `lyrics_genius.song_matches` reads a hit as it reads a song.
    """

    title: str
    artist: str
    primary_artists: tuple[Mapping[str, object], ...]
    url: str | None
    complete: bool
    instrumental: bool
    lyrics: str = ""

    @property
    def has_lyrics(self) -> bool:
        """Whether Genius holds the song's lyrics: complete, and not instrumental."""
        return self.complete and not self.instrumental


def _text(record: Mapping[str, object], key: str) -> str | None:
    """*record*'s *key* when it is text, else None."""
    value = record.get(key)
    return value if isinstance(value, str) else None


def _credit_records(record: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """The ``primary_artists`` records holding a text name, each reduced to it."""
    listed = record.get("primary_artists")
    if not isinstance(listed, list):
        return ()
    names = (item.get("name") for item in listed if isinstance(item, Mapping))
    return tuple(
        MappingProxyType({"name": name}) for name in names if isinstance(name, str)
    )


def _primary_name(record: Mapping[str, object]) -> str:
    """``primary_artist``'s name, or empty when it is missing or not text."""
    artist = record.get("primary_artist")
    if not isinstance(artist, Mapping):
        return ""
    return _text(artist, "name") or ""


def _hit_of(item: object) -> SearchHit | None:
    """The song hit *item* holds, or None when it is not a song with a title."""
    if not isinstance(item, Mapping) or item.get("type") != _SONG_TYPE:
        return None
    record = item.get("result")
    if not isinstance(record, Mapping):
        return None
    title = _text(record, "title")
    if title is None or not title.strip():
        return None
    url = _text(record, "url")
    return SearchHit(
        title=title,
        artist=_primary_name(record),
        primary_artists=_credit_records(record),
        url=url if url is not None and genius_url_allowed(url) else None,
        complete=record.get("lyrics_state") == COMPLETE_STATE,
        instrumental=record.get("instrumental") is True,
    )


@dataclass(frozen=True)
class SearchPage:
    """One search's song hits, in Genius's order, and how many hits its reply *listed*.

    A hit the reply listed but not of a song's shape is not in *hits*, so a page with
    one *skipped* is no clear answer that a song is missing from it.
    """

    hits: tuple[SearchHit, ...]
    listed: int

    @property
    def skipped(self) -> bool:
        """Whether a hit the reply listed could not be read as a song."""
        return len(self.hits) < self.listed

    @property
    def full(self) -> bool:
        """Whether the reply listed as many hits as a search asks: more may follow."""
        return self.listed >= SEARCH_PER_PAGE


# fuzz-target
def parse_search_reply(reply: object) -> SearchPage:
    """The song hits a search *reply* holds, in Genius's order, and how many it listed.

    A hit not of a song's shape is skipped, and counted. A reply that is no mapping,
    or holds no hit list, raises `ProviderError` with `REPLY_NOT_SEARCH`, and one
    listing hits of which none is read raises it with `HITS_UNREADABLE`: reading
    either as no hits would say Genius lacks the song. Raises nothing else.
    """
    hits = reply.get("hits") if isinstance(reply, Mapping) else None
    if not isinstance(hits, list):
        raise ProviderError(
            "Genius answered a search with no hits", detail=REPLY_NOT_SEARCH
        )
    read = (_hit_of(item) for item in hits)
    page = SearchPage(tuple(hit for hit in read if hit is not None), len(hits))
    if hits and not page.hits:
        raise ProviderError(
            "Genius answered a search with no hit it could read",
            detail=HITS_UNREADABLE,
        )
    return page


@dataclass
class GeniusSearcher:
    """The keyed search, paced and retried as the Genius step is."""

    client: GeniusSearchClient
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    #: The per-request deadline, which sets the whole-call bound.
    deadline_s: float = field(kw_only=True)
    #: Held over the pacing and the request: an abandoned call may still be running.
    lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )
    #: The minimum interval between two searches' starts, on this clock.
    pacer: Pacer = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Pace the searches at `GENIUS_MIN_INTERVAL_S`, on the injected clock."""
        self.pacer = Pacer(
            GENIUS_MIN_INTERVAL_S, "Genius", clock=self.clock, sleeper=self.sleeper
        )

    def step_deadline_s(self) -> float:
        """The bound for one search, retries and their waits included."""
        return genius_step_deadline_s(self.deadline_s)

    def _paced_search(self, term: str, give_up_at: float) -> Mapping[str, object]:
        """One paced keyed search for *term*, under the lock; `ProviderTimeout` past it."""
        with held_until(self.lock, self.clock, give_up_at, "Genius"):
            self.pacer.take_turn(give_up_at)
            return self.client.search_songs(term, per_page=SEARCH_PER_PAGE)

    def search(self, term: str) -> SearchPage:
        """The page Genius's keyed search gives for *term*; a challenge is retried.

        As the Genius step does: a retry wait that would end past the call's deadline
        is not begun, and a retry whose turn times out sends nothing, so either ends
        on the last challenge (`lyrics_genius.still_challenged`). Any other error
        status raises at once.
        """
        give_up_at = self.clock() + self.step_deadline_s()
        challenges: list[HttpStatusError] = []
        for wait in (0.0, *GENIUS_RETRY_WAITS_S):
            if challenges and not sleep_within(
                self.clock, self.sleeper, wait, give_up_at
            ):
                break
            try:
                reply = self._paced_search(term, give_up_at)
            except HttpStatusError as error:
                if error.status not in GENIUS_CHALLENGE_STATUSES:
                    raise
                challenges.append(error)
                continue
            except ProviderTimeout:
                if not challenges:
                    raise
                break
            return parse_search_reply(reply)
        raise still_challenged(challenges)


@dataclass(frozen=True)
class SearchAnswer:
    """A search Genius answered: its page, perhaps of no hits."""

    page: SearchPage


@dataclass(frozen=True)
class SearchRefusal:
    """A search not answered: its short *detail*, and whether a request was *sent*.

    The detail is a status, a timeout or an error type (`lyrics_providers.error_detail`),
    or the breaker's own words when it held the search back unsent.
    """

    detail: str
    sent: bool


def guarded_search(
    searcher: GeniusSearcher, guard: BreakerGuard, term: str
) -> SearchAnswer | SearchRefusal:
    """*term* searched through *guard*: never sent while it is open, and told the end.

    The whole call runs under the watchdog at the searcher's bound, as the fetch layer
    runs the Genius step. Any `Exception` is this search's refusal, never raised: a
    check of many tracks goes on to the next. An error outside `Exception` (a
    `KeyboardInterrupt` or `SystemExit` raised by the search's own code) is no refusal:
    it is raised to the caller and tells the breaker nothing, since it says nothing of
    Genius.
    """
    shut = guard.shut()
    if shut is not None:
        return SearchRefusal(shut.detail, sent=False)
    try:
        page = run_with_deadline(
            lambda: searcher.search(term), searcher.step_deadline_s()
        )
    except ProviderTimeout as timeout:
        guard.settle(timeout, classify(timeout))
        return SearchRefusal(TIMEOUT_DETAIL, sent=True)
    # Any failure is the search's refusal, by its short detail, never its message.
    except Exception as error:
        guard.settle(error, classify(error))
        return SearchRefusal(error_detail(error), sent=True)
    guard.settle(None, None)
    return SearchAnswer(page)
