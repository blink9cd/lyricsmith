"""`load_before_sidecar`: the untimed sidecar a backup copied, read back for the check.

The copies are read as the sidecar stage reads the sidecars: a ``.lrc`` copy first, its
words when it has no time stamp and none when it is timed, and the ``.txt`` copy only
beside no ``.lrc`` copy or one of stamps alone. A sidecar recorded as a link is never
read, and a copy reached through a link out of the run folder is refused. A copy is read
as the sidecar stage reads one, so one that is not UTF-8 or is past the lyrics cap is
refused. Each test writes its own run folder; every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above

_TXT = "A gull above the harbor\nturns once and is gone\n"
_LRC_UNTIMED = "A bell across the water\nrings the hour down\n"
_LRC_TIMED = "[00:01.00]A bell across the water\n"
_LRC_STAMPS = "[00:01.00]\n[00:05.00]\n"


class LoadBeforeSidecarTest(fixtures.IsolatedCase):
    """Which backed-up sidecar the check compares with, and how it is read."""

    def copy(self, name: str, data: bytes) -> lr.SidecarCopy:
        """A sidecar copy *name* holding *data* in this test's run folder."""
        file = f"sidecars/{name}"
        (self.tmp / "sidecars").mkdir(exist_ok=True)
        (self.tmp / file).write_bytes(data)
        return lr.SidecarCopy(name, file)

    def load(self, *copies: lr.SidecarCopy) -> lm.Lyrics | None:
        """What the check would compare with, given *copies* and no frames."""
        return lyrics_backup.load_before_sidecar(self.tmp, lr.TrackBefore((), copies))

    def test_no_sidecar_is_none(self) -> None:
        self.assertIsNone(self.load())

    def test_an_untimed_lrc_wins_over_the_txt(self) -> None:
        # As in the run: the sidecar stage reads the .lrc and renames it over the .txt.
        lrc = self.copy("Song.lrc", _LRC_UNTIMED.encode())
        txt = self.copy("Song.txt", _TXT.encode())
        self.assertEqual(self.load(lrc, txt), lm.PlainLyrics(_LRC_UNTIMED))

    def test_a_timed_lrc_beside_a_txt_is_none(self) -> None:
        lrc = self.copy("Song.lrc", _LRC_TIMED.encode())
        txt = self.copy("Song.txt", _TXT.encode())
        self.assertIsNone(self.load(lrc, txt))

    def test_a_stamps_only_lrc_leaves_the_txt(self) -> None:
        lrc = self.copy("Song.lrc", _LRC_STAMPS.encode())
        txt = self.copy("Song.txt", _TXT.encode())
        self.assertEqual(self.load(lrc, txt), lm.PlainLyrics(_TXT))

    def test_a_blank_untimed_lrc_beside_a_txt_is_none(self) -> None:
        # The run renames the blank .lrc over the .txt, which it never reads.
        lrc = self.copy("Song.lrc", b"\n\n")
        txt = self.copy("Song.txt", _TXT.encode())
        self.assertIsNone(self.load(lrc, txt))

    def test_an_untimed_lrc_alone_is_read(self) -> None:
        lrc = self.copy("Song.lrc", _LRC_UNTIMED.encode())
        self.assertEqual(self.load(lrc), lm.PlainLyrics(_LRC_UNTIMED))

    def test_a_timed_lrc_is_none(self) -> None:
        self.assertIsNone(self.load(self.copy("Song.lrc", _LRC_TIMED.encode())))

    def test_a_linked_sidecar_is_never_read(self) -> None:
        txt = lr.SidecarCopy("Song.txt", None, "elsewhere.txt")
        lrc = lr.SidecarCopy("Song.lrc", None, "elsewhere.lrc")
        self.assertIsNone(self.load(txt, lrc))

    def test_a_link_beside_an_untimed_lrc_copy_leaves_the_lrc(self) -> None:
        txt = lr.SidecarCopy("Song.txt", None, "elsewhere.txt")
        lrc = self.copy("Song.lrc", _LRC_UNTIMED.encode())
        self.assertEqual(self.load(lrc, txt), lm.PlainLyrics(_LRC_UNTIMED))

    def test_a_copy_not_utf8_is_refused(self) -> None:
        txt = self.copy("Song.txt", b"caf\xe9\n")
        with self.assertRaisesRegex(lm.SidecarDecodeError, "Song.txt is not UTF-8"):
            self.load(txt)

    def test_a_copy_past_the_cap_is_refused(self) -> None:
        txt = self.copy("Song.txt", b"la " * (lr.MAX_LYRICS_CHARS // 3 + 1))
        with self.assertRaises(lyrics_sidecar.SidecarTooLargeError):
            self.load(txt)


class LoadBeforeSidecarContainmentTest(fixtures.IsolatedCase):
    """A backed-up copy swapped for a link out of the run folder is refused, not read."""

    def test_a_txt_copy_linked_outside_the_run_folder_is_refused(self) -> None:
        run_dir = self.tmp / "run"
        (run_dir / "sidecars").mkdir(parents=True)
        outside = self.tmp / "outside.txt"
        outside.write_bytes(_TXT.encode())
        (run_dir / "sidecars" / "Song.txt").symlink_to(outside)
        before = lr.TrackBefore((), (lr.SidecarCopy("Song.txt", "sidecars/Song.txt"),))
        with self.assertRaisesRegex(
            lyrics_files.OutsideRunFolderError, "leads outside the run folder"
        ):
            lyrics_backup.load_before_sidecar(run_dir, before)
