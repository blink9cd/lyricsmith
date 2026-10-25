"""Invented Tekstowo pages for the page parsers' suite: a search page and a song page.

Each is shaped like the probed pages (the search form, the results heading over a card of
song rows, a sidebar of look-alike rows; a song page's JSON-LD, its ``#songText`` and its
``#songTranslation``, or an instrumental's marker box in their place) but holds invented
names and lyrics only; no saved page is used.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

# Re-exported: a suite builds its own JSON-LD scripts through this module.
from http_fakes import ld_script as ld_script

#: The invented song every default names: `genius_fakes.QUERY`'s artist and title.
ARTIST, TITLE = "Invented Artist", "The Keeper Counts"
#: Its site-relative address, two path segments as the parser requires.
HREF = "/invented-artist/the-keeper-counts"
#: Look-alike rows the sidebar lists below the results: other invented songs.
SIDEBAR = (
    ("/sidebar-artist/sidebar-song", "Sidebar Artist - Sidebar Song"),
    ("/other-invented/other-song", "Other Invented - Other Song"),
)
#: The original lyrics' lines and the translation's, for a default song page.
LINES = ("first invented line", "second invented line")
TRANSLATION = ("pierwsza wymyslona linia", "druga wymyslona linia")
#: The box an instrumental's page shows where its lyrics would be, as probed 2026-10-24.
INSTRUMENTAL_BOX = (
    '<div class="box-przeboje">\n\t\tUtwór instrumentalny <a rel="loginbox nofollow" '
    'href="/edytuj-tekst/invented-artist/the-keeper-counts" class="new" '
    'title="Ten utwór ma słowa? Dodaj tekst">Ten utwór ma\n\t\tsłowa? Dodaj tekst</a>'
    "\n\t</div>"
)


def row(href: str, label: str) -> str:
    """One song row: a ``box-przeboje`` div around a ``title``-class link."""
    return (
        '<div class="box-przeboje"><div class="flex-group">'
        f'<a href="{html.escape(href)}" class="title" title="{html.escape(label)}">'
        f"{html.escape(label)}</a></div></div>"
    )


def search_page(
    *entries: tuple[str, str],
    form: bool = True,
    heading: bool = True,
    sidebar: bool = True,
) -> str:
    """An invented search page listing *entries*, each an ``(href, "Artist - Title")``."""
    search_form = (
        '<form action="/szukaj" method="get" class="search">'
        '<input type="text" name="q"><button type="submit">Szukaj</button></form>'
    )
    results = (
        '<h2 class="mt-3">Znalezione utwory:</h2>' if heading else ""
    ) + f'<div class="card mb-4">{"".join(row(h, t) for h, t in entries)}</div>'
    aside = (
        '<div class="right-column"><h2>Popularne teksty</h2>'
        f"{''.join(row(h, t) for h, t in SIDEBAR)}</div>"
        if sidebar
        else ""
    )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Szukaj</title></head>'
        f"<body>{search_form if form else ''}"
        f'<div class="content">{results}</div>{aside}</body></html>'
    )


def recording(name: object = TITLE, artist: object = ARTIST) -> dict[str, object]:
    """The JSON-LD ``MusicRecording`` object a song page carries."""
    return {
        "@context": "https://schema.org",
        "@type": "MusicRecording",
        "name": name,
        "byArtist": {"@type": "MusicGroup", "name": artist},
    }


def _block(block_id: str, caption: str, lines: Sequence[str]) -> str:
    """A lyrics block: its ``h2`` caption, then an ``.inner-text`` of ``<br />``-joined lines."""
    return (
        f'<div id="{block_id}" class="song-text"><h2>{caption}</h2>'
        f'<div class="inner-text">{"<br />\n".join(lines)}</div></div>'
    )


def song_page(
    *,
    name: str = TITLE,
    artist: str = ARTIST,
    lines: Sequence[str] = LINES,
    translation_lines: Sequence[str] = TRANSLATION,
    song_text: bool = True,
    json_ld: bool = True,
    instrumental: bool = False,
) -> str:
    """An invented song page; the flags leave out ``#songText`` or the JSON-LD.

    *instrumental* gives the page an instrumental's marker box, in place of its lyrics
    block when *song_text* leaves that out.

    Each line is page markup, inserted unescaped, so a test can place an entity or an
    inline element in one.
    """
    script = ld_script(recording(name, artist)) if json_ld else ""
    original = _block("songText", "Tekst piosenki:", lines) if song_text else ""
    translated = _block("songTranslation", "Tłumaczenie:", translation_lines)
    marker = INSTRUMENTAL_BOX if instrumental else ""
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8">{script}</head><body>'
        f'<div class="song-area">{marker}{original}{translated}</div></body></html>'
    )
