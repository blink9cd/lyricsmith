"""Whether a track's text is sung: its word pairs against the ones whisper-cli heard.

The child transcribes the vocal stem with whisper-cli before it aligns, and this module
judges the transcript against the text it was sent as token ids. The rule, its cutoffs
and what they were measured on live in album's `lyrics_heard`, which album's own
text fix judges by too; here the ids are read back to the words they spell, one word
between each two separators, so the text's pairs run across its line ends as its ids
do, and handed to `lyrics_heard.judge_words`. Pure.
"""

from __future__ import annotations

from collections.abc import Sequence

import sy_located  # noqa: F401 - puts album's modules on sys.path
from lyrics_heard import Heard, judge_words
from sy_text import LABELS, SEPARATOR


def text_words(ids: Sequence[int]) -> list[str]:
    """The text's words, read back from its token ids: one between each two separators."""
    letters = "".join(" " if index == SEPARATOR else LABELS[index] for index in ids)
    return letters.split()


def judge(ids: Sequence[int], transcript: str) -> Heard:
    """What *transcript* heard of the text with token *ids*; no share when nothing."""
    return judge_words(text_words(ids), transcript)
