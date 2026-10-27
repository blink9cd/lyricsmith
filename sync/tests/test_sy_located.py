"""Every private module imports alone, the other tools' modules and the libraries located."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path

import located_checks  # noqa: E402 - album's, found beside its fixtures
import sync  # noqa: E402 - import follows the sys.path bootstrap above

#: The module each private module that needs a shared library imports first.
LOCATED = "sy_located"
#: How long the probe's interpreter may take to start and import.
_PROBE_TIMEOUT_S = 60
#: The probe: sync's src/ first on the path, then a marker, then the folders given
#: last; import the located module and write the path it left.
_PROBE = (
    "import json, sys, pathlib; src, marker, out, *late = sys.argv[1:]; "
    "sys.path[:0] = [src, marker]; sys.path += late; "
    f"import {LOCATED}; "
    "pathlib.Path(out).write_text(json.dumps(sys.path), encoding='utf-8')"
)


class LocatedTest(sy_cases.IsolatedCase):
    """The located module comes before any shared library, and every module imports alone."""

    def test_no_module_imports_a_shared_library_before_locating_it(self) -> None:
        self.assertEqual(located_checks.late_shared_imports(sync.SRC_DIR, LOCATED), [])

    def test_every_private_module_imports_alone(self) -> None:
        self.assertEqual(located_checks.alone_failures(sync.SRC_DIR, self.tmp), [])

    def test_the_sibling_srcs_move_ahead_of_a_later_entry(self) -> None:
        project = sync.SRC_DIR.parents[1]
        late = [str(project / tool / "src") for tool in ("sweep", "transcribe")]
        marker, out = str(self.tmp / "marker"), self.tmp / "path.json"
        subprocess.run(
            [sys.executable, "-B", "-I", "-c", _PROBE, str(sync.SRC_DIR), marker]
            + [str(out), *late],
            check=True,
            cwd=self.tmp,
            timeout=_PROBE_TIMEOUT_S,
        )
        path = json.loads(out.read_text(encoding="utf-8"))
        for folder in late:
            with self.subTest(folder=folder):
                self.assertEqual(path.count(folder), 1)
                self.assertLess(path.index(folder), path.index(marker))
        self.assertLess(path.index(late[0]), path.index(late[1]))
