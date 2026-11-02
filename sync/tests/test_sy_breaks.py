"""Long instrumental breaks: the vocal stem far under the band, found from per-frame levels.

Every level profile here is synthesized: a constant band, with the vocal stem dropped far
under it over a chosen stretch of frames.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_breaks as sb  # noqa: E402 - import follows the sys.path bootstrap above
from sy_lines import Span  # noqa: E402 - import follows the sys.path bootstrap above
from sy_text import NO_LINE, tokenize  # noqa: E402 - follows the bootstrap above

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")
#: wav2vec2's own frame length, about 20 ms.
MODEL_FRAME_S = 0.02
#: One frame a second: no smoothing (a one-frame window) and a two-frame margin, so a
#: boundary case reads frame by frame.
WHOLE_SECOND = 1.0
#: The band's level, and the vocal stem's when it is sung (0 dB against the band).
LOUD = 1.0
#: The vocal stem 40 dB under the band.
QUIET = 0.01


def profile(
    frames: int, quiet: range, vocal_low: float = QUIET
) -> tuple[list[float], ...]:
    """Vocal and band levels over *frames*, the vocal stem at *vocal_low* over *quiet*."""
    vocal = [vocal_low if index in quiet else LOUD for index in range(frames)]
    return vocal, [LOUD] * frames


class RatioTest(sy_cases.IsolatedCase):
    def test_the_ratio_is_the_vocal_level_against_the_band_in_db(self) -> None:
        ratio = sb.ratio_db([1.0, 0.1, 0.0], [1.0, 1.0, 0.0])
        self.assertAlmostEqual(ratio[0], 0.0)
        self.assertAlmostEqual(ratio[1], -20.0, places=5)
        self.assertAlmostEqual(ratio[2], 0.0)

    def test_a_level_that_is_no_rms_is_refused(self) -> None:
        for level in (-1.0, float("nan"), float("inf")):
            with self.subTest(level=level):
                with self.assertRaises(ValueError):
                    sb.ratio_db([level], [1.0])

    def test_levels_of_different_lengths_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            sb.ratio_db([1.0, 1.0], [1.0])

    def test_the_rest_is_the_root_sum_of_squares_of_the_other_stems(self) -> None:
        self.assertEqual(sb.combined_level([[3.0, 0.0], [4.0, 2.0]]), [5.0, 2.0])

    def test_the_rest_of_stems_of_different_lengths_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            sb.combined_level([[1.0, 1.0], [1.0]])


class SmoothingTest(sy_cases.IsolatedCase):
    def test_an_odd_window_is_a_centered_running_median_edge_padded(self) -> None:
        # Three frames at a third of a second: one copy of each end pads the window.
        self.assertEqual(sb.smoothed([5.0, 1.0, 9.0, 2.0], 1 / 3), [5.0, 5.0, 2.0, 2.0])

    def test_an_even_window_takes_the_mean_of_its_two_middle_values(self) -> None:
        # Two frames at half a second: one copy of the first value on the left, none on
        # the right, as numpy's edge pad and sliding window give.
        self.assertEqual(sb.smoothed([0.0, 10.0, 20.0], 0.5), [0.0, 5.0, 15.0])

    def test_a_frame_longer_than_the_window_leaves_the_values_as_they_are(self) -> None:
        self.assertEqual(sb.smoothed([3.0, -1.0], 2.0), [3.0, -1.0])

    def test_no_frames_smooth_to_none(self) -> None:
        self.assertEqual(sb.smoothed([], MODEL_FRAME_S), [])


class BreakRangesTest(sy_cases.IsolatedCase):
    def test_a_quiet_stretch_under_the_band_is_one_trimmed_break(self) -> None:
        vocal, rest = profile(3000, range(1000, 2000))
        # The 50-frame median turns the quiet stretch into frames 1000 to 2000, both
        # ends included: at each edge exactly half the window is quiet, and the mean of
        # -40 dB and 0 dB is under the threshold. 75 frames are trimmed from each edge.
        self.assertEqual(
            sb.break_ranges(vocal, rest, MODEL_FRAME_S, after_frame=10),
            ((1075, 1926),),
        )

    def test_a_quiet_stretch_shorter_than_twelve_seconds_is_no_break(self) -> None:
        vocal, rest = profile(3000, range(1000, 1550))
        self.assertEqual(sb.break_ranges(vocal, rest, MODEL_FRAME_S, 10), ())

    def test_a_quiet_stretch_before_the_second_line_is_the_intro(self) -> None:
        vocal, rest = profile(3000, range(0, 1000))
        self.assertEqual(sb.break_ranges(vocal, rest, MODEL_FRAME_S, 1200), ())

    def test_a_break_starting_at_the_second_lines_frame_is_dropped(self) -> None:
        vocal, rest = profile(40, range(10, 30))
        # Trimmed, the stretch starts at frame 12: dropped at 12, kept at 11.
        self.assertEqual(sb.break_ranges(vocal, rest, WHOLE_SECOND, 12), ())
        self.assertEqual(sb.break_ranges(vocal, rest, WHOLE_SECOND, 11), ((12, 28),))

    def test_vocals_loud_against_the_band_are_no_break(self) -> None:
        vocal, rest = profile(3000, range(1000, 2000), vocal_low=0.5)
        self.assertEqual(sb.break_ranges(vocal, rest, MODEL_FRAME_S, 10), ())

    def test_exactly_twelve_seconds_is_a_break_and_eleven_is_not(self) -> None:
        ratio = [0.0] * 5 + [-20.0] * 12 + [0.0] * 5 + [-20.0] * 11 + [0.0] * 5
        self.assertEqual(sb.ratio_breaks(ratio, WHOLE_SECOND, 0), ((7, 15),))

    def test_a_ratio_of_exactly_the_threshold_is_not_under_it(self) -> None:
        ratio = [0.0] * 5 + [sb.BREAK_RATIO_DB] * 20 + [0.0] * 5
        self.assertEqual(sb.ratio_breaks(ratio, WHOLE_SECOND, 0), ())
        ratio[5:25] = [sb.BREAK_RATIO_DB - 0.01] * 20
        self.assertEqual(sb.ratio_breaks(ratio, WHOLE_SECOND, 0), ((7, 23),))

    def test_a_stretch_no_longer_than_both_margins_is_trimmed_away(self) -> None:
        # The measured values never reach this: twelve seconds outlast two margins. A
        # margin of six seconds at one frame a second takes all twelve frames.
        ratio = [0.0] * 3 + [-20.0] * 12 + [0.0] * 3
        with mock.patch.object(sb, "BREAK_MARGIN_S", 6.0):
            self.assertEqual(sb.ratio_breaks(ratio, WHOLE_SECOND, 0), ())
        with mock.patch.object(sb, "BREAK_MARGIN_S", 5.0):
            self.assertEqual(sb.ratio_breaks(ratio, WHOLE_SECOND, 0), ((8, 10),))

    def test_a_break_running_to_the_last_frame_is_found(self) -> None:
        ratio = [0.0] * 5 + [-20.0] * 15
        self.assertEqual(sb.ratio_breaks(ratio, WHOLE_SECOND, 0), ((7, 18),))

    def test_two_breaks_come_back_in_order(self) -> None:
        ratio = ([0.0] * 3 + [-20.0] * 14) * 2
        self.assertEqual(sb.ratio_breaks(ratio, WHOLE_SECOND, 0), ((5, 15), (22, 32)))

    def test_a_frame_length_that_is_no_length_is_refused(self) -> None:
        for frame_seconds in (0.0, -0.02, float("nan"), float("inf")):
            with self.subTest(frame_seconds=frame_seconds):
                with self.assertRaises(ValueError):
                    sb.ratio_breaks([0.0], frame_seconds, 0)

    def test_the_measured_values_are_kept(self) -> None:
        self.assertEqual(
            (sb.BREAK_RATIO_DB, sb.BREAK_MIN_S, sb.BREAK_MARGIN_S, sb.RATIO_SMOOTH_S),
            (-12.0, 12.0, 1.5, 1.0),
        )

    @given(
        hs.lists(
            hs.tuples(
                hs.sampled_from((0.0, 1e-6, QUIET, 0.5, LOUD))
                | hs.floats(0.0, 1e6, allow_nan=False),
                hs.sampled_from((0.0, QUIET, LOUD))
                | hs.floats(0.0, 1e6, allow_nan=False),
            ),
            max_size=300,
        ),
        hs.floats(0.05, 2.0),
        hs.integers(-5, 300),
    )
    def test_fuzz_break_ranges(
        self, levels: list[tuple[float, float]], frame_seconds: float, after: int
    ) -> None:
        vocal, rest = [v for v, _ in levels], [r for _, r in levels]
        ranges = sb.break_ranges(vocal, rest, frame_seconds, after)
        margin = round(sb.BREAK_MARGIN_S / frame_seconds)
        previous_end = 0
        for start, end in ranges:
            self.assertGreater(start, after)
            self.assertLessEqual(previous_end, start)
            self.assertLess(start, end)
            self.assertLessEqual(end, len(levels))
            self.assertGreaterEqual(
                (end - start + 2 * margin) * frame_seconds, sb.BREAK_MIN_S
            )
            previous_end = end


class SecondLineTest(sy_cases.IsolatedCase):
    def test_the_second_line_starts_at_its_own_first_letter(self) -> None:
        line_of = (0, NO_LINE, 1, 1)
        spans = (Span(0, 1, 0.9), Span(2, 3, 0.9), Span(40, 41, 0.9), Span(41, 42, 0.9))
        self.assertEqual(sb.second_line_frame(line_of, spans), 40)

    def test_a_second_line_with_no_letters_takes_the_next_aligned_lines(self) -> None:
        # Line 1 is a section header: nothing of it was tokenized.
        line_of = (0, NO_LINE, 2)
        spans = (Span(0, 1, 0.9), Span(2, 3, 0.9), Span(70, 71, 0.9))
        self.assertEqual(sb.second_line_frame(line_of, spans), 70)

    def test_blank_lines_between_verses_leave_the_second_lines_frame(self) -> None:
        # The same letters at the same frames, with and without blank lines: a blank
        # at index 1 takes the next aligned line's frame, so the break check is unmoved.
        plain = tokenize(("la la", "lo", "li"))
        spaced = tokenize(("la la", "", "lo", "", "", "li"))
        spans = tuple(Span(10 * i, 10 * i + 1, 0.9) for i in range(len(plain.ids)))
        self.assertEqual(spaced.ids, plain.ids)
        self.assertEqual(
            sb.second_line_frame(spaced.line_of, spans),
            sb.second_line_frame(plain.line_of, spans),
        )
        self.assertEqual(sb.second_line_frame(spaced.line_of, spans), 60)

    def test_a_track_of_one_aligned_line_has_no_second_line(self) -> None:
        self.assertIsNone(sb.second_line_frame((0, NO_LINE, 0), (Span(0, 1, 0.9),) * 3))
        self.assertIsNone(sb.second_line_frame((), ()))
