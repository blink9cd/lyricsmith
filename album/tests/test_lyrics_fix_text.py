"""`lyrics_fix_text`: which candidate text wins, and which tracks a fix leaves alone.

The candidates are judged in the order given, the album's other texts before the
providers' answers, and the first one the transcript sings wins; one holding the refused
text's words, or a text already judged, is passed over, and the rest are never drawn, so
no provider is asked once a text has won. A track is fixed only when it holds untimed text
alone, that text is the one its transcript was judged against, and the transcript was
long enough to judge it and did not sing it. Pure; every lyric is invented.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fix_text as lft  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard_store as store  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    FOUNDRY,
    HARBOR,
    LITTLE,
    MEADOW,
    QUARRY,
    words,
)

TRACK = Path("/invented/album/01 Harbor.mp3")


def plain(text: str) -> lm.PlainLyrics:
    """*text* as untimed lyrics."""
    return lm.PlainLyrics(text)


def candidate(text: str, source: lm.Source = lm.Source.LRCLIB) -> lft.Candidate:
    """*text* as a candidate from *source*."""
    return lft.Candidate(plain(text), source, str(source))


def record(transcript: str, held: str) -> store.HeardRecord:
    """A record of *transcript*, judged against the text *held*."""
    return store.HeardRecord(TRACK, lyrics_heard.text_fingerprint(held), transcript)


class FirstSungTest(fixtures.IsolatedCase):
    """The first candidate the transcript sings wins; the rest are never drawn."""

    def test_the_first_sung_wins_in_the_order_given(self) -> None:
        sibling = candidate(HARBOR, lm.Source.SIDECAR)
        provider = candidate(HARBOR + "\nagain and again\n")
        won = lft.first_sung([sibling, provider], HARBOR, words(QUARRY))
        self.assertIs(won, sibling)

    def test_unsung_candidates_are_passed_over(self) -> None:
        sung = candidate(HARBOR)
        won = lft.first_sung(
            [candidate(MEADOW), candidate(FOUNDRY), sung], HARBOR, words(QUARRY)
        )
        self.assertIs(won, sung)

    def test_the_refused_words_never_win(self) -> None:
        # Written another way, the refused text is still the refused text.
        refused = candidate(HARBOR.upper().replace(",", ";"))
        self.assertIsNone(lft.first_sung([refused], HARBOR, words(HARBOR)))

    def test_a_text_already_judged_is_passed_over(self) -> None:
        sung = candidate(HARBOR)
        won = lft.first_sung(
            [candidate(MEADOW), candidate(MEADOW.lower()), sung], HARBOR, words(QUARRY)
        )
        self.assertIs(won, sung)

    def test_nothing_wins_over_a_transcript_too_little_heard(self) -> None:
        self.assertIsNone(lft.first_sung([candidate(HARBOR)], LITTLE, words(QUARRY)))

    def test_no_candidate_after_the_winner_is_drawn(self) -> None:
        drawn: list[str] = []

        def lazily() -> Iterator[lft.Candidate]:
            for text in (MEADOW, HARBOR, FOUNDRY):
                drawn.append(text)
                yield candidate(text)

        won = lft.first_sung(lazily(), HARBOR, words(QUARRY))
        self.assertIsNotNone(won)
        self.assertEqual(drawn, [MEADOW, HARBOR])


class SkipReasonTest(fixtures.IsolatedCase):
    """Why a track with a record is left alone; None when it is fixed."""

    def test_a_wrong_text_is_fixed(self) -> None:
        self.assertIsNone(lft.skip_reason(plain(QUARRY), record(HARBOR, QUARRY)))

    def test_no_untimed_only_text_is_skipped(self) -> None:
        reason = lft.skip_reason(None, record(HARBOR, QUARRY))
        self.assertEqual(reason, lft.SKIP_NOT_UNTIMED)

    def test_a_text_changed_since_it_was_heard_is_skipped(self) -> None:
        reason = lft.skip_reason(plain(QUARRY), record(HARBOR, FOUNDRY))
        self.assertEqual(reason, lft.SKIP_CHANGED)

    def test_a_reformatted_text_is_the_same_text(self) -> None:
        reformatted = plain(QUARRY.upper().replace("\n", "\n\n"))
        self.assertIsNone(lft.skip_reason(reformatted, record(HARBOR, QUARRY)))

    def test_a_transcript_too_short_is_skipped(self) -> None:
        reason = lft.skip_reason(plain(QUARRY), record(LITTLE, QUARRY))
        self.assertEqual(reason, lft.SKIP_LITTLE)

    def test_a_sung_text_is_skipped(self) -> None:
        reason = lft.skip_reason(plain(HARBOR), record(HARBOR, HARBOR))
        self.assertEqual(reason, lft.SKIP_SUNG)
