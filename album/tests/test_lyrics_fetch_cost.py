"""What a provider's answer may cost: the Genius trailer trim is linear, and size is capped.

An answer is handled on the run's own thread after the watchdog has returned, so no
deadline bounds its cleanup or parse. These tests pin that a hostile answer can neither
stall the trailer trim nor reach the super-linear parse at all. Every lyric is invented.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import step_key  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB, NETEASE, GENIUS = lm.Source.LRCLIB, lm.Source.NETEASE, lm.Source.GENIUS
QUERY = lm.TrackQuery("Invented Artist", "The Keeper Counts")
NO_SIDECAR = lm.SidecarState(None)
DEADLINE_S = 5.0
#: A digit run the old trailer pattern took over a minute to scan, and the linear one
#: a few milliseconds: far enough apart that the bound below cannot flake either way.
HOSTILE_DIGITS = 200_000
#: Generous for a slow machine, yet a small fraction of the quadratic scan's time.
LINEAR_BOUND_S = 1.0
#: The trailer pattern before the fix, kept as the reference the fix must agree with.
_OLD_TRAILER = re.compile(r"\d*Embed\s*\Z")
#: A header Genius puts on the first line; cleanup removes it.
_HEADER = "12 ContributorsThe Keeper Counts Lyrics"


def _padded(text: str, length: int) -> str:
    """*text* followed by blank lines until it is *length* characters long."""
    return text + "\n" * (length - len(text))


class TrailerTrimCostTest(fixtures.IsolatedCase):
    """The Embed trailer trim stays linear on a long run of digits, and strips the same."""

    def test_a_long_digit_run_is_cleaned_within_a_tight_bound(self) -> None:
        text = "1" * HOSTILE_DIGITS
        started = time.perf_counter()
        cleaned = lf.clean_genius_text(text)
        elapsed = time.perf_counter() - started
        self.assertEqual(cleaned, text)
        self.assertLess(elapsed, LINEAR_BOUND_S)

    def test_the_trim_agrees_with_the_old_pattern(self) -> None:
        cases = (
            "12Embed ",
            "ab 3Embed",
            "la la\nEmbed\n",
            "line two42Embed",
            "x12Embed\n\n",
            "Embed",
            "99",
            "no trailer here",
            "Embedded in the ice",
            "4Embed then more",
            "1a2Embed",
        )
        for text in cases:
            with self.subTest(text=text):
                expected = _OLD_TRAILER.sub("", text).strip()
                self.assertEqual(lf.clean_genius_text(text), expected)


class AnswerSizeCapTest(fixtures.IsolatedCase):
    """An answer over MAX_LYRICS_CHARS is refused before any cleanup, and the chain moves on."""

    def test_an_answer_one_over_the_cap_is_skipped_for_the_next_provider(self) -> None:
        too_large = _padded(fixtures.LIGHTHOUSE_LRC, lr.MAX_LYRICS_CHARS + 1)
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, S): fixtures.answering(too_large),
                step_key(NETEASE, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC),
            }
        )
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertIs(result.source, NETEASE)
        self.assertEqual(result.lyrics, lm.parse_lrc(fixtures.LIGHTHOUSE_LRC))
        refused = result.attempts[0]
        self.assertEqual(refused.key, step_key(LRCLIB, S))
        self.assertIs(refused.outcome, lm.AttemptOutcome.EMPTY)
        self.assertEqual(refused.detail, lf.DETAIL_TOO_LARGE)
        self.assertIn("too large", refused.detail)

    def test_an_answer_exactly_at_the_cap_is_accepted(self) -> None:
        at_cap = _padded(fixtures.LIGHTHOUSE_LRC, lr.MAX_LYRICS_CHARS)
        table = fixtures.chain_table({step_key(LRCLIB, S): fixtures.answering(at_cap)})
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertIs(result.source, LRCLIB)
        self.assertEqual(result.lyrics, lm.parse_lrc(fixtures.LIGHTHOUSE_LRC))

    def test_the_cap_is_applied_before_genius_cleanup(self) -> None:
        # Cleanup would bring this under the cap; refusing it anyway proves the check
        # reads the raw answer, before the cleanup it exists to spare.
        raw = _padded(_HEADER + fixtures.ORCHARD_PLAIN, lr.MAX_LYRICS_CHARS + 1)
        self.assertLessEqual(len(lf.clean_genius_text(raw)), lr.MAX_LYRICS_CHARS)
        answer, detail = lf.normalize_answer(raw, step_key(GENIUS, U))
        self.assertIsNone(answer)
        self.assertEqual(detail, lf.DETAIL_TOO_LARGE)
