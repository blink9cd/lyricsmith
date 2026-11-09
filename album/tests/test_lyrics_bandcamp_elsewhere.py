"""The Bandcamp step when a page redirects off Bandcamp: served elsewhere, no match.

Bandcamp lets an artist serve its pages from a domain of its own, so the artist's
Bandcamp page answers a redirect to that domain, which the step never follows. A
redirect to an https URL on a host off Bandcamp is the album's no match
(`lyrics_musicbrainz.Miss.PAGE_ELSEWHERE`), as a gone page is; every other refused
redirect stays the album's error. No test here reaches the network:
`bandcamp_fakes.BandcampServer` answers from memory on a fake clock. Every id, title,
name, host and lyric is invented.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import bandcamp_fakes as bf  # noqa: E402 - import follows the sys.path bootstrap above
import bandcamp_pages as bp  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_bandcamp as lb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_musicbrainz as lmb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above

ALBUM = "The Lantern Years"
ARTIST = "Invented Band"
HOST = "invented-band.bandcamp.com"
SITE = f"https://{HOST}"
ALBUM_PATH = "/album/the-lantern-years"
#: Two same-titled albums on the artist's /music page.
PATHS = ("/album/the-lantern-years-1", "/album/the-lantern-years-2")
#: The artist's own domain, which the Bandcamp pages redirect to.
OWN_DOMAIN = "https://music.example.com"
ELSEWHERE = bf.Redirect(f"{OWN_DOMAIN}/album/the-lantern-years")
FORD = "the ford runs cold\nunder the lantern"
KEY = lm.ProviderKey(lm.Source.BANDCAMP, lm.LyricsKind.UNSYNCED)
QUERY = lm.TrackQuery(ARTIST, "The Ford", album=ALBUM)
SEARCH = (
    (bf.MB_HOST, bf.SEARCH_PATH),
    bf.search_reply(bf.release(bf.RELEASE_1, ALBUM, ARTIST)),
)
LINKED = (
    (bf.MB_HOST, bf.release_path(bf.RELEASE_1)),
    bf.relations_reply(("purchase for download", f"{SITE}{ALBUM_PATH}")),
)
UNLINKED = ((bf.MB_HOST, bf.release_path(bf.RELEASE_1)), bf.relations_reply())
ARTIST_SITE = (
    (bf.MB_HOST, bf.artist_path(bf.ARTIST_1)),
    bf.relations_reply(("bandcamp", f"{SITE}/")),
)
#: The refused redirects that keep their class: each names no https URL off Bandcamp
#: on the default port, or names none at all.
KEPT_REFUSALS: tuple[object, ...] = (
    302,
    bf.Redirect("http://music.example.com/album/the-lantern-years"),
    bf.Redirect("https://music.example.com:8443/album/the-lantern-years"),
    bf.Redirect("https://:@music.example.com/album/the-lantern-years"),
    bf.Redirect(f"http://{HOST}{ALBUM_PATH}"),
    # A Bandcamp host the strict host rule refuses is still Bandcamp, never elsewhere.
    bf.Redirect(f"https://{HOST}.{ALBUM_PATH}"),
    bf.Redirect(f"https://invented_band.bandcamp.com{ALBUM_PATH}"),
    bf.Redirect(f"https://{HOST.upper()}.{ALBUM_PATH}"),
    bf.Redirect(f"https://BANDCAMP.COM.{ALBUM_PATH}"),
)


def linked(page: object) -> dict[tuple[str, str], object]:
    """The release-link route, its album page answering *page*."""
    return {**dict((SEARCH, LINKED)), (HOST, ALBUM_PATH): page}


def tied(*pages: object) -> dict[tuple[str, str], object]:
    """The artist route: a /music page listing `PATHS`, their pages answering *pages*."""
    music = bp.music_page([(path, ALBUM) for path in PATHS])
    table = {**dict((SEARCH, UNLINKED, ARTIST_SITE)), (HOST, "/music"): music}
    return {**table, **{(HOST, path): page for path, page in zip(PATHS, pages)}}


class _Case(fixtures.IsolatedCase):
    """A provider on a fake clock, with readers of what it answered and kept."""

    def setUp(self) -> None:
        """A fresh fake clock and provider for each test."""
        super().setUp()
        self.fresh()

    def fresh(self) -> None:
        """A new fake clock and provider, nothing kept: each subtest's own."""
        self.clock = genius_fakes.FakeClock()
        client = lmb.MusicBrainzClient(None, clock=self.clock, sleeper=self.clock.sleep)
        self.provider = lb.BandcampProvider(client, clock=self.clock)

    def attempt(self, table: Mapping[tuple[str, str], object]) -> lm.ProviderAttempt:
        """The one attempt `fetch_lyrics` records for `QUERY` through *table*."""
        server = bf.BandcampServer(table, clock=self.clock)
        with http_fakes.transport(server):
            result = lf.fetch_lyrics(
                QUERY, lm.SidecarState(None), {KEY: self.provider}, deadline_s=30.0
            )
        (tried,) = result.attempts
        return tried

    def kept(self) -> object:
        """The one album outcome the provider kept."""
        (outcome,) = self.provider._albums.values()
        return outcome


class ElsewhereTest(_Case):
    """A Bandcamp page redirecting to an https URL off Bandcamp is the album's no match."""

    def test_a_linked_album_page_served_elsewhere_is_no_match(self) -> None:
        # The refusal first met live: an album page answering 301 to the artist's domain.
        tried = self.attempt(linked(ELSEWHERE))
        self.assertIs(tried.outcome, lm.AttemptOutcome.EMPTY)
        self.assertNotIn(tried.outcome, lm.REFUSED_OUTCOMES)
        self.assertIs(self.kept(), lmb.Miss.PAGE_ELSEWHERE)

    def test_every_redirect_status_to_another_host_reads_the_same(self) -> None:
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.fresh()
                moved = bf.Redirect(ELSEWHERE.location, status)
                self.assertIs(
                    self.attempt(linked(moved)).outcome, lm.AttemptOutcome.EMPTY
                )

    def test_a_music_page_served_elsewhere_is_no_match(self) -> None:
        table = {**dict((SEARCH, UNLINKED, ARTIST_SITE)), (HOST, "/music"): ELSEWHERE}
        self.assertIs(self.attempt(table).outcome, lm.AttemptOutcome.EMPTY)
        self.assertIs(self.kept(), lmb.Miss.PAGE_ELSEWHERE)

    def test_a_hop_within_bandcamp_then_off_it_is_no_match(self) -> None:
        table = linked(bf.Redirect(f"https://bandcamp.com{ALBUM_PATH}"))
        table[("bandcamp.com", ALBUM_PATH)] = ELSEWHERE
        self.assertIs(self.attempt(table).outcome, lm.AttemptOutcome.EMPTY)
        self.assertIs(self.kept(), lmb.Miss.PAGE_ELSEWHERE)


class TiedElsewhereTest(_Case):
    """Among tied /music entries, one served elsewhere is passed over, as a gone one is."""

    def test_an_entry_served_elsewhere_is_passed_over_for_the_next(self) -> None:
        found = bp.album_page([bp.track("The Ford", lyrics=FORD)], name=ALBUM)
        server = bf.BandcampServer(tied(ELSEWHERE, found), clock=self.clock)
        with http_fakes.transport(server):
            self.assertEqual(self.provider(QUERY), FORD)
        self.assertEqual(server.paths()[-2:], list(PATHS))

    def test_every_entry_served_elsewhere_is_no_match(self) -> None:
        self.assertIs(
            self.attempt(tied(ELSEWHERE, ELSEWHERE)).outcome, lm.AttemptOutcome.EMPTY
        )
        self.assertIs(self.kept(), lmb.Miss.PAGE_ELSEWHERE)

    def test_with_none_opened_the_first_entrys_miss_is_kept(self) -> None:
        for pages, miss in (
            ((404, ELSEWHERE), lmb.Miss.PAGE_GONE),
            ((ELSEWHERE, 410), lmb.Miss.PAGE_ELSEWHERE),
        ):
            with self.subTest(pages=pages):
                self.fresh()
                self.attempt(tied(*pages))
                self.assertIs(self.kept(), miss)


class KeptRefusalTest(_Case):
    """Every other refused redirect stays the album's error, recorded as a refusal."""

    def test_a_redirect_not_to_an_https_url_off_bandcamp_is_refused(self) -> None:
        for answer in KEPT_REFUSALS:
            with self.subTest(answer=answer):
                self.fresh()
                tried = self.attempt(linked(answer))
                self.assertIn(tried.outcome, lm.REFUSED_OUTCOMES)
                self.assertEqual(tried.detail, lh.REDIRECT_REFUSED)
                self.assertIsInstance(self.kept(), lh.RedirectRefused)

    def test_a_tied_entry_refused_so_is_the_albums_error(self) -> None:
        for answer in KEPT_REFUSALS:
            with self.subTest(answer=answer):
                self.fresh()
                tried = self.attempt(tied(answer, ELSEWHERE))
                self.assertEqual(tried.detail, lh.REDIRECT_REFUSED)

    def test_too_many_redirects_stays_too_many(self) -> None:
        table = linked(bf.Redirect(f"https://bandcamp.com{ALBUM_PATH}"))
        table[("bandcamp.com", ALBUM_PATH)] = bf.Redirect(f"{SITE}/album/again")
        table[(HOST, "/album/again")] = ELSEWHERE
        tried = self.attempt(table)
        self.assertEqual(tried.detail, lh.TOO_MANY_REDIRECTS)

    def test_a_start_url_off_bandcamp_is_still_refused_unasked(self) -> None:
        server = bf.BandcampServer({})
        with (
            http_fakes.transport(server),
            self.assertRaises(lp.ProviderError) as caught,
        ):
            lb._album_at(lh.cookie_free_session(), f"{OWN_DOMAIN}/album/x")
        self.assertEqual(caught.exception.detail, lh.URL_REFUSED)
        self.assertEqual(server.requests, [])


class FuzzElsewhereTest(fixtures.IsolatedCase):
    """`lb.is_elsewhere` never raises, and is true only of an https URL off Bandcamp."""

    @given(
        location=st.text(max_size=40)
        | st.builds(lambda host: f"https://{host}/x", st.text(max_size=20))
    )
    def test_fuzz_is_elsewhere(self, location: str) -> None:
        if not lb.is_elsewhere(location):
            return
        self.assertFalse(lmb.is_bandcamp_url(location))
        parts = urlsplit(location)
        self.assertEqual(parts.scheme, "https")
        self.assertIn(parts.port, (None, 443))
        self.assertNotIn("@", parts.netloc)
        host = (parts.hostname or "").rstrip(".")
        self.assertFalse(host == "bandcamp.com" or host.endswith(".bandcamp.com"))
