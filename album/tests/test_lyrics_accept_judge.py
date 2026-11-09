"""The accept judgment over real run folders: what it reads, refuses and recognizes.

Each album and run folder is built under the test's own folder with the real backup and
undo; every title and lyric is invented. The pure rules' own cases are in
``test_lyrics_accept_judge_rules``.
"""

from __future__ import annotations

import datetime as dt
import errno
import os
import stat
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from types import FrameType
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import lyrics_accept_judge as laj  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_run_folders import LOCK_NAME  # noqa: E402 - import follows the sys.path bootstrap above

K = laj.RefusalKind
R = laj.AtRiskReason
_EDITED = b"The run's verse, edited after the run\n"


def _mode_000_locks_a_folder() -> bool:
    """True when ``chmod(0)`` stops this process listing a folder.

    Root lists a mode-000 folder, and on Windows ``chmod`` sets only the read-only
    attribute, which never stops a listing. Probed rather than inferred from the
    platform, so the real-permission test runs wherever it can; its twin raises the
    refusal through the listing itself, which runs everywhere.
    """
    with TemporaryDirectory() as scratch:
        folder = Path(scratch) / "probe"
        folder.mkdir()
        folder.chmod(0)
        try:
            list(folder.iterdir())
        except PermissionError:
            return True
        finally:
            folder.chmod(stat.S_IRWXU)
    return False


#: Whether ``chmod(0)`` locks a folder here; probed once, at import.
_MODE_000_LOCKS = _mode_000_locks_a_folder()


def later(minutes: int) -> dt.datetime:
    """A start *minutes* after the fixed clock's, for a second run."""
    return run_fakes.STARTED + dt.timedelta(minutes=minutes)


class AcceptCase(rb.UndoCase):
    """An album of one track holding a ``.txt``; judge it as accept reads it."""

    def setUp(self) -> None:
        """The track every run here backs up."""
        super().setUp()
        self.mp3 = self.track("01.mp3", txt="A verse the album held\n")
        self.folder = self.album / lr.RUN_FOLDER_NAME

    def made_run(self, minutes: int = 0) -> str:
        """A real run of the one track, writing verse text of its own."""
        verse = lyrics_model.PlainLyrics(f"Verse {minutes} the run wrote\n")
        manifest = rb.simulate_run(
            self.album, self.back_up(self.mp3, started=later(minutes)), verse
        )
        return manifest.run_id

    def judged(self) -> laj.Judgment:
        """The judgment of the album as it now stands."""
        return laj.judge_contents(laj.read_contents(self.album))

    def refusals(self) -> list[tuple[laj.RefusalKind, str]]:
        """Each refusal's kind and detail."""
        return [(r.kind, r.detail) for r in self.judged().refusals]

    def risks(self) -> list[tuple[str, str, laj.AtRiskReason]]:
        """Each at-risk entry's track, run and reason."""
        return [(r.track, r.run_id, r.reason) for r in self.judged().at_risk]


class RefusalsTest(AcceptCase):
    """One refusal per rule; anything refused is named."""

    def test_an_unfinished_backed_up_run(self) -> None:
        run_id = self.back_up(self.mp3).run_id
        self.assertEqual([k for k, _ in self.refusals()], [K.UNFINISHED])
        self.assertIn(run_id, self.refusals()[0][1])
        self.assertEqual(self.risks(), [("01.mp3", run_id, R.UNFINISHED)])

    def test_an_unreadable_manifest(self) -> None:
        run_id = self.made_run()
        (self.folder / run_id / lr.MANIFEST_NAME).write_bytes(b"not json")
        ((kind, detail),) = self.refusals()
        self.assertEqual(kind, K.UNREADABLE)
        self.assertIn(run_id, detail)
        self.assertEqual(self.judged().runs, ())

    @unittest.skipUnless(
        _MODE_000_LOCKS, "chmod(0) does not stop a listing here (root, or Windows)"
    )
    def test_an_undo_folder_that_cannot_be_listed(self) -> None:
        run_id = self.made_run()
        inner = self.folder / run_id / "undo" / "inner"
        inner.mkdir(parents=True)
        inner.chmod(0)
        self.addCleanup(inner.chmod, stat.S_IRWXU)
        self.assert_refused_as_an_unreadable_run_folder(run_id)

    def test_an_undo_folder_whose_listing_is_refused(self) -> None:
        # The portable twin of the mode-000 test: the listing raises the refusal
        # itself, so the judgment is proven where chmod cannot lock a folder.
        run_id = self.made_run()
        inner = self.folder / run_id / "undo" / "inner"
        inner.mkdir(parents=True)
        real_iterdir = Path.iterdir

        def refuse_inner(path: Path) -> Iterator[Path]:
            if path == inner:
                raise PermissionError(errno.EACCES, "Permission denied", str(path))
            return real_iterdir(path)

        with mock.patch.object(Path, "iterdir", refuse_inner):
            self.assert_refused_as_an_unreadable_run_folder(run_id)

    def assert_refused_as_an_unreadable_run_folder(self, run_id: str) -> None:
        """Assert the one refusal is run *run_id*'s, as an unreadable run folder."""
        ((kind, detail),) = self.refusals()
        # Its manifest read: what failed is the run folder, so it is named as such.
        self.assertEqual(kind, K.UNREADABLE_FOLDER)
        self.assertEqual(str(kind), "unreadable run folder")
        self.assertIn(run_id, detail)

    def test_each_unknown_entry_is_named(self) -> None:
        self.made_run()
        (self.folder / "notes.txt").write_bytes(b"mine\n")
        (self.folder / "photos").mkdir()
        os.symlink("elsewhere", self.folder / "20261001T000000Z")
        self.assertEqual(
            sorted(self.refusals()),
            [
                (K.UNKNOWN, "20261001T000000Z"),
                (K.UNKNOWN, "notes.txt"),
                (K.UNKNOWN, "photos"),
            ],
        )

    def test_a_lock_that_is_a_folder_is_unknown(self) -> None:
        (self.folder / LOCK_NAME).mkdir(parents=True)
        self.assertEqual(self.refusals(), [(K.UNKNOWN, LOCK_NAME)])

    def test_a_run_folder_that_is_a_file_is_unknown(self) -> None:
        self.folder.write_bytes(b"not a folder\n")
        self.assertEqual(self.refusals(), [(K.UNKNOWN, lr.RUN_FOLDER_NAME)])

    def test_a_linked_run_folder(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "kept.txt").write_bytes(b"not the album's\n")
        self.folder.symlink_to(outside)
        contents = laj.read_contents(self.album)
        self.assertEqual((contents.folder_linked, contents.unknown), (True, ()))
        self.assertEqual([k for k, _ in self.refusals()], [K.LINKED])

    def test_a_linked_lock(self) -> None:
        self.made_run()
        (self.folder / LOCK_NAME).symlink_to(self.tmp / "elsewhere")
        ((kind, detail),) = self.refusals()
        self.assertEqual(kind, K.LINKED)
        self.assertIn(LOCK_NAME, detail)


class RecognizedTest(AcceptCase):
    """What lyricsmith itself leaves is recognized, never refused."""

    def test_no_run_folder_is_empty(self) -> None:
        self.assertEqual(laj.read_contents(self.album), laj.Contents())
        judged = self.judged()
        self.assertEqual((judged.refusals, judged.runs), ((), ()))

    def test_the_lock_and_a_complete_run(self) -> None:
        run_id = self.made_run()
        (self.folder / LOCK_NAME).touch()
        contents = laj.read_contents(self.album)
        # The lock is known: neither an unknown entry nor a run.
        self.assertEqual(contents.unknown, ())
        self.assertEqual([r.name for r in contents.runs], [run_id])
        self.assertEqual(self.refusals(), [])

    def test_a_pruning_leftover(self) -> None:
        run_id = self.made_run()
        (self.folder / run_id).rename(self.folder / f"{run_id}.pruning")
        contents = laj.read_contents(self.album)
        # A prune's leftover is known: neither an unknown entry nor a run.
        self.assertEqual((contents.unknown, contents.runs), ((), ()))
        self.assertEqual((self.refusals(), self.judged().runs), ([], ()))

    def test_an_accepting_leftover(self) -> None:
        (self.album / laj.ACCEPTING_NAME).mkdir()
        self.assertTrue(laj.read_contents(self.album).leftover)
        self.assertEqual(self.refusals(), [])

    def test_a_folder_with_no_manifest(self) -> None:
        (self.folder / "20261001T000000Z").mkdir(parents=True)
        judged = self.judged()
        self.assertEqual(judged.refusals, ())
        self.assertEqual(judged.runs, (laj.RunHeld("20261001T000000Z", None, 0, 0, 0),))


class EveryRunTest(AcceptCase):
    """Every run's backup is the only copy of what it replaced."""

    def test_two_runs_that_changed_one_track_are_both_listed(self) -> None:
        first, second = self.made_run(), self.made_run(5)
        self.assertEqual(
            self.risks(),
            [("01.mp3", first, R.REPLACED), ("01.mp3", second, R.REPLACED)],
        )
        held = self.judged().runs
        self.assertEqual(
            [(h.run_id, h.sidecars) for h in held], [(first, 1), (second, 1)]
        )

    def test_a_newer_runs_undo_leaves_only_the_older_one(self) -> None:
        first, second = self.made_run(), self.made_run(5)
        self.undo(second)
        self.assertEqual(self.risks(), [("01.mp3", first, R.REPLACED)])


class SetAsideTest(AcceptCase):
    """A sidecar undo set aside is at risk, listed once under its track."""

    def test_a_displaced_sidecar(self) -> None:
        run_id = self.made_run()
        self.mp3.with_suffix(".txt").write_bytes(_EDITED)
        self.undo()
        found = self.judged().at_risk
        self.assertEqual(
            [(r.track, r.run_id, r.reason) for r in found],
            [("01.mp3", run_id, R.SET_ASIDE)],
        )
        undo = laj.read_contents(self.album).runs[0].undo_files
        self.assertEqual(found[0].detail, ", ".join(undo))
        self.assertEqual(self.judged().runs[0].set_aside, 1)

    def test_an_undo_entry_that_is_a_file(self) -> None:
        run_id = self.made_run()
        (self.folder / run_id / "undo").write_bytes(b"odd\n")
        undo = f"{lr.RUN_FOLDER_NAME}/{run_id}/undo"
        self.assertEqual(laj.read_contents(self.album).runs[0].undo_files, (undo,))
        self.assertIn((undo, run_id, R.SET_ASIDE), self.risks())

    def test_an_undo_folder_nested_past_the_recursion_limit_is_listed(self) -> None:
        run_id = self.made_run()
        undo = self.folder / run_id / "undo"
        deepest = undo.joinpath(*["d"] * _DEEPER_THAN_THE_LIMIT)
        deepest.mkdir(parents=True)
        (deepest / "f").write_bytes(b"deep\n")
        previous = sys.getrecursionlimit()
        # Room for the frames already on the stack and the read's own, not the nesting.
        sys.setrecursionlimit(_frames_on_the_stack() + _HEADROOM)
        try:
            found = laj.read_contents(self.album).runs[0].undo_files
        finally:
            sys.setrecursionlimit(previous)
        expected = (deepest / "f").relative_to(self.album).as_posix()
        self.assertEqual(found, (expected,))

    def test_a_link_under_the_undo_folder_is_listed_not_followed(self) -> None:
        run_id = self.made_run()
        inner = self.folder / run_id / "undo" / "inner"
        inner.mkdir(parents=True)
        outside = self.tmp / "outside"
        (outside / "sub").mkdir(parents=True)
        try:
            (inner / "link").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")
        (inner / "file").write_bytes(b"kept\n")
        prefix = f"{lr.RUN_FOLDER_NAME}/{run_id}/undo/inner"
        self.assertEqual(
            laj.read_contents(self.album).runs[0].undo_files,
            (f"{prefix}/file", f"{prefix}/link"),
        )


#: How deep the nested undo folder goes, and the stack room the read is given.
_DEEPER_THAN_THE_LIMIT = 150
_HEADROOM = 80


def _frames_on_the_stack() -> int:
    """How many frames the caller's stack holds now."""
    frame: FrameType | None = sys._getframe(1)
    count = 0
    while frame is not None:
        frame, count = frame.f_back, count + 1
    return count


if __name__ == "__main__":
    unittest.main()
