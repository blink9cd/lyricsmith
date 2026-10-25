"""Tekstowo's pages, read: a search page's songs found, and a song page's original lyrics.

Pure and never raising, as a page is untrusted. A probed search page has the search form, an
``h2`` "Znalezione utwory:" over a card of ``title``-class links titled ``Artist - Title``,
then a sidebar of look-alike links sharing those classes, so the box is found by its heading.
A song page has ``MusicRecording`` JSON-LD and ``#songText`` (an ``h2`` caption, then
``.inner-text`` lines split by ``<br />``); its look-alike ``#songTranslation`` is not read.
An instrumental's page, probed 2026-10-24, has no ``#songText``: a ``box-przeboje`` box
whose text opens with "Utwór instrumentalny" stands in its place.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser

#: The heading over the songs a search found ("songs found").
RESULTS_HEADING = "Znalezione utwory"
#: The site's search form's action; a page without the form is not one of the site's pages.
SEARCH_ACTION = "/szukaj"
#: The id of the block holding a song's original lyrics, and the class of its text.
SONG_TEXT_ID, INNER_TEXT_CLASS = "songText", "inner-text"
#: The class of the box an instrumental's marker stands in, and the words it opens with.
BOX_CLASS, INSTRUMENTAL_MARKER = "box-przeboje", "Utwór instrumentalny"

#: A song's canonical address: two site-relative path segments, nothing else.
_SONG_HREF = re.compile(r"/[^/?#\s]+/[^/?#\s]+")
#: Elements with no end tag, which never change the nesting depth.
_VOID = frozenset(
    "area base br col embed hr img input link meta source track wbr".split()
)
#: The script type a song page's structured data is published under, and its song's type.
_LD_TYPE, _RECORDING_TYPE = "application/ld+json", "MusicRecording"
#: What an older ``_markupbase`` raises on a malformed declaration such as ``<![foo[``.
_GAVE_UP = (AssertionError, NotImplementedError)
#: A run of whitespace in a lyric's markup, which the page renders as one space.
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class SearchEntry:
    """One song the search found: its site-relative address, artist and title."""

    href: str
    artist: str
    title: str


@dataclass(frozen=True)
class SearchPage:
    """Whether the page carried the search form, and the songs found, in page order."""

    has_form: bool
    entries: tuple[SearchEntry, ...]


@dataclass(frozen=True)
class SongPage:
    """A song page: whether it had `#songText`, its JSON-LD title and artist, its lyrics.

    *instrumental* is whether it held an instrumental's marker box.
    """

    has_song_text: bool
    name: str | None
    artist: str | None
    text: str
    instrumental: bool = False


def _entry(href: str | None, label: str | None) -> SearchEntry | None:
    """The entry an `a`'s *href* and `title` name, or `None` when they are not a song's."""
    if href is None or label is None or not _SONG_HREF.fullmatch(href):
        return None
    artist, sep, title = label.partition(" - ")
    if not sep or not artist.strip() or not title.strip():
        return None
    return SearchEntry(href, artist.strip(), title.strip())


class _Depth(HTMLParser):
    """Counts nesting depth (no void elements, never below 0) around ``opened``/``closing``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID:
            self.depth += 1
        self.opened(tag, dict(attrs))

    def handle_endtag(self, tag: str) -> None:
        if tag not in _VOID:
            self.closing(tag)
            self.depth = max(0, self.depth - 1)

    def opened(
        self, tag: str, attrs: dict[str, str | None]
    ) -> None: ...  # depth counts it

    def closing(self, tag: str) -> None: ...  # depth still counts it


class _SearchReader(_Depth):
    """Reads the search form's presence and the links in the box under the results heading."""

    def __init__(self) -> None:
        super().__init__()
        self.has_form = False
        self.entries: list[SearchEntry] = []
        self._heading: list[str] | None = None
        self._state = "waiting"  # then "armed", "open" and "done", never back
        self._box_depth = 0

    def opened(self, tag: str, attrs: dict[str, str | None]) -> None:
        if tag == "form" and attrs.get("action") == SEARCH_ACTION:
            self.has_form = True
        elif tag == "h2":
            self._heading = []
        elif tag == "div" and self._state == "armed":
            self._state, self._box_depth = "open", self.depth
        elif tag == "a" and self._state == "open":
            if (found := _entry(attrs.get("href"), attrs.get("title"))) is not None:
                self.entries.append(found)

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading.append(data)

    def closing(self, tag: str) -> None:
        if tag == "h2" and self._heading is not None:
            heading = "".join(self._heading).strip()
            if self._state == "waiting" and heading.startswith(RESULTS_HEADING):
                self._state = "armed"
            self._heading = None
        if self._state == "open" and self.depth == self._box_depth:
            self._state = "done"


class _SongReader(_Depth):
    """Reads ``#songText``'s first ``.inner-text`` and the first MusicRecording JSON-LD."""

    def __init__(self) -> None:
        super().__init__()
        self.has_song_text = False
        self.parts: list[str] = []
        self.recording: dict[str, object] | None = None  # the first JSON-LD one
        self._song_depth: int | None = None  # set while inside #songText
        self._text_depth: int | None = None  # set while inside its .inner-text
        self._script: list[str] | None = None
        self._hidden = False  # inside a script or style, whose text is never lyrics
        self.instrumental = False
        self._box_depth: int | None = None  # set while inside a top box-przeboje
        self._box: list[str] = []  # that box's text so far

    def opened(self, tag: str, attrs: dict[str, str | None]) -> None:
        if tag == "br" and self._text_depth is not None:
            self.parts.append("\n")
        elif tag == "script" and (attrs.get("type") or "").strip().lower() == _LD_TYPE:
            self._script = []
        elif tag in ("script", "style"):
            self._hidden = True
        elif attrs.get("id") == SONG_TEXT_ID and not self.has_song_text:
            self.has_song_text, self._song_depth = True, self.depth
        elif self._song_depth is not None and self._text_depth is None:
            if INNER_TEXT_CLASS in (attrs.get("class") or "").split():
                self._text_depth = self.depth
        if self._box_depth is None and BOX_CLASS in (attrs.get("class") or "").split():
            self._box_depth, self._box = self.depth, []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)
        elif self._text_depth is not None and not self._hidden:
            self.parts.append(_SPACES.sub(" ", data))
        if self._box_depth is not None and self._script is None and not self._hidden:
            self._box.append(data)

    def closing(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.recording = self.recording or _recording("".join(self._script))
            self._script = None
        self._hidden = self._hidden and tag not in ("script", "style")
        if self.depth == self._text_depth:  # the text is read once: #songText is done
            self._text_depth = self._song_depth = None
        if self.depth == self._song_depth:
            self._song_depth = None
        if self.depth == self._box_depth:
            box = _SPACES.sub(" ", "".join(self._box)).strip()
            self.instrumental = self.instrumental or box.startswith(INSTRUMENTAL_MARKER)
            self._box_depth = None


def _read[R: HTMLParser](reader: R, html: str) -> R:
    """*reader* fed *html* and closed, keeping what it gathered if it gives up."""
    try:
        reader.feed(html)
        reader.close()
    except _GAVE_UP:  # pragma: no cover - only an older _markupbase raises these
        pass
    return reader


def _assemble(parts: Sequence[str]) -> str:
    """Lines from *parts*: spaces collapsed across tags, each stripped, end blanks dropped."""
    lines = [_SPACES.sub(" ", line).strip() for line in "".join(parts).split("\n")]
    return "\n".join(lines).strip("\n")


def _recording(script: str) -> dict[str, object] | None:
    """The MusicRecording *script* decodes to, alone or first in a list; else None."""
    try:
        value = json.loads(script)
    except (ValueError, RecursionError):  # the second: a nesting too deep to decode
        return None
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, dict) and item.get("@type") == _RECORDING_TYPE:
            return item
    return None


def _name_of(value: object) -> str | None:
    """The ``name`` of *value* when it is a dict holding a string one, else None."""
    name = value.get("name") if isinstance(value, dict) else None
    return name if isinstance(name, str) else None


# fuzz-target
def parse_search_page(html: str) -> SearchPage:
    """Whether *html* carries the search form, and the songs its results box lists."""
    reader = _read(_SearchReader(), html)
    return SearchPage(reader.has_form, tuple(reader.entries))


# fuzz-target
def parse_song_page(html: str) -> SongPage:
    """A song page's ``#songText`` presence, JSON-LD title and artist, and original lyrics."""
    reader = _read(_SongReader(), html)
    found = reader.recording or {}
    by_artist = found.get("byArtist")
    first = by_artist[0] if isinstance(by_artist, list) and by_artist else by_artist
    text = _assemble(reader.parts)
    return SongPage(
        reader.has_song_text,
        _name_of(found),
        _name_of(first),
        text,
        reader.instrumental,
    )
