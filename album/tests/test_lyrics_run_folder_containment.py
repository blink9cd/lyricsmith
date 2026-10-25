"""The run folder stays where it belongs: reads stay inside it, and it is never a link.

Undo and the check read a run's backup back from its run folder: the manifest, each
frame's export or exact copy, and each sidecar's copy. A link put inside the run folder
since (a linked ``frames/`` or ``sidecars/`` folder, or a linked file) must not make
them read from outside it: the frames or sidecars refused are that track's error, and
a manifest refused is the usual manifest refusal. A manifest past its size cap is
refused unread.

The run folder itself, ``<album>/.lyricsmith``, and the lock file under it are
created by a run, an undo or a strip; a link at either name would put them outside the
album, so each is refused before anything is created. Every lyric here is invented.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above

#: Invented words a track held before the run.
_OLD = "An older verse the backup kept\n"
#: Invented words for a sidecar the backup copied.
_SIDECAR = "A sidecar verse the backup copied\n"
#: What a refusal to read outside the run folder says.
_OUTSIDE = "outside the run folder"
#: What a refusal of a linked run folder or lock says.
_LINKED = "is a symbolic link"


class _BackedUpCase(rb.UndoCase):
    """A one-track album, backed up; the run folder and an outside folder at hand."""

    def back_up_one(
        self, frames: tuple[lm.FrameRecord, ...] = (), *, txt: str | None = None
    ) -> Path:
        """Back up one track holding *frames* and an optional ``.txt``; return it."""
        mp3 = self.track("01.mp3", frames, txt=txt)
        self.back_up(mp3)
        self.run_dir = lr.run_folder(self.album, run_fakes.RUN_ID)
        return mp3

    def move_out(self, relative: str) -> Path:
        """Move *relative* out of the run folder and leave a link to it in its place."""
        inside = self.run_dir / relative
        outside = self.tmp / "outside" / relative
        outside.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(inside, outside)
        inside.symlink_to(outside)
        return outside

    def before(self) -> lr.TrackBefore:
        """The one track's backed-up ``before`` block, read from the manifest."""
        before = lyrics_manifest.read_manifest(self.run_dir).tracks[0].before
        assert before is not None
        return before

    def assert_undo_refused(self, mp3: Path) -> None:
        """Assert undoing the run is the track's error, reading nothing from outside."""
        (result,) = self.undo().tracks
        self.assertEqual(result.status, lr.UndoStatus.ERROR)
        self.assertIn(_OUTSIDE, rb.undo_error(result))


class FrameReadTest(_BackedUpCase):
    """A frame export or exact copy behind a link out of the run folder is refused."""

    def test_a_linked_frames_folder_is_refused(self) -> None:
        mp3 = self.back_up_one((tb.uslt(_OLD),))
        self.move_out("frames")
        with self.assertRaisesRegex(lm.LyricsError, _OUTSIDE):
            lyrics_backup.load_before_frames(self.run_dir, self.before())
        self.assert_undo_refused(mp3)

    def test_a_linked_exact_copy_is_refused(self) -> None:
        # Timed in MPEG frames, which an LRC cannot carry: the backup keeps an exact copy.
        mp3 = self.back_up_one((tb.sylt((1000, "a line"), time_format=tb.MPEG_FRAMES),))
        (export,) = self.before().frames
        assert export.exact is not None
        self.move_out(export.exact)
        with self.assertRaisesRegex(lm.LyricsError, _OUTSIDE):
            lyrics_backup.load_before_frames(self.run_dir, self.before())
        self.assert_undo_refused(mp3)

    def test_a_link_inside_the_run_folder_is_still_read(self) -> None:
        self.back_up_one((tb.uslt(_OLD),))
        (export,) = self.before().frames
        moved = self.run_dir / "kept.txt"
        (self.run_dir / export.file).rename(moved)
        (self.run_dir / export.file).symlink_to(moved)
        (frame,) = lyrics_backup.load_before_frames(self.run_dir, self.before())
        self.assertEqual(frame.payload, _OLD)


class SidecarCopyReadTest(_BackedUpCase):
    """A sidecar copy behind a link out of the run folder is refused by undo."""

    def test_a_linked_sidecars_folder_is_refused(self) -> None:
        mp3 = self.back_up_one(txt=_SIDECAR)
        self.move_out("sidecars")
        mp3.with_suffix(".txt").write_text(
            "edited since\n", encoding="utf-8", newline=""
        )
        self.assert_undo_refused(mp3)
        self.assertEqual(rb.sidecars(mp3), {"01.txt": b"edited since\n"})


class ManifestReadTest(_BackedUpCase):
    """A linked manifest, or one past the size cap, is refused as malformed."""

    def test_a_linked_manifest_is_refused(self) -> None:
        self.back_up_one()
        self.move_out(lr.MANIFEST_NAME)
        with self.assertRaisesRegex(lm.ManifestSchemaError, _OUTSIDE):
            lyrics_manifest.read_manifest(self.run_dir)

    def test_a_manifest_past_the_cap_is_refused(self) -> None:
        self.back_up_one()
        path = self.run_dir / lr.MANIFEST_NAME
        # Still valid JSON: trailing whitespace, so only the size can refuse it.
        padding = lyrics_manifest.MAX_MANIFEST_BYTES + 1 - path.stat().st_size
        with path.open("ab") as handle:
            handle.write(b" " * padding)
        with self.assertRaisesRegex(lm.ManifestSchemaError, "too large"):
            lyrics_manifest.read_manifest(self.run_dir)

    def test_a_manifest_at_the_cap_is_read(self) -> None:
        self.back_up_one()
        path = self.run_dir / lr.MANIFEST_NAME
        padding = lyrics_manifest.MAX_MANIFEST_BYTES - path.stat().st_size
        with path.open("ab") as handle:
            handle.write(b" " * padding)
        self.assertEqual(
            lyrics_manifest.read_manifest(self.run_dir).run_id, run_fakes.RUN_ID
        )


class LinkedRunFolderTest(fixtures.IsolatedCase):
    """A link at ``.lyricsmith`` or at its lock is refused before anything is made."""

    def setUp(self) -> None:
        """An album folder, and an empty folder outside it a link can lead to."""
        super().setUp()
        self.album = self.tmp / "album"
        self.album.mkdir()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()

    def link_run_folder(self) -> None:
        """Make the album's run folder a link to the outside folder."""
        (self.album / lr.RUN_FOLDER_NAME).symlink_to(self.outside)

    def link_lock(self) -> None:
        """Make the lock under a real run folder a link to an outside file name."""
        (self.album / lr.RUN_FOLDER_NAME).mkdir()
        run_fakes.lock_path(self.album).symlink_to(self.outside / "lock")

    def take_lock(self) -> None:
        """Take and drop the album lock with the real acquire."""
        with lyrics_album.album_lock(self.album, acquire=run_fakes.acquire):
            pass

    def test_a_linked_run_folder_is_refused_by_the_lock(self) -> None:
        self.link_run_folder()
        with self.assertRaisesRegex(lyrics_album.AlbumUnusableError, _LINKED):
            self.take_lock()
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_a_linked_lock_is_refused(self) -> None:
        self.link_lock()
        with self.assertRaisesRegex(lyrics_album.AlbumUnusableError, _LINKED):
            self.take_lock()
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_a_linked_run_folder_is_refused_by_the_backup(self) -> None:
        self.link_run_folder()
        with self.assertRaisesRegex(lyrics_album.AlbumUnusableError, _LINKED):
            lyrics_backup.create_run_dir(self.album, run_fakes.STARTED)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_a_real_run_folder_and_lock_are_taken(self) -> None:
        self.take_lock()
        self.assertTrue(run_fakes.lock_path(self.album).is_file())


class LinkedRunFolderCliTest(cli_fakes.RealWiringCliCase):
    """Each mode stops at a linked run folder, as a run does at a held lock."""

    def test_each_mode_cannot_run(self) -> None:
        fixtures.build_album(self.album, (fixtures.TrackSpec("01.mp3"),))
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.album / lr.RUN_FOLDER_NAME).symlink_to(outside)
        for mode in ("run", "strip", "undo", "check"):
            with self.subTest(mode=mode):
                result = self.run_album("--mode", mode)
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn(_LINKED, result.stderr)
                self.assertEqual(list(outside.iterdir()), [])
