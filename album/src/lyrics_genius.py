"""The Genius step: the chain's last, for unsynced lyrics, and only when a key resolved.

`GeniusProvider` paces its calls to a minimum interval on the monotonic clock, through
the shared `lyrics_pace.Pacer`, and retries a 403 / 429 challenge on an escalating
schedule, all inside one chain call, so it sets the watchdog's bound for that call
itself (`genius_step_deadline_s`).
`build_genius_client` is the one place the raw key is handed to the library.

Genius answers a search with one song, its own best guess, and that guess can be
another song, a live take or a remix. So the song goes through `lyrics_match`'s title,
artist and version rules as a `Hit` with no length (`song_hit`), as every other
provider's hits do, and a refused song is nothing found. Its credits are its primary
artists in order, so a joint credit matches by its first artist as NetEase's does. A
query with no artist, a track asked by its file name, is matched when any reading of
that name is (`lyrics_match.filename_readings`): the name whole or without its track
number by the title and version rules alone, and an ``Artist - Title`` reading by the
artist rule too.

The library sends through a ``requests`` session of its own, which reads every reply
whole, follows any redirect, keeps every cookie, and names the library, the operating
system and the Python release in its headers. So the client's session, the library's
private ``_session``, is replaced by a `GeniusSession`; a release that renames it fails
loudly when the client is built. That session:

- raises `HttpStatusError` on an error status, through the status hook from
  `lyrics_providers`, where lyricsgenius would raise a bare ``AssertionError`` whose
  only record of the status is its message text: a reworded message would silently
  stop every retry. The hook also sees the lyrics page request, so a challenge answered
  there with an error status is retried too, instead of reading as no lyrics. The
  error names the refusing host (`genius_host`), the keyed API or the unkeyed site,
  so a refusal records which was refused, and never the URL's path or query;
- reads each reply through `lyrics_http.capped_body` before the library reads it, so a
  reply over `lyrics_http.MAX_REPLY_BYTES`, counted as it inflates, is refused unread
  past the cap, with the same ``reply too large`` detail as every other provider's;
- follows a redirect only to https on a Genius host (`GENIUS_HOSTS`), at most
  `GENIUS_MAX_REDIRECTS` times; any other is ``redirect refused`` before it is asked;
- stores no cookie and sends none, even on a redirect's next hop;
- never sends a login from a netrc file (`lyrics_http.block_netrc`), on a redirect
  either, while the key still goes as the library's own bearer header on each API
  request, dropped by ``requests`` on a redirect to another host, and the
  environment's proxy settings still apply;
- sends ``requests``' default headers with the User-Agent the LRC providers send
  (`lyrics_http.user_agent`), never the library's own.

Nothing here prints.
"""

from __future__ import annotations

import http.cookiejar
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import lyricsgenius
import requests

from lyrics_breakers import BreakerGuard
from lyrics_http import (
    MAX_REDIRECTS,
    REDIRECT_REFUSED,
    OnRetry,
    block_netrc,
    capped_body,
    user_agent,
)
from lyrics_match import (
    Hit,
    filename_readings,
    has_artist,
    match_score,
    title_score,
)
from lyrics_model import Source, TrackQuery
from lyrics_pace import Pacer, held_until, sleep_within
from lyrics_providers import (
    REDIRECT_STATUSES,
    HttpStatusError,
    ProviderError,
    ProviderTimeout,
    hook_status_errors,
    retries_phrase,
)
from lyrics_records import Secret
from lyrics_refusal import ClassedError, Refusal, RefusalFacts, classify

# The statuses of a challenge live in the leaf module the detail reader shares;
# re-exported, so the step's callers and tests read them from here.
from lyrics_refusal_words import (
    GENIUS_CHALLENGE_STATUSES as GENIUS_CHALLENGE_STATUSES,
)
from lyrics_refusal_words import REQUEST_LABELS

#: The Genius client's own per-request timeout; its own retry is off, since ours is explicit.
GENIUS_TIMEOUT_S = 10
#: The least time between two Genius calls, on the monotonic clock.
GENIUS_MIN_INTERVAL_S = 3.0
#: The waits before each retry of a Genius call answered by a challenge; then it is an error.
GENIUS_RETRY_WAITS_S: tuple[float, ...] = (15.0, 45.0, 90.0)
#: The hosts a Genius redirect may lead to, over https on its default port alone: the
#: hosts whose requests a refusal is named by (`lyrics_refusal_words.REQUEST_LABELS`).
GENIUS_HOSTS = frozenset(REQUEST_LABELS)
#: The most redirects one Genius request follows: the shared text GET's limit.
GENIUS_MAX_REDIRECTS = MAX_REDIRECTS
#: The only scheme and port a Genius redirect may name.
_HTTPS = "https"
_HTTPS_PORT = 443


def genius_step_deadline_s(deadline_s: float) -> float:
    """The bound for one Genius call: *deadline_s* per attempt, plus the waits between.

    A call may make the first attempt and one per scheduled retry, each paced, so a
    single *deadline_s* would abandon the retries the schedule exists to make. Needs
    no key and no step, so a caller can size its own bound around a whole run.
    """
    attempts = len(GENIUS_RETRY_WAITS_S) + 1
    paced = attempts * (deadline_s + GENIUS_MIN_INTERVAL_S)
    return paced + sum(GENIUS_RETRY_WAITS_S)


class GeniusChallengeError(ClassedError):
    """Genius still answered with a challenge after the retries a call made.

    Its detail names the last challenge's status and the retries made, such as
    ``HTTP 429 after 3 retries``, or fewer when the call's deadline left room for fewer:
    a challenge a retry got past is no failure at all. Its class is blocked, whatever
    the status, and it keeps the last reply's asked wait, ``Server`` header and host.
    """

    def __init__(
        self,
        status: int,
        retries: int,
        *,
        retry_after_s: float | None = None,
        server: str | None = None,
        host: str | None = None,
    ) -> None:
        """Name the last challenge's *status* and the *retries* made before it."""
        detail = f"HTTP {status} after {retries_phrase(retries)}"
        facts = RefusalFacts(
            Refusal.BLOCKED, status, retries, retry_after_s, server, host=host
        )
        super().__init__(f"still challenged: {detail}", detail=detail, facts=facts)
        self.status = status
        self.retries = retries


class GeniusSong(Protocol):
    """What a Genius search answers with, as far as the adapter reads it."""

    @property
    def lyrics(self) -> str:
        """The song's lyrics text, as the library scraped it."""

    @property
    def title(self) -> str:
        """The song's title, as Genius lists it."""

    @property
    def artist(self) -> str:
        """The song's primary artist's name, as one string for a joint credit."""

    @property
    def primary_artists(self) -> Sequence[Mapping[str, object]]:
        """The song's primary artists' records in credit order, each with a ``name``.

        Empty when the reply lists none.
        """


def _credits(song: GeniusSong) -> tuple[str, ...]:
    """*song*'s primary artists' names in credit order, else its one artist string.

    A record whose name is missing, not text, or blank is skipped.
    """
    names = (record.get("name") for record in song.primary_artists)
    usable = tuple(name for name in names if isinstance(name, str) and name.strip())
    return usable or (song.artist,)


def song_hit(song: GeniusSong) -> Hit:
    """*song* as the matcher reads it: its title, its credits, and no length."""
    return Hit(song.title, _credits(song), None)


def _score(reading: TrackQuery, hit: Hit) -> float | None:
    """*hit*'s score against *reading*, or None if refused.

    Every rule when *reading* names an artist; else the title, version and length
    rules alone.
    """
    return (
        match_score(reading, hit) if has_artist(reading) else title_score(reading, hit)
    )


def song_matches(query: TrackQuery, song: GeniusSong) -> bool:
    """True when *song* is *query*'s track by the matcher's rules.

    A query with no artist is a file stem standing in for the tags, so it matches
    when any of the stem's readings (`lyrics_match.filename_readings`) does: the
    stem whole, or without its track number, by title and version alone; an
    ``Artist - Title`` reading by every rule, so its artist must match the song's.
    """
    hit = song_hit(song)
    readings = (query,) if has_artist(query) else filename_readings(query)
    return any(_score(reading, hit) is not None for reading in readings)


class GeniusClient(Protocol):
    """``lyricsgenius.Genius``, as far as the adapter calls it."""

    def search_song(
        self, title: str | None = ..., artist: str = ...
    ) -> GeniusSong | None:
        """The best match for *title* by *artist*, or None."""


@dataclass
class GeniusProvider:
    """The Genius step: paced to a minimum interval, retrying a challenge on a schedule."""

    client: GeniusClient
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    #: The per-request deadline, which sets this step's whole-call bound. It is the only
    #: input to that bound, so the watchdog's and the step's own give-up cannot disagree.
    deadline_s: float = field(kw_only=True)
    #: The run's breaker for this provider, shared by its steps; the fetch layer asks
    #: it before each call (`lyrics_breakers`). None leaves the steps unguarded.
    breaker: BreakerGuard | None = field(
        default=None, kw_only=True, compare=False, repr=False
    )
    #: Told of each challenge retry made, as `lyrics_http.RetryPolicy.on_retry` is of
    #: its retries (`lyrics_retried`); None counts none.
    on_retry: OnRetry | None = field(
        default=None, kw_only=True, compare=False, repr=False
    )
    #: Held over the pacing and the call together: a call the watchdog abandoned may
    #: still be running when the next begins, and the two must not overlap. A call
    #: waits for it only until its step deadline, the moment the watchdog abandons it.
    lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )
    #: The minimum interval between the starts of two calls, on this step's clock and
    #: sleeper as given at construction.
    pacer: Pacer = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Pace the calls at `GENIUS_MIN_INTERVAL_S`, on the injected clock and sleeper."""
        self.pacer = Pacer(
            GENIUS_MIN_INTERVAL_S, "Genius", clock=self.clock, sleeper=self.sleeper
        )

    def step_deadline_s(self) -> float:
        """The bound for one call, at this step's `deadline_s`."""
        return genius_step_deadline_s(self.deadline_s)

    def _paced_search(
        self, query: TrackQuery, give_up_at: float, sending: Callable[[], None]
    ) -> GeniusSong | None:
        """One paced search for *query*, under the lock so no two calls overlap.

        Past *give_up_at* the watchdog has abandoned the call, so it raises
        `ProviderTimeout` with no request made and the pacing left as it was; so does
        a pace wait that would end past it (`lyrics_pace.Pacer.take_turn`). *sending*
        is called once the turn is taken, just before the request goes out, and never
        when the turn times out.
        """
        with held_until(self.lock, self.clock, give_up_at, "Genius"):
            self.pacer.take_turn(give_up_at)
            sending()
            return self.client.search_song(query.title, query.artist)

    def _sending_hook(self, challenges: list[HttpStatusError]) -> Callable[[], None]:
        """What `_paced_search` calls as a request goes out: a retry is counted then.

        Counting at the send, not after the retry wait, leaves uncounted a retry whose
        turn timed out (the lock held by an abandoned call, or a pace wait past the
        deadline): no request went out, so nothing was retried.
        """
        on_retry = self.on_retry
        if not challenges or on_retry is None:
            return _send_quietly
        reason = classify(challenges[-1]).reason
        return lambda: on_retry(Source.GENIUS, reason)

    def __call__(self, query: TrackQuery) -> str | None:
        """Search Genius for *query*; a challenge is retried on its schedule.

        A song the matcher refuses is None, as no song is: nothing found. A retry wait
        that would end past the call's deadline is not begun, as a pace wait is not, so
        the call is never abandoned mid-wait: the step's error is then the last
        challenge (`still_challenged`). So is a retry whose turn times out (the lock
        held to the deadline, or a pace wait past it): no request went out, so the
        challenge it was for is still what the call last heard.
        """
        give_up_at = self.clock() + self.step_deadline_s()
        challenges: list[HttpStatusError] = []
        for wait in (0.0, *GENIUS_RETRY_WAITS_S):
            if challenges and not sleep_within(
                self.clock, self.sleeper, wait, give_up_at
            ):
                break
            try:
                song = self._paced_search(
                    query, give_up_at, self._sending_hook(challenges)
                )
            except HttpStatusError as error:
                if error.status not in GENIUS_CHALLENGE_STATUSES:
                    raise
                challenges.append(error)
                continue
            except ProviderTimeout:
                if not challenges:
                    raise
                break
            if song is None or not song_matches(query, song):
                return None
            return song.lyrics
        raise still_challenged(challenges)


def _send_quietly() -> None:
    """The sending hook for a first try, or a call no tally watches: nothing to count."""


def still_challenged(challenges: list[HttpStatusError]) -> ProviderError:
    """The error a call ends on after *challenges*: the last, retries counted.

    With no retry made it is the challenge itself, as `lyrics_http`'s retries raise
    an unretried failure; otherwise `GeniusChallengeError` counting the retries.
    """
    retries = len(challenges) - 1
    if not retries:
        return challenges[0]
    last = challenges[-1]
    return GeniusChallengeError(
        last.status,
        retries,
        retry_after_s=last.retry_after_s,
        server=last.server,
        host=last.host,
    )


def genius_url_allowed(url: str) -> bool:
    """Whether *url* is https on a Genius host (`GENIUS_HOSTS`), its default port, no login.

    A port that does not parse, and any user part, even an empty one, refuse it.
    """
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == _HTTPS
        and parts.hostname in GENIUS_HOSTS
        and port in (None, _HTTPS_PORT)
        and "@" not in parts.netloc
    )


def genius_host(url: str) -> str | None:
    """*url*'s hostname when it is a Genius host (`GENIUS_HOSTS`), else None.

    The hostname alone, lowercased as ``urlsplit`` reads it: never the path or the
    query, which hold the track's title and artist.
    """
    host = urlsplit(url).hostname
    return host if host in GENIUS_HOSTS else None


def refuse_foreign_redirect(
    response: requests.Response, *args: object, **kwargs: object
) -> None:
    """A response hook: raise `ProviderError` on a redirect not to a Genius https URL.

    Runs before ``requests`` follows the redirect, so a refused one is never asked. A
    redirect naming no ``Location`` is refused too: it is no reply to read.
    """
    if response.status_code not in REDIRECT_STATUSES:
        return
    location = response.headers.get("Location")
    if not location or not genius_url_allowed(urljoin(response.url, location)):
        raise ProviderError(
            f"{Source.GENIUS} redirect refused", detail=REDIRECT_REFUSED
        )


def read_capped(response: requests.Response, *args: object, **kwargs: object) -> None:
    """A response hook: read *response*'s body now, refused past the reply cap.

    lyricsgenius reads a reply whole, so the body is read here first, through
    `lyrics_http.capped_body`, counted as it inflates, and kept on the response, where
    the library then finds it already read.
    """
    # The library reads `.text` / `.json()`, which take the body from here once read.
    # A reply refused or broken mid-read is closed here, as the other providers' `with`
    # blocks close theirs: the library never sees it, so nothing else would release its
    # connection. Every failure is re-raised unchanged; only the close is added.
    try:
        response._content = capped_body(response, Source.GENIUS)
    except BaseException:
        response.close()
        raise


class GeniusSession(requests.Session):
    """The session the Genius client sends through, in place of the library's own.

    It stores no cookie, follows at most `GENIUS_MAX_REDIRECTS` redirects and only
    to a Genius https URL (`refuse_foreign_redirect`), reads each reply through the
    cap (`read_capped`), raises on an error status, and sends no netrc login. Its
    headers are ``requests``' defaults, with the tool's User-Agent: never the
    library's, which names the operating system and the Python release, nor its
    ``application`` header naming the library.
    """

    def __init__(self) -> None:
        """Set up the cookie policy, the redirect limit, the hooks and the header."""
        super().__init__()
        # requests' own defaults, with the tool's name in place of its User-Agent.
        self.headers["User-Agent"] = user_agent()
        no_domain = http.cookiejar.DefaultCookiePolicy(allowed_domains=[])
        self.cookies.set_policy(no_domain)
        # Past the limit requests raises TooManyRedirects, recorded by its type name
        # alone. Its `response.request.headers` still carry the key on a chain within
        # api.genius.com (the key is kept there, dropped only off that host), so never
        # log, print or record the exception's response or its request.
        self.max_redirects = GENIUS_MAX_REDIRECTS
        block_netrc(self)
        # In order: a refused redirect is never read, an error status is raised
        # unread, and only then is the body read through the cap.
        self.hooks["response"].append(refuse_foreign_redirect)
        hook_status_errors(self, Source.GENIUS, host_of=genius_host)
        self.hooks["response"].append(read_capped)

    def rebuild_auth(
        self, prepared_request: requests.PreparedRequest, response: requests.Response
    ) -> None:
        """A redirect hop's credentials: the key dropped off its host, nothing added.

        As ``requests`` does, the authorization header is removed when the hop leaves
        the host or scheme it was sent to; unlike it, no netrc login is added for the
        new host. The hop's ``Cookie`` header is removed too, since a redirect's
        cookie is copied into the hop's own jar, past this session's refusing policy.
        """
        headers = prepared_request.headers
        before, after = response.request.url or "", prepared_request.url or ""
        if "Authorization" in headers and self.should_strip_auth(before, after):
            del headers["Authorization"]
        headers.pop("Cookie", None)


def build_genius_client(secret: Secret) -> GeniusClient:
    """The real Genius client: the one place the raw key is handed over.

    Its own retry is off, since `GeniusProvider` retries explicitly, and section
    headers are kept as part of the text. Its session is replaced by a
    `GeniusSession`, so its error statuses raise `HttpStatusError`, where the library
    would raise a bare ``AssertionError`` for an API request and parse a lyrics page's
    error body as text; its replies are capped, its redirects held to Genius, and it
    keeps no cookie, sends no netrc login and names the tool, not the machine, in its
    User-Agent. The key still goes only as the library sends it, as each API
    request's own authorization header. Constructing it makes no request.
    """
    genius = lyricsgenius.Genius(
        secret.reveal(),
        timeout=GENIUS_TIMEOUT_S,
        retries=0,
        remove_section_headers=False,
    )
    # The library's private session: why it is replaced, not left to its own rules, is
    # in this module's docstring. Read first, so a release that renames it fails here,
    # loudly, rather than leaving the library's own session in use.
    genius._session.close()
    genius._session = GeniusSession()
    client: GeniusClient = genius
    return client
