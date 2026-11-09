"""The Bandcamp step: an album's own page, found through MusicBrainz, asked once per album.

**The route.** Bandcamp's robots file disallows its search, so the album is found on
MusicBrainz (`MusicBrainzClient.find_bandcamp`): a release linking the album page, else
the artist's Bandcamp site. An album page is read for its ``MusicAlbum`` JSON-LD
(`lyrics_bandcamp_page.parse_album_page`). An artist site's ``/music`` page is read for
its albums, the one whose title best names the track's album tag is asked. Several can
tie on that best title: a band can hold more than one album of one name, and a version
word such as "(Deluxe Edition)" does not lower the score. Then up to `MAX_TIED_ENTRIES`
of them are opened, those whose whole title, version word included, is the tag's
first, then the rest, each group in page order; the album is the first whose best
match for the asked track has lyrics, else the first holding the track at all, else
the first opened. So a tag naming an edition is answered from that edition, and a
single best entry opens one page. A ``/music`` page that redirects to an album page is
that page, kept only when its album name passes the same title rule. A track is then
picked from the album's track list by the usual per-track matcher
(`lyrics_match.rank_hits`). Both matches run: the album match decides which page is the
album at all, and the track match which of its tracks is the one asked, the length
included, so a bonus track or a reprise is not taken for another.

**What is no match.** No release matching, none linking Bandcamp, no ``/music`` entry
passing the title rule, and a page the route reaches answering 404 or 410
(`GONE_STATUSES`): MusicBrainz's links outlive the pages they name, so a gone page is
the album not being there, not Bandcamp refusing. A gone album page or ``/music`` page
is the album's no match; a gone page among tied entries is passed over for the next,
and the album is no match only when every one opened is gone. A page that redirects to
an https URL off Bandcamp (`is_elsewhere`) reads the same way (`Miss.PAGE_ELSEWHERE`):
Bandcamp lets an artist serve its pages from its own domain, which the step never
follows, so the page holds nothing it could use, and that domain was seen to redirect
straight back, a loop. Each is kept for the album as any no match is. Every other error
status stays an error, a 403 or a 5xx included, and is recorded as the refusal it is;
so does every other refused redirect: one naming no target, one too many, and one to
a plain-http URL, another port or user info.

**The cache.** One album's lookup serves all its tracks: the provider instance keeps
each album's outcome (its tracks, why none was found, or the error) for the rest of the
run, keyed by the artist as the matcher compares it and by the album tag's whole title,
version word included, so the run's later tracks and the stripped-title retry send
nothing. Tracks tagged "X" and "X (Deluxe Edition)" are each their own lookup, so each
tag picks its own edition among tied ``/music`` entries; tracks of one tag share the
album chosen with the first of them asked, which is right when the tag names one real
album: its other tracks are on the same page. It lives on the instance because one
table, so one instance, serves a whole run. A `ProviderTimeout` is not kept: it is that
call's own deadline, not the album's answer.

**The lock.** It is held over the cache's read and the lookup together, so two calls
never look up one album twice nor overlap MusicBrainz's pacing. A call waits for it only
until its step deadline; a call the watchdog abandoned still runs to the end under it
and keeps its outcome for the next.

**What is sent.** To MusicBrainz, the album and artist tags and the ids its replies
gave, with the contact in its User-Agent when one is configured. To Bandcamp, at most
two page requests per album (the /music page and one album page), or among tied
entries the /music page and up to `MAX_TIED_ENTRIES` album pages, each with the tool's
plain User-Agent, through a cookie-free session, never retried, following redirects
only to Bandcamp (`lyrics_http.get_text`). Never a file name, a path or a lyric; never
the contact. Nothing prints.
"""

from __future__ import annotations

import dataclasses
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeAlias
from urllib.parse import urljoin, urlsplit

import requests

from lyrics_bandcamp_page import (
    ALBUM_PATH,
    BandcampAlbum,
    BandcampTrack,
    MusicEntry,
    parse_album_page,
    parse_music_page,
)
from lyrics_breakers import BreakerGuard
from lyrics_hosts import is_https_on
from lyrics_http import RedirectRefused, TextReply, cookie_free_session, get_text
from lyrics_match import (
    Hit,
    album_title_score,
    artist_key,
    has_artist,
    rank_hits,
    title_key,
)
from lyrics_model import Source, TrackQuery
from lyrics_musicbrainz import (
    LinkKind,
    MusicBrainzClient,
    Miss,
    is_bandcamp_host,
    is_bandcamp_url,
)
from lyrics_pace import held_until
from lyrics_providers import ProviderError, ProviderTimeout

#: The bound for one Bandcamp call, covering the paced MusicBrainz lookups of the track
#: that starts its album's lookup (at most five, a second apart, each with its own
#: request timeout and retries) and the Bandcamp page requests.
BANDCAMP_STEP_DEADLINE_S = 60.0
#: Why an album page could not serve the album: it holds no track list.
NO_TRACK_LIST = "no track list"
#: Bandcamp's own domain: a host naming it, or a subdomain of it, is never elsewhere.
_BANDCAMP_DOMAIN = "bandcamp.com"
#: What an album's lookup ended with, kept for the rest of the run.
AlbumOutcome: TypeAlias = BandcampAlbum | Miss | Exception
#: The most /music entries tying on the best title that are opened to find the one
#: giving the asked track's lyrics: enough to reach past a self-titled run of
#: suffixed editions (a band naming every album after itself), yet a bound.
MAX_TIED_ENTRIES = 8
#: The path an artist site's album list sits under.
_MUSIC_PATH = "/music"
#: The statuses a Bandcamp page answers when it no longer exists, read as no match:
#: 404, and 410 Gone, which says the same thing more firmly (the page was there and was
#: removed). Neither is a refusal: asking again, or slower, finds nothing more.
GONE_STATUSES = frozenset({404, 410})


def _track_hit(track: BandcampTrack) -> Hit:
    """*track* as the matcher reads it, its title trimmed."""
    return dataclasses.replace(track.hit, title=track.hit.title.strip())


def _album_of(page: TextReply) -> BandcampAlbum:
    """The album *page* describes; one with no track list raises `NO_TRACK_LIST`."""
    album = parse_album_page(page.text)
    if album is None or not album.tracks:
        raise ProviderError(
            "the Bandcamp page holds no track list", detail=NO_TRACK_LIST
        )
    return album


def _named(album: BandcampAlbum, wanted: str) -> BandcampAlbum | Miss:
    """*album* when its name passes the title rule for *wanted*, else no album match."""
    name = (album.name or "").strip()
    return album if album_title_score(wanted, name) is not None else Miss.NO_ALBUM_MATCH


def _whole_title(title: str) -> str:
    """*title* folded to its letters and digits, its bracketed groups KEPT.

    The matcher's `title_key` drops a version word such as "(Deluxe Edition)", which is
    why such entries tie; this key keeps it, so it tells the bare title from its editions.
    """
    folded = unicodedata.normalize("NFKD", title).casefold()
    return "".join(c for c in folded.replace("&", " and ") if c.isalnum())


def _best_entries(
    entries: tuple[MusicEntry, ...], wanted: str
) -> tuple[MusicEntry, ...]:
    """The entries tying on the best title score for *wanted*, in opening order, capped.

    Those whose whole title is *wanted*'s come first, then the rest, each group in page
    order; at most `MAX_TIED_ENTRIES`; empty when no entry passes the title rule.
    """
    scored = [
        (score, entry)
        for entry in entries
        if (score := album_title_score(wanted, entry.title)) is not None
    ]
    best = max((score for score, _ in scored), default=None)
    tied = [entry for score, entry in scored if score == best]
    exact = _whole_title(wanted)
    # sorted is stable, so page order holds within each group.
    tied.sort(key=lambda entry: _whole_title(entry.title) != exact)
    return tuple(tied[:MAX_TIED_ENTRIES])


def _top_track(album: BandcampAlbum, query: TrackQuery) -> BandcampTrack | None:
    """*album*'s track best matching *query*'s, or None when none matches."""
    ranked = rank_hits(query, album.tracks, _track_hit)
    return ranked[0] if ranked else None


def _has_lyrics(track: BandcampTrack | None) -> bool:
    """True when *track* exists and carries lyrics that are not blank."""
    return track is not None and bool((track.lyrics or "").strip())


def _is_off_bandcamp(host: str) -> bool:
    """True for a lowercased *host* that names no Bandcamp host, however malformed.

    Wider than `is_bandcamp_host`'s refusal on purpose: a host that only reads as
    Bandcamp's, ``bandcamp.com`` or a subdomain once any trailing dot is stripped
    (one with a trailing dot, or an underscore in a label), is not off Bandcamp, so a
    redirect to it stays refused rather than reading as no match.
    """
    bare = host.rstrip(".")
    named = bare == _BANDCAMP_DOMAIN or bare.endswith(f".{_BANDCAMP_DOMAIN}")
    return not (named or is_bandcamp_host(host))


# fuzz-target
def is_elsewhere(location: str | None) -> bool:
    """True for a redirect *location* that is an https URL on a host off Bandcamp.

    Held to every rule a followed URL is but the host (`lyrics_hosts.is_https_on`), so
    a plain-http target, another port, user info, or no target at all is not elsewhere;
    the host must be off Bandcamp by `_is_off_bandcamp`, not merely refused by it.
    """
    return location is not None and is_https_on(location, _is_off_bandcamp)


def _page(session: requests.Session, url: str) -> TextReply | Miss:
    """*url* from Bandcamp as text, any redirect followed only to Bandcamp.

    `Miss.PAGE_GONE` when it is gone; `Miss.PAGE_ELSEWHERE` when a hop redirects off
    Bandcamp (`is_elsewhere`), as an artist serving its pages from its own domain
    answers: the page cannot be read here, so it holds nothing the step could use.
    Every other refused redirect is raised as the error it is.
    """
    try:
        page = get_text(
            session, url, Source.BANDCAMP, allowed=is_bandcamp_url, absent=GONE_STATUSES
        )
    except RedirectRefused as refused:
        if is_elsewhere(refused.location):
            return Miss.PAGE_ELSEWHERE
        raise
    return Miss.PAGE_GONE if page is None else page


def _album_at(session: requests.Session, url: str) -> BandcampAlbum | Miss:
    """The album the page at *url* describes, or the `_page` miss it read as."""
    page = _page(session, url)
    return page if isinstance(page, Miss) else _album_of(page)


@dataclass(frozen=True)
class _SiteAsk:
    """One artist-site lookup: the album tag, the track asked, and the call's deadline."""

    wanted: str
    query: TrackQuery
    clock: Callable[[], float]
    give_up_at: float


def _first_with_lyrics(
    session: requests.Session,
    base: str,
    entries: tuple[MusicEntry, ...],
    ask: _SiteAsk,
) -> BandcampAlbum | Miss:
    """The album of *entries* to keep, opened in order until one gives lyrics.

    The first whose best match for the asked track carries lyrics; else the first
    holding the track at all; else the first opened. A page gone or served elsewhere
    (`_page`) is passed over, and when every page asked was, the first one's miss is the
    album's. *entries* is never empty, so neither is that. The first page is
    always asked, a single best entry's alone; a further one only before the deadline,
    past which `ProviderTimeout` is raised, as a MusicBrainz request that could not
    start in time is, so nothing is kept and the next track asks again. A page's error
    is the album's, as the first page's is.
    """
    opened: list[BandcampAlbum] = []
    missed: list[Miss] = []
    holder: BandcampAlbum | None = None
    for asked, entry in enumerate(entries):
        if asked and ask.clock() >= ask.give_up_at:
            raise ProviderTimeout("the Bandcamp step's deadline passed")
        album = _album_at(session, urljoin(base, entry.path))
        if isinstance(album, Miss):
            missed.append(album)
            continue
        opened.append(album)
        top = _top_track(album, ask.query)
        if _has_lyrics(top):
            return album
        if holder is None and top is not None:
            holder = album
    if holder is not None:
        return holder
    return opened[0] if opened else missed[0]


def _from_site(
    session: requests.Session, site: str, ask: _SiteAsk
) -> BandcampAlbum | Miss:
    """The album from the artist *site*: its /music page, then the album it lists best.

    A /music page that redirects to an album page is that page, matched by its name.
    Entries tying on the best title are opened exact title first (`_best_entries`)
    until one gives the asked track's lyrics (`_first_with_lyrics`). A /music page
    gone or served elsewhere is its `_page` miss.
    """
    music = _page(session, f"{site}{_MUSIC_PATH}")
    if isinstance(music, Miss):
        return music
    if urlsplit(music.url).path.startswith(ALBUM_PATH):
        return _named(_album_of(music), ask.wanted)
    entries = _best_entries(parse_music_page(music.text), ask.wanted)
    if not entries:
        return Miss.NO_ALBUM_MATCH
    return _first_with_lyrics(session, music.url, entries, ask)


def _slot(query: TrackQuery) -> tuple[str, str, str]:
    """The key *query*'s album is kept under.

    The whole title tells an edition tag from the bare one; the matcher's key still
    tells apart tags with no letter or digit, whose whole titles are empty.
    """
    album, artist = (query.album or "").strip(), query.artist.strip()
    return (title_key(album), _whole_title(album), artist_key(artist))


@dataclass
class BandcampProvider:
    """The Bandcamp step: each album looked up once, under a lock, its tracks kept."""

    musicbrainz: MusicBrainzClient
    clock: Callable[[], float] = time.monotonic
    page_session: Callable[[], requests.Session] = cookie_free_session
    #: The run's breaker for this provider, shared by its steps; the fetch layer asks
    #: it before each call (`lyrics_breakers`). None leaves the steps unguarded.
    breaker: BreakerGuard | None = field(
        default=None, kw_only=True, compare=False, repr=False
    )
    _albums: dict[tuple[str, str, str], AlbumOutcome] = field(
        default_factory=dict, init=False
    )
    #: Held over the cache and the lookup together, so no two calls overlap; a call
    #: waits for it only until its step deadline, the moment the watchdog abandons it.
    lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def asks(self, query: TrackQuery) -> bool:
        """True for a track with an album tag and an artist; any other sends nothing."""
        return bool((query.album or "").strip()) and has_artist(query)

    def step_deadline_s(self) -> float:
        """The bound for one call, `BANDCAMP_STEP_DEADLINE_S`."""
        return BANDCAMP_STEP_DEADLINE_S

    def _look_up(
        self, query: TrackQuery, album: str, artist: str, give_up_at: float
    ) -> BandcampAlbum | Miss:
        """*album* by *artist* on Bandcamp: its link from MusicBrainz, then its page.

        *query* is the track asked first, which picks among same-titled site entries.
        """
        found = self.musicbrainz.find_bandcamp(album, artist, give_up_at)
        if isinstance(found, Miss):
            return found
        with self.page_session() as session:
            if found.kind is LinkKind.ALBUM:
                return _album_at(session, found.url)
            ask = _SiteAsk(album, query, self.clock, give_up_at)
            return _from_site(session, found.url, ask)

    def kept(self, query: TrackQuery) -> bool:
        """Whether a call for *query* would read an outcome an earlier lookup kept.

        Read under the lock, never waiting for it: a lookup under way holds it, and
        until it ends nothing is kept for its album. The fetch layer reads this so a
        kept answer or failure, which sends nothing, tells the breaker nothing
        (`lyrics_provider_kinds.KeepsAnswers`).
        """
        # An accepted race: a `kept` finding the lock held reads the call as fresh, so a
        # call made while another lookup is under way tells the breaker its outcome even
        # if that outcome turns out kept. A lookup that ended in an error can then count
        # twice, and a kept answer read that way can close a half-open breaker. Waiting
        # for the lock instead would stall the chain behind a lookup it may not need.
        if not self.asks(query) or not self.lock.acquire(blocking=False):
            return False
        try:
            return _slot(query) in self._albums
        finally:
            self.lock.release()

    def _outcome(self, query: TrackQuery, give_up_at: float) -> AlbumOutcome:
        """The album's kept outcome, looked up under the lock the first time it is asked."""
        with held_until(self.lock, self.clock, give_up_at, "Bandcamp"):
            album, artist = (query.album or "").strip(), query.artist.strip()
            slot = _slot(query)
            if slot not in self._albums:
                try:
                    self._albums[slot] = self._look_up(query, album, artist, give_up_at)
                except ProviderTimeout:
                    raise
                # Kept, not swallowed: it is raised for this track and every later one
                # of the album, which would only meet the same failure again.
                except Exception as error:
                    self._albums[slot] = error
            return self._albums[slot]

    def __call__(self, query: TrackQuery) -> str | None:
        """The lyrics Bandcamp gives *query*'s track, or None; the album's error raised."""
        if not self.asks(query):
            return None
        outcome = self._outcome(query, self.clock() + self.step_deadline_s())
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, Miss):
            return None
        top = _top_track(outcome, query)
        lyrics = top.lyrics if top is not None else None
        return lyrics.strip() if lyrics is not None else None
