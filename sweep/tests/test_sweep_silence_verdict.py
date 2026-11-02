"""The silence pass's verdict on a silent track, and the placeholder-title test it uses.

A silent track is judged from its title, its length and how many silent tracks its album
holds, first matching rule winning: a placeholder title on a short track in an album of
several silent tracks is a spacer run, on a short track alone a gap track (both likely
meant); a real title on a longer track is likely a ripping mistake; anything else,
an unknown length included, is unsure. Every title here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence_verdict as sv  # noqa: E402 - import follows the sys.path bootstrap above

# A registered profile of its own name, as the ledger suite does. Another suite's
# profile loaded later in the same process cannot replace it, as `@given` copies the
# settings loaded when it decorates a test. deadline=None: a per-example deadline
# measures machine load, not correctness.
settings.register_profile(
    "sweep_silence_verdict_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("sweep_silence_verdict_properties")

_SHORT = sv.GAP_MAX_S
_LONG = sv.GAP_MAX_S + 0.5
_RUN = sv.SPACER_RUN_MIN


class PlaceholderTitleTest(sf.IsolatedCase):
    """Which titles count as placeholders, after folding case, spacing and punctuation."""

    def test_these_titles_are_placeholders(self) -> None:
        for title in (
            None,
            "",
            "   ",
            "[]",
            "Untitled",
            "  [UNTITLED]  ",
            "(untitled.)",
            "Silence",
            "silent",
            "- Silent -",
            "Track 5",
            "track05",
            "Track_12",
            "[Track 3]",
            "Track ５",
            # An Arabic-Indic five, a digit NFKC leaves as it is.
            "Track \u0665",
        ):
            with self.subTest(title=title):
                self.assertTrue(sv.is_placeholder_title(title))

    def test_these_titles_are_real(self) -> None:
        for title in (
            "Track",
            "Track Five",
            "5",
            "Silent Night Rain",
            "Untitled Harbor",
            "Tracks 5",
            "Back on Track 2",
            "Paper Boats",
        ):
            with self.subTest(title=title):
                self.assertFalse(sv.is_placeholder_title(title))


class VerdictTest(sf.IsolatedCase):
    """The four rules, first match winning, each with a short reason."""

    def test_a_short_placeholder_in_a_run_is_a_spacer_run(self) -> None:
        judged = sv.judge_silent_track("Untitled", _SHORT, _RUN)
        self.assertEqual(judged.verdict, sv.Verdict.LIKELY_MEANT)
        self.assertIn("spacer run", judged.reason)

    def test_a_short_placeholder_alone_is_a_gap_track(self) -> None:
        judged = sv.judge_silent_track("Track 4", 3.0, _RUN - 1)
        self.assertEqual(judged.verdict, sv.Verdict.LIKELY_MEANT)
        self.assertIn("gap track", judged.reason)

    def test_a_long_real_title_is_likely_a_ripping_mistake(self) -> None:
        judged = sv.judge_silent_track("Paper Boats", _LONG, _RUN)
        self.assertEqual(judged.verdict, sv.Verdict.RIPPING_MISTAKE)
        self.assertIn("real title", judged.reason)

    def test_a_long_placeholder_is_unsure(self) -> None:
        judged = sv.judge_silent_track("Silence", _LONG, _RUN)
        self.assertEqual(judged.verdict, sv.Verdict.UNSURE)
        self.assertIn("placeholder title", judged.reason)

    def test_a_short_real_title_is_unsure(self) -> None:
        judged = sv.judge_silent_track("Paper Boats", _SHORT, 1)
        self.assertEqual(judged.verdict, sv.Verdict.UNSURE)
        self.assertIn("real title", judged.reason)

    def test_an_unknown_length_is_always_unsure(self) -> None:
        for title in ("Untitled", "Paper Boats"):
            with self.subTest(title=title):
                judged = sv.judge_silent_track(title, None, _RUN)
                self.assertEqual(judged.verdict, sv.Verdict.UNSURE)
                self.assertIn("length unknown", judged.reason)

    def test_a_length_that_is_not_finite_is_unknown(self) -> None:
        for length in (float("nan"), float("inf"), float("-inf")):
            for title in ("Untitled", "Paper Boats"):
                with self.subTest(length=length, title=title):
                    judged = sv.judge_silent_track(title, length, _RUN)
                    self.assertEqual(judged.verdict, sv.Verdict.UNSURE)
                    self.assertIn("length unknown", judged.reason)

    def test_the_verdicts_read_as_the_list_writes_them(self) -> None:
        self.assertEqual(
            [str(verdict) for verdict in sv.Verdict],
            ["likely meant", "likely a ripping mistake", "unsure", "not measured"],
        )

    def test_the_reason_never_holds_a_tab_or_a_line_break(self) -> None:
        judged = sv.judge_silent_track("Track 1", 2.25, _RUN)
        self.assertNotRegex(judged.reason, "[\t\r\n]")

    @given(
        st.none() | st.text(max_size=40),
        st.none() | st.floats(),
        st.integers(min_value=0, max_value=500),
    )
    def test_every_track_gets_a_judged_verdict(
        self, title: str | None, length_s: float | None, silent: int
    ) -> None:
        judged = sv.judge_silent_track(title, length_s, silent)
        self.assertIsNot(judged.verdict, sv.Verdict.NOT_MEASURED)
        self.assertTrue(judged.reason)
