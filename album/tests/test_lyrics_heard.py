"""lyrics_heard: a text's words, its fingerprint, and whether a transcript sings it.

Every verse here is invented; nothing is read from disk.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard as lh  # noqa: E402 - import follows the sys.path bootstrap above

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
#: Words of the letters a line keeps, lower-cased, as a writer would type them.
_WORDS = st.lists(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz'", min_size=1, max_size=8),
    min_size=1,
    max_size=12,
)
#: What may sit between two words without changing them: spaces, tabs, line ends.
_GAPS = st.sampled_from([" ", "  ", "\t", "\n", " \n\n ", "\r\n"])
#: Punctuation a writer may end a word with; none of it is a letter a line keeps.
_MARKS = st.sampled_from(["", ",", ".", "!", "?", ";", ":", '"', ")"])
#: A section header on a line of its own: it has no words to sing.
_HEADERS = st.sampled_from(["", "[Chorus]", "[Verse 2]", "[Bridge: both]", "[]"])


def _words_of(*lines: str) -> list[str]:
    """Every word of *lines*, each line read as a text line is."""
    return [word for line in lines for word in lh.line_words(line)]


class LineWordsTest(fixtures.IsolatedCase):
    def test_letters_and_the_apostrophe_are_kept_upper_cased(self) -> None:
        self.assertEqual(lh.line_words("Don't stop"), ["DON'T", "STOP"])

    def test_a_typographic_apostrophe_is_the_apostrophe(self) -> None:
        self.assertEqual(lh.line_words("Don’t ‘em"), ["DON'T", "'EM"])

    def test_digits_and_punctuation_are_dropped(self) -> None:
        self.assertEqual(lh.line_words("4 me! -- now"), ["ME", "NOW"])

    def test_an_accented_letter_keeps_its_base_letter(self) -> None:
        self.assertEqual(lh.line_words("Café naïve"), ["CAFE", "NAIVE"])

    def test_a_letter_with_no_base_letter_is_dropped(self) -> None:
        self.assertEqual(lh.line_words("søn"), ["SN"])

    def test_a_section_header_has_no_words(self) -> None:
        self.assertEqual(lh.line_words("  [Chorus x2] "), [])
        self.assertEqual(lh.line_words("[Chorus] la"), ["CHORUS", "LA"])

    def test_a_leading_byte_order_mark_leaves_a_section_header_one(self) -> None:
        self.assertEqual(lh.line_words("\ufeff[Chorus]"), [])
        self.assertEqual(lh.line_words("\ufeffla la"), ["LA", "LA"])

    def test_the_word_letters_are_the_alphabet_and_the_apostrophe(self) -> None:
        self.assertEqual(lh.WORD_LETTERS, frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ'"))


class HeardWordsTest(fixtures.IsolatedCase):
    def test_the_heard_words_are_normalized_as_the_text_is(self) -> None:
        heard = " Don’t stop,\n[Music]\n  Café   NOW!\n"
        self.assertEqual(lh.heard_words(heard), ["DON'T", "STOP", "CAFE", "NOW"])

    def test_pairs_are_consecutive_words_each_once(self) -> None:
        pairs = lh.word_pairs(["LA", "DI", "LA", "DI"])
        self.assertEqual(pairs, frozenset({("LA", "DI"), ("DI", "LA")}))
        self.assertEqual(lh.word_pairs(["LA"]), frozenset())

    @given(st.text(max_size=80))
    def test_fuzz_heard_words(self, text: str) -> None:
        # Any transcript gives words of the kept letters only, and a text heard as
        # itself is wholly sung.
        words = lh.heard_words(text)
        self.assertTrue(all(word and set(word) <= lh.WORD_LETTERS for word in words))
        heard = lh.judge_words(_words_of(*text.splitlines()), text)
        self.assertEqual(heard.pairs, len(lh.word_pairs(words)))
        self.assertEqual(heard.share, 1.0 if heard.pairs else 0.0)


class JudgeWordsTest(fixtures.IsolatedCase):
    def test_the_right_text_is_heard_whole(self) -> None:
        heard = lh.judge_words(_words_of(*VERSE), " ".join(VERSE))
        self.assertEqual(heard, lh.Heard(21, 1.0))
        self.assertFalse(lh.not_sung(heard) or lh.little_heard(heard))

    def test_another_songs_text_is_not_sung(self) -> None:
        heard = lh.judge_words(_words_of(*OTHER), "\n".join(VERSE))
        self.assertEqual(heard, lh.Heard(21, 0.0))
        self.assertTrue(lh.not_sung(heard))

    def test_the_share_is_of_the_distinct_pairs_heard(self) -> None:
        # A repetition loop hears one pair forty times; it counts once.
        loop = " ".join(["seven lanterns"] * 40)
        heard = lh.judge_words(_words_of(*VERSE), f"{loop} in the rain")
        # SEVEN LANTERNS, LANTERNS SEVEN, LANTERNS IN, IN THE and THE RAIN: all but
        # the loop's turn back are the text's.
        self.assertEqual(heard, lh.Heard(5, 0.8))

    def test_a_pair_across_a_line_end_of_the_text_counts(self) -> None:
        heard = lh.judge_words(_words_of("one two", "three four"), "two three")
        self.assertEqual(heard, lh.Heard(1, 1.0))

    def test_nothing_heard_is_no_pairs_and_no_share(self) -> None:
        self.assertEqual(lh.judge_words(_words_of(*VERSE), ""), lh.Heard(0, 0.0))

    def test_the_cutoffs_are_the_measured_ones(self) -> None:
        self.assertEqual((lh.MIN_HEARD_SHARE, lh.MIN_HEARD_PAIRS), (0.25, 20))

    def test_a_share_under_the_cutoff_with_enough_pairs_is_not_sung(self) -> None:
        self.assertTrue(lh.not_sung(lh.Heard(20, 0.24)))
        self.assertFalse(lh.not_sung(lh.Heard(20, 0.25)))

    def test_too_few_pairs_cannot_judge(self) -> None:
        self.assertTrue(lh.little_heard(lh.Heard(19, 0.0)))
        self.assertFalse(lh.not_sung(lh.Heard(19, 0.0)))
        self.assertFalse(lh.little_heard(lh.Heard(20, 0.0)))

    def test_the_share_is_shown_to_two_decimals(self) -> None:
        self.assertEqual(lh.shown_share(lh.Heard(30, 1 / 30)), "0.03")


class FingerprintTest(fixtures.IsolatedCase):
    def test_it_is_the_sha256_of_the_words_joined_by_single_spaces(self) -> None:
        expected = hashlib.sha256(b"DON'T STOP ME NOW").hexdigest()
        self.assertEqual(lh.text_fingerprint("Don't stop\nme now"), expected)

    def test_lines_and_the_text_they_make_agree(self) -> None:
        self.assertEqual(
            lh.text_fingerprint(VERSE), lh.text_fingerprint("\n".join(VERSE))
        )

    def test_a_section_header_line_adds_nothing(self) -> None:
        self.assertEqual(
            lh.text_fingerprint(["[Chorus]", *VERSE]), lh.text_fingerprint(VERSE)
        )

    def test_a_text_with_no_words_has_the_empty_texts_fingerprint(self) -> None:
        empty = hashlib.sha256(b"").hexdigest()
        self.assertEqual(lh.text_fingerprint(""), empty)
        self.assertEqual(lh.text_fingerprint(["[Intro]", "", "42"]), empty)

    def test_another_text_has_another_fingerprint(self) -> None:
        self.assertNotEqual(lh.text_fingerprint(VERSE), lh.text_fingerprint(OTHER))

    @given(_WORDS, st.data())
    def test_fuzz_text_fingerprint(self, words: list[str], data: st.DataObject) -> None:
        # Case, the gaps between words, punctuation and section headers never change
        # the fingerprint; changing one word does.
        pieces: list[str] = []
        for word in words:
            cased = "".join(
                data.draw(st.sampled_from([letter.lower(), letter.upper()]))
                for letter in word
            )
            header = data.draw(_HEADERS)
            pieces += [f"\n{header}\n" if header else "", cased, data.draw(_MARKS)]
            pieces.append(data.draw(_GAPS))
        plain = " ".join(words)
        self.assertEqual(
            lh.text_fingerprint("".join(pieces)), lh.text_fingerprint(plain)
        )
        index = data.draw(st.integers(0, len(words) - 1))
        changed = [*words[:index], words[index] + "x", *words[index + 1 :]]
        self.assertNotEqual(
            lh.text_fingerprint(" ".join(changed)), lh.text_fingerprint(plain)
        )
