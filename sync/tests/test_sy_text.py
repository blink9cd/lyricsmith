"""A track's text as the model's letters: what is kept, and which line each token is in."""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_text as tx  # noqa: E402 - import follows the sys.path bootstrap above

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")


def _ids(letters: str) -> list[int]:
    """The label index of each letter of *letters*."""
    return [tx.LABELS.index(letter) for letter in letters]


class TokenizeTest(sy_cases.IsolatedCase):
    def test_letters_and_the_apostrophe_are_kept_case_folded(self) -> None:
        tokens = tx.tokenize(["Don't stop"])
        self.assertEqual(
            list(tokens.ids), [*_ids("DON'T"), tx.SEPARATOR, *_ids("STOP")]
        )
        self.assertEqual(list(tokens.line_of), [0] * 5 + [tx.NO_LINE] + [0] * 4)

    def test_a_typographic_apostrophe_is_the_apostrophe(self) -> None:
        self.assertEqual(tx.tokenize(["Don’t"]), tx.tokenize(["Don't"]))

    def test_digits_and_punctuation_are_dropped(self) -> None:
        self.assertEqual(list(tx.tokenize(["4 me!"]).ids), _ids("ME"))

    def test_an_accented_letter_keeps_its_base_letter(self) -> None:
        self.assertEqual(tx.tokenize(["Café naïve"]), tx.tokenize(["Cafe naive"]))

    def test_a_section_header_has_nothing_to_align(self) -> None:
        tokens = tx.tokenize(["[Chorus]", "la"])
        self.assertNotIn(0, tokens.line_of)
        self.assertEqual(list(tokens.ids), _ids("LA"))

    def test_the_separator_between_lines_belongs_to_no_line(self) -> None:
        tokens = tx.tokenize(["a", "b"])
        self.assertEqual(list(tokens.line_of), [0, tx.NO_LINE, 1])

    def test_text_lines_strip_and_drop_blank_lines(self) -> None:
        self.assertEqual(tx.text_lines(" one \n\n  \ntwo\r\n"), ("one", "two"))

    @given(hs.lists(hs.text(), max_size=12))
    def test_fuzz_tokenize(self, lines: list[str]) -> None:
        tokens = tx.tokenize(lines)
        self.assertEqual(len(tokens.ids), len(tokens.line_of))
        lines_seen = [line for line in tokens.line_of if line != tx.NO_LINE]
        self.assertEqual(lines_seen, sorted(lines_seen))
        self.assertTrue(all(0 < i < len(tx.LABELS) for i in tokens.ids))
        if tokens.ids:
            self.assertNotEqual(tokens.ids[0], tx.SEPARATOR)
            self.assertNotEqual(tokens.ids[-1], tx.SEPARATOR)
        pairs = zip(tokens.ids, tokens.ids[1:])
        self.assertFalse(any(a == b == tx.SEPARATOR for a, b in pairs))
