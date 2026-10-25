"""How a file is opened: in binary mode everywhere, and never through a link swapped in.

A descriptor from `os.open` on Windows is in the C runtime's text mode unless
``O_BINARY`` is passed: a read then turns each CRLF into LF and stops at Ctrl-Z, and a
write turns each LF into CRLF. The sidecar read feeds the backup's copy and the rename's
hash, and the atomic write lays down every file undo restores, so both must be
byte-exact; each open is checked for the flag here, faked at Windows' own value, since
no test on a POSIX gate box could see the bytes change.

A link put where a checked file was, or a link loop in a run folder's own folders, makes
an open fail rather than read through it; that failure is the one track's error, never
the run's or the dry run's crash. Every lyric here is invented.
"""

from __future__ import annotations

import errno
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: Windows' own value of ``os.O_BINARY``, faked here where the flag does not exist.
_FAKE_BINARY = 0x8000
#: Invented bytes a text-mode descriptor would change: a CRLF, and a Ctrl-Z mid-file.
_TEXT_MODE_BAIT = b"A first line\r\nA second\x1a line after the stop\n"
#: Invented words a track held before the run.
_OLD = "An older verse the backup kept\n"
#: Invented words a swapped-in link leads to, inside the album.
_SWAPPED = "Words a link swapped in after the check leads to\n"


@contextmanager
def _recorded_open_flags() -> Iterator[list[int]]:
    """Fake ``os.O_BINARY`` at Windows' value; yield the flags each `os.open` gets.

    The recording open strips the fake bit again, because on this host it may mean
    something else (0x8000 is O_EVTONLY on macOS), and puts back the host's own flag,
    so the real open behaves as the code under test meant it to on any platform.
    """
    host_binary = getattr(os, "O_BINARY", 0)
    seen: list[int] = []
    real_open = os.open

    def recording_open(path: Path, flags: int, mode: int = 0o777) -> int:
        seen.append(flags)
        return real_open(path, (flags & ~_FAKE_BINARY) | host_binary, mode)

    with (
        mock.patch.object(os, "O_BINARY", _FAKE_BINARY, create=True),
        mock.patch.object(os, "open", recording_open),
    ):
        yield seen


class BinaryOpenTest(fixtures.IsolatedCase):
    """The sidecar read and the atomic write each open their file in binary mode."""

    def test_the_sidecar_read_opens_in_binary_mode(self) -> None:
        source = self.tmp / "01.txt"
        source.write_bytes(_TEXT_MODE_BAIT)
        with _recorded_open_flags() as seen:
            data = lyrics_sidecar.read_bounded(source, source.name)
        self.assertEqual(data, _TEXT_MODE_BAIT)
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0] & _FAKE_BINARY, "the read is opened in text mode")

    def test_the_atomic_write_opens_in_binary_mode(self) -> None:
        target = self.tmp / "out.txt"
        with _recorded_open_flags() as seen:
            lyrics_files.replace_atomically(target, _TEXT_MODE_BAIT)
        self.assertEqual(target.read_bytes(), _TEXT_MODE_BAIT)
        # The first open is the temporary file; any later one is the folder's sync.
        self.assertTrue(seen[0] & _FAKE_BINARY, "the write is opened in text mode")


class IntermediateLoopTest(rb.UndoCase):
    """A link loop in a run folder's own folder is that track's undo error, no crash."""

    def test_a_looped_frames_folder_ends_as_the_tracks_undo_error(self) -> None:
        mp3 = self.track("01.mp3", (tb.uslt(_OLD),))
        self.back_up(mp3)
        run_dir = lr.run_folder(self.album, run_fakes.RUN_ID)
        frames = run_dir / "frames"
        shutil.rmtree(frames)
        # ``frames`` -> ``frames``: the guard passes it, as `realpath` stops at the
        # loop with the name inside the run folder, and the open then fails.
        frames.symlink_to("frames")
        before = lyrics_manifest.read_manifest(run_dir).tracks[0].before
        assert before is not None
        with self.assertRaises(OSError) as caught:
            lyrics_backup.load_before_frames(run_dir, before)
        self.assertEqual(caught.exception.errno, errno.ELOOP)
        (result,) = self.undo().tracks
        self.assertEqual(result.status, lr.UndoStatus.ERROR)
        self.assertTrue(rb.undo_error(result).startswith("OSError: "))


def _swap_after_check(swapped_to: Path) -> Callable[[Path, Path], Path | None]:
    """A `lyrics_sidecar._source` that, once its check is made, swaps in a link.

    The checked file is replaced by a link to *swapped_to*, a real file inside the
    album, so only the open's refusal to follow a link keeps it from being read.
    """
    real_source = lyrics_sidecar._source

    def source(path: Path, root: Path) -> Path | None:
        checked = real_source(path, root)
        if checked is not None:
            checked.unlink()
            checked.symlink_to(swapped_to)
        return checked

    return source


class SwappedSidecarTest(fixtures.IsolatedCase):
    """A sidecar swapped for a link after its check is that track's sidecar error."""

    def setUp(self) -> None:
        """An album of two tracks, the first with a ``.txt``, and a file to link to."""
        super().setUp()
        self.album = self.tmp / "album"
        self.paths = fixtures.build_album(
            self.album,
            (
                TrackSpec("01 Keeper.mp3", txt=_OLD),
                TrackSpec("02 Orchard.mp3", title="The Orchard"),
            ),
        )
        self.elsewhere = self.album / "elsewhere.txt"
        self.elsewhere.write_text(_SWAPPED, encoding="utf-8", newline="")
        self.enterContext(
            mock.patch.object(
                lyrics_sidecar, "_source", _swap_after_check(self.elsewhere)
            )
        )

    def tracks(self) -> tuple[Path, ...]:
        """The album's tracks, as a run discovers them."""
        return lyrics_album.discover_tracks(self.album).tracks

    def assert_link_error(self, error: lr.StageError | None) -> None:
        """Assert *error* is the sidecar stage's refusal of the link, ELOOP."""
        assert error is not None
        self.assertEqual(error.stage, lr.ErrorStage.SIDECAR)
        self.assertEqual(error.exc_type, "OSError")
        self.assertIn(os.strerror(errno.ELOOP), error.message)

    def test_the_run_records_that_tracks_sidecar_error(self) -> None:
        report = lyrics_run.run_album(
            self.album,
            self.tracks(),
            run_fakes.table({}, {"The Orchard": fixtures.ORCHARD_PLAIN}),
            run_fakes.options(),
            run_fakes.make_stages(run_fakes.FakeRunFolder()),
            acquire_lock=run_fakes.acquire,
            started=run_fakes.STARTED,
            progress=lambda _line: None,
        )
        keeper, orchard = (track.after for track in report.tracks)
        assert keeper is not None and orchard is not None
        self.assertEqual(keeper.status, lr.OutcomeStatus.ERROR)
        self.assert_link_error(keeper.error)
        self.assertEqual(orchard.status, lr.OutcomeStatus.WRITTEN)

    def test_the_dry_run_plans_that_track_as_its_error(self) -> None:
        stages = run_fakes.make_stages(run_fakes.FakeRunFolder())
        keeper, orchard = lyrics_modes.plan_album(
            self.album, self.tracks(), stages, offline=False, lang=run_fakes.LANG
        )
        self.assertEqual(keeper.plan, lyrics_modes.PLAN_UNKNOWN)
        self.assert_link_error(keeper.error)
        self.assertEqual(orchard.plan, lyrics_modes.PLAN_FETCH)
