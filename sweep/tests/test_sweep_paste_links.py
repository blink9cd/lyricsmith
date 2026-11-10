"""``--mode paste`` passes over a track reached through a link, never opening its page.

The album tool resolves the album folder it is given, so a walk that pasted into an
``Artist/Album`` reached through a link would write outside ``--library``; and it
refuses a track that is itself a link, so opening that track's page would only waste
the person's copy, as would opening one under a linked folder below the album, which
its walk refuses too. Each row is passed over before the browser opens. Every title, URL
and lyric is invented.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - puts album and the libraries on sys.path
import paste_fixtures as pf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_paste as sp  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import HARBOR, QUARRY, words  # noqa: E402 - follows the bootstrap

#: The answer that pastes: Enter alone.
ENTER = ""


class LinkedFolderTest(pf.PasteCase):
    """A track whose artist or album folder is a link is passed over, said why."""

    def assert_passed_over(self, track: Path, why: str) -> None:
        """A walk over *track* alone opened nothing, ran no child, and said *why*."""
        self.write_has_it([(track, pf.URLS[HARBOR], "Harbor Song")])
        self.terminal = pf.FakeTerminal(answers=[ENTER], clips=[HARBOR])
        self.child = pf.InProcessAlbum()
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.terminal.opened, [])
        self.assertEqual(self.child.calls, [])
        self.assertIn(f"skipped {track}: {why}", result.stdout)
        self.assertEqual(self.words_of(self.tracks[0]), words(QUARRY))

    def test_a_linked_artist_or_album_folder_is_never_opened(self) -> None:
        artist = self.tracks[0].parents[1]
        linked_artist = self.library / "Linked Artist"
        linked_artist.symlink_to(artist, target_is_directory=True)
        linked_album = artist / "Mirror"
        linked_album.symlink_to(self.tracks[0].parent, target_is_directory=True)
        name = self.tracks[0].name
        for track in (linked_artist / "Lanterns" / name, linked_album / name):
            with self.subTest(track=track):
                self.assert_passed_over(track, sp.LINKED_FOLDER)

    def test_a_track_under_a_linked_disc_folder_is_never_opened(self) -> None:
        # `album`'s walk refuses a track under a linked folder, so its page is never
        # opened: an `Artist/Album/Disc/track` row whose disc folder is a link.
        album_dir = self.tracks[0].parent
        disc = album_dir / "Disc 2"
        disc.symlink_to(album_dir, target_is_directory=True)
        self.assert_passed_over(disc / self.tracks[0].name, sp.LINKED_SUBFOLDER)

    def test_a_track_that_is_a_link_is_never_opened(self) -> None:
        link = self.tracks[0].with_name("03 Linked.mp3")
        link.symlink_to(self.tracks[0])
        # Its own sidecar, a plain file holding the heard text, so only the link stops it.
        shutil.copyfile(self.tracks[0].with_suffix(".txt"), link.with_suffix(".txt"))
        self.assert_passed_over(link, sp.LINKED_TRACK)
