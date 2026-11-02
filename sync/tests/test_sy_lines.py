"""Line starts, confidence and the first-line flag, from each token's frame span."""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_lines as sl  # noqa: E402 - import follows the sys.path bootstrap above
from sy_text import NO_LINE  # noqa: E402 - import follows the sys.path bootstrap above

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")
S = sl.Span


@hs.composite
def _alignment(draw: hs.DrawFn) -> tuple[tuple[int, ...], tuple[sl.Span, ...], int]:
    """A token-to-line list in line order, with monotonic spans, and its line count."""
    line_count = draw(hs.integers(1, 8))
    letters = sorted(draw(hs.lists(hs.integers(0, line_count - 1), max_size=40)))
    tokens: list[int] = []
    for index, line in enumerate(letters):
        if index and draw(hs.booleans()):
            tokens.append(NO_LINE)
        tokens.append(line)
    spans, frame = [], 0
    for _ in tokens:
        frame += draw(hs.integers(0, 5))
        spans.append(S(frame, frame + 1, draw(hs.floats(0.0, 1.0))))
        frame += 1
    return tuple(tokens), tuple(spans), line_count


class LineStartsTest(sy_cases.IsolatedCase):
    def test_a_separator_early_in_a_pause_does_not_move_the_next_line(self) -> None:
        # The separator before line 2 sits at frame 2, where the pause after line 1
        # begins; line 2 is sung at frame 50, and must start there.
        line_of = (0, NO_LINE, 1)
        spans = (S(0, 1, 0.9), S(2, 3, 0.9), S(50, 51, 0.9))
        self.assertEqual(sl.line_starts(line_of, spans, 2, 0.5), (0.0, 25.0))

    def test_a_line_with_no_letters_takes_the_next_lines_start(self) -> None:
        line_of = (1,)
        self.assertEqual(sl.line_starts(line_of, (S(8, 9, 0.5),), 2, 0.5), (4.0, 4.0))

    def test_a_last_line_with_no_letters_starts_where_the_last_sung_line_ends(
        self,
    ) -> None:
        # A trailing [Outro] stamped at the last sung line's start would tie with it,
        # and a player showing the later of two tied lines would hide the sung one.
        line_of = (0, 0, NO_LINE, 1, 1)
        spans = (S(2, 3, 0.5), S(4, 5, 0.5), S(5, 6, 0.5), S(8, 9, 0.5), S(10, 12, 0.5))
        starts = sl.line_starts(line_of, spans, 4, 0.5)
        self.assertEqual(starts, (1.0, 4.0, 6.0, 6.0))

    def test_no_letters_at_all_start_at_zero(self) -> None:
        self.assertEqual(sl.line_starts((), (), 2, 0.5), (0.0, 0.0))

    def test_each_lines_start_frame_is_the_frame_its_start_is_read_from(self) -> None:
        line_of = (0, 0, NO_LINE, 2, 2)
        spans = (S(2, 3, 0.5), S(4, 5, 0.5), S(5, 6, 0.5), S(8, 9, 0.5), S(10, 12, 0.5))
        self.assertEqual(sl.line_start_frames(line_of, spans, 4), (2, 8, 8, 12))

    @given(_alignment(), hs.floats(0.001, 1.0))
    def test_fuzz_line_starts(
        self,
        alignment: tuple[tuple[int, ...], tuple[sl.Span, ...], int],
        seconds: float,
    ) -> None:
        line_of, spans, line_count = alignment
        starts = sl.line_starts(line_of, spans, line_count, seconds)
        self.assertEqual(len(starts), line_count)
        self.assertEqual(list(starts), sorted(starts))


class MeasureTest(sy_cases.IsolatedCase):
    def test_frames_needed_counts_a_blank_between_repeats(self) -> None:
        self.assertEqual(sl.frames_needed((5, 5, 6)), 4)

    def test_frames_needed_adds_no_blank_between_different_tokens(self) -> None:
        self.assertEqual(sl.frames_needed((5, 6, 7)), 3)

    def test_a_track_with_nothing_aligned_measures_zero_and_is_low_confidence(
        self,
    ) -> None:
        timing = sl.measure((), (), 2, 0.02)
        self.assertEqual(timing, sl.Timing((0.0, 0.0), 0.0, 0.0, 0.0, 2))
        self.assertTrue(sl.low_confidence(timing))
        self.assertFalse(sl.first_line_stretched(timing))

    def test_a_first_line_well_over_twice_the_median_span_is_stretched(self) -> None:
        # Line spans of 5, 2 and 2 frames: the first is two and a half times the median.
        line_of = (0, 0, NO_LINE, 1, 1, NO_LINE, 2, 2)
        spans = (
            S(0, 1, 0.5), S(4, 5, 0.5), S(5, 6, 0.5), S(6, 7, 0.5),
            S(7, 8, 0.5), S(8, 9, 0.5), S(9, 10, 0.5), S(10, 11, 0.5),
        )  # fmt: skip
        self.assertTrue(sl.first_line_stretched(sl.measure(line_of, spans, 3, 0.02)))

    def test_confidence_is_the_median_of_each_lines_mean(self) -> None:
        line_of = (0, 0, NO_LINE, 1, NO_LINE, 2)
        spans = (
            S(0, 1, 0.2), S(1, 2, 0.4), S(2, 3, 1.0),
            S(3, 4, 0.9), S(4, 5, 1.0), S(5, 6, 0.1),
        )  # fmt: skip
        timing = sl.measure(line_of, spans, 3, 0.02)
        self.assertAlmostEqual(timing.confidence, 0.3)
        self.assertEqual(timing.unaligned, 0)

    def test_a_blank_line_is_not_counted_as_a_line_with_nothing_to_align(self) -> None:
        # Lines 1 and 3 are blank lines between verses, line 4 a section header.
        line_of = (0, NO_LINE, 2, NO_LINE, 5)
        spans = (S(0, 1, 0.9), S(1, 2, 0.9), S(4, 5, 0.9), S(5, 6, 0.9), S(9, 10, 0.9))
        timing = sl.measure(line_of, spans, 6, 0.5, blank=2)
        self.assertEqual(timing.unaligned, 1)
        self.assertEqual(timing.starts, (0.0, 2.0, 2.0, 4.5, 4.5, 4.5))

    def test_a_low_median_is_low_confidence(self) -> None:
        low = sl.measure((0,), (S(0, 1, 0.1),), 1, 0.02)
        self.assertTrue(sl.low_confidence(low))
        self.assertFalse(sl.low_confidence(sl.measure((0,), (S(0, 1, 0.5),), 1, 0.02)))

    def test_a_first_line_over_twice_the_median_span_is_stretched(self) -> None:
        line_of = (0, 0, NO_LINE, 1, 1, NO_LINE, 2, 2)
        spans = (
            S(0, 1, 0.5), S(99, 100, 0.5), S(100, 101, 0.5), S(110, 111, 0.5),
            S(119, 120, 0.5), S(120, 121, 0.5), S(130, 131, 0.5), S(139, 140, 0.5),
        )  # fmt: skip
        self.assertTrue(sl.first_line_stretched(sl.measure(line_of, spans, 3, 0.02)))
        even = line_of, spans[:1] + (S(9, 10, 0.5),) + spans[2:]
        self.assertFalse(sl.first_line_stretched(sl.measure(*even, 3, 0.02)))
