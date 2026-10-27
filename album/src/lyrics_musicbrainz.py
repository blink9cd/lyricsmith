"""MusicBrainz, asked where an album is on Bandcamp: a release search, then lookups.

Bandcamp's robots file disallows its search, so the Bandcamp provider asks MusicBrainz
(`MusicBrainzClient.find_bandcamp`): a release search by the album and artist tags, up
to `MAX_RELEASE_LOOKUPS` lookups of the releases passing the album match
(`lyrics_match.album_score`), best first, then, when none links an album page, one
lookup of the best release's first credited artist for their Bandcamp site. Each
release needs its own lookup: a search answers no URL relations, and one album is often
many releases (pressings, reissues), the link on whichever an editor attached it to.

Sent: the album and artist tags in the query, and the ids the replies gave in each
lookup's path; never a file name, a path or a lyric. An id is used only as a lowercase
UUID, so a reply cannot steer a request. Each call opens a `cookie_free_session`, so no
cookie is sent, and no redirect is followed. Requests are paced to one a second
(`MUSICBRAINZ_MIN_INTERVAL_S`), counted from when the last one ended, by the shared
`lyrics_pace.Pacer` on an injected clock; a wait ending past the call's deadline raises
`ProviderTimeout` with nothing sent. A `RetryPolicy`, when given,
retries a `503` or other transient refusal.

The User-Agent is the tool's own, with the config file's contact appended in
parentheses as MusicBrainz asks (`musicbrainz_user_agent`); the contact goes nowhere
else: not a notice, an error, the manifest or the summary. A reply in another shape is
an `unexpected reply`, named for Bandcamp, whose provider this serves. Nothing prints.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

import requests

from lyrics_bandcamp_page import ALBUM_PATH
from lyrics_hosts import is_https_on
from lyrics_http import Retry, RetryPolicy, cookie_free_session, get_reply, user_agent
from lyrics_match import album_score
from lyrics_model import Source
from lyrics_pace import Pacer
from lyrics_reply import unexpected_reply

#: MusicBrainz's web service root; every request goes under it.
MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2"
#: The least time between two MusicBrainz requests: its documented limit.
MUSICBRAINZ_MIN_INTERVAL_S = 1.0
#: How many releases the search asks for.
SEARCH_LIMIT = 10
#: The most matched releases looked up for a link before the artist is asked.
MAX_RELEASE_LOOKUPS = 3
#: The release relations that name where an album is streamed or sold.
ALBUM_RELATIONS = frozenset({"free streaming", "purchase for download"})
#: The artist relation naming the artist's Bandcamp site.
ARTIST_RELATION = "bandcamp"
#: The one shape an id from a reply may take before it goes into a request's path.
_MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
#: Bandcamp's own host and its subdomains, as `urlsplit` lower-cases a host.
_BANDCAMP_HOST = re.compile(r"(?:[a-z0-9-]+\.)*bandcamp\.com")
_SOURCE = Source.BANDCAMP
#: The fixed parameters of the search, beside its query, and of a lookup.
_SEARCH_PARAMS: Mapping[str, str | int] = {"limit": SEARCH_LIMIT, "fmt": "json"}
_LOOKUP_PARAMS: Mapping[str, str | int] = {"inc": "url-rels", "fmt": "json"}


class LinkKind(StrEnum):
    """What a Bandcamp link found on MusicBrainz points at."""

    ALBUM = "album"
    ARTIST_SITE = "artist site"


@dataclass(frozen=True)
class BandcampLink:
    """An album page's URL, or an artist site's root, https and on Bandcamp."""

    kind: LinkKind
    url: str


class Miss(StrEnum):
    """Why no link was found: no release matched, or none of the matches linked one."""

    NO_ALBUM_MATCH = "no album match"
    NO_LINK = "no link"


@dataclass(frozen=True)
class Credit:
    """One credited artist of a release: its MusicBrainz id and the name as credited."""

    artist_id: str
    name: str


@dataclass(frozen=True)
class Release:
    """A release from the search: its id, title and credited artists, in credit order."""

    release_id: str
    title: str
    credits: tuple[Credit, ...]


def musicbrainz_user_agent(contact: str | None) -> str:
    """The tool's User-Agent, with *contact* in parentheses after it when one is set."""
    return user_agent() if contact is None else f"{user_agent()} ({contact})"


def _phrase(value: str) -> str:
    """*value* as a quoted Lucene phrase, its backslashes and quotes escaped."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# fuzz-target
def release_query(album: str, artist: str) -> str:
    """The search's Lucene query: the release title and the artist, each a phrase."""
    return f"release:{_phrase(album)} AND artist:{_phrase(artist)}"


def _object(value: object, what: str) -> dict[str, object]:
    """*value* as a reply object; anything else is an unexpected reply naming *what*."""
    if not isinstance(value, dict):
        raise unexpected_reply(_SOURCE, f"{what} is not an object")
    return value


def _list(reply: Mapping[str, object], name: str, *, required: bool) -> list[object]:
    """List field *name* of *reply*; absent reads as empty unless *required*."""
    value = reply.get(name, None if required else [])
    if not isinstance(value, list):
        raise unexpected_reply(_SOURCE, f"{name} is not a list")
    return value


def _text(reply: Mapping[str, object], name: str) -> str:
    """Text field *name* of *reply*."""
    value = reply.get(name)
    if not isinstance(value, str):
        raise unexpected_reply(_SOURCE, f"{name} is not text")
    return value


def _credit(value: object) -> Credit:
    """One ``artist-credit`` entry: the artist's id and the name as credited."""
    entry = _object(value, "artist-credit")
    artist = _object(entry.get("artist"), "artist")
    return Credit(_text(artist, "id"), _text(entry, "name"))


def _release(value: object) -> Release:
    """One release of a search reply; one with no ``artist-credit`` has no credit."""
    item = _object(value, "release")
    credits = tuple(map(_credit, _list(item, "artist-credit", required=False)))
    return Release(_text(item, "id"), _text(item, "title"), credits)


# fuzz-target
def parse_releases(value: object) -> tuple[Release, ...]:
    """The releases of a search reply; any other shape is an unexpected reply."""
    reply = _object(value, "reply")
    return tuple(map(_release, _list(reply, "releases", required=True)))


def _relation(value: object) -> tuple[str, str] | None:
    """One relation as ``(type, url)``, or None for one to another entity (no ``url``)."""
    item = _object(value, "relation")
    if "url" not in item:
        return None
    return _text(item, "type"), _text(_object(item["url"], "url"), "resource")


# fuzz-target
def parse_url_relations(value: object) -> tuple[tuple[str, str], ...]:
    """Each URL relation of a lookup's reply as ``(type, url)``; none when absent."""
    items = _list(_object(value, "reply"), "relations", required=False)
    return tuple(found for found in map(_relation, items) if found is not None)


def is_bandcamp_host(host: str) -> bool:
    """True for ``bandcamp.com`` or a subdomain, *host* lowercased as `urlsplit` gives it.

    The host is held to letters, digits, dots and hyphens, so no character a URL parser
    might read differently can carry the request to another host.
    """
    return _BANDCAMP_HOST.fullmatch(host) is not None


# fuzz-target
def is_bandcamp_url(url: str) -> bool:
    """True for an https URL on a Bandcamp host (`lyrics_hosts.is_https_on`)."""
    return is_https_on(url, is_bandcamp_host)


# fuzz-target
def bandcamp_https(url: str) -> str | None:
    """*url* with an ``http`` scheme made ``https``, when that is a Bandcamp URL; else None."""
    try:
        parts = urlsplit(url)
    except ValueError:  # an unbalanced IPv6 bracket
        return None
    if parts.scheme == "http":
        url = urlunsplit(parts._replace(scheme="https"))
    return url if is_bandcamp_url(url) else None


def matched_releases(
    releases: Sequence[Release], album: str, artist: str
) -> list[Release]:
    """The *releases* passing the album match, best first, ties in reply order."""
    scored = []
    for index, found in enumerate(releases):
        names = tuple(credit.name for credit in found.credits)
        score = album_score(album, artist, found.title, names)
        if score is not None:
            scored.append((-score, index, found))
    return [found for _score, _index, found in sorted(scored, key=lambda s: s[:2])]


def _album_link(relations: Sequence[tuple[str, str]]) -> str | None:
    """The first album page URL among a release's *relations*, as https, else None."""
    for kind, url in relations:
        link = bandcamp_https(url) if kind in ALBUM_RELATIONS else None
        if link is not None and urlsplit(link).path.startswith(ALBUM_PATH):
            return link
    return None


def _artist_site(relations: Sequence[tuple[str, str]]) -> str | None:
    """The root of the Bandcamp site an artist's *relations* name, else None."""
    for kind, url in relations:
        link = bandcamp_https(url) if kind == ARTIST_RELATION else None
        if link is not None:
            return f"https://{urlsplit(link).hostname}"
    return None


@dataclass
class MusicBrainzClient:
    """Finds an album's Bandcamp link on MusicBrainz, paced to one request a second.

    It holds no lock: its one caller, the Bandcamp provider, calls it under its own.
    *contact* goes into the User-Agent only (`musicbrainz_user_agent`); *retry*, when
    set, retries each request's transient refusals within the call's deadline.
    """

    contact: str | None = None
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    retry: RetryPolicy | None = None
    session_factory: Callable[[], requests.Session] = cookie_free_session
    #: One request a second, counted from when each request ends: `_get` waits before
    #: a request and marks once it is done, any retry included.
    pacer: Pacer = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Pace the requests at `MUSICBRAINZ_MIN_INTERVAL_S`, on the injected clock."""
        self.pacer = Pacer(
            MUSICBRAINZ_MIN_INTERVAL_S,
            "MusicBrainz",
            clock=self.clock,
            sleeper=self.sleeper,
        )

    def _get(
        self,
        session: requests.Session,
        path: str,
        params: Mapping[str, str | int],
        give_up_at: float,
    ) -> object:
        """GET *path* under `MUSICBRAINZ_URL` in its turn; the decoded JSON reply.

        The next turn counts from when this request ended, any retry included.
        """
        self.pacer.wait(give_up_at)
        retry = None if self.retry is None else Retry(self.retry, give_up_at)
        try:
            url = f"{MUSICBRAINZ_URL}/{path}"
            return get_reply(session, url, params, _SOURCE, retry=retry).body
        finally:
            self.pacer.mark()

    def _search(
        self, session: requests.Session, album: str, artist: str, give_up_at: float
    ) -> tuple[Release, ...]:
        """The releases MusicBrainz finds for *album* by *artist*."""
        params = {"query": release_query(album, artist), **_SEARCH_PARAMS}
        return parse_releases(self._get(session, "release", params, give_up_at))

    def _lookup(
        self, session: requests.Session, entity: str, entity_id: str, give_up_at: float
    ) -> tuple[tuple[str, str], ...]:
        """The URL relations of the *entity* (``release`` or ``artist``) *entity_id*."""
        if _MBID.fullmatch(entity_id) is None:
            raise unexpected_reply(_SOURCE, f"{entity} id is not a MusicBrainz id")
        reply = self._get(session, f"{entity}/{entity_id}", _LOOKUP_PARAMS, give_up_at)
        return parse_url_relations(reply)

    def find_bandcamp(
        self, album: str, artist: str, give_up_at: float
    ) -> BandcampLink | Miss:
        """*album* by *artist*'s Bandcamp album page or artist site, else why not.

        The search, then the best matched releases' lookups, then the best release's
        first credited artist's; nothing is asked past *give_up_at* (`ProviderTimeout`).
        """
        session = self.session_factory()
        session.headers["User-Agent"] = musicbrainz_user_agent(self.contact)
        with session:
            releases = self._search(session, album, artist, give_up_at)
            matched = matched_releases(releases, album, artist)
            if not matched:
                return Miss.NO_ALBUM_MATCH
            for found_id in [r.release_id for r in matched[:MAX_RELEASE_LOOKUPS]]:
                link = _album_link(
                    self._lookup(session, "release", found_id, give_up_at)
                )
                if link is not None:
                    return BandcampLink(LinkKind.ALBUM, link)
            # A matched release always has a credit: the artist rule refuses none.
            artist_id = matched[0].credits[0].artist_id
            site = _artist_site(self._lookup(session, "artist", artist_id, give_up_at))
        return (
            Miss.NO_LINK if site is None else BandcampLink(LinkKind.ARTIST_SITE, site)
        )
