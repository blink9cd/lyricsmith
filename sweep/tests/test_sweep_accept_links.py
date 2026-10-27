"""--mode accept and links: an album swapped for one mid-walk, a linked run folder.

Built on `test_sweep_accept`'s case: `main` runs with the real album stages, stdout and
stderr captured, HOME and the XDG folders redirected, so every album, run folder and
records folder lies under the test's own folder. Each link points at a folder outside
the library, also under the test's folder, and every name is invented.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

# First: importing it puts the album tool's modules on the path, as `sweep` does.
import test_sweep_accept as tsa  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_lock  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_accept_judge import ACCEPTING_NAME  # noqa: E402 - follows the bootstrap


class SwappedTest(tsa.AcceptCase):
    """An album swapped for a link after the walk began is skipped, never followed."""

    def test_an_album_swapped_mid_walk_is_skipped_and_its_target_untouched(
        self,
    ) -> None:
        first = tsa.run_album(self.library, "A")
        swapped = tsa.run_album(self.library, "B")
        outside = self.tmp / "outside" / "B"
        before = tsa.tree_hashes(swapped)

        def acquire(path: Path) -> run_lock.Lock | None:
            # Taking A's lock, the walk has already listed B: swap B for a link now.
            if first in path.parents and not swapped.is_symlink():
                outside.parent.mkdir()
                shutil.move(swapped, outside)
                swapped.symlink_to(outside, target_is_directory=True)
            return run_lock.acquire(path)

        self.lock_with(acquire)
        result = self.accept("--apply")
        # The move kept every byte, so B's tree as it was is the outside one's now.
        self.assertEqual(tsa.tree_hashes(outside), before)
        self.assertIn(
            "skipped album Artist/B: since the survey, its artist or album folder "
            "became a link, or it moved outside the library; not run",
            result.stderr,
        )
        self.assertNotIn("Artist/B", result.stdout.split("\n\n")[0])
        self.assertFalse(os.path.lexists(first / lr.RUN_FOLDER_NAME))
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("albums 2 · cleared 1 · ", result.stdout)
        self.assertIn("skipped 1", result.stdout)


class LinkedRunFolderTest(tsa.AcceptCase):
    """A linked `.lyricsmith` or `.lyricsmith.accepting` refuses its album alone."""

    def outside_run(self) -> Path:
        """A run folder outside the library, as a link's target."""
        return tsa.run_album(self.tmp / "outside", "Target") / lr.RUN_FOLDER_NAME

    def test_a_linked_run_folder_is_refused_and_its_target_untouched(self) -> None:
        target = self.outside_run()
        linked = self.library / "Artist" / "Linked"
        sf.add_track(linked, "01.mp3")
        (linked / lr.RUN_FOLDER_NAME).symlink_to(target, target_is_directory=True)
        cleared = tsa.run_album(self.library, "Open")
        before = tsa.tree_hashes(target)
        result = self.accept("--apply")
        self.assertEqual(tsa.tree_hashes(target), before)
        self.assertIn("refused: linked run folder: ", result.stdout)
        self.assertFalse(os.path.lexists(cleared / lr.RUN_FOLDER_NAME))
        self.assertIn("refused by reason: linked run folder 1", result.stdout)
        self.assertEqual(result.code, 1, result.stderr)

    def test_a_linked_leftover_is_refused_and_its_target_untouched(self) -> None:
        target = self.outside_run()
        linked = tsa.run_album(self.library, "Leftover")
        (linked / ACCEPTING_NAME).symlink_to(target, target_is_directory=True)
        cleared = tsa.run_album(self.library, "Open")
        before = tsa.tree_hashes(target)
        result = self.accept("--apply")
        self.assertEqual(tsa.tree_hashes(target), before)
        self.assertIn("refused: unsafe leftover: ", result.stdout)
        self.assertTrue((linked / lr.RUN_FOLDER_NAME).is_dir())
        self.assertFalse(os.path.lexists(cleared / lr.RUN_FOLDER_NAME))
        self.assertIn("refused by reason: unsafe leftover 1", result.stdout)
        self.assertEqual(result.code, 1, result.stderr)
