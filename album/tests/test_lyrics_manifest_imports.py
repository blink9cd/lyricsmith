"""Reading a manifest loads no provider client: the refusal words live in a leaf module.

Undo, check, prune and the sweep all read manifests; none of them asks a provider, so
none should load Genius's library or a provider's client to do it. Each check imports
the module in a fresh interpreter, as `import_alone` does, and reads its loaded
modules there.
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
#: The modules a manifest reader must not load.
_CLIENTS = ("lyricsgenius", "lyrics_genius", "lyrics_tekstowo", "lyrics_youtube_music")
#: How long the fresh interpreter may take to import and answer.
_TIMEOUT_S = 60.0
#: What the fresh interpreter runs: import the module, print which clients it loaded.
_PROBE = (
    "import json, sys; sys.path.insert(0, sys.argv[1]); import {module}; "
    "print(json.dumps([name for name in sys.argv[2:] if name in sys.modules]))"
)


def loaded_clients(module: str) -> list[str]:
    """Which of `_CLIENTS` importing *module* alone loads, in a fresh interpreter."""
    # The child only imports modules and prints; it starts no process of its own, so no
    # grandchild can hold the captured pipes past the timeout.
    done = subprocess.run(  # reliability: ignore[RL005] the child spawns nothing
        [sys.executable, "-c", _PROBE.format(module=module), str(_SRC), *_CLIENTS],
        capture_output=True,
        text=True,
        check=True,
        timeout=_TIMEOUT_S,
    )
    names: list[str] = json.loads(done.stdout)
    return names


class ManifestImportTest(fixtures.IsolatedCase):
    """The manifest codec and the detail reader load no provider client."""

    def test_reading_a_manifest_loads_no_client(self) -> None:
        for module in ("lyrics_manifest", "lyrics_refusal_detail"):
            with self.subTest(module=module):
                self.assertEqual(loaded_clients(module), [])

    def test_the_probe_sees_a_client_when_one_is_loaded(self) -> None:
        self.assertIn("lyrics_genius", loaded_clients("lyrics_genius"))
