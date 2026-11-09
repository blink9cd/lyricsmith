"""Every read of a file another program could swap for a FIFO pipe refuses it at once.

Opening a FIFO for reading blocks until a writer comes. A run folder's frame exports,
exact copies and sidecar copies, a sidecar beside a track, the track's MP3 itself and
a state file are all read after something else looked at them, so a pipe put there in
between must be refused by the open itself, never waited on. Each read here runs on a
worker thread under `fifo_fakes.within_deadline`, so a reader that blocks fails its
test rather than hanging the suite. Every lyric here is invented.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above
from fifo_fakes import make_pipe, within_deadline  # noqa: E402 - follows the bootstrap

#: Invented words a track held before the run.
_OLD = "An older verse the backup kept\n"
#: Invented words for a sidecar the backup copied.
_SIDECAR = "A sidecar verse the backup copied\n"
#: What every refusal of something that is no regular file says.
_NOT_REGULAR = "not a regular file"


def _swap_for_pipe(path: Path) -> Path:
    """Replace the file at *path* with a FIFO pipe of the same name; return *path*."""
    path.unlink()
    make_pipe(path)
    return path


class ReadRegularFollowTest(fixtures.IsolatedCase):
    """`read_regular`'s *follow*: a link is followed by default, refused without it."""

    def setUp(self) -> None:
        """A regular file and a link to it."""
        super().setUp()
        self.file = self.tmp / "plain.txt"
        self.file.write_bytes(b"words\n")
        self.link = self.tmp / "link.txt"
        try:
            self.link.symlink_to(self.file)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")

    def test_a_link_is_followed_by_default(self) -> None:
        self.assertEqual(lyrics_files.read_regular(self.link), b"words\n")

    def test_a_link_is_refused_unfollowed(self) -> None:
        with self.assertRaises(OSError):
            lyrics_files.read_regular(self.link, follow=False)

    def test_a_file_is_read_unfollowed(self) -> None:
        self.assertEqual(lyrics_files.read_regular(self.file, 3, follow=False), b"wor")


class OpenRegularTest(fixtures.IsolatedCase):
    """`open_regular`: a regular file opened in the mode asked; nothing else, at once."""

    def setUp(self) -> None:
        """A regular file holding a few bytes."""
        super().setUp()
        self.file = self.tmp / "track.mp3"
        self.file.write_bytes(b"0123")

    def test_a_file_opens_for_reading(self) -> None:
        with lyrics_files.open_regular(self.file, "rb") as stream:
            self.assertEqual(stream.read(), b"0123")
            self.assertEqual(stream.name, str(self.file))

    def test_a_file_opens_for_update(self) -> None:
        with lyrics_files.open_regular(self.file, "r+b") as stream:
            stream.write(b"ab")
        self.assertEqual(self.file.read_bytes(), b"ab23")

    @unittest.skipIf(sys.platform == "win32", "Windows has no blocking flag to read")
    def test_the_opened_file_blocks_as_a_plain_open_would(self) -> None:
        with lyrics_files.open_regular(self.file, "rb") as stream:
            self.assertTrue(os.get_blocking(stream.fileno()))

    @unittest.skipIf(sys.platform == "win32", "Windows never resets the blocking flag")
    def test_a_failure_after_the_open_closes_the_descriptor(self) -> None:
        opened: list[int] = []
        real = os.open

        def recording(name: str, flags: int, *args: int) -> int:
            opened.append(real(name, flags, *args))
            return opened[-1]

        with (
            mock.patch.object(os, "open", recording),
            mock.patch.object(os, "set_blocking", side_effect=OSError("refused")),
            self.assertRaisesRegex(OSError, "refused"),
        ):
            lyrics_files.open_regular(self.file, "rb")
        (descriptor,) = opened
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_a_folder_is_refused(self) -> None:
        with self.assertRaises(OSError):
            lyrics_files.open_regular(self.tmp, "rb")

    def test_a_fifo_is_refused_at_once(self) -> None:
        pipe = self.tmp / "pipe.mp3"
        make_pipe(pipe)
        with self.assertRaisesRegex(lyrics_files.NotRegularFileError, _NOT_REGULAR):
            within_deadline(
                self, pipe, lambda: lyrics_files.open_regular(pipe, "rb").close()
            )

    def test_a_fifo_is_refused_for_update(self) -> None:
        pipe = self.tmp / "pipe.mp3"
        make_pipe(pipe)
        with self.assertRaisesRegex(lyrics_files.NotRegularFileError, _NOT_REGULAR):
            within_deadline(
                self, pipe, lambda: lyrics_files.open_regular(pipe, "r+b").close()
            )


class ReadBoundedTest(fixtures.IsolatedCase):
    """`read_bounded`, every sidecar's read, refuses a FIFO at once."""

    def test_a_fifo_sidecar_is_refused_at_once(self) -> None:
        pipe = self.tmp / "01.txt"
        make_pipe(pipe)
        with self.assertRaisesRegex(lyrics_files.NotRegularFileError, _NOT_REGULAR):
            within_deadline(
                self, pipe, lambda: lyrics_sidecar.read_bounded(pipe, pipe.name)
            )


class _BackedUpCase(rb.UndoCase):
    """A one-track album, backed up and run over; its run folder at hand."""

    def back_up_one(
        self, frames: tuple[lm.FrameRecord, ...] = (), *, txt: str | None = None
    ) -> Path:
        """Back up one track holding *frames* and an optional ``.txt``, run it; return it."""
        mp3 = self.track("01.mp3", frames, txt=txt)
        rb.simulate_run(self.album, self.back_up(mp3))
        self.run_dir = lr.run_folder(self.album, run_fakes.RUN_ID)
        return mp3

    def before(self) -> lr.TrackBefore:
        """The one track's backed-up ``before`` block, read from the manifest."""
        before = lyrics_manifest.read_manifest(self.run_dir).tracks[0].before
        assert before is not None
        return before

    def frame_export(self) -> Path:
        """The one frame's readable export in the run folder, swapped for a FIFO."""
        (export,) = self.before().frames
        return _swap_for_pipe(self.run_dir / export.file)

    def exact_copy(self) -> Path:
        """The one frame's exact copy in the run folder, swapped for a FIFO."""
        (export,) = self.before().frames
        assert export.exact is not None
        return _swap_for_pipe(self.run_dir / export.exact)

    def sidecar_copy(self) -> Path:
        """The one sidecar's copy in the run folder, swapped for a FIFO."""
        (copy,) = self.before().sidecars
        assert copy.file is not None
        return _swap_for_pipe(self.run_dir / copy.file)

    def assert_undo_refused(self, pipe: Path) -> None:
        """Assert undoing the run is the track's error, at once, refusing *pipe*."""
        (result,) = within_deadline(self, pipe, self.undo).tracks
        self.assertEqual(result.status, lr.UndoStatus.ERROR)
        self.assertIn(_NOT_REGULAR, rb.undo_error(result))

    def assert_frames_undo_refused(self, mp3: Path, pipe: Path) -> None:
        """Assert undo refuses the frames read from *pipe*, leaving *mp3* untouched."""
        untouched = rb.snapshot(mp3)
        self.assert_undo_refused(pipe)
        self.assertEqual(rb.snapshot(mp3), untouched)

    def assert_frames_refused(self, pipe: Path) -> None:
        """Assert the check's read of the backed-up frames refuses *pipe* at once."""
        with self.assertRaisesRegex(lyrics_files.NotRegularFileError, _NOT_REGULAR):
            within_deadline(
                self,
                pipe,
                lambda: lyrics_backup.load_before_frames(self.run_dir, self.before()),
            )


class FrameExportFifoTest(_BackedUpCase):
    """A frame's export or exact copy swapped for a FIFO: undo and the check refuse it."""

    def test_undo_refuses_a_fifo_frame_export(self) -> None:
        mp3 = self.back_up_one((tb.uslt(_OLD),))
        self.assert_frames_undo_refused(mp3, self.frame_export())

    def test_the_check_refuses_a_fifo_frame_export(self) -> None:
        self.back_up_one((tb.uslt(_OLD),))
        self.assert_frames_refused(self.frame_export())

    def test_undo_refuses_a_fifo_exact_copy(self) -> None:
        # Timed in MPEG frames, which an LRC cannot carry: the backup keeps an exact copy.
        mp3 = self.back_up_one((tb.sylt((40, "counted"), time_format=tb.MPEG_FRAMES),))
        self.assert_frames_undo_refused(mp3, self.exact_copy())

    def test_the_check_refuses_a_fifo_exact_copy(self) -> None:
        self.back_up_one((tb.sylt((40, "counted"), time_format=tb.MPEG_FRAMES),))
        self.assert_frames_refused(self.exact_copy())


class SidecarCopyFifoTest(_BackedUpCase):
    """A sidecar's copy swapped for a FIFO: undo and the check refuse it."""

    def test_undo_refuses_a_fifo_sidecar_copy(self) -> None:
        mp3 = self.back_up_one(txt=_SIDECAR)
        self.assert_undo_refused(self.sidecar_copy())
        self.assertFalse(mp3.with_suffix(".txt").exists())

    def test_the_check_refuses_a_fifo_sidecar_copy(self) -> None:
        self.back_up_one(txt=_SIDECAR)
        pipe = self.sidecar_copy()
        with self.assertRaisesRegex(lyrics_files.NotRegularFileError, _NOT_REGULAR):
            within_deadline(
                self,
                pipe,
                lambda: lyrics_backup.load_before_sidecar(self.run_dir, self.before()),
            )


class TagsFifoTest(fixtures.IsolatedCase):
    """A track's MP3 swapped for a FIFO: reading or probing its tags refuses it at once."""

    def setUp(self) -> None:
        """A FIFO pipe named like a track."""
        super().setUp()
        self.pipe = self.tmp / "01.mp3"
        make_pipe(self.pipe)

    def test_reading_the_tags_refuses_a_fifo(self) -> None:
        with self.assertRaisesRegex(lyrics_files.NotRegularFileError, _NOT_REGULAR):
            within_deadline(self, self.pipe, lambda: lyrics_tags.read_tags(self.pipe))

    def test_reading_the_track_number_refuses_a_fifo(self) -> None:
        with self.assertRaisesRegex(lyrics_files.NotRegularFileError, _NOT_REGULAR):
            within_deadline(
                self, self.pipe, lambda: lyrics_tags.read_track_number(self.pipe)
            )

    def test_the_save_refuses_a_fifo_swapped_in_after_its_tail_is_read(self) -> None:
        track = tb.build_rich_mp3(self.tmp / "02.mp3", 4)
        real = lyrics_tags._v1_tail

        def reading_then_swapping(path: Path) -> bytes:
            tail = real(path)
            _swap_for_pipe(path)
            return tail

        verse = lm.PlainLyrics("An invented verse to embed\n")
        with (
            mock.patch.object(lyrics_tags, "_v1_tail", reading_then_swapping),
            self.assertRaisesRegex(lyrics_files.NotRegularFileError, _NOT_REGULAR),
        ):
            within_deadline(
                self, track, lambda: lyrics_tags.write_lyrics(track, verse, "eng")
            )

    def test_the_write_probe_refuses_a_fifo(self) -> None:
        with self.assertRaisesRegex(lyrics_files.NotRegularFileError, _NOT_REGULAR):
            within_deadline(
                self, self.pipe, lambda: lyrics_tags.check_writable(self.pipe)
            )


class StateFileFifoTest(fixtures.IsolatedCase):
    """A state file swapped for a FIFO is no state and a warning, at once."""

    def test_a_fifo_state_file_is_ignored_with_a_warning(self) -> None:
        pipe = self.tmp / "cooldown.json"
        make_pipe(pipe)
        state, warning = within_deadline(
            self,
            pipe,
            lambda: lyrics_cooldown.read_state(
                pipe,
                json.loads,
                "cool-down",
                max_bytes=lyrics_cooldown.MAX_COOLDOWN_BYTES,
            ),
        )
        self.assertIsNone(state)
        self.assertEqual(
            warning, f"{pipe}: cool-down file ignored (NotRegularFileError)"
        )


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
