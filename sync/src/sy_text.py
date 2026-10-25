"""A track's text as the alignment model's letters, each letter owned by its line.

The model is torchaudio's WAV2VEC2_ASR_BASE_960H. Its labels are the CTC blank, a word
separator, the 26 capital letters and the apostrophe. A line keeps only those letters,
case-folded, with a typographic apostrophe read as the apostrophe. One separator joins
each two words, across lines too, and belongs to no line. So a line's start is its own
first letter: CTC may place the separator before a line anywhere in the pause ahead of it,
and counting that separator as the line's start stamped a line after a pause where the
pause began. A bracketed section header such as ``[Chorus]`` has nothing to align. Pure.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

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
#: Typographic apostrophes, read as the one the model knows.
_APOSTROPHES: Final = str.maketrans({"’": "'", "‘": "'"})
_SECTION_HEADER: Final = re.compile(r"\[[^\]]*\]")


@dataclass(frozen=True)
class Tokens:
    """The model's label index for each token, and the line holding it (`NO_LINE`)."""

    ids: tuple[int, ...]
    line_of: tuple[int, ...]


def text_lines(text: str) -> tuple[str, ...]:
    """The lines of *text* that get a stamp: each stripped, blank ones dropped."""
    return tuple(stripped for raw in text.splitlines() if (stripped := raw.strip()))


def line_words(line: str) -> list[str]:
    """*line*'s words as the capital letters the model aligns; none for a section header."""
    if _SECTION_HEADER.fullmatch(line.strip()):
        return []
    folded = line.translate(_APOSTROPHES).upper()
    words = ("".join(c for c in word if c in _INDEX) for word in folded.split())
    return [word for word in words if word]


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
