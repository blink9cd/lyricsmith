"""The wiring lives in lyrics_wiring; the entry point re-exports the same objects."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_wiring  # noqa: E402 - import follows the sys.path bootstrap above


class ReExportTest(fixtures.IsolatedCase):
    """Tests and the sweep keep reaching the wiring through the album tool."""

    def test_the_entry_point_re_exports_the_wiring(self) -> None:
        self.assertIs(album.Wiring, lyrics_wiring.Wiring)
        self.assertIs(album.real_wiring, lyrics_wiring.real_wiring)

    def test_real_wiring_builds_real_stages(self) -> None:
        wiring = lyrics_wiring.real_wiring()
        self.assertIs(wiring.build_providers, lyrics_wiring.real_providers)
