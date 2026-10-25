"""Reading YouTube Music's decoded replies: search results, the lyrics tab, the lyrics.

Pure: these take the JSON a reply decoded to and return what it holds, never asking
anything. Three replies matter. A songs-filtered search holds the results the matcher
chooses among; the ``next`` reply for the chosen video holds, among its tabs, the
lyrics tab's browse id; and ``browse`` on that id holds the lyrics, either timed (one
cue per line) or plain (one description shelf).

Only a result whose video type is the audio track counts as the song, because a music
video or an upload carries its own cut, often longer, with lyrics timed to it, or none.
Timed lyrics come back as LRC only when every line has a usable start: one untimed line
leaves no honest stamp to give it, so the answer is the lines' words alone, untimed.

A reply whose known containers are missing or of the wrong kind is a `ProviderError`
with the detail ``unexpected reply`` (`lyrics_reply.unexpected_reply`), so a changed
shape shows as an error in the run, never as "nothing found". Inside a known
container, an entry that does not read (an item with no title, a tab with no endpoint)
is passed over, since replies carry entries the tool has no use for.

Nothing here prints.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lyrics_match import Hit
from lyrics_model import Source, SyncedLine, format_lrc
from lyrics_reply import unexpected_reply

#: The video type of a song's audio track; music videos and uploads carry others.
AUDIO_TRACK = "MUSIC_VIDEO_TYPE_ATV"
#: The browse page type of the next reply's lyrics tab.
LYRICS_PAGE = "MUSIC_PAGE_TYPE_TRACK_LYRICS"
#: The browse page type of an artist, which marks a search item's artist runs.
_ARTIST_PAGE = "MUSIC_PAGE_TYPE_ARTIST"

_SOURCE = Source.YOUTUBE_MUSIC
#: A length run, ``m:ss`` or ``h:mm:ss``; bounded so no run reaches int()'s digit limit.
_LENGTH = re.compile(r"(?:([0-9]{1,6}):(?=[0-5][0-9]:))?([0-9]{1,6}):([0-5][0-9])")
#: A start time given as text: decimal digits only. The bound keeps a pathological
#: value from reaching int()'s digit limit; it is still centuries of milliseconds.
_START_TEXT = re.compile(r"[0-9]{1,15}")
_SECONDS_PER_MINUTE = 60
# The key paths the parsers walk, written dotted for reading and split once here.
#: From a search item to its play button's watch endpoint.
_WATCH = tuple(
    "overlay.musicItemThumbnailOverlayRenderer.content.musicPlayButtonRenderer"
    ".playNavigationEndpoint.watchEndpoint".split(".")
)
_VIDEO_TYPE = tuple(
    "watchEndpointMusicSupportedConfigs.watchEndpointMusicConfig.musicVideoType".split(
        "."
    )
)
_PAGE_TYPE = tuple(
    "browseEndpointContextSupportedConfigs.browseEndpointContextMusicConfig.pageType".split(
        "."
    )
)
_COLUMN_RUNS = ("musicResponsiveListItemFlexColumnRenderer", "text", "runs")
_NEXT_TABS = tuple(
    "singleColumnMusicWatchNextResultsRenderer.tabbedRenderer"
    ".watchNextTabbedResultsRenderer.tabs".split(".")
)
_TIMED_LINES = tuple(
    "elementRenderer.newElement.type.componentType.model.timedLyricsModel"
    ".lyricsData.timedLyricsData".split(".")
)
_PLAIN_RUNS = ("contents", 0, "musicDescriptionShelfRenderer", "description", "runs")


@dataclass(frozen=True)
class SongResult:
    """One search result: its video, whether it is the audio track, and what the matcher reads."""

    video_id: str
    audio_track: bool
    hit: Hit


def _at(value: object, *keys: str | int) -> object:
    """The value at *keys* inside *value*, or None where a step is missing.

    A string key steps into an object and an int key into a list; a step into the
    wrong kind of container is missing too.
    """
    for key in keys:
        if isinstance(key, str) and isinstance(value, Mapping):
            value = value.get(key)
        elif isinstance(key, int) and isinstance(value, list) and key < len(value):
            value = value[key]
        else:
            return None
    return value


def _list_at(value: object, *keys: str | int) -> Sequence[object]:
    """The list at *keys* inside *value*; anything else there reads as empty."""
    found = _at(value, *keys)
    return found if isinstance(found, list) else ()


def _text_at(value: object, *keys: str | int) -> str | None:
    """The non-blank string at *keys* inside *value*, else None."""
    found = _at(value, *keys)
    return found if isinstance(found, str) and found.strip() else None


def _required_list(value: object, keys: Sequence[str | int], what: str) -> list[object]:
    """The list at *keys*, which the reply must hold; `unexpected reply` otherwise."""
    found = _at(value, *keys)
    if not isinstance(found, list):
        raise unexpected_reply(_SOURCE, f"{what} is not a list")
    return found


def _length_s(text: str | None) -> float | None:
    """Seconds for a ``m:ss`` or ``h:mm:ss`` length run, else None."""
    match = _LENGTH.fullmatch(text or "")
    if match is None:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return float(
        (hours * _SECONDS_PER_MINUTE + minutes) * _SECONDS_PER_MINUTE + seconds
    )


def _artists(runs: Sequence[object]) -> tuple[str, ...]:
    """The texts of the runs that link to an artist's page, in credit order."""
    return tuple(
        text
        for run in runs
        if _at(run, "navigationEndpoint", "browseEndpoint", *_PAGE_TYPE) == _ARTIST_PAGE
        and (text := _text_at(run, "text")) is not None
    )


def _song(item: object) -> SongResult | None:
    """The result one search item holds, or None when it lacks a title or video id."""
    renderer = _at(item, "musicResponsiveListItemRenderer")
    title = _text_at(renderer, "flexColumns", 0, *_COLUMN_RUNS, 0, "text")
    watch = _at(renderer, *_WATCH)
    video_id = _text_at(watch, "videoId")
    if title is None or video_id is None:
        return None
    runs = _list_at(renderer, "flexColumns", 1, *_COLUMN_RUNS)
    length = _length_s(_text_at(runs[-1], "text")) if runs else None
    hit = Hit(title, _artists(runs), length)
    return SongResult(video_id, _at(watch, *_VIDEO_TYPE) == AUDIO_TRACK, hit)


def _sections(contents: object) -> list[object]:
    """The search's section list, from its one tab or from *contents* directly."""
    if not isinstance(contents, Mapping):
        raise unexpected_reply(_SOURCE, "the search contents are not an object")
    if "tabbedSearchResultsRenderer" in contents:
        tabs = _required_list(
            contents, ("tabbedSearchResultsRenderer", "tabs"), "the tabs"
        )
        contents = _at(tabs, 0, "tabRenderer", "content")
    return _required_list(contents, ("sectionListRenderer", "contents"), "the sections")


# fuzz-target
def parse_search(value: object) -> tuple[SongResult, ...]:
    """The results a search reply holds, none for no contents; `ProviderError` otherwise."""
    if not isinstance(value, Mapping):
        raise unexpected_reply(_SOURCE, "the search answer is not an object")
    if value.get("contents") is None:
        return ()
    items = (
        item
        for section in _sections(value["contents"])
        for item in _list_at(section, "musicShelfRenderer", "contents")
    )
    return tuple(found for item in items if (found := _song(item)) is not None)


# fuzz-target
def parse_lyrics_tab(value: object) -> str | None:
    """The lyrics tab's browse id in a next reply, None for no selectable lyrics tab."""
    tabs = _required_list(_at(value, "contents"), _NEXT_TABS, "the next reply's tabs")
    for tab in tabs:
        endpoint = _at(tab, "tabRenderer", "endpoint", "browseEndpoint")
        browse_id = _text_at(endpoint, "browseId")
        if _at(endpoint, *_PAGE_TYPE) == LYRICS_PAGE and browse_id is not None:
            return browse_id
    return None


def _start_ms(line: object) -> int | None:
    """A timed line's start in milliseconds: a whole number or its decimal text."""
    start = _at(line, "cueRange", "startTimeMilliseconds")
    if isinstance(start, str) and _START_TEXT.fullmatch(start):
        return int(start)
    if isinstance(start, int) and not isinstance(start, bool) and start >= 0:
        return start
    return None


def _timed_text(entries: Sequence[object]) -> tuple[list[str], str]:
    """The timed lines' texts, and the answer: LRC when each has a start, else the texts.

    A line's whitespace is collapsed to single spaces, so a text holding a line break
    still makes one LRC line.
    """
    lines = [
        (" ".join(text.split()), _start_ms(entry))
        for entry in entries
        if isinstance(text := _at(entry, "lyricLine"), str)
    ]
    texts = [text for text, _ in lines]
    starts = [start for _, start in lines if start is not None]
    if len(starts) == len(lines):
        timed = (SyncedLine(start, text) for start, (text, _) in zip(starts, lines))
        answer = format_lrc(tuple(timed))
    else:
        answer = "".join(f"{text}\n" for text in texts)
    return texts, answer


def _plain_text(section_list: object) -> str:
    """The plain lyrics' description runs joined; the shelf's footer credit is not read."""
    return "".join(
        text
        for run in _list_at(section_list, *_PLAIN_RUNS)
        if isinstance(text := _at(run, "text"), str)
    )


def _worded(text: str) -> bool:
    """True when *text* holds a letter or digit."""
    return any(char.isalnum() for char in text)


# fuzz-target
def parse_lyrics(value: object) -> str | None:
    """The lyrics a browse reply holds, timed as LRC or plain; None for none."""
    contents = _at(value, "contents")
    if not isinstance(contents, Mapping):
        raise unexpected_reply(_SOURCE, "the lyrics contents are not an object")
    entries = _at(contents, *_TIMED_LINES)
    if isinstance(entries, list):
        texts, answer = _timed_text(entries)
        return answer if _worded("".join(texts)) else None
    plain = _plain_text(_at(contents, "sectionListRenderer"))
    return plain if _worded(plain) else None
