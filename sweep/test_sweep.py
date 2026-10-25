"""The entry point's bootstrap, and the gate's way into the grouped suites in tests/.

The gate prunes tests/ and src/ by name and reaches them only through this file, so
`load_tests` below folds every suite in tests/ into this module's run; without it none
of them would run under the gate, and the private modules in src/ would be measured with
no tests at all. It also fails the run when any test it runs, this file's own included,
does not derive from the isolated base class, which is what keeps every test off the
network, out of the real home directory, away from the real ledger and away from any
real music library.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(_TOOL_ROOT), str(_TOOL_ROOT / "tests")]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures  # noqa: E402 - import follows the sys.path bootstrap above

#: How long the bootstrap probe's interpreter may take to start and import.
_PROBE_TIMEOUT_S = 60


def _grouped_suite() -> unittest.TestSuite:
    """Every test in tests/, discovered by walking the directory."""
    suite_dir = _TOOL_ROOT / "tests"
    return unittest.TestLoader().discover(
        str(suite_dir), pattern="test_*.py", top_level_dir=str(suite_dir)
    )


def _enforced_suite() -> unittest.TestSuite:
    """Every test the gate runs from this folder: this file's own and the grouped ones."""
    return unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])


class BootstrapTest(sweep_fixtures.IsolatedCase):
    """Importing the entry point makes its private modules and the album tool's importable."""

    def test_the_private_src_folder_is_its_own(self) -> None:
        self.assertEqual(sweep.SRC_DIR, _TOOL_ROOT / "src")

    def test_the_entry_point_alone_makes_every_module_importable(self) -> None:
        # A fresh, isolated interpreter whose path holds only the tool folder, so no
        # suite's own sys.path insert can stand in for the entry point's. It reports
        # through a file rather than a pipe, so the timeout binds.
        report = self.tmp / "found.txt"
        probe = (
            "import sys, pathlib; sys.path.insert(0, sys.argv[1]); "
            "import sweep, sweep_run, lyrics_manifest, library_walk; "
            "pathlib.Path(sys.argv[2]).write_text("
            "sweep_run.__file__, encoding='utf-8')"
        )
        subprocess.run(
            [sys.executable, "-B", "-I", "-c", probe, str(_TOOL_ROOT), str(report)],
            check=True,
            cwd=self.tmp,
            timeout=_PROBE_TIMEOUT_S,
        )
        self.assertEqual(Path(report.read_text(encoding="utf-8")).parent, sweep.SRC_DIR)


class GroupedSuiteIsolationTest(sweep_fixtures.IsolatedCase):
    """Every test the gate runs here uses the isolation, not merely most of them."""

    def test_every_test_this_folder_runs_uses_the_isolated_base(self) -> None:
        self.assertEqual(sweep_fixtures.isolation_offenders(_enforced_suite()), [])

    def test_the_enforced_suite_holds_this_files_tests_and_the_grouped_ones(
        self,
    ) -> None:
        ids = {
            test.id().rsplit(".", 1)[-1]
            for test in sweep_fixtures.tests_in(_enforced_suite())
        }
        self.assertIn("test_the_private_src_folder_is_its_own", ids)
        self.assertIn("test_a_round_trip_is_exact", ids)

    def test_a_case_off_the_isolated_base_is_named(self) -> None:
        class Stray(unittest.TestCase):
            def test_stray(self) -> None:
                """Never run: only its type is judged."""

        suite = unittest.TestSuite([Stray("test_stray")])
        self.assertEqual(len(sweep_fixtures.isolation_offenders(suite)), 1)


def load_tests(
    _loader: unittest.TestLoader, tests: unittest.TestSuite, _pattern: str | None
) -> unittest.TestSuite:
    """unittest's hook: fold every suite in tests/ in beside this file's own tests."""
    return unittest.TestSuite([tests, _grouped_suite()])


if __name__ == "__main__":
    unittest.main()
