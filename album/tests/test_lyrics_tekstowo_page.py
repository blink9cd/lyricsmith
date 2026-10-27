"""lyrics_tekstowo_page: Tekstowo's search results and a song page's original lyrics.

Every page here is invented by `tekstowo_pages`, shaped like the probed pages; no saved
page and no real name or lyric appears. The parsers are pure, so no test needs a fake
transport.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tekstowo_page as ltp  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_pages as pages  # noqa: E402 - import follows the sys.path bootstrap above

FOUND = (pages.HREF, f"{pages.ARTIST} - {pages.TITLE}")
SECOND = ("/invented-artist/second-keeper", "Invented Artist - Second Keeper")


def _entries(*entries: tuple[str, str], **flags: bool) -> tuple[ltp.SearchEntry, ...]:
    """The entries a search page built from *entries* and *flags* parses to."""
    return ltp.parse_search_page(pages.search_page(*entries, **flags)).entries


class ConstantsTest(fixtures.IsolatedCase):
    """The site's names the parsers key on."""

    def test_the_names(self) -> None:
        self.assertEqual(ltp.RESULTS_HEADING, "Znalezione utwory")
        self.assertEqual(ltp.SEARCH_ACTION, "/szukaj")
        self.assertEqual(
            (ltp.SONG_TEXT_ID, ltp.INNER_TEXT_CLASS), ("songText", "inner-text")
        )


class SearchPageTest(fixtures.IsolatedCase):
    """The songs under the results heading, in page order, and never the sidebar's."""

    def test_entries_in_order_without_the_sidebar(self) -> None:
        page = ltp.parse_search_page(pages.search_page(FOUND, SECOND))
        self.assertTrue(page.has_form)
        self.assertEqual(
            page.entries,
            (
                ltp.SearchEntry(pages.HREF, pages.ARTIST, pages.TITLE),
                ltp.SearchEntry(SECOND[0], pages.ARTIST, "Second Keeper"),
            ),
        )

    def test_an_entity_in_an_artist_is_decoded(self) -> None:
        found = _entries((pages.HREF, "Invented & Co - The Keeper Counts"))
        self.assertEqual(found[0].artist, "Invented & Co")

    def test_no_heading_is_no_entries_with_the_form(self) -> None:
        page = ltp.parse_search_page(pages.search_page(FOUND, heading=False))
        self.assertEqual(page, ltp.SearchPage(True, ()))

    def test_no_form(self) -> None:
        page = ltp.parse_search_page(pages.search_page(FOUND, form=False))
        self.assertFalse(page.has_form)
        self.assertEqual(len(page.entries), 1)

    def test_a_form_with_another_action_is_not_the_search_form(self) -> None:
        html = pages.search_page(FOUND).replace(
            'action="/szukaj"', 'action="/elsewhere"'
        )
        self.assertFalse(ltp.parse_search_page(html).has_form)

    def test_no_sidebar_still_reads_the_results(self) -> None:
        self.assertEqual(len(_entries(FOUND, sidebar=False)), 1)

    def test_a_second_results_heading_is_never_read(self) -> None:
        html = (
            pages.search_page(FOUND)
            .replace("<h2>Popularne teksty</h2>", "<h2>Znalezione utwory:</h2><div>")
            .replace("</body>", "</div></body>")
        )
        self.assertEqual(len(ltp.parse_search_page(html).entries), 1)

    def test_a_link_outside_any_row_inside_the_box_is_read(self) -> None:
        html = pages.search_page().replace(
            '<div class="card mb-4">',
            '<div class="card mb-4"><a href="/a/b" title="A - B">A - B</a>',
        )
        self.assertEqual(
            ltp.parse_search_page(html).entries, (ltp.SearchEntry("/a/b", "A", "B"),)
        )

    def test_a_link_without_an_href_or_title_is_skipped(self) -> None:
        html = pages.search_page().replace(
            '<div class="card mb-4">',
            '<div class="card mb-4"><a title="A - B">x</a><a href="/a/b">y</a>',
        )
        self.assertEqual(ltp.parse_search_page(html).entries, ())


class SearchEntryShapeTest(fixtures.IsolatedCase):
    """Only a two-segment site-relative href and an ``Artist - Title`` label make an entry."""

    def test_a_three_segment_href_is_skipped(self) -> None:
        self.assertEqual(_entries(("/a/b/c", FOUND[1])), ())

    def test_an_absolute_href_is_skipped(self) -> None:
        self.assertEqual(_entries(("https://example.com" + pages.HREF, FOUND[1])), ())

    def test_a_query_in_the_href_is_skipped(self) -> None:
        self.assertEqual(_entries((pages.HREF + "?x=1", FOUND[1])), ())

    def test_a_label_without_the_separator_is_skipped(self) -> None:
        self.assertEqual(
            _entries((pages.HREF, "Invented Artist The Keeper Counts")), ()
        )

    def test_a_blank_artist_is_skipped(self) -> None:
        self.assertEqual(_entries((pages.HREF, "  - The Keeper Counts")), ())

    def test_a_blank_title_is_skipped(self) -> None:
        self.assertEqual(_entries((pages.HREF, "Invented Artist -  ")), ())

    def test_the_first_separator_splits(self) -> None:
        found = _entries((pages.HREF, "A - B - C"))
        self.assertEqual(found, (ltp.SearchEntry(pages.HREF, "A", "B - C"),))

    def test_artist_and_title_are_stripped(self) -> None:
        found = _entries((pages.HREF, "  A   -   B  "))
        self.assertEqual(found, (ltp.SearchEntry(pages.HREF, "A", "B"),))


class SongTextTest(fixtures.IsolatedCase):
    """The original lyrics, line by line, and nothing of the caption or the translation."""

    def test_the_lines(self) -> None:
        page = ltp.parse_song_page(pages.song_page())
        self.assertTrue(page.has_song_text)
        self.assertEqual(page.text, "first invented line\nsecond invented line")

    def test_a_stanza_break_is_one_blank_line(self) -> None:
        lines = ("first invented line", "", "second invented line")
        text = ltp.parse_song_page(pages.song_page(lines=lines)).text
        self.assertEqual(text, "first invented line\n\nsecond invented line")

    def test_a_source_newline_inside_a_line_does_not_split_it(self) -> None:
        lines = ("first\n   invented line", "second invented line")
        text = ltp.parse_song_page(pages.song_page(lines=lines)).text
        self.assertEqual(text, "first invented line\nsecond invented line")

    def test_entities_are_decoded(self) -> None:
        lines = ("keeper &amp; counter &#8217;s line",)
        text = ltp.parse_song_page(pages.song_page(lines=lines)).text
        self.assertEqual(text, "keeper & counter ’s line")

    def test_nested_inline_text_is_kept(self) -> None:
        lines = ("the <i>keeper</i> counts", "<b><i>all</i> of them</b>")
        text = ltp.parse_song_page(pages.song_page(lines=lines)).text
        self.assertEqual(text, "the keeper counts\nall of them")

    def test_whitespace_split_by_an_inline_tag_is_one_space(self) -> None:
        lines = ("a <i> b</i>", "c\t<b>\n d</b>")
        text = ltp.parse_song_page(pages.song_page(lines=lines)).text
        self.assertEqual(text, "a b\nc d")

    def test_script_and_style_text_is_not_lyrics(self) -> None:
        lines = ("a<script>var x=1;</script>", "b<style>p { color: red }</style>")
        text = ltp.parse_song_page(pages.song_page(lines=lines)).text
        self.assertEqual(text, "a\nb")

    def test_leading_and_trailing_blank_lines_are_dropped(self) -> None:
        lines = ("", "", "only invented line", "", "")
        text = ltp.parse_song_page(pages.song_page(lines=lines)).text
        self.assertEqual(text, "only invented line")

    def test_no_caption_and_no_translation(self) -> None:
        text = ltp.parse_song_page(pages.song_page()).text
        self.assertNotIn("Tekst piosenki", text)
        for line in pages.TRANSLATION:
            self.assertNotIn(line, text)

    def test_no_song_text(self) -> None:
        page = ltp.parse_song_page(pages.song_page(song_text=False))
        self.assertFalse(page.has_song_text)
        self.assertEqual(page.text, "")

    def test_a_blank_inner_text(self) -> None:
        page = ltp.parse_song_page(pages.song_page(lines=("  ", "")))
        self.assertTrue(page.has_song_text)
        self.assertEqual(page.text, "")

    def test_a_second_inner_text_is_never_read(self) -> None:
        html = pages.song_page().replace(
            '</div></div><div id="songTranslation"',
            '</div><div class="inner-text">a stray block</div></div>'
            '<div id="songTranslation"',
        )
        text = ltp.parse_song_page(html).text
        self.assertEqual(text, "first invented line\nsecond invented line")

    def test_an_inner_text_outside_song_text_is_never_read(self) -> None:
        page = ltp.parse_song_page(pages.song_page(song_text=False))
        self.assertEqual(page.text, "")

    def test_a_second_song_text_is_never_read(self) -> None:
        html = pages.song_page().replace(
            '<div id="songTranslation"',
            '<div id="songText"><div class="inner-text">stray</div></div>'
            '<div id="songTranslation"',
        )
        text = ltp.parse_song_page(html).text
        self.assertEqual(text, "first invented line\nsecond invented line")

    def test_a_marked_section_after_the_text_still_reads_it(self) -> None:
        # An older _markupbase raises on an unknown marked section; the page before it reads.
        html = pages.song_page() + "<![foo[ x ]]><p>after</p>"
        self.assertEqual(
            ltp.parse_song_page(html).text, "first invented line\nsecond invented line"
        )


class InstrumentalTest(fixtures.IsolatedCase):
    """An instrumental's page: no ``#songText``, its marker box in its place."""

    def test_the_marker_box_makes_the_page_an_instrumental(self) -> None:
        page = ltp.parse_song_page(pages.song_page(song_text=False, instrumental=True))
        self.assertTrue(page.instrumental)
        self.assertFalse(page.has_song_text)
        self.assertEqual((page.name, page.artist), (pages.TITLE, pages.ARTIST))
        self.assertEqual(page.text, "")

    def test_a_page_with_no_marker_box_is_no_instrumental(self) -> None:
        for html in (pages.song_page(), pages.song_page(song_text=False)):
            with self.subTest(html=html):
                self.assertFalse(ltp.parse_song_page(html).instrumental)

    def test_a_box_that_only_names_the_marker_later_is_no_instrumental(self) -> None:
        # A sidebar row titled with the words is a song's name, not the page's marker.
        row = pages.row("/someone/song", "Someone - Utwór instrumentalny")
        html = pages.song_page(song_text=False).replace("<body>", f"<body>{row}")
        self.assertFalse(ltp.parse_song_page(html).instrumental)

    def test_the_marker_in_a_script_is_no_instrumental(self) -> None:
        box = '<div class="box-przeboje"><script>"Utwór instrumentalny"</script></div>'
        html = pages.song_page(song_text=False).replace("<body>", f"<body>{box}")
        self.assertFalse(ltp.parse_song_page(html).instrumental)


class JsonLdTest(fixtures.IsolatedCase):
    """The title and artist a song page's MusicRecording JSON-LD names."""

    def _parsed(self, *scripts: object) -> ltp.SongPage:
        """A song page without its own JSON-LD, carrying *scripts* in its head."""
        html = pages.song_page(json_ld=False).replace(
            "</head>", "".join(pages.ld_script(s) for s in scripts) + "</head>"
        )
        return ltp.parse_song_page(html)

    def test_the_title_and_artist(self) -> None:
        page = ltp.parse_song_page(pages.song_page())
        self.assertEqual((page.name, page.artist), (pages.TITLE, pages.ARTIST))

    def test_absent(self) -> None:
        page = ltp.parse_song_page(pages.song_page(json_ld=False))
        self.assertEqual((page.name, page.artist), (None, None))

    def test_malformed_is_skipped_for_the_next(self) -> None:
        html = pages.song_page().replace(
            "<script", '<script type="application/ld+json">{not json</script><script', 1
        )
        page = ltp.parse_song_page(html)
        self.assertEqual((page.name, page.artist), (pages.TITLE, pages.ARTIST))

    def test_too_deep_to_decode_is_skipped(self) -> None:
        deep = "[" * 100_000 + "]" * 100_000
        html = pages.song_page().replace(
            "<script", f'<script type="application/ld+json">{deep}</script><script', 1
        )
        page = ltp.parse_song_page(html)
        self.assertEqual((page.name, page.artist), (pages.TITLE, pages.ARTIST))

    def test_another_type_is_skipped_for_the_next(self) -> None:
        page = self._parsed({"@type": "WebSite", "name": "x"}, pages.recording())
        self.assertEqual((page.name, page.artist), (pages.TITLE, pages.ARTIST))

    def test_a_list_form(self) -> None:
        page = self._parsed([{"@type": "BreadcrumbList"}, pages.recording()])
        self.assertEqual((page.name, page.artist), (pages.TITLE, pages.ARTIST))

    def test_by_artist_as_a_list(self) -> None:
        document = pages.recording()
        document["byArtist"] = [{"name": pages.ARTIST}, {"name": "Second"}]
        page = self._parsed(document)
        self.assertEqual(page.artist, pages.ARTIST)

    def test_by_artist_as_an_empty_list_or_a_string(self) -> None:
        for by_artist in ([], ["Invented Artist"], "Invented Artist"):
            document = pages.recording()
            document["byArtist"] = by_artist
            with self.subTest(by_artist=by_artist):
                self.assertIsNone(self._parsed(document).artist)

    def test_non_string_names(self) -> None:
        page = self._parsed(pages.recording(name=7, artist=["x"]))
        self.assertEqual((page.name, page.artist), (None, None))

    def test_another_script_type_is_not_collected(self) -> None:
        html = pages.song_page(json_ld=False).replace(
            "</head>",
            f'<script type="text/javascript">{pages.recording()}</script></head>',
        )
        self.assertIsNone(ltp.parse_song_page(html).name)


def _fragments(html: str) -> tuple[str, ...]:
    """*html* cut before each tag, so fragments can be drawn and shuffled."""
    return tuple("<" + piece for piece in html.split("<") if piece)


_SEARCH_FRAGMENTS = _fragments(pages.search_page(FOUND, SECOND)) + (
    '<a href="/x/y" title="X - Y">',
    "<br>",
    "</div>",
)
_SONG_FRAGMENTS = _fragments(
    pages.song_page(lines=("one &amp; two", "", "<i>three</i>"))
)


def _pages(fragments: tuple[str, ...]) -> st.SearchStrategy[str]:
    """Arbitrary text, or *fragments* drawn with repeats, or all of them shuffled."""
    return st.one_of(
        st.text(),
        st.lists(st.sampled_from(fragments)).map("".join),
        st.permutations(fragments).map("".join),
    )


class FuzzTekstowoPageTest(fixtures.IsolatedCase):
    """The page parsers never raise, and return well-formed values."""

    @given(_pages(_SEARCH_FRAGMENTS))
    def test_fuzz_parse_search_page(self, html: str) -> None:
        page = ltp.parse_search_page(html)
        self.assertIsInstance(page.entries, tuple)
        for entry in page.entries:
            self.assertIsInstance(entry, ltp.SearchEntry)
            self.assertIsNotNone(ltp._SONG_HREF.fullmatch(entry.href))
            self.assertTrue(entry.artist.strip() and entry.title.strip())

    @given(_pages(_SONG_FRAGMENTS))
    def test_fuzz_parse_song_page(self, html: str) -> None:
        page = ltp.parse_song_page(html)
        self.assertFalse(page.text.startswith("\n") or page.text.endswith("\n"))
        self.assertTrue(page.has_song_text or not page.text)
