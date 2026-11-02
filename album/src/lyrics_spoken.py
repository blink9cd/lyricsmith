"""Whether an album looks spoken, so a fix withholds the removal of a text not sung.

A fix (``--mode fix-text``) removes a stored text when its transcript sings no text
tried. On a stand-up or spoken-word album the transcript is speech, so no text is ever
sung, and the removal would take a text that was right. So a fix
withholds every removal on an album that looks spoken, by either of two signals:

- a genre tag: any track of the album tagged with one of `SPOKEN_GENRES`, compared
  whole, trimmed and case-folded, as `lyrics_genre.no_lyrics_genre` compares. The tags
  are read through mutagen, which names an ID3v1 numeric reference (``(57)`` reads as
  ``Comedy``), so a numeric tag needs no decoding here. A track whose tags cannot be
  read gives no genre.
- most of the album's tracks, strictly more than `NOT_SUNG_SHARE` of them, holding a
  kept ``not sung`` transcript in the store.

A replacement still goes ahead on such an album: a text the transcript sings is
written, since speech that matches a text is that text. Only the removal is withheld.

`spoken_reason` is pure; `album_genres` reads each track's tags.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from lyrics_genre import NO_LYRICS_GENRES, no_lyrics_genre
from lyrics_stages import Stages

#: The genres that mark an album spoken: the ruled two, Comedy and Spoken Word, and
#: the built-in no-lyrics genres, which are stand-up's own spellings. Comedy is here
#: though the no-lyrics list leaves it out: that list decides a miss's outcome, where
#: a musical comedy album must still be looked up; here a wrong match only keeps a
#: text, which costs nothing a later look cannot undo.
SPOKEN_GENRES: tuple[str, ...] = tuple(
    dict.fromkeys(("Comedy", "Spoken Word", *NO_LYRICS_GENRES))
)
#: The share of the album's tracks heard not sung that marks it spoken, when exceeded.
#: Over half, as ruled: on a music album sync finds a wrong text on a track or two,
#: while on a stand-up album nearly every track's speech sings no text, so a majority
#: tells the two apart without a genre tag.
NOT_SUNG_SHARE = 0.5
#: How a withheld removal's reason opens.
SPOKEN_WORD = "spoken word"


def spoken_reason(
    genres: Sequence[Sequence[str]], heard: int, total: int
) -> str | None:
    """Why the album looks spoken, or None when it does not.

    *genres* holds each track's genres, as tagged; *heard* counts the album's tracks
    with a kept ``not sung`` transcript, of its *total* tracks. A spoken genre is
    named first (``spoken word (genre Comedy)``), else the share heard not sung
    (``spoken word (7 of 9 tracks not sung)``).
    """
    for track_genres in genres:
        genre = no_lyrics_genre(track_genres, SPOKEN_GENRES)
        if genre is not None:
            return f"{SPOKEN_WORD} (genre {genre})"
    if total and heard > NOT_SUNG_SHARE * total:
        return f"{SPOKEN_WORD} ({heard} of {total} tracks not sung)"
    return None


def album_genres(stages: Stages, tracks: Sequence[Path]) -> list[tuple[str, ...]]:
    """Each of *tracks*' genres, as tagged; a track whose tags cannot be read gives none."""
    genres: list[tuple[str, ...]] = []
    for path in tracks:
        try:
            genres.append(stages.read_tags(path).genres)
        # An unreadable track is the fix's to report, at its own stage; here it only
        # gives no genre, so the other tracks' tags still decide.
        except Exception:  # noqa: BLE001 - any failure reading a track's tags
            continue
    return genres
