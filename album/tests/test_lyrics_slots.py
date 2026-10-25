"""A track's sidecar slot: what its two names hold, and how each kind of entry is treated.

A file and a link are a sidecar; nothing is fine; a folder, a pipe or a device is
refused by strip and by undo before anything of the track changes. A link is backed up
as a link, by its target, and put back as that link: a strip, a run's write and an undo
each act on the link itself, never on the file it points at. Every lyric here is
invented.
"""

from __future__ import annotations

import os
import stat
import sys
import unittest
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_slots  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_strip  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above

_TXT = "Lanterns on the jetty\nburn until the morning\n"
_LRC = "[00:01.00]Lanterns on the jetty\n[00:04.00]burn until the morning\n"
_HAND = b"A verse typed in by hand\nafter the run was over\n"
_FRAMES = (
    tb.uslt("A verse kept in the tag\n"),
    tb.sylt((1000, "A timed line"), (4000, "and another")),
)
#: A character device's mode, which an unprivileged test cannot create on disk.
_DEVICE_MODE = stat.S_IFCHR | 0o644


class LinkedSidecarTest(rb.UndoCase):
    """A link under a sidecar name is backed up as a link and comes back as one."""

    def shared(self, name: str, text: str) -> Path:
        """A file outside the album holding *text*, for a sidecar link to point at."""
        target = self.tmp / name
        target.write_text(text, encoding="utf-8", newline="")
        return target

    def test_a_dangling_link_survives_a_strip_and_its_undo(self) -> None:
        mp3 = self.track("01.mp3", (tb.uslt(_TXT),))
        slot = mp3.with_suffix(".lrc")
        slot.symlink_to("gone.lrc")
        self.back_up(mp3)
        lyrics_strip.strip_track(mp3)
        self.assertFalse(os.path.lexists(slot))
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.UNDONE)
        self.assertEqual(os.readlink(slot), "gone.lrc")

    def test_a_link_to_a_file_survives_a_run_and_its_undo_as_that_link(self) -> None:
        shared = self.shared("shared.txt", _TXT)
        mp3 = self.track("01.mp3")
        slot = mp3.with_suffix(".txt")
        slot.symlink_to(shared)
        rb.simulate_run(self.album, self.back_up(mp3), rb.RUN_PLAIN)
        # The run's write replaced the link itself, never the file it points at.
        self.assertFalse(slot.is_symlink())
        self.assertEqual(shared.read_text(encoding="utf-8"), _TXT)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.UNDONE)
        self.assertEqual(os.readlink(slot), str(shared))
        self.assertEqual(shared.read_text(encoding="utf-8"), _TXT)
        self.assertEqual(report.tracks[0].displaced, ())

    def test_a_strip_then_undo_restores_a_relative_link_to_a_file(self) -> None:
        self.shared("shared.lrc", _LRC)
        mp3 = self.track("01.mp3", (tb.uslt(_TXT),))
        slot = mp3.with_suffix(".lrc")
        slot.symlink_to("../shared.lrc")
        self.back_up(mp3)
        lyrics_strip.strip_track(mp3)
        self.undo()
        self.assertEqual(os.readlink(slot), "../shared.lrc")
        self.assertEqual(slot.read_text(encoding="utf-8"), _LRC)

    def test_a_link_an_untouched_track_keeps_is_left_alone(self) -> None:
        shared = self.shared("shared.txt", _TXT)
        mp3 = self.track("01.mp3")
        slot = mp3.with_suffix(".txt")
        slot.symlink_to(shared)
        manifest = self.back_up(mp3)
        before = manifest.tracks[0].before
        assert before is not None
        self.assertEqual(
            before.sidecars, (lr.SidecarCopy("01.txt", None, str(shared)),)
        )
        inode = slot.lstat().st_ino
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.UNDONE)
        self.assertEqual(slot.lstat().st_ino, inode)
        self.assertEqual(os.readlink(slot), str(shared))

    def test_a_file_put_where_a_link_was_is_displaced_for_the_link(self) -> None:
        shared = self.shared("shared.txt", _TXT)
        mp3 = self.track("01.mp3")
        slot = mp3.with_suffix(".txt")
        slot.symlink_to(shared)
        manifest = self.back_up(mp3)
        slot.unlink()
        slot.write_bytes(_HAND)
        report = self.undo()
        self.assertEqual(os.readlink(slot), str(shared))
        kept = lr.run_folder(self.album, manifest.run_id) / "undo" / "01.txt"
        self.assertEqual(kept.read_bytes(), _HAND)
        self.assertEqual(len(report.tracks[0].displaced), 1)

    def test_a_link_retargeted_since_is_put_back_without_a_copy(self) -> None:
        shared = self.shared("shared.txt", _TXT)
        other = self.shared("other.txt", "another verse\n")
        mp3 = self.track("01.mp3")
        slot = mp3.with_suffix(".txt")
        slot.symlink_to(shared)
        manifest = self.back_up(mp3)
        slot.unlink()
        slot.symlink_to(other)
        report = self.undo()
        self.assertEqual(os.readlink(slot), str(shared))
        self.assertEqual(other.read_text(encoding="utf-8"), "another verse\n")
        self.assertEqual(report.tracks[0].displaced, ())
        self.assertFalse((lr.run_folder(self.album, manifest.run_id) / "undo").exists())

    def test_a_link_to_a_folder_survives_a_strip_and_its_undo(self) -> None:
        folder = self.tmp / "shared"
        folder.mkdir()
        (folder / "kept.txt").write_bytes(_HAND)
        mp3 = self.track("01.mp3", (tb.uslt(_TXT),))
        slot = mp3.with_suffix(".lrc")
        slot.symlink_to(folder, target_is_directory=True)
        before = self.back_up(mp3).tracks[0].before
        assert before is not None
        self.assertEqual(
            before.sidecars, (lr.SidecarCopy("01.lrc", None, str(folder)),)
        )
        lyrics_strip.strip_track(mp3)
        self.assertEqual(rb.sidecars(mp3), {})
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.UNDONE)
        self.assertEqual(rb.sidecars(mp3), {"01.lrc": rb.LinkTo(str(folder))})
        self.assertEqual((folder / "kept.txt").read_bytes(), _HAND)

    def test_an_untimed_linked_lrc_is_renamed_as_a_link_and_undone(self) -> None:
        # The link stays inside the album: one leading out is never read, so never
        # renamed (test_lyrics_sidecar_containment).
        shared = self.album / "shared.lrc"
        shared.write_text(_TXT, encoding="utf-8", newline="")
        mp3 = self.track("CD1/01.mp3")
        mp3.with_suffix(".lrc").symlink_to("../shared.lrc")
        self.back_up(mp3)
        # No time stamp, so the sidecar stage renames it onto the .txt name: the
        # link itself moves, still leading where it led, and the file stays put.
        lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=True)
        self.assertEqual(rb.sidecars(mp3), {"01.txt": rb.LinkTo("../shared.lrc")})
        self.assertEqual(shared.read_text(encoding="utf-8"), _TXT)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.UNDONE)
        self.assertEqual(rb.sidecars(mp3), {"01.lrc": rb.LinkTo("../shared.lrc")})
        self.assertEqual(shared.read_text(encoding="utf-8"), _TXT)


class SlotEntryTest(rb.UndoCase):
    """A slot name holding a link is removed itself; anything else not a file is refused."""

    def test_a_link_in_a_slot_the_backup_did_not_hold_is_removed_alone(self) -> None:
        mp3 = self.track("01.mp3")
        manifest = self.back_up(mp3)
        elsewhere = self.tmp / "elsewhere.txt"
        elsewhere.write_bytes(_HAND)
        mp3.with_suffix(".txt").symlink_to(elsewhere)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.UNDONE)
        self.assertFalse(os.path.lexists(mp3.with_suffix(".txt")))
        self.assertEqual(elsewhere.read_bytes(), _HAND)
        self.assertFalse((lr.run_folder(self.album, manifest.run_id) / "undo").exists())

    def test_a_link_where_the_backup_held_a_file_gives_way_to_the_copy(self) -> None:
        mp3 = self.track("01.mp3", txt=_TXT)
        manifest = self.back_up(mp3)
        elsewhere = self.tmp / "elsewhere.txt"
        elsewhere.write_bytes(_HAND)
        mp3.with_suffix(".txt").unlink()
        mp3.with_suffix(".txt").symlink_to(elsewhere)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.UNDONE)
        self.assertFalse(mp3.with_suffix(".txt").is_symlink())
        self.assertEqual(rb.sidecars(mp3), {"01.txt": _TXT.encode()})
        self.assertEqual(elsewhere.read_bytes(), _HAND)
        self.assertFalse((lr.run_folder(self.album, manifest.run_id) / "undo").exists())

    def test_a_folder_in_a_slot_is_that_tracks_error_and_stays(self) -> None:
        mp3 = self.track("01.mp3")
        self.back_up(mp3)
        mp3.with_suffix(".lrc").mkdir()
        (mp3.with_suffix(".lrc") / "kept.txt").write_bytes(_HAND)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ERROR)
        self.assertIn("01.lrc is not a file", rb.undo_error(report.tracks[0]))
        self.assertEqual((mp3.with_suffix(".lrc") / "kept.txt").read_bytes(), _HAND)

    def test_a_folder_in_a_slot_is_refused_before_any_frame_is_restored(self) -> None:
        # The run changed the frames first, so a restore ahead of the refusal would
        # show: the track must keep the run's frames, not half of an undo.
        mp3 = self.track("01.mp3", (tb.uslt("A verse held before the run\n"),))
        backed_up = rb.lyrics_frames(mp3)
        manifest = rb.simulate_run(self.album, self.back_up(mp3))
        run_frames, run_sidecars = rb.lyrics_frames(mp3), rb.sidecars(mp3)
        self.assertNotEqual(run_frames, backed_up)
        mp3.with_suffix(".txt").mkdir()
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ERROR)
        self.assertIn("01.txt is not a file", rb.undo_error(report.tracks[0]))
        self.assertEqual(rb.lyrics_frames(mp3), run_frames)
        self.assertEqual(rb.sidecars(mp3), run_sidecars)
        self.assertFalse(self.manifest(manifest.run_id).tracks[0].undone)


def _make_folder(target: Path) -> None:
    """A folder, holding a file, under *target*'s name."""
    target.mkdir()
    (target / "kept.txt").write_text(_TXT, encoding="utf-8", newline="")


# mkfifo(3) is POSIX-only. The split is an `if` so a win32 mypy pass still sees the
# name; the win32 arm exists only for that pass, and skips the one test using it.
if sys.platform == "win32":

    def _make_pipe(target: Path) -> None:
        """Skip: Windows has no named pipes in its file system."""
        raise unittest.SkipTest("Windows has no named pipes in its file system")

else:

    def _make_pipe(target: Path) -> None:
        """A FIFO pipe under *target*'s name."""
        os.mkfifo(target)


class SlotRefusalTest(rb.UndoCase):
    """A folder, a pipe or a device under a sidecar name refuses the track whole.

    The bad entry sits under the ``.txt`` name, the second one checked, beside a real
    ``.lrc``: a stage that removed as it checked would have removed the ``.lrc`` first.
    """

    def assert_refused_untouched(self, make: Callable[[Path], None]) -> None:
        """Assert a track with *make*'s entry under its ``.txt`` name is left as it was."""
        mp3 = self.track("01.mp3", _FRAMES, lrc=_LRC)
        bad = mp3.with_suffix(".txt")
        make(bad)
        before = mp3.read_bytes()
        mode = bad.lstat().st_mode
        with self.assertRaises(lyrics_slots.SlotNotAFileError) as caught:
            lyrics_strip.strip_track(mp3)
        self.assertIn("01.txt is not a file", str(caught.exception))
        self.assertEqual(mp3.read_bytes(), before)
        self.assertEqual(rb.lyrics_frames(mp3), Counter(_FRAMES))
        self.assertEqual(mp3.with_suffix(".lrc").read_bytes(), _LRC.encode())
        # Under the device test's lstat patch this compares the patched mode with
        # itself; that test's read of the file's content is its real survival check.
        self.assertEqual(bad.lstat().st_mode, mode)

    def test_a_folder_is_refused(self) -> None:
        self.assert_refused_untouched(_make_folder)
        self.assertEqual(
            (self.album / "01.txt" / "kept.txt").read_text(encoding="utf-8"), _TXT
        )

    def test_a_pipe_is_refused(self) -> None:
        self.assert_refused_untouched(_make_pipe)

    def test_a_device_is_refused(self) -> None:
        # No unprivileged test can create a device node, so a plain file stands in for
        # one: the slot check reads it through lstat, which reports a character device.
        real_lstat = Path.lstat

        def device_lstat(path: Path) -> os.stat_result:
            found = real_lstat(path)
            if path.name != "01.txt":
                return found
            fields = list(found)
            fields[stat.ST_MODE] = _DEVICE_MODE
            return os.stat_result(fields)

        def make_device(target: Path) -> None:
            target.write_text(_TXT, encoding="utf-8", newline="")

        with mock.patch.object(Path, "lstat", autospec=True, side_effect=device_lstat):
            self.assert_refused_untouched(make_device)
        self.assertEqual((self.album / "01.txt").read_text(encoding="utf-8"), _TXT)


if __name__ == "__main__":
    unittest.main()
