"""A track's text as the alignment model's letters, each letter owned by its line.

The model is torchaudio's WAV2VEC2_ASR_BASE_960H. Its labels are the CTC blank, a word
separator, the 26 capital letters and the apostrophe. A line keeps only those letters,
upper-cased, with a typographic apostrophe read as the apostrophe. One separator joins
each two words, across lines too, and belongs to no line. So a line's start is its own
first letter: CTC may place the separator before a line anywhere in the pause ahead of it,
and counting that separator as the line's start would stamp a line after a pause where
the pause began. A bracketed section header such as ``[Chorus]``, like a blank line, has
nothing to align. A line's words are read by album's `lyrics_heard.line_words`, the one
reading the sung check and the transcript store's fingerprint share; its letters are
these labels' (`lyrics_heard.WORD_LETTERS`, held equal by the tests). Pure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

import sy_located  # noqa: F401 - puts album's modules on sys.path
from lyrics_heard import line_words

#: The model's labels in index order; the child process checks the loaded model's
#: labels against these before aligning.
LABELS: Final = (
    "-", "|", "E", "T", "A", "O", "N", "I", "H", "S", "R", "D", "L", "U", "M",
    "W", "C", "F", "G", "Y", "P", "B", "V", "K", "'", "X", "J", "Q", "Z",
)  # fmt: skip
BLANK: Final = 0
SEPARATOR: Final = 1
#: The line index of a token that belongs to no line: every separator.
NO_LINE: Final = -1
#: Each alignable letter's label index.
_INDEX: Final[Mapping[str, int]] = MappingProxyType(
    {label: index for index, label in enumerate(LABELS) if index > SEPARATOR}
)


@dataclass(frozen=True)
class Tokens:
    """The model's label index for each token, and the line holding it (`NO_LINE`)."""

    ids: tuple[int, ...]
    line_of: tuple[int, ...]


def text_lines(text: str) -> tuple[str, ...]:
    """The lines of *text* that get a stamp, each stripped: line 0 is never blank.

    A blank line between two non-blank lines is kept as an empty line, each one of a run,
    because album embeds the untimed lyrics from the .lrc's lines and would otherwise
    lose the breaks between verses. Blank lines before the first and after the last
    non-blank line are dropped.
    """
    stripped = [raw.strip() for raw in text.splitlines()]
    sung = [index for index, line in enumerate(stripped) if line]
    return tuple(stripped[sung[0] : sung[-1] + 1]) if sung else ()


# fuzz-target
def tokenize(lines: Sequence[str]) -> Tokens:
    """*lines* as one token sequence, a separator between each two words."""
    ids: list[int] = []
    line_of: list[int] = []
    for index, line in enumerate(lines):
        for word in line_words(line):
            if ids:
                ids.append(SEPARATOR)
                line_of.append(NO_LINE)
            ids.extend(_INDEX[letter] for letter in word)
            line_of.extend(index for _ in word)
    return Tokens(tuple(ids), tuple(line_of))
