"""Accept's records outside the library, their read-back copy, and the rename-then-delete.

Every album, run folder and records root is built under the test's own folder, with
HOME and the XDG folders redirected there; the runs are made by the real backup, and
every title and lyric is invented.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import sys
import unittest
from unittest import mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import lyrics_accept_judge as laj  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_records as lar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above

#: A fixed accept time, and the folder-name stamp it gives.
_NOW = dt.datetime(2026, 10, 25, 9, 8, 7, tzinfo=dt.UTC)
_STAMP = "20261025T090807Z"
#: The records folder's name: the UTC stamp, then a short album-path hash.
_NAME_SHAPE = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{16}")
_ACCEPTED = ("lyricsmith", "accepted")
_DELETE_FAILED = "the delete was refused"


def album_files(album: Path) -> dict[str, str]:
    """Every file of *album* outside lyricsmith's folders, by path, to its sha256."""
    found: dict[str, str] = {}
    for path in sorted(album.rglob("*")):
        relative = path.relative_to(album)
        if relative.parts[0].startswith(lr.RUN_FOLDER_NAME) or not path.is_file():
            continue
        found[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def failing_delete(path: Path) -> None:
    """A delete that is always refused, as a busy folder's would be."""
    raise OSError(_DELETE_FAILED)


class RecordsRootTest(rb.UndoCase):
    """The records root follows the one state-root rule (`lyrics_state.state_root`)."""

    def test_an_absolute_xdg_state_home(self) -> None:
        state = self.tmp / "state"
        root = lar.records_root({"XDG_STATE_HOME": str(state)})
        self.assertEqual(root, state.joinpath(*_ACCEPTED))

    def test_a_relative_xdg_state_home_falls_back_to_home(self) -> None:
        root = lar.records_root({"XDG_STATE_HOME": "relative/state"})
        self.assertEqual(
            root, self.home / ".local" / "state" / "lyricsmith" / "accepted"
        )

    def test_an_unset_xdg_state_home_falls_back_to_home(self) -> None:
        root = lar.records_root({"HOME": str(self.home)})
        self.assertEqual(
            root, self.home / ".local" / "state" / "lyricsmith" / "accepted"
        )

    def test_the_fallback_reads_home_from_the_environment_given(self) -> None:
        other = self.tmp / "another-home"
        root = lar.records_root({"HOME": str(other)})
        self.assertEqual(root, other / ".local" / "state" / "lyricsmith" / "accepted")


class RecordsFolderTest(rb.UndoCase):
    """One folder per accept, named by time and album, made exclusively."""

    def setUp(self) -> None:
        """A records root that does not exist yet."""
        super().setUp()
        self.root = self.tmp / "state" / "lyricsmith" / "accepted"

    def test_the_name_is_the_utc_time_and_a_short_album_path_hash(self) -> None:
        folder = lar.make_records_folder(self.root, self.album, _NOW)
        digest = hashlib.sha256(str(self.album.resolve()).encode()).hexdigest()
        self.assertTrue(folder.is_dir())
        self.assertEqual(folder.parent, self.root)
        self.assertRegex(folder.name, _NAME_SHAPE)
        self.assertEqual(folder.name, f"{_STAMP}-{digest[:16]}")

    def test_a_local_time_is_named_in_utc(self) -> None:
        local = _NOW.astimezone(dt.timezone(dt.timedelta(hours=5)))
        folder = lar.make_records_folder(self.root, self.album, local)
        self.assertTrue(folder.name.startswith(f"{_STAMP}-"))

    def test_a_clash_takes_the_next_suffix(self) -> None:
        first = lar.make_records_folder(self.root, self.album, _NOW)
        second = lar.make_records_folder(self.root, self.album, _NOW)
        third = lar.make_records_folder(self.root, self.album, _NOW)
        self.assertEqual(second.name, f"{first.name}-2")
        self.assertEqual(third.name, f"{first.name}-3")
        self.assertEqual(len(list(self.root.iterdir())), 3)

    def test_another_album_names_another_folder(self) -> None:
        other = self.tmp / "other"
        other.mkdir()
        first = lar.make_records_folder(self.root, self.album, _NOW)
        second = lar.make_records_folder(self.root, other, _NOW)
        self.assertNotEqual(first.name.split("-")[1], second.name.split("-")[1])

    def test_the_new_folder_is_synced_into_the_records_root(self) -> None:
        synced: list[Path] = []
        folder = lar.make_records_folder(
            self.root, self.album, _NOW, sync=synced.append
        )
        self.assertEqual(synced, [self.root])
        self.assertEqual(folder.parent, self.root)


class RunsCase(rb.UndoCase):
    """An album of one track with two real runs, and an empty records folder."""

    def setUp(self) -> None:
        """Two finished runs of the one track, read as accept reads them."""
        super().setUp()
        self.mp3 = self.track("01.mp3", txt="A verse the album held\n")
        self.run_ids = [self.made_run(0), self.made_run(5)]
        self.records = self.tmp / "records"
        self.records.mkdir()
        self.before = album_files(self.album)

    def made_run(self, minutes: int) -> str:
        """A real run of the one track, *minutes* after the fixed clock."""
        verse = lyrics_model.PlainLyrics(f"Verse {minutes} the run wrote\n")
        started = run_fakes.STARTED + dt.timedelta(minutes=minutes)
        manifest = rb.simulate_run(
            self.album, self.back_up(self.mp3, started=started), verse
        )
        return manifest.run_id

    def manifest_bytes(self, run_id: str) -> bytes:
        """Run *run_id*'s manifest as it stands in the album."""
        return (lr.run_folder(self.album, run_id) / lr.MANIFEST_NAME).read_bytes()

    def runs(self) -> tuple[laj.RunEntry, ...]:
        """The album's run-id folders, read as accept reads them."""
        return laj.read_contents(self.album).runs

    def assert_run_folder_kept(self) -> None:
        """Every album file is as it was, and ``.lyricsmith`` is still in place."""
        self.assertEqual(album_files(self.album), self.before)
        self.assertTrue((self.album / lr.RUN_FOLDER_NAME).is_dir())

    def assert_album_untouched(self) -> None:
        """As `assert_run_folder_kept`, with no ``.lyricsmith.accepting`` beside it."""
        self.assert_run_folder_kept()
        self.assertFalse(os.path.lexists(self.album / laj.ACCEPTING_NAME))


class WriteRecordsTest(RunsCase):
    """Each manifest copied verbatim beside ``album.txt``; nothing else written."""

    def test_each_manifest_is_copied_verbatim_with_album_txt(self) -> None:
        lar.write_records(self.records, self.album, self.runs())
        names = sorted(path.name for path in self.records.iterdir())
        self.assertEqual(
            names, sorted(["album.txt", *(f"{r}.json" for r in self.run_ids)])
        )
        for run_id in self.run_ids:
            copy = (self.records / f"{run_id}.json").read_bytes()
            self.assertEqual(copy, self.manifest_bytes(run_id))
        album_txt = (self.records / "album.txt").read_bytes()
        self.assertEqual(album_txt, os.fsencode(self.album.resolve()) + b"\n")
        self.assert_album_untouched()

    def test_a_relative_album_is_recorded_resolved(self) -> None:
        previous = Path.cwd()
        os.chdir(self.album.parent)
        self.addCleanup(os.chdir, previous)
        relative = Path(self.album.name)
        lar.write_records(self.records, relative, self.runs())
        album_txt = (self.records / "album.txt").read_bytes()
        self.assertEqual(album_txt, os.fsencode(self.album.resolve()) + b"\n")

    def test_an_unreadable_manifest_is_copied_verbatim(self) -> None:
        garbled = b"\xff{ not a manifest at all"
        unreadable = self.run_ids[0]
        (lr.run_folder(self.album, unreadable) / lr.MANIFEST_NAME).write_bytes(garbled)
        runs = self.runs()
        kinds = {run.name: run.kind for run in runs}
        self.assertIs(kinds[unreadable], lyrics_prune.Kind.UNREADABLE)
        lar.write_records(self.records, self.album, runs)
        self.assertEqual((self.records / f"{unreadable}.json").read_bytes(), garbled)

    def test_a_run_folder_with_no_manifest_is_not_copied(self) -> None:
        (self.album / lr.RUN_FOLDER_NAME / "20261014T153012Z-9").mkdir()
        lar.write_records(self.records, self.album, self.runs())
        self.assertEqual(len(list(self.records.iterdir())), 1 + len(self.run_ids))

    def test_a_failed_write_raises_and_leaves_the_album_untouched(self) -> None:
        def refused(target: Path, data: bytes) -> None:
            raise OSError("no room left for the records")

        with self.assertRaises(OSError):
            lar.write_records(self.records, self.album, self.runs(), write=refused)
        self.assert_album_untouched()

    def test_a_copy_that_reads_back_wrong_raises_and_deletes_nothing(self) -> None:
        def corrupting(target: Path, data: bytes) -> None:
            lyrics_files.replace_atomically(target, data + b"stray")

        with self.assertRaises(lar.RecordsReadBackError) as caught:
            lar.write_records(self.records, self.album, self.runs(), write=corrupting)
        self.assertIsInstance(caught.exception, lyrics_model.LyricsError)
        self.assert_album_untouched()

    def test_a_copy_changed_after_its_write_is_caught_on_read_back(self) -> None:
        written: list[Path] = []

        def writing_then_changing(target: Path, data: bytes) -> None:
            lyrics_files.write_confirmed(target, data)
            written.append(target)
            if len(written) == 2:
                written[0].write_bytes(b"changed underneath")

        with self.assertRaises(lar.RecordsReadBackError) as caught:
            lar.write_records(
                self.records, self.album, self.runs(), write=writing_then_changing
            )
        self.assertIn(written[0].name, str(caught.exception))
        self.assert_album_untouched()

    def test_a_copy_swapped_for_a_link_is_refused_on_read_back(self) -> None:
        written: list[Path] = []

        def writing_then_linking(target: Path, data: bytes) -> None:
            lyrics_files.write_confirmed(target, data)
            moved = target.with_name(f"moved-{target.name}")
            target.rename(moved)
            try:
                target.symlink_to(moved)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlinks unavailable: {error}")
            written.append(target)

        with self.assertRaises(OSError):
            lar.write_records(
                self.records, self.album, self.runs(), write=writing_then_linking
            )
        self.assertTrue(written)
        self.assert_album_untouched()


class ManifestSwapTest(RunsCase):
    """A manifest swapped after the judgment is refused by the copy, never copied."""

    #: Bytes no run wrote, held by a file outside the album.
    OUTSIDE = b"bytes from outside the album\n"

    def first_manifest(self) -> Path:
        """The first run's manifest file in the album."""
        return lr.run_folder(self.album, self.run_ids[0]) / lr.MANIFEST_NAME

    def copied(self) -> list[bytes]:
        """Every file's bytes now in the records folder."""
        return [path.read_bytes() for path in self.records.iterdir()]

    def test_a_link_out_of_the_run_folder_is_refused(self) -> None:
        runs = self.runs()
        outside = self.tmp / "outside.json"
        outside.write_bytes(self.OUTSIDE)
        self.first_manifest().unlink()
        try:
            self.first_manifest().symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")
        with self.assertRaises(lyrics_files.OutsideRunFolderError):
            lar.write_records(self.records, self.album, runs)
        self.assertNotIn(self.OUTSIDE, self.copied())

    def test_a_manifest_one_byte_over_the_cap_is_refused(self) -> None:
        runs = self.runs()
        # Sparse, so the oversized file costs no disk to make.
        os.truncate(self.first_manifest(), lyrics_manifest.MAX_MANIFEST_BYTES + 1)
        with self.assertRaises(lyrics_model.LyricsError) as caught:
            lar.write_records(self.records, self.album, runs)
        self.assertIn("too large", str(caught.exception))
        self.assertEqual(self.copied(), [])

    def test_a_manifest_at_the_cap_is_still_copied(self) -> None:
        runs = self.runs()
        # The largest manifest's size stands in for the real cap, so no 16 MiB copy is
        # written; the first run's manifest is grown to exactly that cap.
        manifests = (self.album / lar.RUN_FOLDER_NAME).glob(f"*/{lr.MANIFEST_NAME}")
        cap = max(path.stat().st_size for path in manifests)
        os.truncate(self.first_manifest(), cap)
        with mock.patch.object(lar, "MAX_MANIFEST_BYTES", cap):
            lar.write_records(self.records, self.album, runs)
        copy = self.records / f"{self.run_ids[0]}.json"
        self.assertEqual(copy.stat().st_size, cap)


class RemoveLyricsmithTest(RunsCase):
    """Renamed to ``.lyricsmith.accepting``, then deleted; the album's files stay.

    The whole-folder rename is pinned on every host. Windows' rule
    (`RENAMES_HELD_FOLDERS` false) exists only because accept holds the album lock open
    inside the folder; here no file in it is open, so the rename is Windows' too. The
    entry-by-entry path that host takes under a held lock is
    ``test_lyrics_accept_held_rename``'s.
    """

    def setUp(self) -> None:
        """The two runs, removed by renaming the folder whole on any host."""
        super().setUp()
        self.enterContext(mock.patch.object(lar, "RENAMES_HELD_FOLDERS", True))

    def test_rename_then_delete_leaves_only_the_album_files(self) -> None:
        self.assertIsNone(lar.remove_lyricsmith(self.album))
        self.assertFalse(os.path.lexists(self.album / lr.RUN_FOLDER_NAME))
        self.assertFalse(os.path.lexists(self.album / laj.ACCEPTING_NAME))
        self.assertEqual(album_files(self.album), self.before)

    def test_the_delete_is_given_the_renamed_folder(self) -> None:
        seen: list[Path] = []
        self.assertIsNone(lar.remove_lyricsmith(self.album, seen.append))
        self.assertEqual(seen, [self.album / laj.ACCEPTING_NAME])
        self.assertFalse(os.path.lexists(self.album / lr.RUN_FOLDER_NAME))

    def test_a_failed_delete_leaves_the_renamed_folder_and_says_why(self) -> None:
        error = lar.remove_lyricsmith(self.album, failing_delete)
        self.assertEqual(error, _DELETE_FAILED)
        self.assertFalse(os.path.lexists(self.album / lr.RUN_FOLDER_NAME))
        self.assertTrue((self.album / laj.ACCEPTING_NAME).is_dir())
        self.assertEqual(album_files(self.album), self.before)

    def test_finish_leftover_then_clears_a_killed_accept(self) -> None:
        lar.remove_lyricsmith(self.album, failing_delete)
        self.assertIsNone(lar.finish_leftover(self.album))
        self.assertFalse(os.path.lexists(self.album / laj.ACCEPTING_NAME))
        self.assertEqual(album_files(self.album), self.before)

    def test_a_missing_folder_is_a_failed_rename(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertIsInstance(lar.remove_lyricsmith(empty), str)

    def test_an_existing_leftover_is_never_renamed_over(self) -> None:
        leftover = self.album / laj.ACCEPTING_NAME
        leftover.mkdir()
        with self.assertRaises(lar.LeftoverAcceptingError) as caught:
            lar.remove_lyricsmith(self.album)
        self.assertIn(laj.ACCEPTING_NAME, str(caught.exception))
        self.assertTrue(leftover.is_dir())
        self.assert_run_folder_kept()


class UnsafeAcceptingTest(RunsCase):
    """A link or a file named ``.lyricsmith.accepting`` is refused, never deleted through."""

    def setUp(self) -> None:
        """A folder outside the album holding one file a link could lead to."""
        super().setUp()
        self.target = self.tmp / "elsewhere"
        self.target.mkdir()
        (self.target / "keep.txt").write_bytes(b"not the album's\n")
        self.accepting = self.album / laj.ACCEPTING_NAME

    def assert_target_untouched(self) -> None:
        """The link's target folder still holds its one file, unchanged."""
        self.assertEqual((self.target / "keep.txt").read_bytes(), b"not the album's\n")

    def test_finish_leftover_refuses_a_link(self) -> None:
        self.accepting.symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(lar.UnsafeAcceptingError) as caught:
            lar.finish_leftover(self.album)
        self.assertIn(laj.ACCEPTING_NAME, str(caught.exception))
        self.assertIsInstance(caught.exception, lyrics_model.LyricsError)
        self.assertTrue(self.accepting.is_symlink())
        self.assert_target_untouched()

    def test_finish_leftover_refuses_a_file(self) -> None:
        self.accepting.write_bytes(b"a file, not a folder\n")
        with self.assertRaises(lar.UnsafeAcceptingError):
            lar.finish_leftover(self.album)
        self.assertTrue(self.accepting.is_file())

    def test_remove_refuses_a_linked_leftover_before_renaming(self) -> None:
        self.accepting.symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(lar.UnsafeAcceptingError):
            lar.remove_lyricsmith(self.album)
        self.assert_target_untouched()
        self.assert_run_folder_kept()

    def test_remove_refuses_a_file_leftover_before_renaming(self) -> None:
        self.accepting.write_bytes(b"a file, not a folder\n")
        with self.assertRaises(lar.UnsafeAcceptingError):
            lar.remove_lyricsmith(self.album)
        self.assert_run_folder_kept()

    def test_remove_refuses_a_linked_run_folder(self) -> None:
        folder = self.album / lr.RUN_FOLDER_NAME
        moved = self.tmp / "moved"
        folder.rename(moved)
        folder.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(lar.UnsafeAcceptingError) as caught:
            lar.remove_lyricsmith(self.album)
        self.assertIn(lr.RUN_FOLDER_NAME, str(caught.exception))
        self.assertTrue(folder.is_symlink())
        self.assertTrue(moved.is_dir())

    def test_a_missing_leftover_is_nothing_to_finish(self) -> None:
        self.assertIsNone(lar.finish_leftover(self.album, failing_delete))

    def test_a_failed_leftover_delete_says_why(self) -> None:
        self.accepting.mkdir()
        self.assertEqual(
            lar.finish_leftover(self.album, failing_delete), _DELETE_FAILED
        )
        self.assertTrue(self.accepting.is_dir())


if __name__ == "__main__":  # pragma: no cover - run directly, outside the gate
    unittest.main()
