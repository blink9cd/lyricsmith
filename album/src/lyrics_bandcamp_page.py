"""Bandcamp's pages, read: an album page's track list and an artist's /music album list.

Pure, standard library only, and never raising, since a page is untrusted: anything of
the wrong type or shape is skipped. An album page is read from its ``MusicAlbum`` JSON-LD
(each track's name, length, credit, lyrics), not markup each redesign changes; a /music
page, from its ``/album/`` links' ``title``-class text and its ``data-client-items``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

from lyrics_match import Hit, usable_length

#: The script type an album page's structured data is published under, and its album's type.
_LD_TYPE, _ALBUM_TYPE = "application/ld+json", "MusicAlbum"
#: What an older ``_markupbase`` raises on a malformed declaration such as ``<![foo[``.
_GAVE_UP = (AssertionError, NotImplementedError)
#: The path every Bandcamp album page sits under, so the prefix of every album link
#: on a /music page; the Bandcamp step and the MusicBrainz link reader read it here.
ALBUM_PATH = "/album/"
#: The attribute listing a /music page's unrendered albums as JSON, and its tags.
_CLIENT_ITEMS, _CLIENT_ITEM_TAGS = "data-client-items", frozenset({"ol", "div"})
#: An ISO 8601 duration, the ``T`` optional because Bandcamp writes ``P00H03M21S``.
_ISO_DURATION = re.compile(
    r"P(?:(\d+(?:\.\d+)?)D)?T?(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?"
)


@dataclass(frozen=True)
class BandcampTrack:
    """One JSON-LD track: what the matcher reads, and its lyrics when the artist entered them."""

    hit: Hit
    lyrics: str | None


@dataclass(frozen=True)
class BandcampAlbum:
    """An album page's JSON-LD: the album's name and its tracks, in page order."""

    name: str | None
    tracks: tuple[BandcampTrack, ...]


@dataclass(frozen=True)
class MusicEntry:
    """One album an artist's /music page lists: its /album/ path and its title."""

    path: str
    title: str


class _LdScripts(HTMLParser):
    """Gathers the text of every JSON-LD script, in page order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self._open: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        kind = dict(attrs).get("type") or ""
        if tag == "script" and kind.strip().lower() == _LD_TYPE:
            self._open = []

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._open.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._open is not None:
            self.scripts.append("".join(self._open))
            self._open = None


class _MusicLinks(HTMLParser):
    """Gathers a /music page's albums by path, each under the first title found for it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: dict[str, str] = {}
        self._path: str | None = None
        self._title: list[str] | None = None
        self._reading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        named = dict(attrs)
        self._reading = False
        if tag == "a":
            self._path, self._title = _album_path(named.get("href") or "") or None, None
        elif self._path is not None and self._title is None:
            if "title" in (named.get("class") or "").split():
                self._title, self._reading = [], True
        if tag in _CLIENT_ITEM_TAGS and named.get(_CLIENT_ITEMS) is not None:
            for path, title in _client_items(named[_CLIENT_ITEMS] or ""):
                self._add(path, title)

    def handle_data(self, data: str) -> None:
        if self._reading and self._title is not None:
            self._title.append(data)

    def handle_endtag(self, tag: str) -> None:
        self._reading = False
        if tag == "a" and self._path is not None:
            self._add(self._path, "".join(self._title or ()))
            self._path = None

    def _add(self, path: str, title: str) -> None:
        words = " ".join(title.split())
        if path.startswith(ALBUM_PATH) and words:
            self.found.setdefault(path, words)


def _read[R: HTMLParser](reader: R, html: str) -> R:
    """*reader* fed *html* and closed, keeping what it gathered if it gives up."""
    try:
        reader.feed(html)
        reader.close()
    except _GAVE_UP:  # pragma: no cover - only an older _markupbase raises these
        pass
    return reader


def _album_path(url: str) -> str:
    """*url*'s path when it is an album's, else empty; an unparsable URL is empty."""
    try:
        path = urlsplit(url.strip()).path
    except ValueError:
        return ""
    return path if path.startswith(ALBUM_PATH) else ""


def _client_items(text: str) -> list[tuple[str, str]]:
    """The (path, title) pairs a client-items attribute lists; anything else is skipped."""
    items = _json_of(text)
    return [
        (_album_path(item["page_url"]), item["title"])
        for item in (items if isinstance(items, list) else ())
        if isinstance(_at(item, "page_url"), str)
        and isinstance(_at(item, "title"), str)
    ]


def _json_of(text: str) -> object:
    """*text* as JSON, or None when it is not JSON."""
    try:
        return json.loads(text)
    except (ValueError, RecursionError):  # the second: a nesting too deep to decode
        return None


def _at(value: object, *keys: str) -> object:
    """The member *keys* lead to through nested JSON objects, else None."""
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _text_of(value: object) -> str | None:
    """*value* when it is a string with a non-whitespace character, else None."""
    return value if isinstance(value, str) and value.strip() else None


def _album_document(value: object) -> dict[str, object] | None:
    """The object *value* is, or the first a list *value* holds, typed MusicAlbum."""
    for item in value if isinstance(value, list) else [value]:
        kind = _at(item, "@type")
        named = kind == _ALBUM_TYPE or (isinstance(kind, list) and _ALBUM_TYPE in kind)
        if named and isinstance(item, dict):
            return item
    return None


def _track_from(item: object, album_artist: str | None) -> BandcampTrack | None:
    """One track item, credited to its own artist, else the album's; None when unnamed."""
    if (title := _text_of(_at(item, "name"))) is None:
        return None
    artist = _text_of(_at(item, "byArtist", "name")) or album_artist
    duration = _at(item, "duration")
    length = iso_duration_s(duration) if isinstance(duration, str) else None
    lyrics = _text_of(_at(item, "recordingOf", "lyrics", "text"))
    return BandcampTrack(Hit(title, (artist,) if artist else (), length), lyrics)


def _album_from(document: dict[str, object]) -> BandcampAlbum:
    """A MusicAlbum object's name and its usable tracks, in page order."""
    artist = _text_of(_at(document, "byArtist", "name"))
    elements = _at(document, "track", "itemListElement")
    items = elements if isinstance(elements, list) else []
    found = [_track_from(_at(element, "item"), artist) for element in items]
    return BandcampAlbum(_text_of(document.get("name")), tuple(t for t in found if t))


# fuzz-target
def parse_album_page(html: str) -> BandcampAlbum | None:
    """The album the first MusicAlbum JSON-LD script describes; None without one."""
    scripts = _read(_LdScripts(), html).scripts
    documents = (_album_document(_json_of(script)) for script in scripts)
    return next((_album_from(found) for found in documents if found is not None), None)


# fuzz-target
def parse_music_page(html: str) -> tuple[MusicEntry, ...]:
    """The albums a /music page lists, in page order, each path once under its first title."""
    found = _read(_MusicLinks(), html).found
    return tuple(MusicEntry(path, title) for path, title in found.items())


# fuzz-target
def iso_duration_s(text: str) -> float | None:
    """An ISO 8601 duration's length in seconds; None for any other text or a non-positive one."""
    match = _ISO_DURATION.fullmatch(text.strip())
    if match is None or all(group is None for group in match.groups()):
        return None
    d, h, m, s = (float(group or 0) for group in match.groups())
    return usable_length(((d * 24 + h) * 60 + m) * 60 + s)
