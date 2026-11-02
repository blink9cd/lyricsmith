"""The state files are private: each file 0o600, each state folder a tool makes 0o700.

The cool-down and breaker files and accept's records hold track titles, album paths and
provider refusals, so no other account on the machine may read them. A state folder
that already exists keeps its mode: only what a tool makes is made private. A write's
mode is a keyword of the atomic write itself, defaulting to the old mode, so files
beside the library are unchanged. Each test sets the umask it measures under and works
in its own folder, with HOME and the XDG folders redirected there.
"""

from __future__ import annotations

import datetime as dt
import os
import stat
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_judge as laj  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_records as lar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breaker as lbk  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lcd  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run_folders as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_state  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above

#: The umask every test here measures under: the common default.
_UMASK = 0o022
#: What a file and a folder get under it when nothing asks for less.
_OPEN_FILE, _OPEN_FOLDER = 0o644, 0o755
_PRIVATE_FILE, _PRIVATE_FOLDER = 0o600, 0o700
_NOW = dt.datetime(2026, 10, 25, 9, 8, 7, tzinfo=dt.UTC)


def mode_of(path: Path) -> int:
    """*path*'s permission bits."""
    return stat.S_IMODE(path.stat().st_mode)


class UmaskCase(fixtures.IsolatedCase):
    """Runs each test under `_UMASK`, restoring the process's own after it."""

    def setUp(self) -> None:
        """Set the umask, and put the old one back when the test ends."""
        super().setUp()
        self.addCleanup(os.umask, os.umask(_UMASK))


class WriteModeTest(UmaskCase):
    """The atomic write takes the new file's mode, defaulting to the old one."""

    def test_a_write_keeps_the_default_mode_unless_asked(self) -> None:
        target = self.tmp / "beside-the-library.txt"
        lyrics_files.replace_atomically(target, b"kept")
        self.assertEqual(mode_of(target), _OPEN_FILE)

    def test_a_replace_asked_for_a_mode_lands_with_it(self) -> None:
        target = self.tmp / "state.json"
        lyrics_files.replace_atomically(target, b"private", mode=_PRIVATE_FILE)
        self.assertEqual(mode_of(target), _PRIVATE_FILE)

    def test_a_confirmed_write_narrows_an_open_file_it_replaces(self) -> None:
        target = self.tmp / "state.json"
        target.write_bytes(b"old")
        target.chmod(_OPEN_FILE)
        lyrics_files.write_confirmed(target, b"new", mode=_PRIVATE_FILE)
        self.assertEqual(mode_of(target), _PRIVATE_FILE)
        self.assertEqual(target.read_bytes(), b"new")


class StateFolderTest(UmaskCase):
    """`make_state_folder` makes each missing folder private and leaves the rest."""

    def test_every_folder_it_makes_is_private(self) -> None:
        folder = self.tmp / "state" / "lyricsmith" / "cooldown"
        lyrics_state.make_state_folder(folder)
        for made in (folder, folder.parent, folder.parent.parent):
            with self.subTest(folder=made.name):
                self.assertEqual(mode_of(made), _PRIVATE_FOLDER)

    def test_an_existing_folder_keeps_its_mode(self) -> None:
        existing = self.tmp / "state" / "lyricsmith"
        existing.mkdir(parents=True)
        existing.chmod(_OPEN_FOLDER)
        lyrics_state.make_state_folder(existing / "sweep")
        self.assertEqual(mode_of(existing), _OPEN_FOLDER)
        self.assertEqual(mode_of(existing / "sweep"), _PRIVATE_FOLDER)

    def test_a_file_in_the_way_is_an_error(self) -> None:
        blocker = self.tmp / "lyricsmith"
        blocker.write_bytes(b"not a folder")
        with self.assertRaises(FileExistsError):
            lyrics_state.make_state_folder(blocker)

    def test_a_state_file_is_written_private(self) -> None:
        target = self.tmp / "state" / "lyricsmith" / "record.json"
        lyrics_state.write_state_file(target, b"{}")
        self.assertEqual(mode_of(target), _PRIVATE_FILE)
        self.assertEqual(mode_of(target.parent), _PRIVATE_FOLDER)


class CooldownModeTest(UmaskCase):
    """The cool-down and breaker files, and the folder holding them, are private."""

    def test_a_cooldown_file_and_its_folders_are_private(self) -> None:
        path = lcd.cooldown_path(os.environ, lyrics_model.Source.TEKSTOWO)
        lcd.write_cooldown(path, _NOW, "HTTP 429")
        self.assertEqual(mode_of(path), _PRIVATE_FILE)
        self.assertEqual(mode_of(path.parent), _PRIVATE_FOLDER)
        self.assertEqual(mode_of(path.parent.parent), _PRIVATE_FOLDER)

    def test_a_breaker_file_is_private(self) -> None:
        path = lbk.breaker_path(os.environ, lyrics_model.Source.LRCLIB)
        lbk.write_breaker(path, lbk.BreakerState(run=1))
        self.assertEqual(mode_of(path), _PRIVATE_FILE)


class AcceptRecordsModeTest(UmaskCase, rb.UndoCase):
    """Accept's records root, its own folder and each record are private."""

    def test_the_records_and_their_folders_are_private(self) -> None:
        mp3 = self.track("01.mp3", txt="A verse the album held\n")
        rb.simulate_run(
            self.album,
            self.back_up(mp3, started=run_fakes.STARTED),
            lyrics_model.PlainLyrics("A verse the run wrote\n"),
        )
        root = lar.records_root(os.environ)
        folder = lar.make_records_folder(root, self.album, _NOW)
        lar.write_records(folder, self.album, laj.read_contents(self.album).runs)
        self.assertEqual(mode_of(root), _PRIVATE_FOLDER)
        self.assertEqual(mode_of(folder), _PRIVATE_FOLDER)
        records = sorted(folder.iterdir())
        self.assertEqual(len(records), 2)
        for record in records:
            with self.subTest(record=record.name):
                self.assertEqual(mode_of(record), _PRIVATE_FILE)

    def test_the_records_folder_is_made_private_by_its_own_mkdir(self) -> None:
        # With every chmod a no-op, only the mkdir's own mode can make it 0o700, so
        # the folder is never there with the umask's wider mode, even for a moment.
        root = lar.records_root(os.environ)
        with mock.patch.object(Path, "chmod", autospec=True):
            folder = lar.make_records_folder(root, self.album, _NOW)
        self.assertEqual(mode_of(folder), _PRIVATE_FOLDER)


class ExclusiveDirModeTest(UmaskCase):
    """`make_exclusive_dir` takes the new folder's mode, defaulting to mkdir's own."""

    def test_a_folder_asked_for_a_mode_is_made_with_it(self) -> None:
        folder = lrf.make_exclusive_dir(self.tmp, "base", mode=_PRIVATE_FOLDER)
        self.assertEqual(mode_of(folder), _PRIVATE_FOLDER)

    def test_a_taken_name_gets_the_mode_too(self) -> None:
        (self.tmp / "base").mkdir()
        folder = lrf.make_exclusive_dir(self.tmp, "base", mode=_PRIVATE_FOLDER)
        self.assertEqual(folder.name, "base-2")
        self.assertEqual(mode_of(folder), _PRIVATE_FOLDER)

    def test_a_folder_keeps_the_default_mode_unless_asked(self) -> None:
        folder = lrf.make_exclusive_dir(self.tmp, "base")
        self.assertEqual(mode_of(folder), _OPEN_FOLDER)
