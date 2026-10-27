"""Every private module imports alone, the album tool's modules and the libraries located."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above

sys.path.append(str(sweep.ALBUM_ENTRY.parent / "tests"))

import located_checks  # noqa: E402 - album's, found beside its fixtures

#: The module each private module that needs a shared library imports first.
LOCATED = "sweep_located"
#: How long the probe's interpreter may take to start and import.
_PROBE_TIMEOUT_S = 60
#: The probe: *src* first on the path, then a marker, then the folders given last;
#: import the located module and write the path it left.
_PROBE = (
    "import json, sys, pathlib; src, marker, out, *late = sys.argv[1:]; "
    "sys.path[:0] = [src, marker]; sys.path += late; "
    "import {module}; "
    "pathlib.Path(out).write_text(json.dumps(sys.path), encoding='utf-8')"
)


def path_after_locating(
    module: str, src: Path, late: list[str], tmp: Path
) -> tuple[list[str], str]:
    """The path a fresh interpreter holds once *module* is imported, and its marker.

    *src* goes first, then the marker, then *late* at the end, where an earlier
    bootstrap would have left them.
    """
    marker, out = str(tmp / "marker"), tmp / "path.json"
    subprocess.run(
        [sys.executable, "-B", "-I", "-c", _PROBE.format(module=module), str(src)]
        + [marker, str(out), *late],
        check=True,
        cwd=tmp,
        timeout=_PROBE_TIMEOUT_S,
    )
    return json.loads(out.read_text(encoding="utf-8")), marker


class LocatedTest(sf.IsolatedCase):
    """The located module comes before any shared library, and every module imports alone."""

    def test_no_module_imports_a_shared_library_before_locating_it(self) -> None:
        self.assertEqual(located_checks.late_shared_imports(sweep.SRC_DIR, LOCATED), [])

    def test_every_private_module_imports_alone(self) -> None:
        self.assertEqual(located_checks.alone_failures(sweep.SRC_DIR, self.tmp), [])

    def test_the_album_tools_src_moves_ahead_of_a_later_entry(self) -> None:
        album_src = str(sweep.ALBUM_ENTRY.parent / "src")
        path, marker = path_after_locating(
            LOCATED, sweep.SRC_DIR, [album_src], self.tmp
        )
        self.assertEqual(path.count(album_src), 1)
        self.assertLess(path.index(album_src), path.index(marker))
