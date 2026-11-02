"""The Bandcamp step's artist-site route when several /music entries tie on the title.

A band can hold several albums of one title, and version words ("(Deluxe Edition)") do
not lower the title score. When entries tie on the best score, at most
`lyrics_bandcamp.MAX_TIED_ENTRIES` of them are opened, the whole-title match first and
then page order; the first whose best track match has lyrics is the album, else the
first holding the track, else the first opened. No test reaches the network:
`bandcamp_fakes.BandcampServer` answers from memory on a fake clock. Every id, title,
name, host and lyric is invented.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import bandcamp_fakes as bf  # noqa: E402 - import follows the sys.path bootstrap above
import bandcamp_pages as bp  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_bandcamp as lb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_musicbrainz as lmb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above

ALBUM = "The Lantern Years"
ARTIST = "Invented Band"
HOST = "invented-band.bandcamp.com"
SITE = f"https://{HOST}"
#: Four albums sharing one title, each its own page.
PATHS = tuple(f"/album/the-lantern-years-{n}" for n in range(1, 5))
OTHER_PATH = "/album/the-lantern-year"
SONGS = ("Harbor Bell", "The Ford", "Quiet Hour", "Salt Road")
FORD = "the ford runs cold\nunder the lantern"
BELL = "the harbor bell\nrings twice"
SEARCH = (
    (bf.MB_HOST, bf.SEARCH_PATH),
    bf.search_reply(bf.release(bf.RELEASE_1, ALBUM, ARTIST)),
)
UNLINKED = ((bf.MB_HOST, bf.release_path(bf.RELEASE_1)), bf.relations_reply())
ARTIST_SITE = (
    (bf.MB_HOST, bf.artist_path(bf.ARTIST_1)),
    bf.relations_reply(("bandcamp", f"{SITE}/")),
)


def query(title: str) -> lm.TrackQuery:
    """A track of `ALBUM` by `ARTIST`, titled *title*, its length unknown."""
    return lm.TrackQuery(ARTIST, title, album=ALBUM)


def album_with(song: str) -> str:
    """An album page titled `ALBUM` holding *song* alone, its lyrics `FORD` or `BELL`."""
    lyrics = FORD if song == "The Ford" else BELL
    return bp.album_page([bp.track(song, lyrics=lyrics)], name=ALBUM)


def tied_site(
    count: int, extra: Mapping[tuple[str, str], object] | None = None
) -> dict[tuple[str, str], object]:
    """The artist route: a /music page listing *count* same-titled albums, in order.

    The n-th album holds the n-th of `SONGS` alone, so "The Ford" is on the second.
    """
    entries = [(path, ALBUM) for path in PATHS[:count]]
    table: dict[tuple[str, str], object] = {
        **dict((SEARCH, UNLINKED, ARTIST_SITE)),
        (HOST, "/music"): bp.music_page(entries),
    }
    for path, song in zip(PATHS[:count], SONGS):
        table[(HOST, path)] = album_with(song)
    return {**table, **dict(extra or {})}


class ClockJumpServer(bf.BandcampServer):
    """A server moving its clock when *path* is asked: past any deadline, or to *to*."""

    def __init__(
        self,
        table: Mapping[tuple[str, str], object],
        clock: genius_fakes.FakeClock,
        path: str,
        to: float | None = None,
    ) -> None:
        """Answer *table* on *clock*, which jumps when *path* is requested."""
        super().__init__(table, clock)
        self.clock_ = clock
        self.path = path
        self.to = to

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """Answer as routed; a request for `path` first moves the clock on."""
        if request.url == f"{SITE}{self.path}":
            if self.to is None:
                self.clock_.now += lb.BANDCAMP_STEP_DEADLINE_S * 2
            else:
                self.clock_.now = self.to
        return super().__call__(request)


class SiteCase(fixtures.IsolatedCase):
    """A provider on a fake clock, with readers of what it sent and kept."""

    def setUp(self) -> None:
        """A fresh fake clock and provider for each test."""
        super().setUp()
        self.clock = genius_fakes.FakeClock()
        client = lmb.MusicBrainzClient(None, clock=self.clock, sleeper=self.clock.sleep)
        self.provider = lb.BandcampProvider(client, clock=self.clock)

    def ask(
        self, server: bf.BandcampServer, *queries: lm.TrackQuery
    ) -> list[str | None]:
        """The provider's answer for each of *queries* in turn, through *server*."""
        with http_fakes.transport(server):
            return [self.provider(q) for q in queries]

    def bandcamp_paths(self, server: bf.BandcampServer) -> list[str]:
        """The paths of the requests sent to Bandcamp, in order."""
        return [p for p in server.paths() if not p.startswith("/ws/2/")]

    def kept_titles(self) -> list[str]:
        """The track titles of the one album outcome the provider kept."""
        (outcome,) = self.provider._albums.values()
        assert isinstance(outcome, lb.BandcampAlbum)
        return [t.hit.title for t in outcome.tracks]


class TiedEntriesTest(SiteCase):
    """Same-titled /music entries are opened in page order until one holds the track."""

    def test_the_track_on_the_second_of_three_is_found(self) -> None:
        server = bf.BandcampServer(tied_site(3), clock=self.clock)
        self.assertEqual(self.ask(server, query("The Ford")), [FORD])
        self.assertEqual(self.bandcamp_paths(server), ["/music", *PATHS[:2]])

    def test_the_track_on_none_keeps_the_first_album(self) -> None:
        server = bf.BandcampServer(tied_site(3), clock=self.clock)
        # The album is chosen with the first track asked; later ones read the cache.
        asked = self.ask(server, query("Not There"), query("Harbor Bell"))
        self.assertEqual(asked, [None, BELL])
        self.assertEqual(self.bandcamp_paths(server), ["/music", *PATHS[:3]])
        self.assertEqual(self.kept_titles(), ["Harbor Bell"])

    def test_the_track_on_the_fourth_of_four_is_found(self) -> None:
        server = bf.BandcampServer(tied_site(4), clock=self.clock)
        self.assertEqual(self.ask(server, query("Salt Road")), [BELL])
        self.assertEqual(self.bandcamp_paths(server), ["/music", *PATHS])

    def test_a_single_best_entry_opens_one_album_page(self) -> None:
        # The near-miss title passes the title rule but scores below the exact one.
        table = tied_site(1, {(HOST, OTHER_PATH): album_with("The Ford")})
        table[(HOST, "/music")] = bp.music_page(
            [(PATHS[0], ALBUM), (OTHER_PATH, "The Lantern Year")]
        )
        server = bf.BandcampServer(table, clock=self.clock)
        self.assertEqual(self.ask(server, query("The Ford")), [None])
        self.assertEqual(self.bandcamp_paths(server), ["/music", PATHS[0]])

    def test_no_further_page_is_asked_past_the_deadline(self) -> None:
        server = ClockJumpServer(tied_site(3), self.clock, PATHS[0])
        with http_fakes.transport(server), self.assertRaises(lp.ProviderTimeout):
            self.provider(query("The Ford"))
        self.assertEqual(self.bandcamp_paths(server), ["/music", PATHS[0]])
        # A timeout is the call's own, so nothing is kept and the next track asks again.
        self.assertEqual(self.provider._albums, {})

    def test_a_gone_first_page_past_the_deadline_asks_no_further_page(self) -> None:
        # The deadline counts pages asked, not opened: a gone first page still spends it.
        server = ClockJumpServer(
            tied_site(3, {(HOST, PATHS[0]): 404}), self.clock, PATHS[0]
        )
        with http_fakes.transport(server), self.assertRaises(lp.ProviderTimeout):
            self.provider(query("The Ford"))
        self.assertEqual(self.bandcamp_paths(server), ["/music", PATHS[0]])
        self.assertEqual(self.provider._albums, {})

    def test_a_clock_exactly_at_the_deadline_asks_no_further_page(self) -> None:
        # The call's deadline is its start plus the step bound; the jump lands on it.
        give_up_at = self.clock() + lb.BANDCAMP_STEP_DEADLINE_S
        server = ClockJumpServer(tied_site(3), self.clock, PATHS[0], to=give_up_at)
        with http_fakes.transport(server), self.assertRaises(lp.ProviderTimeout):
            self.provider(query("The Ford"))
        self.assertEqual(self.bandcamp_paths(server), ["/music", PATHS[0]])

    def assert_second_page_error_kept(self, answer: object) -> None:
        """A second tied page answering *answer* fails the album; no third is asked."""
        # The first holds the track without lyrics, so the second must be opened.
        table = tied_site(3, {(HOST, PATHS[1]): answer})
        table[(HOST, PATHS[0])] = bp.album_page(
            [bp.track("The Ford", lyrics=None)], name=ALBUM
        )
        server = bf.BandcampServer(table, clock=self.clock)
        with (
            http_fakes.transport(server),
            self.assertRaises(lp.ProviderError) as raised,
        ):
            self.provider(query("The Ford"))
        self.assertNotIsInstance(raised.exception, lp.ProviderTimeout)
        self.assertEqual(self.bandcamp_paths(server), ["/music", *PATHS[:2]])
        # Kept, so the album's next track meets the same error with no request.
        (kept,) = self.provider._albums.values()
        self.assertIs(kept, raised.exception)

    def test_a_later_tied_page_forbidden_is_the_albums_error(self) -> None:
        self.assert_second_page_error_kept(403)

    def test_a_tied_page_gone_is_passed_over_for_the_next(self) -> None:
        # The first holds the track without lyrics, the second is gone: the third is
        # still opened, and the first is kept as the album holding the track.
        table = tied_site(3, {(HOST, PATHS[1]): 404})
        table[(HOST, PATHS[0])] = bp.album_page(
            [bp.track("The Ford", lyrics=None)], name=ALBUM
        )
        server = bf.BandcampServer(table, clock=self.clock)
        self.assertEqual(self.ask(server, query("The Ford")), [None])
        self.assertEqual(self.bandcamp_paths(server), ["/music", *PATHS[:3]])
        self.assertEqual(self.kept_titles(), ["The Ford"])

    def test_every_tied_page_gone_is_no_match(self) -> None:
        gone = {(HOST, path): 410 for path in PATHS[:2]}
        server = bf.BandcampServer(tied_site(2, gone), clock=self.clock)
        self.assertEqual(self.ask(server, query("The Ford")), [None])
        self.assertEqual(self.bandcamp_paths(server), ["/music", *PATHS[:2]])
        self.assertEqual(list(self.provider._albums.values()), [lmb.Miss.PAGE_GONE])

    def test_a_later_tied_page_without_tracks_is_the_albums_error(self) -> None:
        self.assert_second_page_error_kept(bp.album_page([], name=ALBUM))


#: The same title with a version word, each its own album page; "" is the bare title.
FOURTH, LIVE, JUBILEE, COVERS = (
    "(Fourth Pressing)",
    "(Live at the Pier)",
    "(Jubilee Edition)",
    "(Covers)",
)
THIRD, SECOND, DELUXE, BARE, EP = (
    "(Third Pressing)",
    "(Second Pressing)",
    "(Deluxe Edition)",
    "",
    "(EP)",
)
#: A self-titled run told apart only by its suffixes, none of them the bare title.
VOLUMES = tuple(
    f"(Volume {n})"
    for n in ("One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight")
)
DELUXE_FORD = "the deluxe ford\nruns colder"
COVERS_FORD = "a borrowed ford\nsung by others"
NO_TRACK = ""


def edition(suffix: str) -> tuple[str, str]:
    """The /music (path, title) of `ALBUM`'s edition named by *suffix*."""
    slug = "".join(c for c in suffix.lower() if c.isalnum()) or "bare"
    return f"/album/the-lantern-years-{slug}", f"{ALBUM} {suffix}".strip()


def quiet(suffix: str) -> str:
    """The lyrics of "Quiet Hour" on *suffix*'s edition, so each edition's differ."""
    return f"a quiet hour\nby the lantern {suffix}".strip()


def edition_page(suffix: str, ford: str | None) -> str:
    """*suffix*'s album page: "The Ford" with lyrics *ford* (`NO_TRACK` omits it)."""
    tracks = [bp.track("Quiet Hour", lyrics=quiet(suffix))]
    if ford != NO_TRACK:
        tracks.insert(0, bp.track("The Ford", lyrics=ford))
    return bp.album_page(tracks, name=edition(suffix)[1])


def edition_site(pages: Mapping[str, str | None]) -> dict[tuple[str, str], object]:
    """The artist route whose /music page lists *pages*' editions, in their order."""
    table: dict[tuple[str, str], object] = {
        **dict((SEARCH, UNLINKED, ARTIST_SITE)),
        (HOST, "/music"): bp.music_page([edition(s) for s in pages]),
    }
    for suffix, ford in pages.items():
        table[(HOST, edition(suffix)[0])] = edition_page(suffix, ford)
    return table


def opened(*suffixes: str) -> list[str]:
    """The Bandcamp paths sent when the /music page and *suffixes*' editions are read."""
    return ["/music", *(edition(s)[0] for s in suffixes)]


#: Many self-titled editions tying on the title: version-worded ones first, a holder of
#: the track without its lyrics among the first three, the bare title deep in the page.
PROBED = {
    FOURTH: NO_TRACK,
    LIVE: NO_TRACK,
    JUBILEE: None,
    COVERS: COVERS_FORD,
    THIRD: NO_TRACK,
    SECOND: NO_TRACK,
    DELUXE: DELUXE_FORD,
    BARE: FORD,
    EP: NO_TRACK,
}


class ExactTitleFirstTest(SiteCase):
    """The tie whose whole title is the tag's is opened first; lyrics beat a bare hold."""

    def test_the_bare_title_answers_from_one_album_page(self) -> None:
        server = bf.BandcampServer(edition_site(PROBED), clock=self.clock)
        self.assertEqual(self.ask(server, query("The Ford")), [FORD])
        self.assertEqual(self.bandcamp_paths(server), opened(BARE))

    def test_an_edition_tag_picks_its_own_edition(self) -> None:
        server = bf.BandcampServer(edition_site(PROBED), clock=self.clock)
        tagged = lm.TrackQuery(ARTIST, "The Ford", album=edition(DELUXE)[1])
        self.assertEqual(self.ask(server, tagged), [DELUXE_FORD])
        self.assertEqual(self.bandcamp_paths(server), opened(DELUXE))

    def test_a_hold_without_lyrics_loses_to_a_later_one_with_them(self) -> None:
        site = edition_site({FOURTH: NO_TRACK, JUBILEE: " \n ", COVERS: COVERS_FORD})
        server = bf.BandcampServer(site, clock=self.clock)
        self.assertEqual(self.ask(server, query("The Ford")), [COVERS_FORD])
        self.assertEqual(self.bandcamp_paths(server), opened(FOURTH, JUBILEE, COVERS))

    def test_no_lyrics_within_the_bound_keeps_the_first_holder(self) -> None:
        within = (FOURTH, JUBILEE, *VOLUMES[:6])
        pages = {s: NO_TRACK for s in within} | {JUBILEE: None, COVERS: COVERS_FORD}
        server = bf.BandcampServer(edition_site(pages), clock=self.clock)
        # Each edition's Quiet Hour differs, so its lyrics prove which album was kept.
        asked = self.ask(server, query("The Ford"), query("Quiet Hour"))
        self.assertEqual(asked, [None, quiet(JUBILEE)])
        self.assertEqual(self.bandcamp_paths(server), opened(*within))


class SelfTitledRunTest(SiteCase):
    """A run of suffixed ties with no bare title is searched up to the bound."""

    def test_the_bound_is_eight(self) -> None:
        self.assertEqual(lb.MAX_TIED_ENTRIES, 8)

    def test_a_holder_sixth_among_the_ties_is_found(self) -> None:
        pages = {s: NO_TRACK for s in VOLUMES[:5]} | {COVERS: COVERS_FORD}
        server = bf.BandcampServer(edition_site(pages), clock=self.clock)
        self.assertEqual(self.ask(server, query("The Ford")), [COVERS_FORD])
        self.assertEqual(self.bandcamp_paths(server), opened(*VOLUMES[:5], COVERS))

    def test_the_exact_title_tenth_on_the_page_is_opened_first(self) -> None:
        # Nine ties precede it, so a cap taken before the sort would leave it out.
        pages = {s: NO_TRACK for s in (*VOLUMES, COVERS)} | {BARE: FORD}
        server = bf.BandcampServer(edition_site(pages), clock=self.clock)
        self.assertEqual(self.ask(server, query("The Ford")), [FORD])
        self.assertEqual(self.bandcamp_paths(server), opened(BARE))

    def test_a_holder_ninth_among_the_ties_is_not_reached(self) -> None:
        pages = {s: NO_TRACK for s in VOLUMES} | {COVERS: COVERS_FORD}
        server = bf.BandcampServer(edition_site(pages), clock=self.clock)
        asked = self.ask(server, query("The Ford"), query("Quiet Hour"))
        # None holds the track within the bound, so the first opened is the album.
        self.assertEqual(asked, [None, quiet(VOLUMES[0])])
        self.assertEqual(self.bandcamp_paths(server), opened(*VOLUMES))


class EditionCacheTest(SiteCase):
    """Each edition tag keeps its own outcome; one tag asked twice looks up once."""

    def test_each_edition_tag_answers_its_own_edition(self) -> None:
        server = bf.BandcampServer(
            edition_site({BARE: FORD, DELUXE: DELUXE_FORD}), clock=self.clock
        )
        deluxe = lm.TrackQuery(ARTIST, "The Ford", album=edition(DELUXE)[1])
        asked = self.ask(server, query("The Ford"), deluxe)
        self.assertEqual(asked, [FORD, DELUXE_FORD])
        self.assertEqual(self.bandcamp_paths(server), [*opened(BARE), *opened(DELUXE)])

    def test_one_tag_asked_twice_sends_one_set_of_requests(self) -> None:
        server = bf.BandcampServer(
            edition_site({BARE: FORD, DELUXE: DELUXE_FORD}), clock=self.clock
        )
        asked = self.ask(server, query("The Ford"), query("Quiet Hour"))
        self.assertEqual(asked, [FORD, quiet(BARE)])
        self.assertEqual(self.bandcamp_paths(server), opened(BARE))
        self.assertEqual(len(self.provider._albums), 1)

    def test_tags_without_letters_keep_their_own_outcomes(self) -> None:
        # Both tags have an empty whole title; the matcher's key still tells them apart.
        server = bf.BandcampServer(edition_site({BARE: FORD}), clock=self.clock)
        signs = ("\N{DIVISION SIGN}", "\N{MULTIPLICATION SIGN}")
        self.ask(server, *(lm.TrackQuery(ARTIST, "The Ford", album=s) for s in signs))
        self.assertEqual(len(self.provider._albums), 2)
