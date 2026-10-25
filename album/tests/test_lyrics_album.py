"""Discovery, the album lock and the deferred interrupt: the run's album-level steps.

Also how a --track path is named relative to the album.

Discovery is exercised on built albums; the lock through the real kernel lock, held
by a second open of the same file where a test needs another holder; the interrupt
by raising a real SIGINT inside the deferred block.
"""

from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import run_lock  # noqa: E402 - reachable through run_fakes' bootstrap


class ResolveAlbumTest(fixtures.IsolatedCase):
    """The album path is resolved once and must be a directory."""

    def test_a_directory_resolves_to_itself(self) -> None:
        self.assertEqual(lyrics_album.resolve_album(self.tmp), self.tmp)

    def test_a_symlinked_album_resolves_to_its_target(self) -> None:
        album = self.tmp / "album"
        album.mkdir()
        link = self.tmp / "link"
        link.symlink_to(album, target_is_directory=True)
        self.assertEqual(lyrics_album.resolve_album(link), album)

    def test_a_missing_album_cannot_run(self) -> None:
        with self.assertRaisesRegex(lyrics_album.AlbumUnusableError, "not a directory"):
            lyrics_album.resolve_album(self.tmp / "missing")

    def test_a_file_is_not_an_album(self) -> None:
        path = self.tmp / "file.mp3"
        path.write_bytes(b"")
        with self.assertRaises(lyrics_album.AlbumUnusableError):
            lyrics_album.resolve_album(path)


class AlbumRelativeTest(fixtures.IsolatedCase):
    """A --track names a file under the album, by its album-relative POSIX path."""

    def test_a_track_under_the_album_is_named_relative_to_it(self) -> None:
        self.assertEqual(
            lyrics_album.album_relative(self.tmp, self.tmp / "CD1" / "01 A.mp3"),
            "CD1/01 A.mp3",
        )

    def test_a_track_outside_the_album_is_refused(self) -> None:
        with self.assertRaisesRegex(lyrics_album.AlbumUnusableError, "not under"):
            lyrics_album.album_relative(self.tmp / "album", self.tmp / "01 A.mp3")


class DiscoverTracksTest(fixtures.IsolatedCase):
    """Every MP3 under the album, recursively, sorted, hidden and linked parts left out."""

    def test_tracks_are_found_recursively_case_insensitively_and_sorted(self) -> None:
        for rel in ("CD2/01 B.mp3", "CD1/02 A.MP3", "CD1/01 C.Mp3", "cover.jpg"):
            (self.tmp / rel).parent.mkdir(exist_ok=True)
            (self.tmp / rel).write_bytes(b"")
        found = lyrics_album.discover_tracks(self.tmp).tracks
        rels = [path.relative_to(self.tmp).as_posix() for path in found]
        self.assertEqual(rels, ["CD1/01 C.Mp3", "CD1/02 A.MP3", "CD2/01 B.mp3"])

    def test_hidden_folders_and_files_are_pruned(self) -> None:
        for rel in (".lyricsmith/run/x.mp3", "._01 A.mp3", "01 A.mp3"):
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / rel).write_bytes(b"")
        found = lyrics_album.discover_tracks(self.tmp)
        self.assertEqual(found.tracks, (self.tmp / "01 A.mp3",))
        self.assertEqual(found.links, ())

    def test_linked_folders_and_files_are_not_followed(self) -> None:
        album, other = self.tmp / "album", self.tmp / "other"
        other.mkdir()
        (other / "elsewhere.mp3").write_bytes(b"")
        album.mkdir()
        (album / "01 A.mp3").write_bytes(b"")
        (album / "linked").symlink_to(other, target_is_directory=True)
        (album / "02 B.mp3").symlink_to(other / "elsewhere.mp3")
        (album / ".hidden.mp3").symlink_to(other / "elsewhere.mp3")
        found = lyrics_album.discover_tracks(album)
        self.assertEqual(found.tracks, (album / "01 A.mp3",))
        self.assertEqual(found.links, (album / "02 B.mp3", album / "linked"))

    def test_an_album_of_links_alone_cannot_run_and_says_why(self) -> None:
        album = self.tmp / "album"
        album.mkdir()
        (album / "01 A.mp3").symlink_to(self.tmp / "elsewhere.mp3")
        with self.assertRaisesRegex(lyrics_album.AlbumUnusableError, "1 link skipped"):
            lyrics_album.discover_tracks(album)

    def test_an_album_with_no_mp3_cannot_run(self) -> None:
        (self.tmp / "notes.txt").write_text("none", encoding="utf-8", newline="")
        with self.assertRaisesRegex(lyrics_album.AlbumUnusableError, "no MP3"):
            lyrics_album.discover_tracks(self.tmp)


class AlbumLockTest(fixtures.IsolatedCase):
    """One run per album: the kernel lock refuses a second holder, and dies with its own."""

    def _lock(self) -> Path:
        """Where the album lock lives under this test's album."""
        return run_fakes.lock_path(self.tmp)

    def test_the_lock_is_held_for_the_block_and_released_after(self) -> None:
        with lyrics_album.album_lock(self.tmp, acquire=run_fakes.acquire) as path:
            during = run_lock.is_held(path)
        self.assertEqual((path, during), (self._lock(), True))
        self.assertFalse(run_lock.is_held(self._lock()))

    def test_the_lock_file_stays_between_runs(self) -> None:
        with lyrics_album.album_lock(self.tmp, acquire=run_fakes.acquire):
            pass
        self.assertTrue(self._lock().is_file())

    def test_the_lock_is_released_even_when_the_block_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            with lyrics_album.album_lock(self.tmp, acquire=run_fakes.acquire):
                raise RuntimeError("inside")
        self.assertFalse(run_lock.is_held(self._lock()))

    def test_a_second_run_is_refused_while_the_first_holds_the_lock(self) -> None:
        with lyrics_album.album_lock(self.tmp, acquire=run_fakes.acquire):
            with self.assertRaisesRegex(lyrics_album.LockHeldError, "another run"):
                with lyrics_album.album_lock(self.tmp, acquire=run_fakes.acquire):
                    self.fail("the lock was taken twice")

    def test_a_lock_left_by_a_dead_holder_is_simply_taken(self) -> None:
        # A run killed outright leaves its file behind with no lock on it.
        self._lock().parent.mkdir()
        self._lock().write_text("4242", encoding="utf-8", newline="")
        with lyrics_album.album_lock(self.tmp, acquire=run_fakes.acquire):
            held = run_lock.is_held(self._lock())
        self.assertTrue(held)

    def test_an_album_that_cannot_hold_a_lock_raises_the_os_error(self) -> None:
        (self.tmp / lr.RUN_FOLDER_NAME).write_bytes(b"")
        with self.assertRaises(OSError):
            with lyrics_album.album_lock(self.tmp, acquire=run_fakes.acquire):
                self.fail("a lock was taken in a file")


class DeferredInterruptTest(fixtures.IsolatedCase):
    """An interrupt inside the block is held until the block ends, then raised."""

    def test_an_interrupt_waits_for_the_block_to_finish(self) -> None:
        steps: list[str] = []
        with self.assertRaises(KeyboardInterrupt):
            with lyrics_album.deferred_interrupt():
                signal.raise_signal(signal.SIGINT)
                steps.append("after the signal")
        self.assertEqual(steps, ["after the signal"])

    def test_no_interrupt_raises_nothing_and_restores_the_handler(self) -> None:
        before = signal.getsignal(signal.SIGINT)
        with lyrics_album.deferred_interrupt():
            during = signal.getsignal(signal.SIGINT)
        self.assertIsNot(during, before)
        self.assertIs(signal.getsignal(signal.SIGINT), before)

    def test_an_interrupt_beside_an_error_is_still_honored(self) -> None:
        with self.assertRaises(KeyboardInterrupt) as raised:
            with lyrics_album.deferred_interrupt():
                signal.raise_signal(signal.SIGINT)
                raise OSError("disk full")
        self.assertIsInstance(raised.exception.__context__, OSError)

    def test_off_the_main_thread_nothing_is_installed(self) -> None:
        handlers: list[object] = []

        def worker() -> None:
            with lyrics_album.deferred_interrupt():
                handlers.append(signal.getsignal(signal.SIGINT))

        before = signal.getsignal(signal.SIGINT)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(fixtures.HANG_LIMIT_S)
        self.assertEqual(handlers, [before])

    def test_an_ignored_interrupt_is_neither_deferred_nor_raised(self) -> None:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        with lyrics_album.deferred_interrupt():
            during = signal.getsignal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
        self.assertIs(during, signal.SIG_IGN)

    def test_a_handler_python_did_not_install_is_left_alone(self) -> None:
        with mock.patch.object(lyrics_album.signal, "getsignal", return_value=None):
            with mock.patch.object(lyrics_album.signal, "signal") as install:
                with lyrics_album.deferred_interrupt():
                    pass
        install.assert_not_called()
