"""A fix's rules: which tracks it takes, the other texts on the album, and which text wins.

sync keeps the transcript of a track whose untimed text it found not sung
(`lyrics_heard_store`). A fix (``--mode fix-text``) takes such a track only when it still
holds untimed text alone, read as compare reads it (`lyrics_compare.judged_lyrics`: its
sidecar's words, else its served untimed frame), that text's fingerprint is the one the
transcript was judged against, and the transcript, judged again here, heard enough to
judge (`lyrics_heard.little_heard`) and did not sing it (`lyrics_heard.not_sung`).
Otherwise `skip_reason` says why it is left alone, and its record is kept.

The candidates come in a settled order: first every other untimed text stored on the
album (`sibling_texts`, read once before anything is written, so a text one track gives
up is still there for the track it belongs to), then the providers' untimed answers
(`lyrics_fix_step`). `first_sung` judges them in turn with the one rule sync judges by
(`lyrics_heard.judge_words`): the first neither not sung nor too little heard wins. A
candidate whose words are the refused text's, or a text already judged, is passed over,
and none after the winner is drawn, so no provider is asked once a text has won.

A sibling's text keeps the provider the album's runs record for that sibling
(`lyrics_provenance`). One no run recorded is written under `Source.ALBUM_TEXT`,
another track's text: no provider is named that did not give it, the provider that
gave the wrong text no longer decides the track, so a later compare reads it as
unrecorded, as the sibling was, and the outcome names the sibling it came from.

Pure apart from `sibling_texts` and `stored_text`, which read the album's files.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from lyrics_compare import judged_lyrics
from lyrics_heard import judge_words, little_heard, not_sung, text_fingerprint
from lyrics_heard import words_of_text as words_of_text
from lyrics_heard_store import HeardRecord
from lyrics_model import Lyrics, LyricsError, Source
from lyrics_provenance import SourceRecords
from lyrics_stages import Stages

#: Why a track with a record is left alone.
SKIP_NOT_UNTIMED = "its lyrics are not untimed only"
SKIP_CHANGED = "its text changed since it was heard"
SKIP_LITTLE = "too little heard to judge"
SKIP_SUNG = "the transcript sings its text"
#: What a sibling's text is written under when no run recorded where it came from.
UNRECORDED_SIBLING = Source.ALBUM_TEXT


class TextChangedError(LyricsError):
    """A track's text no longer one a fix takes, between its plan and its turn."""


@dataclass(frozen=True)
class Candidate:
    """One text a fix may write: the lyrics, the source written, where they came from.

    *origin* is the sibling's album-relative path, None for a provider's answer.
    """

    lyrics: Lyrics
    source: Source
    origin: str | None = None


@dataclass(frozen=True)
class SiblingTexts:
    """The album's stored untimed texts, and the tracks whose text could not be read.

    Each text is a `Candidate` whose origin is its track's album-relative path.
    """

    texts: tuple[Candidate, ...]
    unreadable: tuple[str, ...]

    def besides(self, track: str) -> tuple[Candidate, ...]:
        """Every text but the one *track* holds."""
        return tuple(text for text in self.texts if text.origin != track)


def stored_text(stages: Stages, album: Path, path: Path, lang: str) -> Lyrics | None:
    """The untimed text the track at *path* holds alone, None when it holds none.

    Read as compare reads it (`judged_lyrics`): its sidecar's words, else its served
    untimed frame; a track holding timed lyrics, or none, holds none. Raises what reading
    its tags or its sidecars raises.
    """
    tags = stages.read_tags(path)
    return judged_lyrics(stages, album, path, tags, lang)


def skip_reason(refused: Lyrics | None, record: HeardRecord) -> str | None:
    """Why a fix leaves alone the track holding *refused*, judged as *record* says.

    None when the fix takes it: it holds untimed text alone, that text is the one the
    transcript was judged against, and the transcript heard enough of the track to
    judge it and did not sing it.
    """
    if refused is None:
        return SKIP_NOT_UNTIMED
    if text_fingerprint(refused.plain_text()) != record.fingerprint:
        return SKIP_CHANGED
    heard = judge_words(words_of_text(refused.plain_text()), record.transcript)
    if little_heard(heard):
        return SKIP_LITTLE
    if not not_sung(heard):
        return SKIP_SUNG
    return None


def sibling_texts(
    stages: Stages,
    album: Path,
    tracks: Sequence[Path],
    lang: str,
    sources: SourceRecords,
) -> SiblingTexts:
    """Every untimed text the album's *tracks* store, and the tracks that could not be read.

    Each keeps the provider *sources* records for its track, else `UNRECORDED_SIBLING`.
    A track holding timed lyrics, or none, gives no text.
    """
    texts: list[Candidate] = []
    unreadable: list[str] = []
    for path in tracks:
        rel = path.relative_to(album).as_posix()
        try:
            lyrics = stored_text(stages, album, path, lang)
        # A sibling that cannot be read is no candidate, and a fix that finds no sung
        # text then removes nothing: the unread text might have been the one.
        except Exception:  # noqa: BLE001 - any failure reading a sibling is reported
            unreadable.append(rel)
            continue
        if lyrics is not None:
            source = sources.source_of(rel) or UNRECORDED_SIBLING
            texts.append(Candidate(lyrics, source, rel))
    return SiblingTexts(tuple(texts), tuple(unreadable))


def first_sung(
    candidates: Iterable[Candidate], transcript: str, refused: Sequence[str]
) -> Candidate | None:
    """The first of *candidates* the *transcript* sings, drawing no more after it.

    A candidate whose words are the *refused* words, or another candidate's already
    judged, is passed over unjudged.
    """
    judged = [list(refused)]
    for candidate in candidates:
        words = words_of_text(candidate.lyrics.plain_text())
        if words in judged:
            continue
        judged.append(words)
        heard = judge_words(words, transcript)
        if not little_heard(heard) and not not_sung(heard):
            return candidate
    return None
