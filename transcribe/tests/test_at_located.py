"""Every private module imports alone: each one that needs a shared library locates it.

The checks are the album tool's, loaded from that tool's tests folder by file rather than
put on the path: that folder is a package named `tests`, the name this folder answers to.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import transcribe
from tests._isolation import IsolatedCase

#: The module each private module that needs a shared library imports first.
LOCATED = "at_located"
#: The album tool's import checks, a fixed file under the project root.
_CHECKS = Path(__file__).resolve().parents[2] / "album" / "tests" / "located_checks.py"


def _load_checks() -> ModuleType:
    """The album tool's import checks, loaded from their file."""
    found = importlib.util.spec_from_file_location(_CHECKS.stem, _CHECKS)
    assert found is not None and found.loader is not None  # a .py file always has one
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    return module


located_checks = _load_checks()


class LocatedTest(IsolatedCase):
    """The located module comes before any shared library, and every module imports alone."""

    def test_no_module_imports_a_shared_library_before_locating_it(self) -> None:
        late = located_checks.late_shared_imports(transcribe.SRC_DIR, LOCATED)
        self.assertEqual(late, [])

    def test_every_private_module_imports_alone(self) -> None:
        failures = located_checks.alone_failures(transcribe.SRC_DIR, self.root)
        self.assertEqual(failures, [])
