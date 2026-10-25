"""Invented YouTube Music replies, in the shapes its search, next and browse answer with.

Each builder returns the decoded JSON a reply would hold, trimmed to the fields the
parsers read plus a few neighbors they must ignore (a "no results" section, an album
run, a related tab, the source credit). Every title, artist and lyric is invented.
"""

from __future__ import annotations

from collections.abc import Sequence

#: The video type of a song's audio track, as the search reply names it.
AUDIO_TRACK = "MUSIC_VIDEO_TYPE_ATV"
#: An official music video's video type.
MUSIC_VIDEO = "MUSIC_VIDEO_TYPE_OMV"
#: A user upload's video type.
UPLOAD = "MUSIC_VIDEO_TYPE_UGC"
#: The page types the replies' browse endpoints carry.
ARTIST_PAGE = "MUSIC_PAGE_TYPE_ARTIST"
ALBUM_PAGE = "MUSIC_PAGE_TYPE_ALBUM"
LYRICS_PAGE = "MUSIC_PAGE_TYPE_TRACK_LYRICS"
RELATED_PAGE = "MUSIC_PAGE_TYPE_TRACK_RELATED"
#: The credit line a lyrics reply carries beside its text.
SOURCE = "Source: Invented Provider"
#: The separator runs the search reply puts between an item's subtitle parts.
_DOT = {"text": " • "}


def _browse(browse_id: str, page_type: str) -> dict[str, object]:
    """A browse endpoint to *browse_id*, of *page_type*."""
    config = {"browseEndpointContextMusicConfig": {"pageType": page_type}}
    return {
        "browseEndpoint": {
            "browseId": browse_id,
            "browseEndpointContextSupportedConfigs": config,
        }
    }


def _column(runs: Sequence[object]) -> dict[str, object]:
    """One flex column holding *runs*."""
    return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": runs}}}


def _subtitle(artists: Sequence[str], length: str) -> list[object]:
    """The second column's runs: the artists, an album, then *length*."""
    runs: list[object] = []
    for index, artist in enumerate(artists):
        if index:
            runs.append({"text": " & "})
        endpoint = _browse(f"UCinvented{index:02d}", ARTIST_PAGE)
        runs.append({"text": artist, "navigationEndpoint": endpoint})
    album = {
        "text": "An Invented Album",
        "navigationEndpoint": _browse("MPREb_x", ALBUM_PAGE),
    }
    return [*runs, _DOT, album, _DOT, {"text": length}]


def song_item(
    title: str,
    artists: Sequence[str],
    length: str = "3:21",
    video_id: str | None = "vid00000001",
    video_type: str = AUDIO_TRACK,
) -> dict[str, object]:
    """One search result; a *video_id* of None leaves the play button without one."""
    watch: dict[str, object] = {
        "watchEndpointMusicSupportedConfigs": {
            "watchEndpointMusicConfig": {"musicVideoType": video_type}
        }
    }
    if video_id is not None:
        watch["videoId"] = video_id
    button = {
        "musicPlayButtonRenderer": {"playNavigationEndpoint": {"watchEndpoint": watch}}
    }
    overlay = {"musicItemThumbnailOverlayRenderer": {"content": button}}
    renderer = {
        "overlay": overlay,
        "flexColumns": [
            _column([{"text": title}]),
            _column(_subtitle(artists, length)),
        ],
        "playlistItemData": {"videoId": "playlistDataNotRead"},
    }
    return {"musicResponsiveListItemRenderer": renderer}


def _section_list(sections: Sequence[object]) -> dict[str, object]:
    """A section list holding *sections*."""
    return {"sectionListRenderer": {"contents": list(sections)}}


def search_reply(*items: object) -> dict[str, object]:
    """A songs-filtered search reply whose one shelf holds *items*, read or not."""
    shelf: dict[str, object] = {
        "musicShelfRenderer": {
            "title": {"runs": [{"text": "Songs"}]},
            "contents": list(items),
        }
    }
    tab = {"tabRenderer": {"content": _section_list([shelf])}}
    return {"contents": {"tabbedSearchResultsRenderer": {"tabs": [tab]}}}


def empty_search_reply() -> dict[str, object]:
    """A search reply holding only the "no results" message section."""
    message: dict[str, object] = {
        "itemSectionRenderer": {"contents": [{"messageRenderer": {}}]}
    }
    tab = {"tabRenderer": {"content": _section_list([message])}}
    return {"contents": {"tabbedSearchResultsRenderer": {"tabs": [tab]}}}


def _lyrics_tab(browse_id: str | None) -> dict[str, object]:
    """The lyrics tab: selectable with an endpoint, or unselectable with none."""
    if browse_id is None:
        return {"tabRenderer": {"title": "Lyrics", "unselectable": True}}
    endpoint = _browse(browse_id, LYRICS_PAGE)
    return {"tabRenderer": {"title": "Lyrics", "endpoint": endpoint}}


def next_reply(browse_id: str | None) -> dict[str, object]:
    """A next reply whose lyrics tab browses to *browse_id*, or has none for None."""
    up_next = {"tabRenderer": {"title": "Up next", "content": {}}}
    related_endpoint = _browse("MPTRt_invented01", RELATED_PAGE)
    related = {"tabRenderer": {"title": "Related", "endpoint": related_endpoint}}
    tabs = [up_next, _lyrics_tab(browse_id), related]
    watch_next = {"watchNextTabbedResultsRenderer": {"tabs": tabs}}
    renderer = {"tabbedRenderer": watch_next}
    return {"contents": {"singleColumnMusicWatchNextResultsRenderer": renderer}}


def _timed_line(text: str, ms: int | None, as_text: bool) -> dict[str, object]:
    """One timed lyric line; a *ms* of None leaves it without a cue range."""
    line: dict[str, object] = {"lyricLine": text}
    if ms is not None:
        start: object = str(ms) if as_text else ms
        line["cueRange"] = {"startTimeMilliseconds": start, "endTimeMilliseconds": "0"}
    return line


def timed_browse_reply(
    lines: Sequence[tuple[str, int | None]],
    source: str = SOURCE,
    as_text: bool = True,
) -> dict[str, object]:
    """A browse reply holding timed *lines*; *as_text* gives each start as a string."""
    entries = [_timed_line(text, ms, as_text) for text, ms in lines]
    return timed_entries_reply(entries, source)


def timed_entries_reply(
    entries: Sequence[object], source: str = SOURCE
) -> dict[str, object]:
    """A browse reply whose timed line list holds *entries* as given, of any shape."""
    data = {"timedLyricsData": list(entries), "sourceMessage": source}
    model = {"timedLyricsModel": {"lyricsData": data}}
    element = {"newElement": {"type": {"componentType": {"model": model}}}}
    return {"contents": {"elementRenderer": element}}


def plain_browse_reply(text: str, source: str = SOURCE) -> dict[str, object]:
    """A browse reply holding untimed *text*, its credit in the shelf's footer."""
    shelf = {
        "description": {"runs": [{"text": text}]},
        "footer": {"runs": [{"text": source}]},
    }
    return {"contents": _section_list([{"musicDescriptionShelfRenderer": shelf}])}
