"""Tekstowo.pl's chain step, and its client: paced, cookie-free, stopped at a refusal.

Tekstowo offers its lyrics for home use, and a probe on 2026-10-21 found it
serves its pages to this tool's own User-Agent with no cookie and no challenge. Asking it
stands only on these hard rules:

- **No cookie.** Each request goes through a fresh `lyrics_http.cookie_free_session`,
  whose jar refuses every cookie a reply sets; no ``Cookie`` header is ever written.
- **No browser pose.** The tool's own User-Agent (`lyrics_http.user_agent`), nothing added.
- **No script run, no challenge worked around**: a challenge page is a stop.
- **No retry**: a refusal is an answer, not a hiccup.
- **Only the track's artist and title are sent**, in the search's path (`search_url`).
- **At most two redirects**, each to an ``https`` URL on `TEKSTOWO_HOST` with no port
  but 443 and no user info (`on_site`, through `lyrics_hosts.is_https_on`). A third is
  a stop; a redirect anywhere else fails only that track, so one odd ``Location`` never
  turns the source off for a day.
- **A 5-second pace**: at least `TEKSTOWO_MIN_INTERVAL_S` between the starts of any two
  of its lookups' requests, under one lock, so no two overlap and a call the watchdog
  abandoned ends its request before the next begins. A same-site redirect's hop (at
  most two) follows its lookup's request at once, unpaced. The lock, the pace and the
  stop are the shared cooling client's (`lyrics_cooling_client.CoolingClient`).

The stop rule: HTTP 403, 429 or 503; one redirect too many; a challenge page; or
`UNRECOGNIZED_STOP_RUN` search pages, or song pages, in a row that its reader does not
recognize. Each turns
Tekstowo off for the rest of the run and writes the 24-hour cool-down
(`lyrics_cooldown`); every later call raises `TekstowoStopped` unsent. One unrecognized
page short of that run is weak evidence of a block, so it is that track's error alone
(`TekstowoUnrecognized`), and a page of the same kind read as expected starts its count
again. The kinds are counted apart because every track's search
page comes before its song page: one shared count would be reset each track, and a
change to the song pages alone would never stop it. A page its reader recognizes is read whatever its words, so lyrics
quoting a marker stop nothing. Any other error status, a refused redirect or start URL,
or a body that is not UTF-8, is that track's error alone and leaves the count as it was.

The step (`TekstowoProvider`) is the chain's last, after Genius's: it searches, opens
the best-matching listed song (`rank_hits`, with no length, so the length rule never
fires), and takes its original lyrics only when the page's own JSON-LD names the track's
title and artist too. An instrumental's page, which has no lyrics block, is read as one
with none: nothing found. It is not asked for a query with no artist, nor after the
client stopped (`asks`); the cool-down is not read here, as the run leaves the step out
of its table while one holds. Nothing here prints.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar, TypeVar
from urllib.parse import quote

from lyrics_cooldown import Stop
from lyrics_cooling_client import CoolingClient, Refusal, status_refusal
from lyrics_hosts import is_https_on
from lyrics_http import TOO_MANY_REDIRECTS, get_text
from lyrics_match import Hit, has_artist, match_score, rank_hits
from lyrics_model import Source, TrackQuery
from lyrics_providers import HttpStatusError, ProviderError
from lyrics_tekstowo_page import (
    SearchEntry,
    SearchPage,
    SongPage,
    parse_search_page,
    parse_song_page,
)

_T = TypeVar("_T")

#: The site's address, and the one host a request or a redirect may name.
TEKSTOWO_URL = "https://www.tekstowo.pl"
TEKSTOWO_HOST = "www.tekstowo.pl"
#: The least time between any two Tekstowo requests.
TEKSTOWO_MIN_INTERVAL_S = 5.0
#: The bound for one call: two requests at least 5 s apart, each with its own timeout.
TEKSTOWO_STEP_DEADLINE_S = 45.0
#: The statuses that stop Tekstowo for the run; any other error status fails one track.
STOP_STATUSES = frozenset({403, 429, 503})
#: How many unrecognized pages of one kind in a row stop Tekstowo; fewer fail a track each.
UNRECOGNIZED_STOP_RUN = 3
#: Lowercased body markers of a challenge page.
CHALLENGE_MARKERS = ("cf-chl", "<title>just a moment")
#: The stop words a challenge and an unrecognized page record.
CHALLENGE, SEARCH_NOT_RECOGNIZED, SONG_NOT_RECOGNIZED = (
    "challenge page",
    "search page not recognized",
    "song page not recognized",
)


class TekstowoStopped(ProviderError):
    """Tekstowo refused or challenged a request, or had already; its detail is *what*."""

    def __init__(self, what: str) -> None:
        """Carry *what* as both the message's tail and the recorded detail."""
        super().__init__(f"Tekstowo stopped: {what}", detail=what)


class TekstowoUnrecognized(ProviderError):
    """A page Tekstowo's reader did not recognize, short of a stop; its detail is *what*."""

    def __init__(self, what: str) -> None:
        """Carry *what* as both the message's tail and the recorded detail."""
        super().__init__(f"Tekstowo page: {what}", detail=what)


# fuzz-target
def search_url(artist: str, title: str) -> str:
    """The plain search's address for *artist* and *title*, a `/` in either made a space."""
    term = " ".join(f"{artist} {title}".replace("/", " ").split())
    return f"{TEKSTOWO_URL}/szukaj/{quote(term, safe='')}"


def _is_tekstowo_host(host: str) -> bool:
    """True for Tekstowo's own host, exactly."""
    return host == TEKSTOWO_HOST


def on_site(url: str) -> bool:
    """True for an `https` URL on Tekstowo's own host (`lyrics_hosts.is_https_on`)."""
    return is_https_on(url, _is_tekstowo_host)


def is_challenge(text: str) -> bool:
    """Whether *text*, lowercased, holds any of `CHALLENGE_MARKERS`."""
    lowered = text.lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


def stop_reason(error: ProviderError) -> str | None:
    """The stop *error* means, or `None` when it fails only the track in hand.

    A refused redirect is the track's alone: a single odd ``Location`` is no refusal.
    """
    if isinstance(error, HttpStatusError):
        return error.detail if error.status in STOP_STATUSES else None
    return error.detail if error.detail == TOO_MANY_REDIRECTS else None


def stop_refusal(error: ProviderError) -> Refusal:
    """The class of the stop *error* means, once `stop_reason` named it one.

    A stop status is that status's own refusal; one redirect too many is a block.
    """
    if isinstance(error, HttpStatusError):
        return status_refusal(error.status)
    return Refusal.BLOCKED


@dataclass
class TekstowoClient(CoolingClient):
    """Tekstowo's paced, cookie-free GET, which stops for the run at the first refusal.

    *state_path* is the cool-down file a stop writes. The clock, sleeper, now and
    session factory are injected so tests run with no time passing and no network.
    """

    NAME: ClassVar[str] = "Tekstowo"
    MIN_INTERVAL_S: ClassVar[float] = TEKSTOWO_MIN_INTERVAL_S
    stopped_error: ClassVar[type[ProviderError]] = TekstowoStopped

    #: Per kind of page (its unrecognized word), how many in a row were unrecognized.
    unrecognized_runs: dict[str, int] = field(default_factory=dict, init=False)

    def get(
        self,
        url: str,
        give_up_at: float,
        *,
        read: Callable[[str], _T | None],
        unrecognized: str,
    ) -> _T:
        """One paced GET of *url*, read by *read*; or a stop for the rest of the run.

        In its own turn (`CoolingClient.turn`), so no two requests overlap and an
        abandoned call's request ends before the next begins. Past *give_up_at* it
        raises `ProviderTimeout` unsent. Once stopped, every call raises
        `TekstowoStopped` unsent. *read* returns `None` for a page not shaped as
        expected, which is the stop `CHALLENGE` when it holds a challenge marker, and
        otherwise the unrecognized refusal *unrecognized* (`refuse`). A page *read*
        recognizes is never searched for a marker: a song's own words may quote one.
        """
        with self.turn(give_up_at):
            try:
                with self.session_factory() as session:
                    text = get_text(session, url, Source.TEKSTOWO, allowed=on_site).text
            except ProviderError as error:
                reason = stop_reason(error)
                if reason is None:
                    raise
                raise self.refuse(stop_refusal(error), reason) from error
            page = read(text)
            if page is None:
                if is_challenge(text):
                    raise self.refuse(Refusal.BLOCKED, CHALLENGE)
                raise self.refuse(Refusal.UNRECOGNIZED, unrecognized)
            self.unrecognized_runs.pop(unrecognized, None)
            return page

    def refuse(self, refusal: Refusal, what: str) -> ProviderError:
        """The error for a *refusal* recorded as *what*: unrecognized pages are counted.

        An unrecognized page *what* names its kind, and each kind is counted apart:
        `UNRECOGNIZED_STOP_RUN` in a row stop the run, fewer are the track's error
        (`TekstowoUnrecognized`). Every other class stops at once
        (`CoolingClient.refuse`).
        """
        if refusal is not Refusal.UNRECOGNIZED:
            return super().refuse(refusal, what)
        run = self.unrecognized_runs.get(what, 0) + 1
        self.unrecognized_runs[what] = run
        if run >= UNRECOGNIZED_STOP_RUN:
            return super().refuse(refusal, what)
        return TekstowoUnrecognized(what)


def _search_page(text: str) -> SearchPage | None:
    """The search page in *text*, or `None` when it lacks the site's search form."""
    page = parse_search_page(text)
    return page if page.has_form else None


def _song_page(text: str) -> SongPage | None:
    """The song page in *text*, or `None` when it lacks both `#songText` and the marker.

    An instrumental's page is recognized, with no lyrics, so it is nothing found.
    """
    page = parse_song_page(text)
    return page if page.has_song_text or page.instrumental else None


def _entry_hit(entry: SearchEntry) -> Hit:
    """A search entry as the matcher's hit: no length, so the length rule never fires."""
    return Hit(entry.title, (entry.artist,), None)


@dataclass
class TekstowoProvider:
    """The Tekstowo step: the search, then the matched song's page, on the run's client."""

    client: TekstowoClient

    def asks(self, query: TrackQuery) -> bool:
        """False for a query with no artist, and for every query once the client stopped.

        The chain reads this to skip the step unrecorded (`lyrics_fetch.AsksSelectively`).
        """
        return has_artist(query) and self.client.available()

    def step_deadline_s(self) -> float:
        """The watchdog's bound for one call (`lyrics_fetch.OwnsDeadline`)."""
        return TEKSTOWO_STEP_DEADLINE_S

    def __call__(self, query: TrackQuery) -> str | None:
        """*query*'s original lyrics on Tekstowo, or None when no song lists and matches.

        A query with no artist sends nothing. The client raises a refusal as
        `TekstowoStopped` and a page it does not recognize as `TekstowoUnrecognized`
        until a run of them stops it; the chain records either as the track's error.
        """
        if not has_artist(query):
            return None
        give_up_at = self.client.clock() + TEKSTOWO_STEP_DEADLINE_S
        search = self.client.get(
            search_url(query.artist, query.title),
            give_up_at,
            read=_search_page,
            unrecognized=SEARCH_NOT_RECOGNIZED,
        )
        ranked = rank_hits(query, search.entries, _entry_hit)
        if not ranked:
            return None
        page = self.client.get(
            f"{TEKSTOWO_URL}{ranked[0].href}",
            give_up_at,
            read=_song_page,
            unrecognized=SONG_NOT_RECOGNIZED,
        )
        if page.name is None or page.artist is None:
            return None
        if match_score(query, Hit(page.name, (page.artist,), None)) is None:
            return None
        return page.text or None

    def provider_stop(self) -> Stop | None:
        """What stopped the client for the run, or None while it still runs.

        The table reads it through `lyrics_cooldown.table_stop`.
        """
        return self.client.stop
