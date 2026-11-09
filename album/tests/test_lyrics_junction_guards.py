"""Every "not a link" guard on a folder the tool reads or acts on refuses a junction too.

A Windows junction reads as no symbolic link (`Path.is_symlink` is False) and as a
folder (`Path.is_dir` follows it), so a guard testing `is_symlink` alone lets one
through. These cases read a real folder as a junction by handing the guards' look
(`lyrics_run_folders.lstat`) a junction's reparse tag, so they run on every host; the
Windows-only cases make a real one. Every album and run
is built under the test's own folder; every lyric is invented.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import sys
import unittest
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import lyrics_accept_judge as laj  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run_folders as rf  # noqa: E402 - import follows the sys.path bootstrap above
import test_lyrics_accept_judge as tlaj  # noqa: E402 - import follows the sys.path bootstrap above
import test_lyrics_prune as tlp  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_undo import UNDO_DIR  # noqa: E402 - import follows the sys.path bootstrap above

#: What a refusal of a folder or lock that is a link or a junction says.
_LINKED = "is a symbolic link or junction"
#: An invented run id, for an entry no run made.
_RUN_ID = "20261001T000000Z"
#: What a file outside the album holds, unchanged by every case.
_OUTSIDE = b"not the album's\n"
#: Windows' reparse tags for a junction, a name surrogate, and for a cloud file
#: placeholder, which is none and is read as the folder or file it stands for.
TAG_JUNCTION = 0xA0000003
TAG_CLOUD = 0x9000001A


@contextmanager
def read_with_tag(tag: int, *paths: Path) -> Iterator[None]:
    """Hold a guards' look that reads exactly *paths* as a folder carrying *tag*."""
    tagged = frozenset(paths)
    real = os.lstat

    def look(path: Path) -> os.stat_result:
        if path in tagged:
            # A junction's own look, as Windows gives it: a folder, and its tag.
            found = SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_reparse_tag=tag)
            return cast(os.stat_result, found)
        return real(path)

    with mock.patch.object(rf, "lstat", look):
        yield


def read_as_junctions(*paths: Path) -> AbstractContextManager[None]:
    """Hold a guards' look that reads exactly *paths* as junctions."""
    return read_with_tag(TAG_JUNCTION, *paths)


def make_junction(target: Path, at: Path) -> None:
    """Make a real junction at *at* to the folder *target*; skip the test off Windows."""
    if sys.platform != "win32":
        raise unittest.SkipTest("a junction is a Windows folder link")
    import _winapi  # only Windows has it, so it is imported only here

    _winapi.CreateJunction(str(target), str(at))


class RunFolderTest(tlp.PruneCase):
    """The album's ``.lyricsmith`` and its lock, read as junctions."""

    def setUp(self) -> None:
        """A real run folder, holding a lock and a run."""
        super().setUp()
        self.folder = self.album / lr.RUN_FOLDER_NAME
        self.made_run()
        (self.folder / rf.LOCK_NAME).touch()

    def test_a_junction_run_folder_is_refused(self) -> None:
        with read_as_junctions(self.folder):
            with self.assertRaisesRegex(rf.RunFolderLinkError, _LINKED):
                rf.refuse_linked_run_folder(self.album)

    def test_a_junction_lock_is_refused(self) -> None:
        with read_as_junctions(self.folder / rf.LOCK_NAME):
            with self.assertRaisesRegex(rf.RunFolderLinkError, _LINKED):
                rf.refuse_linked_run_folder(self.album)

    def test_a_junction_run_folder_is_no_run_parent(self) -> None:
        with read_as_junctions(self.folder):
            self.assertIsNone(rf.run_parent(self.album))

    def test_a_junction_run_folder_is_never_listed_by_accept(self) -> None:
        with read_as_junctions(self.folder):
            contents = laj.read_contents(self.album)
            refusals = laj.judge_contents(contents).refusals
        self.assertEqual((contents.folder_linked, contents.runs), (True, ()))
        ((kind, detail),) = [(r.kind, r.detail) for r in refusals]
        self.assertEqual(kind, laj.RefusalKind.LINKED)
        self.assertIn(_LINKED, detail)


class LinkRuleTest(tlp.PruneCase):
    """The guards' link is the reads' link: a symbolic link or a name surrogate."""

    def setUp(self) -> None:
        """A real run folder."""
        super().setUp()
        self.folder = self.album / lr.RUN_FOLDER_NAME
        self.made_run()

    def test_a_cloud_placeholder_tag_is_no_link_to_the_run_folder_guard(self) -> None:
        # A reparse point that is no name surrogate leads nowhere else: a OneDrive or
        # deduplicated folder is the folder it is.
        with read_with_tag(TAG_CLOUD, self.folder):
            self.assertFalse(rf.is_link_or_junction(self.folder))
            rf.refuse_linked_run_folder(self.album)
            self.assertEqual(rf.run_parent(self.album), self.folder)

    def test_a_missing_path_is_no_link(self) -> None:
        self.assertFalse(rf.is_link_or_junction(self.album / "missing"))
        self.assertFalse(rf.is_link_or_junction(self.mp3 / "under-a-file"))

    def test_a_look_that_fails_otherwise_is_raised(self) -> None:
        # Only a missing path is known to be no link; any other failure is not read
        # as one, which would let a guard act on what it could not see.
        denied = PermissionError(errno.EACCES, "invented denial")
        with mock.patch.object(rf, "lstat", side_effect=denied):
            with self.assertRaises(PermissionError):
                rf.is_link_or_junction(self.folder)


class RunEntryTest(tlp.PruneCase):
    """An entry under a run id's name, read as a junction, is never read as a run."""

    def setUp(self) -> None:
        """A run-id folder holding a manifest, as a run made it."""
        super().setUp()
        self.parent = self.album / lr.RUN_FOLDER_NAME
        self.run_id = self.made_run()
        self.entry = self.parent / self.run_id

    def test_a_junction_under_a_run_id_is_kept_as_no_folder(self) -> None:
        with read_as_junctions(self.entry):
            outcomes = self.outcomes(self.prune())
        self.assertEqual(outcomes, {self.run_id: lp.Outcome.KEEP_NOT_FOLDER})
        self.assertTrue(self.entry.is_dir())

    def test_a_junction_under_a_pruning_name_is_kept_not_finished(self) -> None:
        pruning = self.parent / f"{_RUN_ID}{lp.PRUNING_SUFFIX}"
        pruning.mkdir()
        with read_as_junctions(pruning):
            outcomes = self.outcomes(self.prune(dry_run=True))
        self.assertEqual(outcomes[pruning.name], lp.Outcome.KEEP_NOT_FOLDER)

    def test_a_junction_under_a_run_id_is_no_run_to_undo(self) -> None:
        with read_as_junctions(self.entry):
            with self.assertRaises(rf.NoRunError):
                rf.select_run(self.album, None)


class UndoFolderTest(tlaj.AcceptCase):
    """A run's ``undo`` folder read as a junction is never walked into."""

    def setUp(self) -> None:
        """A run whose ``undo`` folder holds one file."""
        super().setUp()
        self.run_id = self.made_run()
        self.undo_dir = self.folder / self.run_id / UNDO_DIR
        self.undo_dir.mkdir(exist_ok=True)
        (self.undo_dir / "inside.txt").write_bytes(_OUTSIDE)

    def test_accept_lists_a_junction_undo_as_itself(self) -> None:
        with read_as_junctions(self.undo_dir):
            (run,) = laj.read_contents(self.album).runs
        self.assertEqual(run.undo_files, (f"{lr.RUN_FOLDER_NAME}/{self.run_id}/undo",))

    def test_accept_reads_a_junction_lock_as_linked(self) -> None:
        lock = self.folder / rf.LOCK_NAME
        lock.mkdir()
        with read_as_junctions(lock):
            contents = laj.read_contents(self.album)
        self.assertEqual((contents.lock_linked, contents.unknown), (True, ()))


class DisplacedTest(tlp.PruneCase):
    """Prune counts an ``undo`` read as a junction as holding something, unread."""

    def test_an_empty_junction_undo_keeps_an_undone_run(self) -> None:
        run_id = self.made_run()
        self.undo()
        undo_dir = self.album / lr.RUN_FOLDER_NAME / run_id / UNDO_DIR
        shutil.rmtree(undo_dir, ignore_errors=True)
        undo_dir.mkdir()
        with read_as_junctions(undo_dir):
            outcomes = self.outcomes(self.prune())
        self.assertEqual(outcomes, {run_id: lp.Outcome.KEEP_DISPLACED})


class DiscoveryTest(tlp.PruneCase):
    """Discovery skips and reports a folder read as a junction, never walking it."""

    def test_a_junction_folder_is_reported_and_its_tracks_skipped(self) -> None:
        linked = self.album / "linked"
        linked.mkdir()
        (linked / "elsewhere.mp3").write_bytes(b"")
        with read_as_junctions(linked):
            found = lyrics_album.walk_album(self.album)
        self.assertEqual(found.tracks, (self.mp3,))
        self.assertEqual(found.links, (linked,))


class OtherAlbumTest(tlp.PruneCase):
    """Prune through a ``.lyricsmith`` junction to another album's leaves that album be."""

    def setUp(self) -> None:
        """This album's undone runs moved to a second album, and the second's tree."""
        super().setUp()
        self.made_run()
        self.undo()
        self.second = self.tmp / "second"
        self.second.mkdir()
        self.target = self.second / lr.RUN_FOLDER_NAME
        (self.album / lr.RUN_FOLDER_NAME).rename(self.target)
        self.before = tlp.tree(self.second)
        self.runs = sorted(p.name for p in self.target.iterdir())

    def assert_untouched(self) -> None:
        """The second album's run folders and files are all still there, unchanged."""
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), self.runs)
        self.assertEqual(tlp.tree(self.second), self.before)

    def test_a_link_read_as_a_junction_is_refused_and_leaves_it(self) -> None:
        # A junction reads as no symbolic link and as a folder, as this link then does.
        link = self.album / lr.RUN_FOLDER_NAME
        link.symlink_to(self.target, target_is_directory=True)
        real: Callable[[Path], bool] = Path.is_symlink

        def is_symlink(path: Path) -> bool:
            return False if path == link else real(path)

        with (
            mock.patch.object(Path, "is_symlink", is_symlink),
            read_as_junctions(link),
        ):
            with self.assertRaisesRegex(lyrics_album.RunFolderLinkError, _LINKED):
                self.prune()
        self.assert_untouched()

    def test_a_real_junction_is_refused_and_leaves_it(self) -> None:
        make_junction(self.target, self.album / lr.RUN_FOLDER_NAME)
        with self.assertRaisesRegex(lyrics_album.RunFolderLinkError, _LINKED):
            self.prune()
        self.assert_untouched()

    def test_a_real_junction_under_a_run_id_is_kept_and_left(self) -> None:
        parent = self.album / lr.RUN_FOLDER_NAME
        parent.mkdir()
        (outside := self.tmp / "outside").mkdir()
        (outside / "keep.bin").write_bytes(_OUTSIDE)
        make_junction(outside, parent / _RUN_ID)
        self.assertEqual(
            self.outcomes(self.prune()), {_RUN_ID: lp.Outcome.KEEP_NOT_FOLDER}
        )
        self.assertEqual((outside / "keep.bin").read_bytes(), _OUTSIDE)
        self.assertTrue(os.path.lexists(parent / _RUN_ID))


if __name__ == "__main__":  # pragma: no cover - run directly, outside the gate
    unittest.main()
