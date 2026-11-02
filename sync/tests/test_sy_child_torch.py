"""The child's PyTorch steps no live clip reaches: the break mask and the frame levels.

Skipped unless this interpreter has PyTorch and numpy, as the live smoke test is, so an
interpreter without the sync install skips the whole class. The tensors are small
and invented: no model is loaded and no audio decoded. Each expected value is computed
here from the rule as measured, not read back from the child.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_child  # noqa: E402 - import follows the sys.path bootstrap above
from sy_text import BLANK  # noqa: E402 - import follows the sys.path bootstrap above

READY = all(importlib.util.find_spec(name) for name in ("torch", "numpy"))
#: Invented emissions: frames by the model's labels, each a distinct value.
FRAMES, LABEL_COUNT = 12, 5
#: Two breaks, with untouched frames before, between and after them.
RANGES = ((2, 4), (7, 10))
#: The second window's samples: a tail shorter than a whole window.
TAIL_SAMPLES = 8000
#: Emission frames for a whole window and for the tail; neither divides its samples
#: evenly, so the slice edges are rounded down as measured.
WINDOW_FRAMES = (999, 24)
#: The other stems the rest of the band is made of: drums, bass, other.
REST_STEMS = 3
#: Levels agree to float32's precision; a broken rule misses by far more.
PLACES = 6


def expected_levels(samples: Any, frames: int) -> list[float]:
    """*samples*' RMS over each of *frames* slices, as the break rule was measured."""
    import numpy

    edges = numpy.linspace(0, samples.shape[0], frames + 1).astype(int)
    return [
        float(numpy.sqrt(numpy.mean(samples[edges[i] : edges[i + 1]] ** 2) + 1e-12))
        for i in range(frames)
    ]


def windowed_levels(samples: Any) -> list[float]:
    """*samples*' frame levels window by window, each window cut on its own."""
    found: list[float] = []
    for index, frames in enumerate(WINDOW_FRAMES):
        start = index * sy_child.WINDOW_SAMPLES
        found += expected_levels(
            samples[start : start + sy_child.WINDOW_SAMPLES], frames
        )
    return found


@unittest.skipUnless(READY, "needs PyTorch and numpy")
class ChildTorchTest(sy_cases.IsolatedCase):
    def emissions(self) -> Any:
        """Invented log-probabilities, every entry distinct and none the mask's."""
        import torch

        return (
            -torch.arange(FRAMES * LABEL_COUNT, dtype=torch.float32).view(
                FRAMES, LABEL_COUNT
            )
            - 0.5
        )

    def test_the_mask_forbids_every_letter_in_each_range_but_the_blank(self) -> None:
        masked = sy_child._mask(self.emissions(), RANGES)
        for start, end in RANGES:
            for row in range(start, end):
                for label in range(LABEL_COUNT):
                    expected = 0.0 if label == BLANK else sy_child.FORBIDDEN_LOG_PROB
                    self.assertEqual(float(masked[row, label]), expected)

    def test_the_mask_leaves_rows_outside_and_its_input_untouched(self) -> None:
        import torch

        emissions = self.emissions()
        masked = sy_child._mask(emissions, RANGES)
        self.assertTrue(torch.equal(emissions, self.emissions()))
        inside = {row for start, end in RANGES for row in range(start, end)}
        for row in set(range(FRAMES)) - inside:
            self.assertTrue(torch.equal(masked[row], emissions[row]))

    def test_a_frames_level_is_its_slices_rms(self) -> None:
        import torch

        window = torch.randn(sy_child.WINDOW_SAMPLES, generator=self.generator())
        found = sy_child._frame_levels(window, WINDOW_FRAMES[0])
        expected = expected_levels(window.numpy(), WINDOW_FRAMES[0])
        self.assert_levels(found, expected)

    def test_each_window_is_cut_into_frames_on_its_own(self) -> None:
        vocal, rest = self.track()
        found = sy_child._levels(vocal, rest, WINDOW_FRAMES)
        self.assertEqual(len(found.vocal), sum(WINDOW_FRAMES))
        self.assert_levels(list(found.vocal), windowed_levels(vocal.numpy()))

    def test_the_rests_level_is_the_root_sum_of_squares_of_the_other_stems(
        self,
    ) -> None:
        vocal, rest = self.track()
        found = sy_child._levels(vocal, rest, WINDOW_FRAMES)
        stems = [windowed_levels(stem.numpy()) for stem in rest]
        expected = [math.sqrt(sum(x * x for x in frame)) for frame in zip(*stems)]
        self.assert_levels(list(found.rest), expected)

    def track(self) -> tuple[Any, list[Any]]:
        """A mono vocal and the other stems, a whole window and a tail long, each louder."""
        import torch

        length = sy_child.WINDOW_SAMPLES + TAIL_SAMPLES
        generator = self.generator()
        vocal = torch.randn(length, generator=generator)
        rest = [
            (index + 2) * torch.randn(length, generator=generator)
            for index in range(REST_STEMS)
        ]
        return vocal, rest

    def generator(self) -> Any:
        """A seeded generator, so a failure repeats."""
        import torch

        return torch.Generator().manual_seed(0)

    def assert_levels(self, found: list[float], expected: list[float]) -> None:
        """*found* matches *expected* frame by frame."""
        self.assertEqual(len(found), len(expected))
        for index, (got, want) in enumerate(zip(found, expected, strict=True)):
            self.assertAlmostEqual(got, want, places=PLACES, msg=f"frame {index}")
