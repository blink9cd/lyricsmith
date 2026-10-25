"""compare_hints: the weak hints beside a compare verdict, few words and no title word.

Every title and lyric here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_check  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_compare  # noqa: E402 - import follows the sys.path bootstrap above

# A registered profile, never an @settings decorator: a fuzzing campaign that re-applies
# its own settings to each property is refused by Hypothesis on a decorated one. The
# name and settings are test_lyrics_model's, so whichever suite loads first, the other's
# properties run under the same profile. deadline=None: a per-example wall-clock
# deadline measures machine load, not correctness. too_slow is suppressed because a
# cold cache's first text draw builds Hypothesis's unicode tables inside that health
# check's window.
settings.register_profile(
    "album_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("album_properties")

#: A title whose word "keeper" the judged words below hold.
TITLE = "Keeper"
#: Three minutes: 18 words a song must hold to pass the floor, 17 hint.
THREE_MINUTES_S = 180.0
#: The Unicode category the case property draws its words from: letters.
LETTERS: tuple[Literal["L"]] = ("L",)
NO_TITLE_WORD = "no title word"
#: Punctuation the check turns into a space: no bracket, which a title suffix or a
#: tag would take with its contents, and no underscore, which is a word character.
SPLITTERS = '-.,!?;:&/"*+=#%@~|'
#: The apostrophes the check deletes, so they may sit inside a word too.
APOSTROPHES = "'\u2019"
#: The plain words a punctuated title is built from.
TITLE_WORD = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8)


def keeper_words(count: int) -> list[str]:
    """*count* judged words, the title's word among them."""
    return ["keeper", *(["lamp"] * (count - 1))]


class FewWordsTest(fixtures.IsolatedCase):
    """Fewer words a second than the floor hint, with the rate, count and duration."""

    def test_seventeen_words_in_three_minutes_hint(self) -> None:
        self.assertEqual(
            lyrics_compare.compare_hints(keeper_words(17), TITLE, THREE_MINUTES_S),
            ("few words (0.094 words per second: 17 words in 180.000 s)",),
        )

    def test_eighteen_words_in_three_minutes_do_not(self) -> None:
        hints = lyrics_compare.compare_hints(keeper_words(18), TITLE, THREE_MINUTES_S)
        self.assertEqual(hints, ())

    def test_an_unreadable_duration_gives_no_few_words(self) -> None:
        for duration in (None, 0.0, -3.0, float("nan"), float("inf")):
            with self.subTest(duration=duration):
                hints = lyrics_compare.compare_hints(keeper_words(1), TITLE, duration)
                self.assertEqual(hints, ())

    def test_the_floor_is_the_named_constant(self) -> None:
        self.assertEqual(lyrics_compare.MIN_WORDS_PER_SECOND, 0.1)


class NoTitleWordTest(fixtures.IsolatedCase):
    """A title word of three letters or more missing from the words hints."""

    def test_a_missing_title_word_hints(self) -> None:
        hints = lyrics_compare.compare_hints(["the", "mill"], "Harbor Lights", None)
        self.assertEqual(hints, (NO_TITLE_WORD,))

    def test_a_title_of_short_words_only_gives_none(self) -> None:
        hints = lyrics_compare.compare_hints(["the", "mill"], "Me & Us", None)
        self.assertEqual(hints, ())

    def test_a_three_letter_title_word_counts(self) -> None:
        hints = lyrics_compare.compare_hints(["the", "moon"], "Sun", None)
        self.assertEqual(hints, (NO_TITLE_WORD,))

    def test_a_title_word_present_in_any_case_gives_none(self) -> None:
        words = lyrics_check.normalize_words("the keeper said")
        self.assertEqual(lyrics_compare.compare_hints(words, "KEEPER", None), ())

    def test_one_title_word_present_is_enough(self) -> None:
        words = lyrics_check.normalize_words("the keeper said")
        hints = lyrics_compare.compare_hints(words, "Lonely Keeper", None)
        self.assertEqual(hints, ())

    def test_a_trailing_group_is_not_part_of_the_title(self) -> None:
        keeper = lyrics_check.normalize_words("a keeper said")
        live = lyrics_check.normalize_words("live from a hall")
        self.assertEqual(
            lyrics_compare.compare_hints(keeper, "The Keeper (Live)", None), ()
        )
        hints = lyrics_compare.compare_hints(live, "The Keeper (Live)", None)
        self.assertEqual(hints, (NO_TITLE_WORD,))

    def test_both_hints_come_in_order(self) -> None:
        hints = lyrics_compare.compare_hints(["lamp"], "Harbor Lights", THREE_MINUTES_S)
        self.assertEqual(
            hints,
            ("few words (0.005 words per second: 1 words in 180.000 s)", NO_TITLE_WORD),
        )


class FuzzHintsTest(fixtures.IsolatedCase):
    """compare_hints never raises, and ignores the title's case and punctuation."""

    @given(st.lists(st.text()), st.text(), st.one_of(st.none(), st.floats()))
    def test_fuzz_compare_hints(
        self, words: list[str], title: str, duration: float | None
    ) -> None:
        lyrics_compare.compare_hints(words, title, duration)

    # fuzz-explore: compare_hints
    @given(st.text(alphabet=st.characters(categories=LETTERS), min_size=3))
    def test_hints_ignore_the_title_case(self, word: str) -> None:
        # Case is ignored as far as the check's own normalization ignores it: a few
        # letters casefold apart from their other case (a dotless "\u0131" folds to
        # itself, its capital "I" to "i"), and the title is normalized as the check
        # normalizes, so those letters are left out here.
        upper, lower = word.upper(), word.lower()
        normalize = lyrics_check.normalize_words
        assume(normalize(upper) == normalize(lower))
        lowered = normalize(word)
        self.assertEqual(
            lyrics_compare.compare_hints(lowered, upper, None),
            lyrics_compare.compare_hints(lowered, lower, None),
        )

    # fuzz-explore: compare_hints
    @given(
        st.lists(TITLE_WORD, min_size=1, max_size=4),
        st.lists(TITLE_WORD, max_size=4),
        st.data(),
    )
    def test_hints_ignore_the_title_punctuation(
        self, title_words: list[str], other: list[str], data: st.DataObject
    ) -> None:
        # A splitter goes only between or around words, where it stands for the space
        # already there; an apostrophe goes anywhere, since the check deletes it.
        judged = lyrics_check.normalize_words(" ".join([*title_words[:1], *other]))
        gaps = st.text(alphabet=SPLITTERS + APOSTROPHES, max_size=3)
        inside = st.text(alphabet=APOSTROPHES, max_size=1)
        parts = [data.draw(gaps)]
        for word in title_words:
            cut = data.draw(st.integers(min_value=0, max_value=len(word)))
            parts.append(f" {word[:cut]}{data.draw(inside)}{word[cut:]} ")
            parts.append(data.draw(gaps))
        bare = " ".join(title_words)
        self.assertEqual(
            lyrics_compare.compare_hints(judged, "".join(parts), None),
            lyrics_compare.compare_hints(judged, bare, None),
        )
