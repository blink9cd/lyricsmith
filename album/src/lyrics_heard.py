"""A text's words, its fingerprint, and whether a transcript of the track sings it.

A line's words are its letters and apostrophes, upper-cased, with a typographic
apostrophe read as the apostrophe and an accented letter as its base letter; a bracketed
section header such as ``[Chorus]``, like a blank line, has none, and neither does one
behind a byte order mark (U+FEFF), which is never a letter. They are the letters
sync's alignment model knows: the labels of sync's ``sy_text.LABELS`` after its blank and
its word separator, held equal to `WORD_LETTERS` by sync's tests, so sync's token ids and
these words are one reading of a text.

**The judge.** Forced alignment scores how well a text fits where it placed it, never
whether that text is sung at all: over a loud mix it finds a plausible path for another
song's words. So sync transcribes the vocal stem with whisper-cli first, and the
transcript is judged against the text here. A word pair is two consecutive words,
nothing dropped, and the text's pairs run across its line ends. The verdict is the share
of the transcript's distinct pairs found among the text's.

**What was measured**, on about 190 test-library tracks, each with its own text and with
another song's: a wrong text scored a share of at most 0.16; a right one at least 0.34
(a song not in English) and 0.55 at the 5th percentile. Distinct pairs, not every pair
heard, because whisper can fall into a loop repeating one line, which would inflate a
count of pair tokens. Single words were rejected: a long wrong text matches common
words, up to 0.68.

**The fingerprint** (`text_fingerprint`) is what a kept transcript was judged against:
the SHA-256 of the text's words in order, joined by single spaces. Case, spacing, line
breaks, punctuation and section headers leave it as it is, so a text re-stored with
only those changed still matches its transcript, while any changed word does not.

Here, in album, because the dependency direction is sync, then sweep, then album: both
tools judge a text by the one rule and cutoff. Pure.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

#: The letters a word keeps: the alignment model's, the 26 capitals and the apostrophe.
WORD_LETTERS: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ'")
#: Typographic apostrophes, read as the one the model knows.
_APOSTROPHES: Final = str.maketrans({"’": "'", "‘": "'"})
_SECTION_HEADER: Final = re.compile(r"\[[^\]]*\]")
#: A byte order mark, left at a line's start by a file that began with one; dropped
#: before a line is read, since `str.strip` keeps it and it would hide a header.
_BYTE_ORDER_MARK: Final = "\ufeff"
#: Joins a text's words before they are hashed.
_WORD_JOIN: Final = " "

#: The share under which a text is not sung, ruled between the wrong texts' highest
#: (0.16) and the right texts' lowest (0.34).
MIN_HEARD_SHARE: Final = 0.25
#: The fewest distinct pairs heard that can judge a text: under 20, a wrong text could
#: score 0.5 by chance. About 1 track in 11 fell under it; such a track is timed as
#: before the check, its row noting that it was not checked.
MIN_HEARD_PAIRS: Final = 20
#: Two consecutive words.
Pair = tuple[str, str]


@dataclass(frozen=True)
class Heard:
    """What whisper-cli heard: its distinct word pairs, and the share in the text."""

    pairs: int
    share: float


def line_words(line: str) -> list[str]:
    """*line*'s words as the capital letters the model aligns; none for a section header."""
    line = line.replace(_BYTE_ORDER_MARK, "")
    if _SECTION_HEADER.fullmatch(line.strip()):
        return []
    folded = _fold_accents(line.translate(_APOSTROPHES)).upper()
    words = ("".join(c for c in word if c in WORD_LETTERS) for word in folded.split())
    return [word for word in words if word]


def _fold_accents(text: str) -> str:
    """*text* with each accented letter as its base letter: café is cafe.

    Decomposed, an accented letter is its base letter and combining marks, and the
    marks are dropped; a letter with no decomposition into the model's letters, as ø,
    is left for the letter filter to drop.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def words_of_text(lines: str | Sequence[str]) -> list[str]:
    """Every word of a text, given whole or as its *lines*, each line read on its own."""
    split = lines.splitlines() if isinstance(lines, str) else lines
    return [word for line in split for word in line_words(line)]


# fuzz-target
def text_fingerprint(lines: str | Sequence[str]) -> str:
    """The SHA-256 hex of a text's words in order, joined by single spaces.

    The text is given whole or as its *lines*, lines without line breaks as
    `str.splitlines` or sync's ``sy_text.text_lines`` gives them: either gives the one
    fingerprint. A
    word holds only `WORD_LETTERS`, so the joined words are ASCII.
    """
    joined = _WORD_JOIN.join(words_of_text(lines))
    return hashlib.sha256(joined.encode("ascii")).hexdigest()


# fuzz-target
def heard_words(transcript: str) -> list[str]:
    """whisper-cli's transcript as the model's words, line by line, in order.

    Each line is read as a text line is, so a bracketed line such as ``[Music]`` gives
    no words, as a section header gives none.
    """
    return words_of_text(transcript.splitlines())


def word_pairs(words: Sequence[str]) -> frozenset[Pair]:
    """Each two consecutive *words*, each pair once."""
    return frozenset(pairwise(words))


def judge_words(words: Sequence[str], transcript: str) -> Heard:
    """What *transcript* heard of the text whose words are *words*; no share when nothing."""
    heard = word_pairs(heard_words(transcript))
    if not heard:
        return Heard(0, 0.0)
    found = heard & word_pairs(words)
    return Heard(len(heard), len(found) / len(heard))


def little_heard(heard: Heard) -> bool:
    """Too few pairs heard to judge the text either way."""
    return heard.pairs < MIN_HEARD_PAIRS


def not_sung(heard: Heard) -> bool:
    """Enough heard to judge, and too little of it the text's: another song's words."""
    return not little_heard(heard) and heard.share < MIN_HEARD_SHARE


def shown_share(heard: Heard) -> str:
    """The share as a row and the by-hand list print it: two decimals."""
    return f"{heard.share:.2f}"
