"""The placeholder rule: text that stands in for lyrics, in an answer or a stored copy.

Providers answer some tracks, an instrumental or a stand-up bit, with a stand-in rather
than nothing, and a track may already hold one, written by an earlier run or another
tool. `is_placeholder_lyrics` is the one rule for both. Lyrics are a placeholder when:

- their text, whitespace aside, is NetEase's instrumental notice, or nothing but
  music-note glyphs (`is_instrumental_placeholder`);
- every non-blank line is an LRC header tag, a bracketed ``name:value`` whose name
  opens with a letter, so never a time stamp, with nothing outside the brackets, as
  LRCLIB's ``[au:comedy]`` or ``[au:instrumental]``;
- they are synced lyrics whose lines hold no word: time stamps alone.

Blank untimed text is no placeholder: it is simply nothing, which every caller already
treats as no lyrics. An answer that is a placeholder is not found (`lyrics_fetch`); a
stored one reads as no lyrics held, so the providers are asked again and, when nothing
answers, it is removed (`lyrics_refetch`); the check flags an accepted track holding
one (`lyrics_check`). `without_placeholder_frames` sets a track's placeholder frames
aside, so what a fresh run's decline and the check compare with is the best frame
holding real lyrics, never a placeholder ranked above it.

Pure: no I/O, no printing, standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from lyrics_model import FrameRecord, Lyrics, SyncedLyrics, lyrics_from_frames

#: What providers answer for an instrumental track instead of nothing: NetEase's
#: "pure music, please enjoy", and the lone music-note glyphs others use. Extended as
#: real runs find more; embedding one would put a non-lyric into the file.
INSTRUMENTAL_PLACEHOLDERS = frozenset(
    {
        "纯音乐，请欣赏",
        # The longer form: "this song is pure music with no lyrics written, please enjoy".
        "此歌曲为没有填词的纯音乐，请您欣赏",
        "♩",
        "♪",
        "♫",
        "♬",
    }
)
#: The note glyphs, any run of which alone is a placeholder.
_NOTE_GLYPHS = frozenset("♩♪♫♬")
#: One line of LRC header tags and nothing else: each a bracketed name, opening with a
#: letter (``[^\W\d_]``), then ``:`` and a value. Neither part holds a bracket and the
#: name no colon, so each tag ends at the first ``]`` and the match never backtracks
#: into a tag.
_HEADER_LINE = re.compile(r"\s*(?:\[[^\W\d_][^\[\]:]*:[^\[\]]*\]\s*)+")


def is_instrumental_placeholder(text: str) -> bool:
    """True when *text*, whitespace aside, is a provider's stand-in for no lyrics."""
    compact = "".join(text.split())
    if compact in INSTRUMENTAL_PLACEHOLDERS:
        return True
    return bool(compact) and set(compact) <= _NOTE_GLYPHS


def is_header_line(line: str) -> bool:
    """True when *line* is LRC header tags and nothing else, as ``[ar:...]``."""
    return _HEADER_LINE.fullmatch(line) is not None


def _is_header_only(text: str) -> bool:
    """True when *text* has a non-blank line and every such line is LRC header tags."""
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and all(is_header_line(line) for line in lines)


# fuzz-target
def is_placeholder_lyrics(lyrics: Lyrics) -> bool:
    """True when *lyrics* stand in for lyrics rather than hold any (the module's rule)."""
    text = lyrics.plain_text()
    if isinstance(lyrics, SyncedLyrics) and not text.strip():
        return True
    return is_instrumental_placeholder(text) or _is_header_only(text)


def unless_placeholder(lyrics: Lyrics | None) -> Lyrics | None:
    """*lyrics*, or None when they are a placeholder: what a held copy serves as."""
    if lyrics is None or is_placeholder_lyrics(lyrics):
        return None
    return lyrics


def without_placeholder_frames(
    frames: Sequence[FrameRecord],
) -> tuple[FrameRecord, ...]:
    """*frames* less each whose lyrics, converted alone, are a placeholder.

    Each frame is converted as `lyrics_from_frames` converts it when it is the only
    one; one converting to nothing, a blank frame, is kept, as the conversion of the
    rest already passes it over.
    """
    return tuple(frame for frame in frames if not _placeholder_frame(frame))


def _placeholder_frame(frame: FrameRecord) -> bool:
    """True when *frame*, converted alone, holds placeholder lyrics."""
    held = lyrics_from_frames((frame,), frame.lang)
    return held is not None and is_placeholder_lyrics(held)
