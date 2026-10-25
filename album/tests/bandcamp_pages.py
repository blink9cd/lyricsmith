"""Invented Bandcamp pages for the page parsers' suite: an album page and a /music page.

Each is shaped like the pages probed on 2026-10-21 (an album page's JSON-LD script, a
/music page's grid links and its client-items list) but holds invented names and lyrics
only; no saved page is used.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``.
"""

from __future__ import annotations

import html
import json
from collections.abc import Sequence

# Re-exported: a suite builds its own JSON-LD scripts through this module.
from http_fakes import ld_script as ld_script


def track(
    name: str,
    *,
    duration: str | None = "P00H03M21S",
    lyrics: str | None = "the lantern hangs\nabove the ford",
    artist: str | None = None,
) -> dict[str, object]:
    """One JSON-LD track item, shaped like a probed album page's."""
    item: dict[str, object] = {"@type": "MusicRecording", "name": name}
    if duration is not None:
        item["duration"] = duration
    if artist is not None:
        item["byArtist"] = {"@type": "MusicGroup", "name": artist}
    if lyrics is not None:
        item["recordingOf"] = {
            "@type": "MusicComposition",
            "lyrics": {"@type": "CreativeWork", "text": lyrics},
        }
    return item


def album_document(
    tracks: Sequence[dict[str, object]],
    *,
    name: str = "The Lantern Years",
    artist: str = "Invented Band",
) -> dict[str, object]:
    """The JSON-LD object an invented album page carries."""
    return {
        "@context": "https://schema.org",
        "@type": "MusicAlbum",
        "name": name,
        "byArtist": {"@type": "MusicGroup", "name": artist},
        "track": {
            "@type": "ItemList",
            "itemListElement": [
                {"@type": "ListItem", "position": i, "item": t}
                for i, t in enumerate(tracks, 1)
            ],
        },
    }


def album_page(
    tracks: Sequence[dict[str, object]],
    *,
    name: str = "The Lantern Years",
    artist: str = "Invented Band",
    ld: bool = True,
) -> str:
    """An invented album page; *ld* False leaves its JSON-LD out."""
    document = album_document(tracks, name=name, artist=artist)
    script = ld_script(document) if ld else ""
    return (
        f"<!DOCTYPE html><html><head>{script}</head><body><h2>{name}</h2></body></html>"
    )


def grid_link(path: str, title: str) -> str:
    """One /music grid entry: a link around its art and its title paragraph."""
    return (
        f'<li class="music-grid-item"><a href="{html.escape(path)}">'
        f'<div class="art"><img src="https://example.com/a.jpg" alt=""></div>'
        f'<p class="title">\n    {title}\n  </p></a></li>'
    )


def music_page(
    entries: Sequence[tuple[str, str]], client_items: Sequence[tuple[str, str]] = ()
) -> str:
    """An invented /music page: one grid link per (path, title), and the client-items list."""
    links = "".join(grid_link(path, title) for path, title in entries)
    items = [{"page_url": path, "title": title} for path, title in client_items]
    attribute = html.escape(json.dumps(items), quote=True)
    return (
        "<!DOCTYPE html><html><body>"
        f'<ol id="music-grid">{links}</ol>'
        f'<div class="more" data-client-items="{attribute}"></div>'
        "</body></html>"
    )
