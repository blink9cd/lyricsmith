"""Suite aggregator: runs every grouped suite under tests/.

The subject modules live under src/ and are imported by bare name, which works
because this file's own directory plus src/ are on sys.path (see _SRC below).
Each behavior has its own module in tests/; this file owns only the wiring and
the check on the suite's own shape: the floor assertions that keep a
silently-emptied suite from passing.
"""

import sys
import unittest
from collections.abc import Iterator
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
# The suites get this tool folder from discovery's `top_level_dir` below (they import
# as `tests.test_x`), and src/ from this insert.
# import-alone: ., src
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_TESTS = Path(__file__).resolve().parent / "tests"

#: Per-file floors. A test deleted from a grouped suite drops its file's count
#: below this and fails loudly, rather than shrinking the suite unnoticed.
#: Raise a floor when you add tests; never lower one to make a run pass.
#: Lower one only when its tests move to another file or lose their subject
#: with a removed feature: a floor kept above those can only be met by padding.
_MIN_GROUPED_TESTS_BY_FILE = {
    "test_isolation.py": 4,
    "test_at_types.py": 6,
    "test_at_models_manifest.py": 2,
    "test_cli_options.py": 18,
    "test_at_engine.py": 11,
    "test_at_discover.py": 10,
    "test_at_plan.py": 18,
    "test_at_label.py": 5,
    "test_at_place.py": 5,
    "test_at_summary.py": 5,
    "test_at_models_fetch.py": 16,
    "test_cli_transcribe.py": 20,
    "test_cli_fetch.py": 3,
    "test_label_roundtrip.py": 1,
    "test_cli_robustness.py": 13,
    "test_cli_prog.py": 2,
    "test_cli_hardening.py": 13,
    "test_live_whisper.py": 1,
}


def _grouped_suite() -> unittest.TestSuite:
    """Discover every test module under tests/."""
    return unittest.TestLoader().discover(str(_TESTS), top_level_dir=str(_TESTS.parent))


def _iter_cases(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    """Yield every actual TestCase leaf out of a nested suite tree.

    unittest.TestLoader().discover() nests three deep -- top, per-module,
    per-class -- not the flat two levels a fixed-depth loop would assume; a
    module with more than one test class makes a two-level walk land on inner
    TestSuite objects instead of TestCase instances, miscounting every file.
    Recursing keeps this correct regardless of how many classes a grouped file
    has.
    """
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_cases(item)
        else:
            yield item


class TestSuiteDiscovery(unittest.TestCase):
    """The grouped suites are actually discovered and actually run."""

    def test_every_grouped_file_meets_its_floor(self) -> None:
        found: dict[str, int] = {}
        for case in _iter_cases(_grouped_suite()):
            module = sys.modules[type(case).__module__]
            name = Path(str(module.__file__)).name
            found[name] = found.get(name, 0) + 1
        self.assertEqual(
            sorted(found),
            sorted(_MIN_GROUPED_TESTS_BY_FILE),
            "tests/ discovery and _MIN_GROUPED_TESTS_BY_FILE disagree on which "
            f"files exist: found {sorted(found)}, mapped "
            f"{sorted(_MIN_GROUPED_TESTS_BY_FILE)}",
        )
        for filename, floor in _MIN_GROUPED_TESTS_BY_FILE.items():
            self.assertGreaterEqual(
                found.get(filename, 0),
                floor,
                f"tests/{filename} yielded {found.get(filename, 0)} tests, expected "
                f"at least {floor} -- a test may have been removed from it",
            )


def load_tests(
    _loader: unittest.TestLoader, tests: unittest.TestSuite, _pattern: str | None
) -> unittest.TestSuite:
    """unittest's hook: run this module's own tests plus every grouped suite."""
    return unittest.TestSuite([tests, _grouped_suite()])


if __name__ == "__main__":
    unittest.main()
