"""The Genius check's judging: what a track's keyed search hits say Genius has.

`judge_hits` reads one track's hits (`lyrics_genius_search.SearchHit`, in Genius's
order) against the track's query by lyricsmith's own matcher, the rules the Genius step
holds its songs to (`lyrics_genius.song_matches`): title, artist and version, with a
file stem's readings when the track has no tags. A track is:

- **has it** when a hit matches and Genius holds its lyrics: complete, and not
  instrumental. The first such hit is named.
- **unsure** when a hit matches but its lyrics are not complete (`NOT_COMPLETE`) or it
  is instrumental (`INSTRUMENTAL`), the first match named; or when no hit matches but
  one is close (`NEAR_MISS`, `is_near`), the first close one named.
- **not on Genius** only when the search gave a clear, complete answer that nothing
  is close, no hits at all included: every hit it listed was read as a song, it listed
  fewer than a full page (`lyrics_genius_search.SEARCH_PER_PAGE`), the track was asked
  by its tags, and no hit is by the track's artist. Otherwise a track with nothing close
  is **unsure**: a hit could not be read (`HIT_UNREADABLE`), the page was full and the
  track may sit past it (`FULL_PAGE`), the term was a file name's guess
  (`BY_FILE_NAME`), or Genius knows the artist under other titles (`ARTIST_FOUND`, the
  artist's first hit named), in that order.
  `not-on-genius.txt` may drive removals later, so any doubt is unsure.

**Close** is a lower bar than a match, for a person to look at, never to act on. A hit
is close to a reading that names an artist when its title matches at the normal bar but
its artist does not, or when its artist matches at the normal bar
(`lyrics_match.ARTIST_MIN_SIMILARITY`) and its title is at least
`NEAR_TITLE_MIN_SIMILARITY` alike. To a reading with no artist, the stem whole or
without its track number, a hit is close when its title is at least that alike.
Nothing here prints, reads or writes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_genius import song_hit, song_matches
from lyrics_genius_search import SearchHit, SearchPage
from lyrics_match import (
    ARTIST_MIN_SIMILARITY,
    Hit,
    artist_key,
    filename_readings,
    has_artist,
    similarity,
    title_key,
    title_score,
)
from lyrics_model import TrackQuery

#: How alike two titles must be for a hit to be close, not matched: well under the
#: match's own bar (`lyrics_match.TITLE_MIN_SIMILARITY`), so a reworded or shortened
#: title reaches a person, and over a half, so a title sharing a word or two does not.
NEAR_TITLE_MIN_SIMILARITY: Final = 0.6
#: The reasons a track is unsure from its hits alone.
NEAR_MISS: Final = "near miss"
NOT_COMPLETE: Final = "matched, lyrics not complete"
INSTRUMENTAL: Final = "matched, instrumental"
#: The reasons a track with no close hit is unsure, not lacking: the answer had a doubt.
HIT_UNREADABLE: Final = "a hit could not be read"
FULL_PAGE: Final = "no close hit in the first page"
BY_FILE_NAME: Final = "no close hit, searched by file name"
ARTIST_FOUND: Final = "artist found, no close title"


class Finding(StrEnum):
    """Which list a checked track goes in."""

    HAS_IT = "has it"
    NOT_ON_GENIUS = "not on Genius"
    UNSURE = "unsure"


@dataclass(frozen=True)
class Judged:
    """A track's finding, why it is unsure (None otherwise), and the hit it names."""

    finding: Finding
    reason: str | None
    hit: SearchHit | None


def _titles_alike(reading: TrackQuery, hit: Hit) -> float:
    """How alike *reading*'s title and *hit*'s are, by their comparison keys."""
    return similarity(title_key(reading.title), title_key(hit.title))


def _artist_matches(reading: TrackQuery, hit: Hit) -> bool:
    """Whether *hit*'s first credit, or its credits joined, is *reading*'s artist."""
    mine = artist_key(reading.artist)
    # A song's hit always holds one credit at least (`lyrics_genius.song_hit`).
    credits = (hit.artists[0], " & ".join(hit.artists))
    return any(
        similarity(mine, artist_key(credit)) >= ARTIST_MIN_SIMILARITY
        for credit in credits
    )


def _near_reading(reading: TrackQuery, hit: Hit) -> bool:
    """Whether *hit* is close to the one *reading*, by the rules in the docstring."""
    near_title = _titles_alike(reading, hit) >= NEAR_TITLE_MIN_SIMILARITY
    if not has_artist(reading):
        return near_title
    if title_score(reading, hit) is not None:
        return True
    return near_title and _artist_matches(reading, hit)


def is_near(query: TrackQuery, hit: SearchHit) -> bool:
    """Whether *hit* is close to *query*'s track: to the query, or one stem reading."""
    readings = (query,) if has_artist(query) else filename_readings(query)
    matched = song_hit(hit)
    return any(_near_reading(reading, matched) for reading in readings)


def _matched(matches: Sequence[SearchHit]) -> Judged:
    """The finding for a track whose hits *matches* match it: the first with lyrics."""
    for hit in matches:
        if hit.has_lyrics:
            return Judged(Finding.HAS_IT, None, hit)
    first = matches[0]
    reason = INSTRUMENTAL if first.instrumental else NOT_COMPLETE
    return Judged(Finding.UNSURE, reason, first)


def _nothing_close(query: TrackQuery, page: SearchPage) -> Judged:
    """The finding for a track no hit of *page* is close to: lacking only when clear."""
    doubts = (
        (page.skipped, HIT_UNREADABLE),
        (page.full, FULL_PAGE),
        (query.from_filename, BY_FILE_NAME),
    )
    reason = next((why for doubt, why in doubts if doubt), None)
    if reason is not None:
        return Judged(Finding.UNSURE, reason, None)
    # Genius knowing the artist but no title near this one is as likely a title tag
    # that misnames the song as a song Genius lacks, so it is doubt too, the artist's
    # first hit named for a person to look at. A file stem names no artist to find.
    known = next(
        (hit for hit in page.hits if _artist_matches(query, song_hit(hit))), None
    )
    if known is not None:
        return Judged(Finding.UNSURE, ARTIST_FOUND, known)
    return Judged(Finding.NOT_ON_GENIUS, None, None)


def judge_hits(query: TrackQuery, page: SearchPage) -> Judged:
    """What *page*, one search's hits in Genius's order, says of *query*'s track."""
    hits = page.hits
    matches = [hit for hit in hits if song_matches(query, hit)]
    if matches:
        return _matched(matches)
    near = next((hit for hit in hits if is_near(query, hit)), None)
    if near is not None:
        return Judged(Finding.UNSURE, NEAR_MISS, near)
    return _nothing_close(query, page)
