"""The options module stays a light import: it loads no provider, no fetch, no HTTP library.

`lyrics_options` is pure argparse (the modes and which options each refuses), so
importing it must not load the chain (`lyrics_fetch`), the tally that derives the
provider names (`lyrics_tally`, `lyrics_only`) or `requests`. The check imports it in a
fresh interpreter, as `import_alone` does, and reads its loaded modules there.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above

_SRC = _HERE.parent / "src"
#: The modules importing the options must not load.
_HEAVY = ("requests", "lyrics_fetch", "lyrics_tally", "lyrics_only")
#: How long the fresh interpreter may take to import and answer.
_TIMEOUT_S = 60.0
#: What the fresh interpreter runs: import the module, print which heavy ones it loaded.
_PROBE = (
    "import json, sys; sys.path.insert(0, sys.argv[1]); import {module}; "
    "print(json.dumps([name for name in sys.argv[2:] if name in sys.modules]))"
)


def loaded_heavy(module: str) -> list[str]:
    """Which of `_HEAVY` importing *module* alone loads, in a fresh interpreter."""
    # The child only imports modules and prints; it starts no process of its own, so no
    # grandchild can hold the captured pipes past the timeout.
    done = subprocess.run(  # reliability: ignore[RL005] the child spawns nothing
        [sys.executable, "-c", _PROBE.format(module=module), str(_SRC), *_HEAVY],
        capture_output=True,
        text=True,
        check=True,
        timeout=_TIMEOUT_S,
    )
    names: list[str] = json.loads(done.stdout)
    return names


class OptionsImportTest(fixtures.IsolatedCase):
    """The options module loads nothing heavy; the probe sees a heavy import."""

    def test_the_options_load_nothing_heavy(self) -> None:
        self.assertEqual(loaded_heavy("lyrics_options"), [])

    def test_the_probe_sees_a_heavy_import(self) -> None:
        self.assertIn("lyrics_fetch", loaded_heavy("lyrics_only"))
