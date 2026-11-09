"""The shift test: whether a `not sung` transcript sings another text of its album.

Each transcript a hearing found `not sung` is judged again, by the hearing's own
rule unchanged (`lyrics_heard.judge_words`), against every other text its album holds.
A text it sings is a shift: the stored text was a neighbor's song, and the verdict is
right. So is a track whose own stored text is word for word another text of its album
(one `text_fingerprint`), the neighbor keeping it too: its transcript sings no other
text, since its own song is stored nowhere, yet the text is plainly misplaced. Neither
is a suspect miss, kept with its track's genre tags
(`lyrics_spoken.album_genres`), so a cluster of suspect misses in one genre points at
the hearing rather than the text.

**The album's other texts** are read by the fix's own reader
(`lyrics_fix_text.sibling_texts`, which reads and never writes), so every text the fix
would draw as a candidate is one the test tries: each sibling's untimed text, from its
sidecar or else its served untimed frame, and a timed ``.lrc``'s words only, never its
stamps. The fix's language is the album tool's default, since the report takes no
``--lang``, and no run's sources are read, since only the words are judged here. A
sibling that cannot be read is passed over and counted; the test still runs on the
rest. The album's tracks are found as the album tool finds them (`discover_tracks`),
the heard track itself left out; an album that cannot be listed is not tested. The
heard track's own text is read by the same reader (`lyrics_fix_text.text_of`); one that
cannot be read, or holds no words, matches no other.
Nothing here writes or prints.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from album import DEFAULT_LANG
from lyrics_album import AlbumUnusableError, discover_tracks
from lyrics_fix_text import sibling_texts, text_of
from lyrics_heard import (
    judge_words,
    little_heard,
    not_sung,
    text_fingerprint,
    words_of_text,
)
from lyrics_provenance import SourceRecords
from lyrics_spoken import album_genres
from lyrics_stages import Stages
from lyrics_wiring import real_wiring

#: The genre a suspect miss is tallied under when its track holds no genre tag.
NO_GENRE: Final = "no genre tag"


class ShiftOutcome(StrEnum):
    """What the shift test found for one `not sung` transcript."""

    SHIFT = "shift"
    SUSPECT = "suspect miss"
    #: The album could not be listed, so no other text was tried.
    UNTESTED = "not tested"


@dataclass(frozen=True)
class ShiftFound:
    """One shift test: its outcome, the siblings unread, and a suspect miss's genres."""

    outcome: ShiftOutcome
    unread: int
    genres: tuple[str, ...]


@dataclass(frozen=True)
class OtherTexts:
    """The words of each other text of an album, and how many siblings were unread."""

    words: tuple[tuple[str, ...], ...]
    unread: int

    @property
    def fingerprints(self) -> frozenset[str]:
        """Each other text's `text_fingerprint`, read from its words."""
        return frozenset(text_fingerprint(list(words)) for words in self.words)


def tag_stages() -> Stages:
    """The album tool's real stages: the shift test reads tags and sidecars, never writes."""
    return real_wiring().stages


def other_texts(album: Path, track: Path, stages: Stages) -> OtherTexts | None:
    """The words of each text *album*'s tracks but *track* hold; None when it is unlisted."""
    try:
        tracks = discover_tracks(album).tracks
    except (AlbumUnusableError, OSError):
        return None
    others = [mp3 for mp3 in tracks if mp3 != track]
    found = sibling_texts(stages, album, others, DEFAULT_LANG, SourceRecords())
    words = tuple(
        tuple(words_of_text(text.lyrics.plain_text()))
        for text in (*found.texts, *found.timed)
    )
    return OtherTexts(words, len(found.unreadable))


def own_fingerprint(album: Path, track: Path, stages: Stages) -> str | None:
    """*track*'s own stored text's fingerprint; None when unread, absent or wordless."""
    try:
        stored = text_of(stages, album, track, stages.read_tags(track), DEFAULT_LANG)
    # Its own text unread leaves the transcript test alone to judge it.
    except Exception:  # noqa: BLE001 - any failure reading the text matches no other
        return None
    if stored.lyrics is None:
        return None
    text = stored.lyrics.plain_text()
    return text_fingerprint(text) if words_of_text(text) else None


def holds_a_siblings_text(
    album: Path, track: Path, stages: Stages, others: OtherTexts
) -> bool:
    """True when *track*'s own stored text is word for word one of *others*."""
    own = own_fingerprint(album, track, stages)
    return own is not None and own in others.fingerprints


def sings_any(transcript: str, texts: Iterable[tuple[str, ...]]) -> bool:
    """True when *transcript* sings any of *texts*, by the hearing's own rule."""
    for words in texts:
        heard = judge_words(words, transcript)
        if not little_heard(heard) and not not_sung(heard):
            return True
    return False


def genre_tags(stages: Stages, track: Path) -> tuple[str, ...]:
    """*track*'s genre tags, as tagged; `NO_GENRE` alone for none or an unread tag."""
    genres = tuple(g for found in album_genres(stages, (track,)) for g in found)
    return genres or (NO_GENRE,)


def shift_test(transcript: str, track: Path, album: Path, stages: Stages) -> ShiftFound:
    """The shift test of *track*'s `not sung` *transcript* against *album*'s other texts."""
    others = other_texts(album, track, stages)
    if others is None:
        return ShiftFound(ShiftOutcome.UNTESTED, 0, ())
    if sings_any(transcript, others.words) or holds_a_siblings_text(
        album, track, stages, others
    ):
        return ShiftFound(ShiftOutcome.SHIFT, others.unread, ())
    return ShiftFound(ShiftOutcome.SUSPECT, others.unread, genre_tags(stages, track))


def genre_tally(found: Iterable[ShiftFound]) -> dict[str, int]:
    """Each genre tag's count of suspect misses in *found*, a track counted once a tag."""
    tally: dict[str, int] = {}
    for one in found:
        if one.outcome is ShiftOutcome.SUSPECT:
            for genre in dict.fromkeys(one.genres):
                tally[genre] = tally.get(genre, 0) + 1
    return tally
