"""Suite aggregator: runs every grouped suite under tests/, each held to its floor.

The gate reaches tests/ and src/ only through this file. It also fails the run when any
test it runs does not derive from album's isolated base class, which keeps every test off
the network and out of the real home folder.
"""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TESTS = _HERE / "tests"
# import-alone: ., src, tests
sys.path[:0] = [str(_HERE), str(_HERE / "src"), str(_TESTS)]

import sy_cases  # noqa: E402 - import follows the sys.path bootstrap above

#: Per-file floors. Raise one when you add tests; lower one only when its tests move to
#: another file or lose their subject.
_MIN_GROUPED_TESTS_BY_FILE = {
    "test_sy_types.py": 6,
    "test_sy_text.py": 11,
    # 6 of its tests moved with the rule they test to album's test_lyrics_heard.py.
    "test_sy_heard.py": 8,
    "test_sy_lines.py": 14,
    "test_sy_breaks.py": 26,
    "test_sy_lrc.py": 5,
    "test_sy_plan.py": 18,
    "test_sy_summary.py": 20,
    "test_sy_models.py": 4,
    "test_sy_request.py": 20,
    "test_sy_child.py": 29,
    "test_sy_whisper.py": 10,
    "test_sy_child_torch.py": 5,
    "test_sy_engine.py": 11,
    "test_sy_embed.py": 22,
    "test_sy_track.py": 23,
    # Its unreadable-folder test moved to test_sy_entries.py, with its twin.
    "test_sy_cli.py": 33,
    "test_sy_cli_heard.py": 6,
    "test_sy_live.py": 2,
    "test_sy_album.py": 4,
    "test_sy_reads.py": 3,
    "test_sy_located.py": 3,
    "test_sy_entries.py": 12,
    "test_sy_by_hand.py": 36,
    "test_sy_by_hand_cli.py": 11,
    "test_sy_heard_dir.py": 25,
    "test_sy_heard_dir_cli.py": 22,
    "test_sy_flag_defaults.py": 4,
    "test_sy_request_heard.py": 11,
    "test_sy_engine_hear.py": 10,
    "test_sy_child_hear.py": 4,
    "test_sy_kind.py": 5,
    "test_sy_heard_dir_spent.py": 6,
    "test_sy_hear_plan.py": 22,
    "test_sy_hear_cli.py": 31,
    "test_sy_hear_live.py": 1,
}


def _grouped_suite() -> unittest.TestSuite:
    """Every test in tests/."""
    return unittest.TestLoader().discover(
        str(_TESTS), pattern="test_*.py", top_level_dir=str(_TESTS)
    )


def _iter_cases(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    """Every case in *suite*, nested suites flattened."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_cases(item)
        else:
            yield item


class SuiteShapeTest(sy_cases.IsolatedCase):
    """The grouped suites are all there, each at its floor, each isolated."""

    def test_every_grouped_file_meets_its_floor(self) -> None:
        counts = Counter(
            f"{type(case).__module__.split('.')[-1]}.py"
            for case in _iter_cases(_grouped_suite())
        )
        self.assertEqual(set(counts), set(_MIN_GROUPED_TESTS_BY_FILE))
        for name, floor in _MIN_GROUPED_TESTS_BY_FILE.items():
            self.assertGreaterEqual(counts[name], floor, name)

    def test_every_test_is_isolated(self) -> None:
        suite = unittest.TestSuite([_grouped_suite()])
        self.assertEqual(sy_cases.fixtures.isolation_offenders(suite), [])


def load_tests(
    _loader: unittest.TestLoader, tests: unittest.TestSuite, _pattern: str | None
) -> unittest.TestSuite:
    """This module's tests and every grouped suite."""
    return unittest.TestSuite([tests, _grouped_suite()])


if __name__ == "__main__":
    unittest.main()
