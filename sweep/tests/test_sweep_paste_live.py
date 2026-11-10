"""The paste walk across a real process boundary: a corrupt tag is never "copy again".

The album tool once exited `1` on a tag it could not read, the same code it
then gave a paste not sung, so the walker asked the person to copy the lyrics again
for a track no copy could mend. The in-process child (`paste_fixtures.InProcessAlbum`)
cannot see that, as its exit never crosses a process, so here the walker's own command
(`sweep_paste.paste_argv`) runs through the real `sweep_child.run_child`, the album
tool a real child process, against a track whose ID3 header is corrupted after the
walker's own look at it, as a tag changed between the walk's check and the child
would be. Every title, URL, path and lyric is invented; no clipboard, browser or
network is touched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - puts album and the libraries on sys.path
import paste_fixtures as pf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_paste as sp  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import HARBOR  # noqa: E402 - follows the bootstrap above

#: The answer that pastes: Enter alone.
ENTER = ""
#: An ID3v2.4 header whose size is not synchsafe: mutagen refuses the whole tag.
_CORRUPT_HEADER = b"ID3\x04\x00\x00\x80\x80\x80\x80"


def corrupt_tag(track: Path) -> None:
    """Overwrite *track*'s ID3 header with one no reader accepts."""
    data = track.read_bytes()
    track.write_bytes(_CORRUPT_HEADER + data[len(_CORRUPT_HEADER) :])


class CorruptTagLiveTest(pf.PasteCase):
    """A real child refusing an unreadable tag is that row's end, the walk going on."""

    def test_a_corrupt_tag_is_the_row_s_refusal_never_copy_again(self) -> None:
        corrupt_tag(self.tracks[0])
        self.terminal.answers = [ENTER, "q"]
        self.terminal.clips = [HARBOR]
        # The walker's own look passes the track, as it did before the tag changed.
        with mock.patch.object(sp, "paste_refusal", return_value=None):
            result = self.walk(child=sweep_child.run_child)
        self.assertNotIn(sp.NOT_SUNG_AGAIN, result.stderr)
        self.assertIn(
            f"refused {self.tracks[0]}: the album tool refused the track",
            result.stdout,
        )
        self.assertEqual(result.code, 0, result.stdout)
        self.assertEqual(self.terminal.clips, [])
