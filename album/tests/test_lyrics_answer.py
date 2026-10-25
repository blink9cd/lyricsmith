"""One LRC provider's answer for both kinds of step: each kind's text, or its failure.

Every lyric here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_answer as la  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED


class LrcAnswerTest(fixtures.IsolatedCase):
    """Each kind reads its own text; a kind left unsettled raises its failure."""

    def test_each_kind_reads_its_own_text(self) -> None:
        answer = la.LrcAnswer(synced="[00:01.00]timed", plain="untimed")
        self.assertEqual(answer.text(S), "[00:01.00]timed")
        self.assertEqual(answer.text(U), "untimed")

    def test_the_same_text_answers_both_kinds(self) -> None:
        for text in ("words", None):
            with self.subTest(text=text):
                answer = la.LrcAnswer.same(text)
                self.assertEqual((answer.text(S), answer.text(U)), (text, text))

    def test_an_unsettled_kind_raises_its_failure_and_the_other_still_reads(
        self,
    ) -> None:
        failure = lp.ProviderError("the search failed")
        answer = la.LrcAnswer(synced=failure, plain="untimed")
        with self.assertRaises(lp.ProviderError) as raised:
            answer.text(S)
        self.assertIs(raised.exception, failure)
        self.assertEqual(answer.text(U), "untimed")
