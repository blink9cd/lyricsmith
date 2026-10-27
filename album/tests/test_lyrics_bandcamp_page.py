"""lyrics_bandcamp_page: an album page's JSON-LD, an artist's /music page, ISO durations.

Every page here is invented by `bandcamp_pages`, shaped like the probed pages; no saved
page and no real name or lyric appears. The parsers are pure, so no test needs a fake
transport.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import bandcamp_pages as pages  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_bandcamp_page as lbp  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_match import Hit  # noqa: E402 - import follows the sys.path bootstrap above

LYRICS = "the lantern hangs\nabove the ford"
BAND = "Invented Band"


class AlbumPageTest(fixtures.IsolatedCase):
    """An album page's tracks, credits and lyrics come from its MusicAlbum JSON-LD."""

    def test_tracks_in_page_order(self) -> None:
        html = pages.album_page([pages.track("Ford Song"), pages.track("Second Lamp")])
        album = lbp.parse_album_page(html)
        assert album is not None
        self.assertEqual(album.name, "The Lantern Years")
        self.assertEqual(
            album.tracks,
            (
                lbp.BandcampTrack(Hit("Ford Song", (BAND,), 201.0), LYRICS),
                lbp.BandcampTrack(Hit("Second Lamp", (BAND,), 201.0), LYRICS),
            ),
        )

    def test_track_without_lyrics_is_none(self) -> None:
        html = pages.album_page([pages.track("Quiet", lyrics=None)])
        album = lbp.parse_album_page(html)
        assert album is not None
        self.assertIsNone(album.tracks[0].lyrics)

    def test_blank_lyrics_text_is_none(self) -> None:
        html = pages.album_page([pages.track("Quiet", lyrics="   \n ")])
        album = lbp.parse_album_page(html)
        assert album is not None
        self.assertIsNone(album.tracks[0].lyrics)

    def test_track_artist_wins_over_album_artist(self) -> None:
        html = pages.album_page([pages.track("Guest Turn", artist="Invented Guest")])
        album = lbp.parse_album_page(html)
        assert album is not None
        self.assertEqual(album.tracks[0].hit.artists, ("Invented Guest",))

    def test_no_artist_anywhere_is_no_credit(self) -> None:
        document = pages.album_document([pages.track("Lone")])
        del document["byArtist"]
        album = lbp.parse_album_page(pages.ld_script(document))
        assert album is not None
        self.assertEqual(album.tracks[0].hit.artists, ())

    def test_no_duration_is_none(self) -> None:
        html = pages.album_page([pages.track("Untimed", duration=None)])
        album = lbp.parse_album_page(html)
        assert album is not None
        self.assertIsNone(album.tracks[0].hit.duration_s)

    def test_no_json_ld_is_none(self) -> None:
        self.assertIsNone(lbp.parse_album_page(pages.album_page([], ld=False)))

    def test_other_type_before_album_is_passed_over(self) -> None:
        page = pages.ld_script(
            {"@type": "WebPage", "name": "Not It"}
        ) + pages.album_page([pages.track("Ford Song")])
        album = lbp.parse_album_page(page)
        assert album is not None
        self.assertEqual(album.name, "The Lantern Years")

    def test_malformed_json_is_none(self) -> None:
        page = '<script type="application/ld+json">{"@type": "MusicAlbum",</script>'
        self.assertIsNone(lbp.parse_album_page(page))

    def test_list_valued_type_is_read(self) -> None:
        document = pages.album_document([pages.track("Ford Song")])
        document["@type"] = ["Product", "MusicAlbum"]
        album = lbp.parse_album_page(pages.ld_script(document))
        assert album is not None
        self.assertEqual(len(album.tracks), 1)

    def test_list_document_holding_album_is_read(self) -> None:
        document = pages.album_document([pages.track("Ford Song")])
        album = lbp.parse_album_page(pages.ld_script([{"@type": "WebPage"}, document]))
        assert album is not None
        self.assertEqual(album.tracks[0].hit.title, "Ford Song")

    def test_other_script_types_are_ignored(self) -> None:
        document = pages.album_document([pages.track("Ford Song")])
        page = f'<script type="text/javascript">{json.dumps(document)}</script>'
        self.assertIsNone(lbp.parse_album_page(page))

    def test_unnamed_or_non_string_names_are_skipped(self) -> None:
        nameless = pages.track("x")
        del nameless["name"]
        numbered = pages.track("x")
        numbered["name"] = 7
        blank = pages.track("  ")
        tracks = [nameless, numbered, blank, pages.track("Kept")]
        album = lbp.parse_album_page(pages.album_page(tracks))
        assert album is not None
        self.assertEqual([t.hit.title for t in album.tracks], ["Kept"])

    def test_wrong_shapes_are_skipped(self) -> None:
        document = pages.album_document([])
        document["name"] = 5
        document["track"] = {
            "itemListElement": ["not an entry", {"item": "not an item"}]
        }
        album = lbp.parse_album_page(pages.ld_script(document))
        self.assertEqual(album, lbp.BandcampAlbum(None, ()))

    def test_track_list_of_wrong_type_is_empty(self) -> None:
        document = pages.album_document([])
        document["track"] = {"itemListElement": "none"}
        album = lbp.parse_album_page(pages.ld_script(document))
        assert album is not None
        self.assertEqual(album.tracks, ())

    def test_marked_section_after_the_page_still_reads_it(self) -> None:
        # An older _markupbase raises on an unknown marked section; the page before it reads.
        html = (
            pages.album_page([pages.track("Ford Song")]) + "<![foo[ x ]]><p>after</p>"
        )
        album = lbp.parse_album_page(html)
        assert album is not None
        self.assertEqual([t.hit.title for t in album.tracks], ["Ford Song"])


class DurationTest(fixtures.IsolatedCase):
    """ISO 8601 durations, Bandcamp's T-less form included."""

    def test_valid_durations(self) -> None:
        for text, seconds in (
            ("P00H03M21S", 201.0),
            ("PT3M21.5S", 201.5),
            ("P0DT1H", 3600.0),
            (" P1D ", 86400.0),
        ):
            with self.subTest(text=text):
                self.assertEqual(lbp.iso_duration_s(text), seconds)

    def test_refused_durations(self) -> None:
        for text in ("P", "", "PT", "3:21", "P-1M", "PT0S"):
            with self.subTest(text=text):
                self.assertIsNone(lbp.iso_duration_s(text))


class MusicPageTest(fixtures.IsolatedCase):
    """An artist's /music page lists its albums by /album/ path and title."""

    def test_grid_links_then_client_items(self) -> None:
        html = pages.music_page(
            [
                ("/album/first-lamp", "First Lamp"),
                ("/album/second-ford", "Second Ford"),
            ],
            [("/album/third-bell", "Third Bell")],
        )
        self.assertEqual(
            lbp.parse_music_page(html),
            (
                lbp.MusicEntry("/album/first-lamp", "First Lamp"),
                lbp.MusicEntry("/album/second-ford", "Second Ford"),
                lbp.MusicEntry("/album/third-bell", "Third Bell"),
            ),
        )

    def test_track_link_is_ignored(self) -> None:
        html = pages.music_page([("/track/x", "A Single"), ("/album/y", "An Album")])
        self.assertEqual(
            lbp.parse_music_page(html), (lbp.MusicEntry("/album/y", "An Album"),)
        )

    def test_absolute_link_keeps_its_path(self) -> None:
        html = pages.music_page(
            [("https://invented-band.bandcamp.com/album/x?a=1", "Ex")]
        )
        self.assertEqual(
            lbp.parse_music_page(html), (lbp.MusicEntry("/album/x", "Ex"),)
        )

    def test_title_stops_at_break(self) -> None:
        title = 'Lamp  Light<br><span class="artist-override">Guest</span>'
        html = pages.music_page([("/album/x", title)])
        self.assertEqual(
            lbp.parse_music_page(html), (lbp.MusicEntry("/album/x", "Lamp Light"),)
        )

    def test_link_without_title_is_skipped(self) -> None:
        html = '<a href="/album/x"><p class="title">  </p></a><a href="/album/y">no title</a>'
        self.assertEqual(lbp.parse_music_page(html), ())

    def test_first_title_wins(self) -> None:
        html = pages.music_page(
            [("/album/x", "Grid Name")], [("/album/x", "Other Name")]
        )
        self.assertEqual(
            lbp.parse_music_page(html), (lbp.MusicEntry("/album/x", "Grid Name"),)
        )

    def test_client_items_not_json_are_ignored(self) -> None:
        html = '<ol data-client-items="[not json"></ol>' + pages.grid_link(
            "/album/x", "Ex"
        )
        self.assertEqual(
            lbp.parse_music_page(html), (lbp.MusicEntry("/album/x", "Ex"),)
        )

    def test_client_items_of_wrong_shape_are_skipped(self) -> None:
        items = json.dumps(
            [{"page_url": 3, "title": "x"}, "y", {"page_url": "/album/z", "title": " "}]
        )
        html = f"<div data-client-items='{items}'></div><div data-client-items='{{}}'></div>"
        self.assertEqual(lbp.parse_music_page(html), ())

    def test_unparsable_href_is_skipped(self) -> None:
        html = '<a href="http://[bad/album/x"><p class="title">Ex</p></a>'
        self.assertEqual(lbp.parse_music_page(html), ())

    def test_link_outside_an_album_ends_the_open_one(self) -> None:
        html = '<a href="/album/x"><a href="/about"><p class="title">About</p></a>'
        self.assertEqual(lbp.parse_music_page(html), ())

    def test_marked_section_after_a_link_still_reads_it(self) -> None:
        # An older _markupbase raises on an unknown marked section; the link before it reads.
        html = (
            pages.grid_link("/album/x", "Ex")
            + "<![foo[ x ]]>"
            + pages.grid_link("/album/y", "Why")
        )
        self.assertEqual(
            lbp.parse_music_page(html)[:1], (lbp.MusicEntry("/album/x", "Ex"),)
        )


#: The JSON-LD field names a drawn value may be keyed by.
_LD_KEYS = (
    "@type",
    "name",
    "track",
    "itemListElement",
    "item",
    "duration",
    "byArtist",
    "recordingOf",
    "lyrics",
    "text",
)
#: Any JSON-like value, standing in for a field of the wrong type or shape.
_DRAWN = http_fakes.json_values(_LD_KEYS, max_size=4, max_leaves=12)
#: The pieces a /music page is assembled from: links, titles, breaks and client items.
_MUSIC_FRAGMENTS = (
    '<a href="/album/x">',
    '<a href="https://h.bandcamp.com/album/y?q=1">',
    '<a href="/about">',
    '<p class="title">',
    "T",
    " ",
    "<br>",
    "<span>",
    "</p>",
    "</a>",
    '<div data-client-items="[{&quot;page_url&quot;: &quot;/album/z&quot;, '
    '&quot;title&quot;: &quot;Z&quot;}]"></div>',
    '<div data-client-items="[{&quot;page_url&quot;: &quot;/track/z&quot;, '
    '&quot;title&quot;: &quot; &quot;}]"></div>',
    '<ol data-client-items="[not json"></ol>',
)


def _mostly(good: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Mostly *good*; about one draw in four a value of any shape in its place.

    Weighted by a sampled flag: repeating a branch inside ``st.one_of`` was measured not to
    weight it, and an even split left a drawn value at almost every level, so a page with a
    usable track came up about twice in a hundred.
    """
    return st.sampled_from((True, True, True, False)).flatmap(
        lambda keep: good if keep else _DRAWN
    )


#: A field's value: mostly a string, blank ones included.
_FIELD = _mostly(st.text(min_size=1, max_size=8))


def _nested(key: str, inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Mostly ``{key: inner}``."""
    return _mostly(st.fixed_dictionaries({key: inner}))


def _track_items() -> st.SearchStrategy[object]:
    """A track item whose every field may be of the wrong type (a missing one reads alike)."""
    lyrics = _nested("lyrics", _nested("text", _FIELD))
    duration = st.one_of(st.sampled_from(["P00H03M21S", "PT0S"]), _FIELD)
    return st.fixed_dictionaries(
        {
            "name": _FIELD,
            "duration": duration,
            "byArtist": _nested("name", _FIELD),
            "recordingOf": lyrics,
        }
    )


def _album_pages() -> st.SearchStrategy[str]:
    """A JSON-LD script holding a MusicAlbum skeleton whose fields are drawn."""
    elements = st.lists(_nested("item", _track_items()), max_size=4)
    album = st.fixed_dictionaries(
        {
            "@type": st.sampled_from(["MusicAlbum", ["Product", "MusicAlbum"]]),
            "track": _nested("itemListElement", _mostly(elements)),
        },
        optional={"name": _FIELD, "byArtist": _nested("name", _FIELD)},
    )
    document = st.one_of(album, st.tuples(_DRAWN, album).map(list))
    return document.map(
        lambda value: f'<script type="application/ld+json">{json.dumps(value)}</script>'
    )


class FuzzBandcampPageTest(fixtures.IsolatedCase):
    """The page parsers never raise, and return well-formed values."""

    @given(st.one_of(st.text(), _album_pages()))
    def test_fuzz_parse_album_page(self, html: str) -> None:
        album = lbp.parse_album_page(html)
        if album is not None:
            for found in album.tracks:
                self.assertTrue(found.hit.title.strip())
                self.assertTrue(found.lyrics is None or found.lyrics.strip())

    @given(
        st.one_of(st.text(), st.lists(st.sampled_from(_MUSIC_FRAGMENTS)).map("".join))
    )
    def test_fuzz_parse_music_page(self, html: str) -> None:
        for entry in lbp.parse_music_page(html):
            self.assertTrue(entry.path.startswith("/album/"))
            self.assertTrue(entry.title.strip())

    @given(st.text())
    def test_fuzz_iso_duration_s(self, text: str) -> None:
        seconds = lbp.iso_duration_s(text)
        self.assertTrue(seconds is None or (math.isfinite(seconds) and seconds > 0))
