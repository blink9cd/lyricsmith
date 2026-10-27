"""Every private module imports alone: each one that needs a shared library locates it."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import located_checks  # noqa: E402 - import follows the sys.path bootstrap above

#: The module each private module that needs a shared library imports first.
LOCATED = "lyrics_located"


class LocatedTest(fixtures.IsolatedCase):
    """The located module comes before any shared library, and every module imports alone."""

    def test_no_module_imports_a_shared_library_before_locating_it(self) -> None:
        self.assertEqual(located_checks.late_shared_imports(album.SRC_DIR, LOCATED), [])

    def test_every_private_module_imports_alone(self) -> None:
        self.assertEqual(located_checks.alone_failures(album.SRC_DIR, self.tmp), [])
