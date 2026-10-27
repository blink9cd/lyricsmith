"""The silence pass's HTML page: its sections, counts, rows, header and escaping.

`format_page` lays the list's rows out through the shared HTML page library: a summary
of the count per verdict, then a section per verdict in a fixed order, each row's
album, track number, title, length, peak and reason. Every value from the list is
text the tool did not choose, so each is escaped, and the page fetches and runs
nothing. Every album and title here is INVENTED.
"""

from __future__ import annotations

import html
import sys
from datetime import UTC, datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import page_fixtures as pf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence_page as ssp  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_silence_list import ReadRow  # noqa: E402 - follows the sys.path bootstrap
from stdio_utf8 import spell_unsafe  # noqa: E402 - follows the sys.path bootstrap
from sweep_silence_verdict import Verdict  # noqa: E402 - follows the sys.path bootstrap

_WRITTEN = datetime(2026, 10, 19, 9, 30, 0, tzinfo=UTC)
_LIST = "silent-20261019T093000Z.tsv"
#: The section order, as the anchors name it.
_ORDER = ["ripping-mistake", "unsure", "not-measured", "likely-meant"]


def _row(
    verdict: Verdict = Verdict.UNSURE,
    *,
    album: str = "Artist/Album",
    title: str = "Paper Boats",
    path: str = "",
    reason: str = "real title, 8.0 s",
) -> ReadRow:
    """A row as the reader gives it, its path the album's and the title's by default."""
    return ReadRow(
        album,
        verdict,
        reason,
        "-91.0",
        "8.0",
        "2/9",
        title,
        path or f"{album}/{title}.mp3",
    )


def _page(*rows: ReadRow, name: str = _LIST) -> str:
    """The page of *rows*, written at the fixed time from the list *name*."""
    return ssp.format_page(rows, list_name=name, written_at=_WRITTEN)


def _section(page: str, anchor: str) -> str:
    """The markup of the section *anchor* names."""
    start = page.index(f'<section id="{anchor}">')
    return page[start : page.index("</section>", start)]


class StructureTest(sf.IsolatedCase):
    """The page is whole, its links resolve, and it fetches nothing."""

    def test_a_page_is_well_formed_and_fetches_nothing(self) -> None:
        rows = [_row(verdict) for verdict in Verdict]
        parsed = pf.assert_well_formed(self, _page(*rows))
        self.assertEqual(
            sorted(f"#{anchor}" for anchor in _ORDER), sorted(parsed.links)
        )

    def test_an_empty_list_is_a_well_formed_page(self) -> None:
        pf.assert_well_formed(self, _page())

    def test_the_footer_says_nothing_was_changed(self) -> None:
        self.assertIn(f"<p>{ssp.READ_ONLY}</p>", _page())


class SectionTest(sf.IsolatedCase):
    """A section per verdict, in a fixed order, each with its count."""

    def test_the_sections_come_in_their_order(self) -> None:
        page = _page(*(_row(verdict) for verdict in Verdict))
        found = [page.index(f'<section id="{anchor}">') for anchor in _ORDER]
        self.assertEqual(found, sorted(found))
        self.assertLess(page.index('<section id="summary">'), found[0])

    def test_each_section_and_summary_row_counts_its_verdict(self) -> None:
        page = _page(
            _row(Verdict.UNSURE, title="One"),
            _row(Verdict.UNSURE, title="Two"),
            _row(Verdict.LIKELY_MEANT),
        )
        for anchor, count in zip(_ORDER, (0, 2, 0, 1), strict=True):
            with self.subTest(anchor=anchor):
                self.assertIn(
                    f'<span class="count">{count}</span>', _section(page, anchor)
                )
                summary = _section(page, "summary")
                self.assertRegex(
                    summary, f'href="#{anchor}">[^<]*</a></td><td class="num">{count}<'
                )

    def test_an_empty_section_shows_zero_and_a_note_and_no_table(self) -> None:
        section = _section(_page(), "unsure")
        self.assertIn('<span class="count">0</span>', section)
        self.assertIn(ssp.EMPTY_NOTE, section)
        self.assertNotIn("<table>", section)

    def test_a_row_shows_its_columns_in_order(self) -> None:
        section = _section(_page(_row(Verdict.RIPPING_MISTAKE)), "ripping-mistake")
        self.assertIn(
            "<tr><td>Artist/Album</td><td>Paper Boats.mp3</td>"
            '<td class="num">2/9</td><td>Paper Boats</td>'
            '<td class="num">8.0</td><td class="num">-91.0</td>'
            "<td>real title, 8.0 s</td></tr>",
            section,
        )
        heads = "".join(f"<th>{html.escape(c)}</th>" for c in ssp.COLUMNS)
        self.assertIn(heads, section)

    def test_two_untitled_tracks_of_one_album_differ_by_their_file(self) -> None:
        page = _page(
            _row(title="", path="Artist/Album/01.mp3"),
            _row(title="", path="Artist/Album/Disc 2/01.mp3"),
        )
        section = _section(page, "unsure")
        self.assertIn("<td>Artist/Album</td><td>01.mp3</td>", section)
        self.assertIn("<td>Artist/Album</td><td>Disc 2/01.mp3</td>", section)

    def test_the_file_is_the_whole_path_for_the_root_or_an_edited_row(self) -> None:
        for album, path in ((".", "01.mp3"), ("Artist/Album", "Elsewhere/01.mp3")):
            with self.subTest(album=album):
                section = _section(_page(_row(album=album, path=path)), "unsure")
                self.assertIn(f"<td>{album}</td><td>{path}</td>", section)

    def test_the_file_drops_an_album_written_tsv_safe(self) -> None:
        # The marker keeps a tab and a surrogate; the row's path holds them made safe.
        row = _row(album="A\tB/caf\udce9", path="A B/caf\\udce9/01.mp3")
        section = _section(_page(row), "unsure")
        self.assertIn("<td>01.mp3</td>", section)

    def test_an_empty_field_shows_a_dash(self) -> None:
        row = ReadRow("A/B", Verdict.NOT_MEASURED, "timed out", "", "", "", "", "A/B/x")
        section = _section(_page(row), "not-measured")
        self.assertEqual(section.count(f"<td>{ssp.DASH}</td>"), 1)
        self.assertIn("<td>x</td>", section)
        self.assertEqual(section.count(f'<td class="num">{ssp.DASH}</td>'), 3)

    def test_rows_sort_by_album_then_track_path(self) -> None:
        page = _page(
            _row(album="B", title="a", path="B/01.mp3"),
            _row(album="A", title="z", path="A/02.mp3"),
            _row(album="A", title="y", path="A/01.mp3"),
        )
        section = _section(page, "unsure")
        found = [section.index(f"<td>{title}</td>") for title in ("y", "z", "a")]
        self.assertEqual(found, sorted(found))


class HeaderTest(sf.IsolatedCase):
    """The header names when the page was written and the list's file name."""

    def test_the_header_names_the_time_and_the_list(self) -> None:
        page = _page()
        self.assertIn("2026-10-19 at 09:30 UTC", page)
        self.assertIn(_LIST, page)

    def test_a_list_name_is_escaped(self) -> None:
        page = _page(name="<b>&\udce9.tsv")
        self.assertIn("&lt;b&gt;&amp;\\udce9.tsv", page)
        self.assertNotIn("<b>", page)


class EscapeTest(sf.IsolatedCase):
    """A hostile title, album or reason is shown as text, never as markup."""

    def test_hostile_fields_render_escaped(self) -> None:
        row = _row(
            Verdict.RIPPING_MISTAKE,
            album="<script>alert(\"x\")</script>/A & 'B'",
            title="<img onerror=x>\x1b[31m\x07",
            reason='"quoted" & <b>',
        )
        page = _page(row)
        pf.assert_well_formed(self, page)
        self.assertIn(
            "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;/A &amp; &#x27;B&#x27;",
            page,
        )
        self.assertIn("&lt;img onerror=x&gt;\\x1b[31m\\x07", page)
        self.assertIn("&quot;quoted&quot; &amp; &lt;b&gt;", page)
        self.assertNotIn("\x1b", page)

    def test_a_layout_character_shows_as_the_terminal_shows_it(self) -> None:
        album = "Turn\u202eed\u061c/Album"
        page = _page(_row(album=album, title="Back\u2028Again"))
        self.assertIn(html.escape(spell_unsafe(album)), page)
        self.assertIn("Turn\\u202eed\\u061c/Album", page)
        self.assertIn("Back\\u2028Again", page)
        self.assertNotIn("\u202e", page)

    def test_a_surrogate_escaped_album_shows_its_escape(self) -> None:
        page = _page(_row(album="caf\udce9/Album"))
        self.assertIn("caf\\udce9/Album", page)
        page.encode("utf-8")  # raises UnicodeEncodeError when a surrogate remains
