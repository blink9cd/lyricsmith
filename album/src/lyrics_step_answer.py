"""What a chain step accepts from a provider's text: the lyrics, or why none.

`normalize_answer` cleans and parses one answer for its step: a synced step takes only
timed lines, a plain step keeps the words of any answer, and blank text, a placeholder
or text over the lyrics cap is none. `judge_answer` then declines lyrics the track's
duration rules out, by the check's `past end` and `too many words` rows
(`lyrics_check.implausible_flag`), as a placeholder is declined. Each comes back with
the attempt's detail, which says why. `lyrics_fetch` runs them for every step and
re-exports them.

`SameSong` carries one track's fetch past a timed answer declined far past the end
(`far_past_end`): such an answer is another, longer song, and a provider's plain text
of that song is no truer for losing its stamps, so a later plain answer whose words
are its (the check's `word_similarity`, at `MIN_SIMILARITY` or above) is declined too.
A timed answer only a little past the end may be a longer version of the right song,
so its words decline nothing.

Pure: no I/O, no printing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lyrics_check import (
    MIN_SIMILARITY,
    implausible_flag,
    latest_line_s,
    normalize_words,
    past_end,
    shown,
    word_similarity,
)
from lyrics_model import (
    Lyrics,
    LyricsKind,
    PlainLyrics,
    ProviderKey,
    Source,
    SyncedLyrics,
    parse_lrc,
    remove_word_stamps,
)
from lyrics_placeholder import is_header_line, is_placeholder_lyrics
from lyrics_records import MAX_LYRICS_CHARS

#: Attempt details: why an answer was or was not accepted.
DETAIL_ACCEPTED = "accepted"
DETAIL_NOTHING = "nothing found"
DETAIL_UNTIMED = "no timed line"
DETAIL_PLACEHOLDER = "placeholder"
#: Opens the detail of an answer the track's duration rules out (`implausible_flag`).
DETAIL_IMPLAUSIBLE = "implausible"
#: The detail of a plain answer whose words are a far-past timed answer's (`SameSong`),
#: before its similarity.
DETAIL_SAME_SONG = f"{DETAIL_IMPLAUSIBLE}: same song as a declined timed answer"
DETAIL_TOO_LARGE = f"too large: over {MAX_LYRICS_CHARS} characters"

#: Genius's "<n> Contributors ... <title> Lyrics" header, on the text's first line. Greedy
#: to the line's last "Lyrics", since a title may hold the word itself. The trade-off,
#: accepted: were Genius to put the first lyric line on the header's line, and that line
#: held "Lyrics", it would be stripped with the header.
_GENIUS_HEADER = re.compile(r"\A\s*[\d,]+\s*Contributors?[^\n]*Lyrics")
#: Genius's "<n>Embed" trailer, at the very end. The lookbehind starts a match only at
#: the head of a digit run: without it, every digit of a long run was a fresh start that
#: rescanned the rest, so the trim grew with the run's square. It strips the same text,
#: since the leftmost match never starts mid-run.
_GENIUS_TRAILER = re.compile(r"(?<!\d)\d*Embed\s*\Z")


def clean_genius_text(text: str) -> str:
    """*text* without Genius's contributors header and Embed trailer, trimmed.

    Section headers such as ``[Chorus]`` stay: they are part of the published text.
    """
    text = _GENIUS_HEADER.sub("", text, count=1)
    return _GENIUS_TRAILER.sub("", text).strip()


def _line_words(line: str) -> str | None:
    """*line*'s words with its leading stamps dropped, or None for an untimed line."""
    parsed = parse_lrc(line)
    return parsed.lines[0].text if isinstance(parsed, SyncedLyrics) else None


def _partly_timed_words(text: str) -> str | None:
    """Every line's words, in file order, when only some lines of *text* are timed.

    None when every line holding words is timed, so the parsed lyrics serve. Header
    tag lines are dropped; blank lines stay, so stanza breaks survive. Holding a timed
    line makes the text LRC, so an untimed line loses its word stamps as a timed one
    does (`remove_word_stamps`); one holding none is kept as it is.
    """
    kept: list[str] = []
    partly = False
    for line in text.splitlines():
        if is_header_line(line):
            continue
        words = _line_words(line)
        if words is None and line.strip():
            partly = True
        kept.append(remove_word_stamps(line) if words is None else words)
    return "\n".join(kept) if partly else None


def _plain_answer(text: str, parsed: Lyrics) -> PlainLyrics:
    """The untimed lyrics a plain step keeps from *text*, parsed as *parsed*.

    A timed answer keeps its parsed words, stamps dropped. One with a stray stamped
    line parses as timed too, yet holds untimed lines, so it keeps every line's words.
    """
    partly = _partly_timed_words(text) if isinstance(parsed, SyncedLyrics) else None
    return PlainLyrics((parsed.plain_text() if partly is None else partly).strip())


def normalize_answer(text: str | None, key: ProviderKey) -> tuple[Lyrics | None, str]:
    """The lyrics a step accepts from *text*, or None, with the attempt's detail.

    A synced step accepts only text with a timed line; untimed text is demoted, so it
    is accepted only at a plain step. A plain step keeps the words of a timed answer
    and drops the stamps (`_plain_answer`). Blank text and placeholders
    (`is_placeholder_lyrics`) are not found.
    Text over `MAX_LYRICS_CHARS` is refused first, untouched: the cleanup, the parse
    and the later check all grow faster than the text, and none runs under a deadline.
    """
    if text is not None and len(text) > MAX_LYRICS_CHARS:
        return None, DETAIL_TOO_LARGE
    if key.source is Source.GENIUS and text is not None:
        text = clean_genius_text(text)
    if text is None or not text.strip():
        return None, DETAIL_NOTHING
    parsed = parse_lrc(text)
    if is_placeholder_lyrics(parsed):
        return None, DETAIL_PLACEHOLDER
    if key.kind is LyricsKind.SYNCED:
        if isinstance(parsed, SyncedLyrics):
            return parsed, DETAIL_ACCEPTED
        return None, DETAIL_UNTIMED
    return _plain_answer(text, parsed), DETAIL_ACCEPTED


def judge_answer(
    found: tuple[Lyrics | None, str], duration_s: float | None
) -> tuple[Lyrics | None, str]:
    """*found*, unless the track's duration rules its lyrics out: then None, and why.

    Declined as a placeholder is, by the check's `past end` or `too many words` row
    (`implausible_flag`), named in the detail. A *duration_s* that cannot be read
    declines nothing.
    """
    lyrics = found[0]
    flag = None if lyrics is None else implausible_flag(lyrics, duration_s)
    if flag is None:
        return found
    return None, f"{DETAIL_IMPLAUSIBLE}: {flag.rule} ({flag.detail})"


#: How many times the track's length a declined timed answer's latest line must start
#: beyond for its words to be another song's. Past the end by a little is a longer
#: version of the right song (a live take, an extended mix), whose plain text is still
#: this track's words; past half the track's length again, a whole other song's lyrics
#: sit on a short interlude, as the library scan found.
FAR_PAST_END_FACTOR = 1.5


def far_past_end(lyrics: Lyrics, duration_s: float | None) -> bool:
    """True when *lyrics* are timed, declined as `past end`, and run far past it.

    Far past: the latest line starts beyond `FAR_PAST_END_FACTOR` times *duration_s*.
    A duration that cannot be read declines nothing, so nothing is far past it.
    """
    if not isinstance(lyrics, SyncedLyrics) or duration_s is None:
        return False
    if past_end(lyrics, duration_s) is None:
        return False
    return latest_line_s(lyrics) > FAR_PAST_END_FACTOR * duration_s


@dataclass
class SameSong:
    """One track's fetch: the words of each timed answer declined far past the end.

    `judge` stands in for `judge_answer` at every step of the fetch, its stripped-title
    retry included, so a plain answer of a song already declined is declined again.
    """

    #: Each far-past answer's words, as the similarity compares them.
    declined: list[list[str]] = field(default_factory=list)

    def judge(
        self, found: tuple[Lyrics | None, str], duration_s: float | None
    ) -> tuple[Lyrics | None, str]:
        """*found* judged as `judge_answer` judges it, then against the songs declined.

        A timed answer declined far past the end (`far_past_end`) is kept; a plain
        answer as alike to one kept as two transcriptions of one song is declined.
        """
        lyrics, detail = judge_answer(found, duration_s)
        answer = found[0]
        if lyrics is None:
            if answer is not None and far_past_end(answer, duration_s):
                self.declined.append(normalize_words(answer.plain_text()))
            return lyrics, detail
        if isinstance(lyrics, SyncedLyrics) or not self.declined:
            return lyrics, detail
        words = normalize_words(lyrics.plain_text())
        alike = max(word_similarity(words, kept) for kept in self.declined)
        if alike < MIN_SIMILARITY:
            return lyrics, detail
        return None, f"{DETAIL_SAME_SONG} ({shown(alike)})"
