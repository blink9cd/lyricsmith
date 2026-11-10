"""The pin holds every open of the pinned file in a real paste and a real fix-text step.

`lyrics_pinned.refuse_unless_pinned` matches the pin by the path's spelling, the one every
stage of a step is passed. The pinned path is canonical already (the album is resolved
first, `lyrics_album.resolve_album`), so a stage that resolves it again gets the same path
and stays pinned; one that spells it differently (relative, say) would open the planned
file unchecked, and nothing else would fail. So each step here is run whole, and every
open of the pinned file itself, told by the opened descriptor's identity, must have been
asked under the pin's own spelling. Every title and lyric is invented.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_paste_mode as pm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_pinned  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    HARBOR,
    QUARRY,
    TITLES,
    FixCase,
    song_track,
)


class PinnedOpensTest(FixCase):
    """No open of the pinned file in a step escapes the pin by its path's spelling."""

    def setUp(self) -> None:
        """Track 1 holds the quarry song, heard as the harbor song; opens are watched."""
        super().setUp()
        self.paths = self.build(song_track(1, HARBOR, txt=QUARRY))
        self.hear(self.paths[0], HARBOR)
        #: For each open of the pinned file under a pin: whether its path matched.
        self.matched: list[bool] = []
        real = lyrics_files.refuse_unless_pinned
        # The pin is the running step's, read where the open asks it.
        current = lyrics_pinned._PIN  # noqa: SLF001 - the spy reads the live pin

        def watched(descriptor: int, path: Path) -> None:
            pin = current.get()
            if pin is not None:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) == pin.identity:
                    self.matched.append(path == pin.path)
            real(descriptor, path)

        patcher = mock.patch.object(lyrics_files, "refuse_unless_pinned", watched)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_every_open_pinned(self) -> None:
        """The pinned file was opened under the pin, and only by the pin's spelling."""
        self.assertTrue(self.matched, "the step never opened the pinned file")
        self.assertNotIn(False, self.matched)

    def test_a_paste_opens_the_track_only_by_the_pinned_spelling(self) -> None:
        pasted = self.tmp / "pasted.txt"
        pasted.write_text(HARBOR, encoding="utf-8", newline="")
        result = self.run_album(
            "--mode",
            "paste",
            "--track",
            str(self.paths[0]),
            "--heard-dir",
            str(self.heard),
            "--text-from",
            str(pasted),
        )
        self.assertEqual(result.code, pm.EXIT_WRITTEN, result.stdout + result.stderr)
        self.assert_every_open_pinned()

    def test_a_fix_opens_the_track_only_by_the_pinned_spelling(self) -> None:
        self.answer({TITLES[HARBOR]: HARBOR})
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assert_every_open_pinned()
