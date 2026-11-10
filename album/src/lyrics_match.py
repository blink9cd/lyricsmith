"""The matcher: whether a provider's search hit is the track asked for, and how close it is.

A wrong match is the failure that matters most. It embeds another song's words, and
nothing later in a run can tell them from the right ones: the check compares timing and
the backup, not meaning. So a hit is accepted only when every rule below holds, and a
near-miss is refused rather than ranked low.

- **Title.** Both titles are reduced to a comparison key (`title_key`): accents and
  case folded, bracketed groups such as ``(Remastered 2011)`` or ``(feat. X)`` removed
  wherever they sit, a `` - `` qualifier and a trailing featuring credit cut, ``&`` read
  as "and", and every character that is not a letter or a digit dropped. A featuring
  credit is cut only where it is written as one: ``feat.`` or ``ft.`` with the period,
  or the word ``featuring``. A bare ``feat`` or ``ft`` is the title's own word, so ``A
  Feat of Clay`` keeps its whole key, while ``Up - 2011 Remaster`` and ``Go feat.
  Guest`` still reduce to ``up`` and ``go``. The keys must be at least
  `TITLE_MIN_SIMILARITY` alike, and hold the same unbracketed digit runs, so ``Song 2``
  is never ``Song 3``. Digits inside brackets are removed with their group, and Roman
  numerals are not digits, so ``Song (Part 2)`` against ``Song (Part 3)``, or ``Song
  II`` against ``Song III``, falls to the length check.
- **Artist.** Reduced the same way, a leading "the" dropped too (`artist_key`). The
  track's artist must be `ARTIST_MIN_SIMILARITY` alike to the hit's first credited
  artist, or to all of its credits together.
- **Version.** A hit whose title names a version the track's does not (`VERSION_WORDS`:
  live, remix, acoustic ...; or a `VERSION_MARKERS` term such as 伴奏, a backing track)
  is refused, since the words of a live take or a remix are not the studio track's, and
  bracket removal alone would make them compare equal.
- **Length.** When both lengths are known, they must agree within `DURATION_TOLERANCE_S`.

`match_score` applies every rule. `title_score` applies all but the artist rule, for a
query with no artist that a provider answers with a single hit of its own choosing
(Genius, asked by a file name). Such a query's title is a file stem, which usually
holds more than the title, so `filename_readings` gives the ways to read it: whole,
without a leading track number, and as ``Artist - Title``. The caller scores the
title-only readings with `title_score` and an artist reading with `match_score`.
`album_score` matches an album, not a track: the title and artist rules alone, since
each of its tracks is judged again with `match_score` once the album's page is read.

Titles that differ only inside brackets therefore compare equal unless the length or
a version word tells them apart: that is the trade for matching ``Song`` to ``Song
(2011 Remaster)``.

Pure and standard-library only: no I/O, nothing printed.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import TypeVar

from lyrics_model import TrackQuery

_T = TypeVar("_T")

#: How alike two title keys must be (difflib's ratio, 0 to 1). High, because a near
#: title is usually another song: at 0.9 one changed letter in a ten-letter title passes,
#: two do not.
TITLE_MIN_SIMILARITY = 0.9
#: How alike two artist keys must be. As strict as titles: a near name is usually
#: another act, and spelling variants (case, accents, punctuation) fold away first.
ARTIST_MIN_SIMILARITY = 0.9
#: How far apart two lengths may be, in seconds. A tag's length and a catalog's differ
#: by a second or two for one recording (encoder padding, rounding to whole seconds);
#: a live take, an edit or a remix almost always differs by more.
DURATION_TOLERANCE_S = 3.0
#: Words naming a version other than the studio recording. A hit with one the track's
#: own title lacks is refused, whatever its length.
VERSION_WORDS = frozenset(
    {
        "acoustic",
        "cover",
        "demo",
        "instrumental",
        "karaoke",
        "live",
        "mix",
        "remix",
        "reprise",
        "unplugged",
    }
)
#: Chinese terms naming a version other than the studio recording, matched anywhere in
#: a title: Chinese writes no space between words, so ``Live版`` or ``伴奏版`` holds them
#: inside a longer run. A hit with one the track's own title lacks is refused.
VERSION_MARKERS = frozenset(
    {
        "伴奏",  # backing track: the music with the vocal removed
        "现场",  # live
        "翻唱",  # cover
        "纯音乐",  # instrumental ("pure music")
    }
)
#: The longest name compared, in characters. Catalog names are far shorter; the cap
#: bounds the folding and difflib's quadratic comparison on a hostile reply.
MAX_NAME_CHARS = 512

#: One bracketed group with no group inside it; removed repeatedly, innermost first.
_GROUP = re.compile(r"\([^()]*\)|\[[^\[\]]*\]|\{[^{}]*\}")
#: A spaced dash: the separator before a qualifier, and between a file name's fields.
_SPACED_DASH = r"\s+[-–—]\s"
#: A `` - Live at ...`` or `` - 2011 Remaster`` qualifier, to the end of the name.
_DASH_QUALIFIER = re.compile(_SPACED_DASH + ".*", re.DOTALL)
#: Where a file stem splits into fields such as ``Artist - Title``.
_STEM_FIELD_BREAK = re.compile(_SPACED_DASH + r"\s*")
#: A leading track number on a file stem, with what separates it from the rest:
#: ``03 ``, ``03 - ``, ``03. ``, ``03_``, or a disc-and-track ``1-03 ``. Something must
#: follow, so a stem that is only a number keeps it.
_TRACK_NUMBER = re.compile(r"^\s*(?:\d{1,2}[-.])?\d{1,3}(?:\s*[-._]\s*|\s+)(?=\S)")
#: A featuring credit after the name itself, to the end of the name: ``feat.`` or
#: ``ft.`` with the period, or the word ``featuring``. The period is what tells a
#: credit from a title's own word (``A Feat of Clay``). Matched on folded text.
_FEATURING = re.compile(r"(?<=\S)\s+(?:feat\.|ft\.|featuring\b).*", re.DOTALL)
#: A leading article on an artist's name.
_LEADING_THE = re.compile(r"the\s+")
_NUMBER = re.compile(r"\d+")
#: A run of Latin letters. Not ``\w``, which is Unicode: it reads ``Live版`` as one word
#: and misses the ``live`` in it.
_LATIN_WORD = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class Hit:
    """A search hit as the matcher reads it: its title, credited artists and length.

    *artists* is in credit order; a source that gives one string gives one entry.
    *duration_s* is None when the source gave no length.
    """

    title: str
    artists: tuple[str, ...]
    duration_s: float | None


# --- Comparison keys ---------------------------------------------------------------------


def _fold(text: str) -> str:
    """*text* with accents removed and case folded, as compatibility characters."""
    decomposed = unicodedata.normalize("NFKD", text)
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return bare.casefold()


def _without_groups(text: str) -> str:
    """*text* with every bracketed group removed, nested ones included."""
    while True:
        shorter = _GROUP.sub(" ", text)
        if shorter == text:
            return text
        text = shorter


def _letters_and_digits(text: str) -> str:
    """*text*'s letters and digits alone, ``&`` read as "and"."""
    return "".join(c for c in text.replace("&", " and ") if c.isalnum())


def _core(folded: str) -> str:
    """A folded name with its groups, a dash qualifier and a featuring credit removed."""
    return _FEATURING.sub("", _DASH_QUALIFIER.sub("", _without_groups(folded)))


def _key(text: str, trim: Callable[[str], str]) -> str:
    """The comparison key of *text*, its folded core reduced by *trim*.

    Falls back, so only blank text has an empty key: to the whole folded name when
    the qualifiers were all there was (a title such as ``(Intro)``), then to the raw
    characters when the name has no letter or digit at all.
    """
    raw = text[:MAX_NAME_CHARS]
    folded = _fold(raw)
    key = _letters_and_digits(trim(_core(folded))) or _letters_and_digits(folded)
    return (key or "".join(raw.casefold().split()))[:MAX_NAME_CHARS]


# fuzz-target
def title_key(title: str) -> str:
    """*title* as the matcher compares it; empty only for a blank title."""
    return _key(title, lambda core: core)


# fuzz-target
def artist_key(artist: str) -> str:
    """*artist* as the matcher compares it, a leading "the" dropped; empty only if blank."""
    return _key(artist, lambda core: _LEADING_THE.sub("", core.strip(), count=1))


def similarity(left: str, right: str) -> float:
    """How alike two keys are, from 0 to 1: difflib's ratio, with no junk heuristic."""
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


# --- The rules ---------------------------------------------------------------------------


def has_artist(query: TrackQuery) -> bool:
    """True when *query* names an artist, without which no hit can be confirmed.

    A query without one is a file stem standing in for a missing artist or title tag.
    """
    return bool(query.artist.strip())


def strip_track_number(stem: str) -> str:
    """*stem* without a leading track number (``03 - Song`` as ``Song``), else as it is.

    A stem that is a number alone keeps it, since nothing would follow.
    """
    return _TRACK_NUMBER.sub("", stem, count=1)


# fuzz-target
def filename_readings(query: TrackQuery) -> tuple[TrackQuery, ...]:
    """The ways *query*'s title, a file stem standing in for missing tags, can be read.

    Each reading is *query* with its artist and title replaced, every other field
    kept, so a length still applies. In order: the stem whole as the title, with no
    artist; the stem without a leading track number (``03 - Song`` as ``Song``), when
    it had one; then, if that splits at spaced dashes, the last field as the title
    with each earlier field that is not a bare number as the artist (``Artist -
    Song``). A blank stem has no reading, and no reading has a blank title.
    """
    stem = query.title
    if not stem.strip():
        return ()
    whole = replace(query, artist="")
    rest = strip_track_number(stem)
    numberless = (replace(whole, title=rest),) if rest != stem else ()
    *fields, title = (part.strip() for part in _STEM_FIELD_BREAK.split(rest))
    if not title:
        return (whole, *numberless)
    splits = tuple(
        replace(whole, artist=field, title=title)
        for field in fields
        if field and not field.isdigit()
    )
    return (whole, *numberless, *splits)


def usable_length(duration_s: float | None) -> float | None:
    """*duration_s* when it is a finite positive length, else None."""
    if duration_s is None or not math.isfinite(duration_s) or duration_s <= 0:
        return None
    return duration_s


def _length_gap(query: TrackQuery, hit: Hit) -> float | None:
    """How far apart the two lengths are, or None when either is unknown."""
    mine, theirs = usable_length(query.duration_s), usable_length(hit.duration_s)
    return None if mine is None or theirs is None else abs(mine - theirs)


def _version_words(title: str) -> frozenset[str]:
    """The `VERSION_WORDS` *title* names as Latin words, and the `VERSION_MARKERS` in it."""
    folded = _fold(title[:MAX_NAME_CHARS])
    words = frozenset(_LATIN_WORD.findall(folded)) & VERSION_WORDS
    return words | {marker for marker in VERSION_MARKERS if marker in folded}


def _artist_similarity(artist: str, credits: tuple[str, ...]) -> float:
    """How alike *artist* is to the first credit, or to all credits together."""
    if not credits:
        return 0.0
    mine = artist_key(artist)
    candidates = {artist_key(credits[0]), artist_key(" & ".join(credits))}
    return max(similarity(mine, theirs) for theirs in candidates)


def _title_similarity(title: str, theirs: str) -> float:
    """How alike two titles are; 0 when their keys hold different digit runs."""
    mine_key, their_key = title_key(title), title_key(theirs)
    if _NUMBER.findall(mine_key) != _NUMBER.findall(their_key):
        return 0.0
    return similarity(mine_key, their_key)


def title_score(query: TrackQuery, hit: Hit) -> float | None:
    """How closely *hit*'s title names *query*'s, or None if refused; artists unread.

    The title, version and length rules alone, for a provider that answers a query
    with no artist (a track asked by its file name) with one hit of its own choosing.
    A query with a blank title matches nothing.
    """
    if not title_key(query.title):
        return None
    if _version_words(hit.title) - _version_words(query.title):
        return None
    gap = _length_gap(query, hit)
    if gap is not None and gap > DURATION_TOLERANCE_S:
        return None
    title = _title_similarity(query.title, hit.title)
    return title if title >= TITLE_MIN_SIMILARITY else None


# fuzz-target
def match_score(query: TrackQuery, hit: Hit) -> float | None:
    """How closely *hit* names *query*'s track (higher is closer), or None if refused.

    A query with a blank artist or title matches nothing: there is nothing to check
    a hit against.
    """
    if not artist_key(query.artist):
        return None
    title = title_score(query, hit)
    if title is None:
        return None
    artist = _artist_similarity(query.artist, hit.artists)
    return title + artist if artist >= ARTIST_MIN_SIMILARITY else None


def album_title_score(album: str, theirs: str) -> float | None:
    """How closely the title *theirs* names *album*, or None if refused or blank.

    The title rule alone, no version rule: each track is judged again by `rank_hits`.
    """
    title = _title_similarity(album, theirs) if title_key(album) else 0.0
    return title if title >= TITLE_MIN_SIMILARITY else None


def album_score(
    album: str, artist: str, theirs: str, credits: tuple[str, ...]
) -> float | None:
    """How closely a release titled *theirs*, by *credits*, is *album* by *artist*.

    `album_title_score` with the artist rule as `match_score` applies it; None if
    refused or the artist is blank. No version rule: `rank_hits` judges each track.
    """
    title = album_title_score(album, theirs) if artist_key(artist) else None
    if title is None:
        return None
    credited = _artist_similarity(artist, credits)
    return title + credited if credited >= ARTIST_MIN_SIMILARITY else None


def rank_hits(
    query: TrackQuery, items: Sequence[_T], describe: Callable[[_T], Hit]
) -> list[_T]:
    """The *items* whose hit matches *query*, closest first.

    Ranked by name score, then by the smaller length gap, an unknown length last;
    full ties keep the provider's own order.
    """
    scored: list[tuple[float, float, int, _T]] = []
    for index, item in enumerate(items):
        hit = describe(item)
        score = match_score(query, hit)
        if score is not None:
            gap = _length_gap(query, hit)
            scored.append((-score, math.inf if gap is None else gap, index, item))
    scored.sort(key=lambda entry: entry[:3])
    return [entry[3] for entry in scored]
