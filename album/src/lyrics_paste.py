"""A paste's rules: the text cleaned as Genius's, the track's guards, and the sung judge.

``--mode paste`` writes one text a person copied off a Genius page in a browser, which a
script cannot read (Cloudflare challenges it). `clean_paste` cleans it exactly as a
run cleans Genius's own text (`lyrics_step_answer.normalize_answer` under Genius's
untimed key: its ``<n> Contributors ... Lyrics`` header and ``<n>Embed`` trailer
stripped, blank text, a placeholder and text over the lyrics cap refused), after first
dropping each whole line in `BROWSER_CLUTTER`, which a browser copy of the page picks up
and the scrape never held, then judges it against the track's length as a run judges
an answer (`judge_answer`). Section headers such as ``[Verse 1]`` stay: they give no
words, so the judge reads past them, and they are part of Genius's published text.

`paste_refusal` is the fix's own guard (`lyrics_fix_text.fix_skip_reason`): a track is
taken only when it has a transcript record, holds untimed or provider-timed text alone,
that text is the one the record was judged against, and the transcript heard enough of
the track and did not sing it. The sweep's walker asks the same question to pass over a
row already pasted. `judge_paste` is the fix's sung rule (`lyrics_fix_text.first_sung`):
the paste is sung when at least `lyrics_heard.MIN_HEARD_SHARE` of the word pairs heard
are its own, and never when its words are the track's wrong text.

Pure apart from `paste_refusal`, which reads the track's tags and sidecars.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from lyrics_fix_text import (
    SKIP_NOT_UNTIMED,
    Candidate,
    StoredText,
    first_sung,
    fix_skip_reason,
    text_of,
)
from lyrics_heard import MIN_HEARD_SHARE, judge_words, shown_share, words_of_text
from lyrics_heard_store import HeardRecord
from lyrics_model import Lyrics, LyricsKind, ProviderKey, Source, TrackTags
from lyrics_records import MAX_LYRICS_CHARS, PASTED_BY_HAND
from lyrics_stages import Stages
from lyrics_step_answer import DETAIL_TOO_LARGE as DETAIL_TOO_LARGE
from lyrics_step_answer import judge_answer, normalize_answer

#: Whole lines a browser copy of a Genius lyrics page picks up that Genius's own text
#: never holds, matched as the page renders them, case and all, spaces around them
#: aside. Genius inserts its "You might also like" recommendations panel between the
#: verses inside the lyrics container, so selecting the lyrics copies its heading as a
#: line of its own. Extend only with a line Genius renders inside that container, never
#: one a song might sing.
BROWSER_CLUTTER: Final = ("You might also like",)
#: The key a pasted text is cleaned under: Genius's untimed step's.
PASTE_KEY: Final = ProviderKey(Source.GENIUS, LyricsKind.UNSYNCED)
#: Why a track is refused when the store holds no record of it.
NO_RECORD: Final = "no transcript record in --heard-dir"
#: Why a paste of the track's own wrong text is not sung.
OWN_WRONG_TEXT: Final = "not sung: the pasted text is the track's own wrong text"


def strip_browser_clutter(text: str) -> str:
    """*text* without each whole line in `BROWSER_CLUTTER`; every other line as it was."""
    kept = (
        line
        for line in text.splitlines(keepends=True)
        if line.strip() not in BROWSER_CLUTTER
    )
    return "".join(kept)


def clean_paste(text: str, duration_s: float | None) -> tuple[Lyrics | None, str]:
    """The lyrics *text* gives once cleaned, or None, with why: the attempt's detail.

    Text over the lyrics cap is refused untouched, as a run refuses an answer, before
    any line is read.
    """
    if len(text) <= MAX_LYRICS_CHARS:
        text = strip_browser_clutter(text)
    return judge_answer(normalize_answer(text, PASTE_KEY), duration_s)


def judge_paste(
    lyrics: Lyrics, record: HeardRecord, refused: Lyrics
) -> Candidate | str:
    """The paste to write when *record*'s transcript sings *lyrics*, else why not.

    *refused* is the track's own wrong text, whose words are never sung. A sung paste
    wins as Genius's, its origin `PASTED_BY_HAND`; an unsung one's why says the share
    of the pairs heard that are its own.
    """
    candidate = Candidate(lyrics, Source.GENIUS, PASTED_BY_HAND)
    refused_words = words_of_text(refused.plain_text())
    winner = first_sung([candidate], record.transcript, refused_words)
    if winner is not None:
        return winner
    words = words_of_text(lyrics.plain_text())
    if words == refused_words:
        return OWN_WRONG_TEXT
    heard = judge_words(words, record.transcript)
    return (
        f"not sung: {shown_share(heard)} of the {heard.pairs} word pairs heard are "
        f"the pasted text's, under the {MIN_HEARD_SHARE} needed"
    )


@dataclass(frozen=True)
class Taken:
    """A track a paste takes: its transcript record, and the wrong text it holds."""

    record: HeardRecord
    refused: Lyrics


def paste_refusal(
    stages: Stages,
    album: Path,
    path: Path,
    record: HeardRecord | None,
    lang: str,
    *,
    tags: TrackTags | None = None,
) -> Taken | str:
    """The track at *path* as a paste takes it, with its *record*; else why not.

    *tags* are the track's, when the caller has read them already; else they are read
    here. Raises what reading its tags or sidecars raises.
    """
    if record is None:
        return NO_RECORD
    read = tags if tags is not None else stages.read_tags(path)
    return taken_from(text_of(stages, album, path, read, lang), record)


def taken_from(stored: StoredText, record: HeardRecord) -> Taken | str:
    """The track holding *stored* as a paste takes it, with its *record*; else why not."""
    reason = fix_skip_reason(stored, record)
    if reason is not None or stored.lyrics is None:
        # A track holding no text has a reason too: its lyrics are not untimed only.
        return reason or SKIP_NOT_UNTIMED
    return Taken(record, stored.lyrics)
