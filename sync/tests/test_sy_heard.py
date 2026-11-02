"""Whether a track's text is sung: its token ids read back to words and judged.

The rule itself, its cutoffs and the word reading are album's `lyrics_heard`, tested
beside it; these tests hold sync's half, the ids read back and judged end to end.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_heard as hd  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import (  # noqa: E402 - follows the bootstrap above
    Heard,
    heard_words,
    little_heard,
    not_sung,
    word_pairs,
)
from sy_text import tokenize  # noqa: E402 - follows the bootstrap above

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: An invented verse of 21 distinct word pairs, one line a pair or so.
VERSE = (
    "red kites over the harbor wall",
    "seven lanterns in the rain",
    "a quiet engine hums all night",
    "and nobody comes home again",
)
#: An invented verse sharing no word pair with `VERSE`.
OTHER = (
    "paper boats drift down the gutter",
    "every window holds a candle",
    "morning trains are running late",
    "while the bakers knead the dough",
)


def ids_of(*lines: str) -> tuple[int, ...]:
    """The token ids the parent sends for *lines*."""
    return tokenize(lines).ids


class WordsTest(sy_cases.IsolatedCase):
    def test_the_texts_words_come_back_from_its_token_ids(self) -> None:
        ids = ids_of("Don't stop", "me now")
        self.assertEqual(hd.text_words(ids), ["DON'T", "STOP", "ME", "NOW"])

    def test_no_ids_are_no_words(self) -> None:
        self.assertEqual(hd.text_words(()), [])


class JudgeTest(sy_cases.IsolatedCase):
    def test_the_right_text_is_heard_whole(self) -> None:
        heard = hd.judge(ids_of(*VERSE), " ".join(VERSE))
        self.assertEqual(heard, Heard(21, 1.0))
        self.assertFalse(not_sung(heard) or little_heard(heard))

    def test_another_songs_text_is_not_sung(self) -> None:
        heard = hd.judge(ids_of(*OTHER), "\n".join(VERSE))
        self.assertEqual(heard, Heard(21, 0.0))
        self.assertTrue(not_sung(heard))

    def test_the_share_is_of_the_distinct_pairs_heard(self) -> None:
        # A repetition loop hears one pair forty times; it counts once.
        loop = " ".join(["seven lanterns"] * 40)
        heard = hd.judge(ids_of(*VERSE), f"{loop} in the rain")
        # SEVEN LANTERNS, LANTERNS SEVEN, LANTERNS IN, IN THE and THE RAIN: all but
        # the loop's turn back are the text's.
        self.assertEqual(heard, Heard(5, 0.8))

    def test_a_pair_across_a_line_end_of_the_text_counts(self) -> None:
        heard = hd.judge(ids_of("one two", "three four"), "two three")
        self.assertEqual(heard, Heard(1, 1.0))

    def test_nothing_heard_is_no_pairs_and_no_share(self) -> None:
        self.assertEqual(hd.judge(ids_of(*VERSE), ""), Heard(0, 0.0))


class PropertyTest(sy_cases.IsolatedCase):
    @given(hs.text(max_size=80))
    def test_a_text_heard_as_itself_is_wholly_sung(self, text: str) -> None:
        # Any text's ids read back to the words a transcript of it gives, so a text
        # heard as itself is wholly sung. The word reading's own property test is
        # album's (`test_fuzz_heard_words`).
        words = heard_words(text)
        self.assertEqual(hd.text_words(tokenize(text.splitlines()).ids), words)
        heard = hd.judge(tokenize(text.splitlines()).ids, text)
        self.assertEqual(heard.pairs, len(word_pairs(words)))
        self.assertEqual(heard.share, 1.0 if heard.pairs else 0.0)
