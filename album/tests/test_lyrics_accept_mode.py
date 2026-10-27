"""Accept's in-process pass, `accept_album`, and its report, `format_accept`.

Every album, run folder and records folder is built under the test's own folder, with
HOME and the XDG folders redirected there; the runs are made by the real backup, the
check by the real stages, and every title and lyric is invented.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_judge as laj  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_mode as lam  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_records as lar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import run_lock  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_album import LockHeldError  # noqa: E402 - import follows the sys.path bootstrap above

#: A fixed accept time.
_NOW = dt.datetime(2026, 10, 25, 9, 8, 7, tzinfo=dt.UTC)
_DELETE_FAILED = "the delete was refused"
_PROG = "album.py"


def tree_hashes(root: Path) -> dict[str, str]:
    """Every file under *root*, ``.lyricsmith`` included, by path, to its sha256."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def outside_hashes(root: Path) -> dict[str, str]:
    """As `tree_hashes`, every lyricsmith folder left out."""
    return {
        name: digest
        for name, digest in tree_hashes(root).items()
        if not name.startswith(lr.RUN_FOLDER_NAME)
    }


def failing_delete(path: Path) -> None:
    """A delete that is always refused, as a busy folder's would be."""
    raise OSError(_DELETE_FAILED)


class AcceptCase(rb.UndoCase):
    """An album of one track whose sidecar a real run replaced."""

    def setUp(self) -> None:
        """One finished run over a track holding a sidecar; its state folder redirected."""
        super().setUp()
        self.mp3 = self.track("01.mp3", txt="A verse the album held\n")
        self.state = self.tmp / "state"
        self.environ = {"XDG_STATE_HOME": str(self.state)}
        self.run_id = self.made_run(0)
        # A real run leaves its lock file behind; the stand-in run takes no lock.
        lock = run_lock.acquire(self.album / lr.RUN_FOLDER_NAME / "lock")
        assert lock is not None
        lock.release()

    def made_run(
        self, minutes: int, *, record: bool = True, verse: str | None = None
    ) -> str:
        """A real run of the one track, *minutes* after the fixed clock.

        *verse* None writes a verse naming *minutes*; runs sharing a start each need
        their own, or the track is left unchanged.
        """
        words = f"Verse {minutes} the run wrote\n" if verse is None else verse
        lyrics = lyrics_model.PlainLyrics(words)
        started = run_fakes.STARTED + dt.timedelta(minutes=minutes)
        backed = self.back_up(self.mp3, started=started)
        return rb.simulate_run(self.album, backed, lyrics, record=record).run_id

    def accept(
        self, *, apply: bool, delete: lam.Delete | None = None
    ) -> lam.AcceptReport:
        """Accept this test's album, real stages and lock, the fixed clock."""
        return lam.accept_album(
            self.album,
            apply=apply,
            acquire_lock=run_lock.acquire,
            stages=album.real_wiring().stages,
            environ=self.environ,
            now=lambda: _NOW,
            delete=delete,
        )

    def records_folders(self) -> list[Path]:
        """Each records folder an accept made under the redirected state folder."""
        root = lar.records_root(self.environ)
        return sorted(root.iterdir()) if root.is_dir() else []


class ReportOnlyTest(AcceptCase):
    """Without apply, the album is judged and checked, and not one byte changes."""

    def test_no_byte_changes_and_nothing_is_recorded(self) -> None:
        before = tree_hashes(self.album)
        report = self.accept(apply=False)
        self.assertEqual(tree_hashes(self.album), before)
        self.assertFalse(self.state.exists())
        self.assertIsNone(report.records)
        self.assertEqual([r.outcome for r in report.rows], [lam.WOULD_REMOVE])
        self.assertEqual(report.removed, 0)

    def test_at_risk_holds_the_judgment_and_the_check_by_track_then_run(self) -> None:
        newer = self.made_run(5)
        report = self.accept(apply=False)
        keys = [(r.track, r.run_id) for r in report.at_risk]
        self.assertEqual(keys, sorted(keys))
        replaced = {
            r.run_id for r in report.at_risk if r.reason is laj.AtRiskReason.REPLACED
        }
        # Each run's backup is the only copy of what that run replaced.
        self.assertEqual(replaced, {self.run_id, newer})

    def test_at_risk_rows_of_one_track_sort_by_run_order(self) -> None:
        # Runs started at the same time take the suffixes -2, -3 and so on, so a
        # string sort would put the run ending -10 before the one ending -2.
        later = [
            self.made_run(0, verse=f"Verse {n} at the same start\n") for n in range(9)
        ]
        self.assertEqual(later[-1], f"{self.run_id}-10")
        report = self.accept(apply=False)
        replaced = [
            r.run_id for r in report.at_risk if r.reason is laj.AtRiskReason.REPLACED
        ]
        self.assertEqual(replaced, [self.run_id, *later])

    def test_at_risk_holds_the_check_beside_the_judgment(self) -> None:
        reasons = [r.reason for r in self.accept(apply=False).at_risk]
        self.assertEqual(
            reasons, [laj.AtRiskReason.REPLACED, laj.AtRiskReason.CHECK_FLAG]
        )

    def test_a_lock_less_run_folder_gains_only_an_empty_lock(self) -> None:
        # Pins the lock's cost: report-only still takes the album lock, its safety,
        # and taking it creates the file; nothing else is written or changed.
        (self.album / lr.RUN_FOLDER_NAME / "lock").unlink()
        before = tree_hashes(self.album)
        self.accept(apply=False)
        empty = hashlib.sha256(b"").hexdigest()
        self.assertEqual(
            tree_hashes(self.album), {**before, f"{lr.RUN_FOLDER_NAME}/lock": empty}
        )

    def test_each_row_holds_its_run(self) -> None:
        (row,) = self.accept(apply=False).rows
        self.assertEqual(row.name, self.run_id)
        assert row.held is not None
        self.assertEqual(row.held.state, lr.RunState.COMPLETE)
        self.assertEqual(row.held.sidecars, 1)


class ApplyTest(AcceptCase):
    """With apply, the manifests are recorded and then ``.lyricsmith`` goes."""

    def test_the_run_folder_goes_and_every_album_file_stays(self) -> None:
        before = outside_hashes(self.album)
        manifest = (
            lr.run_folder(self.album, self.run_id) / lr.MANIFEST_NAME
        ).read_bytes()
        report = self.accept(apply=True)
        self.assertFalse(os.path.lexists(self.album / lr.RUN_FOLDER_NAME))
        self.assertFalse(os.path.lexists(self.album / laj.ACCEPTING_NAME))
        self.assertEqual(outside_hashes(self.album), before)
        (folder,) = self.records_folders()
        self.assertEqual(report.records, folder)
        self.assertEqual((folder / f"{self.run_id}.json").read_bytes(), manifest)
        self.assertTrue((folder / lar.ALBUM_FILE).is_file())
        self.assertEqual([r.outcome for r in report.rows], [lam.REMOVED])
        self.assertEqual((report.removed, report.errors), (1, 0))

    def test_a_records_failure_deletes_nothing(self) -> None:
        self.state.write_text(
            "a file where the state folder goes", encoding="utf-8", newline="\n"
        )
        before = tree_hashes(self.album)
        report = self.accept(apply=True)
        self.assertEqual(tree_hashes(self.album), before)
        self.assertIsNotNone(report.error)
        self.assertIsNone(report.records)
        self.assertIsNone(report.partial)
        self.assertEqual([r.outcome for r in report.rows], [lam.KEPT])
        self.assertEqual(report.removed, 0)

    def test_a_records_write_failure_names_the_partial_folder(self) -> None:
        (self.album / laj.ACCEPTING_NAME).mkdir()
        before = tree_hashes(self.album / lr.RUN_FOLDER_NAME)
        with mock.patch.object(lam, "write_records", side_effect=OSError("invented")):
            report = self.accept(apply=True)
        (folder,) = self.records_folders()
        self.assertEqual((report.records, report.partial), (None, folder))
        self.assertEqual([r.outcome for r in report.rows], [lam.KEPT])
        self.assertEqual(tree_hashes(self.album / lr.RUN_FOLDER_NAME), before)
        # The leftover was finished before the records were begun, and stays finished.
        assert report.leftover is not None
        self.assertEqual(report.leftover.outcome, f"{lam.REMOVED}: {lam.LEFT_BY}")
        self.assertEqual(lam.accept_exit(report), lam.EXIT_CANNOT_RUN)

    def test_a_failed_removal_leaves_a_leftover_the_next_accept_finishes(self) -> None:
        report = self.accept(apply=True, delete=failing_delete)
        self.assertEqual(report.rows[0].outcome, f"ERROR: {_DELETE_FAILED}")
        self.assertEqual((report.removed, report.errors), (0, 1))
        self.assertTrue((self.album / laj.ACCEPTING_NAME).is_dir())
        again = self.accept(apply=True)
        assert again.leftover is not None
        self.assertEqual(again.leftover.outcome, f"{lam.REMOVED}: {lam.LEFT_BY}")
        self.assertFalse(os.path.lexists(self.album / laj.ACCEPTING_NAME))
        # Finished without the lock, so no `.lyricsmith` is made for it.
        self.assertFalse(os.path.lexists(self.album / lr.RUN_FOLDER_NAME))
        self.assertEqual(again.rows, ())

    def test_a_leftover_beside_runs_is_finished_first(self) -> None:
        (self.album / laj.ACCEPTING_NAME).mkdir()
        report = self.accept(apply=True)
        assert report.leftover is not None
        self.assertEqual(report.leftover.outcome, f"{lam.REMOVED}: {lam.LEFT_BY}")
        self.assertEqual([r.outcome for r in report.rows], [lam.REMOVED])
        self.assertFalse(os.path.lexists(self.album / laj.ACCEPTING_NAME))

    def test_a_leftover_that_cannot_go_stops_before_the_records(self) -> None:
        (self.album / laj.ACCEPTING_NAME).mkdir()
        before = tree_hashes(self.album / lr.RUN_FOLDER_NAME)
        report = self.accept(apply=True, delete=failing_delete)
        assert report.leftover is not None
        self.assertEqual(report.leftover.outcome, f"ERROR: {_DELETE_FAILED}")
        self.assertEqual([r.outcome for r in report.rows], [lam.KEPT])
        self.assertEqual(tree_hashes(self.album / lr.RUN_FOLDER_NAME), before)
        self.assertEqual(self.records_folders(), [])
        self.assertEqual(report.errors, 1)


class EdgesTest(AcceptCase):
    """No run, a leftover alone, the refusals and the lock."""

    def test_an_album_with_no_run_is_untouched(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        self.album = empty
        report = self.accept(apply=True)
        self.assertFalse(report.found)
        self.assertEqual(list(empty.iterdir()), [])

    def test_a_run_folder_holding_only_its_lock_goes_with_no_table(self) -> None:
        shutil.rmtree(lr.run_folder(self.album, self.run_id))
        report = self.accept(apply=True)
        self.assertEqual((report.rows, report.leftover), ((), None))
        self.assertFalse(os.path.lexists(self.album / lr.RUN_FOLDER_NAME))
        text = lam.format_accept(report, album=self.album, prog=_PROG)
        self.assertNotIn("outcome", text)
        self.assertTrue(text.endswith("removed 0 · at risk 0 · errors 0\n"))

    def test_a_leftover_alone_is_reported_without_the_lock(self) -> None:
        (self.album / lr.RUN_FOLDER_NAME).rename(self.album / laj.ACCEPTING_NAME)
        report = self.accept(apply=False)
        assert report.leftover is not None
        self.assertEqual(report.leftover.outcome, f"{lam.WOULD_REMOVE}: {lam.LEFT_BY}")
        self.assertTrue((self.album / laj.ACCEPTING_NAME).is_dir())
        self.assertFalse(os.path.lexists(self.album / lr.RUN_FOLDER_NAME))

    def test_a_linked_leftover_is_refused_on_a_report_only_pass(self) -> None:
        target = self.tmp / "elsewhere"
        target.mkdir()
        (self.album / laj.ACCEPTING_NAME).symlink_to(target)
        with self.assertRaises(lar.UnsafeAcceptingError):
            self.accept(apply=False)

    def test_an_unfinished_run_refuses_the_album_whole(self) -> None:
        self.made_run(5, record=False)
        self.assert_refused(laj.RefusalKind.UNFINISHED)

    def test_an_unreadable_manifest_refuses_the_album(self) -> None:
        manifest = lr.run_folder(self.album, self.run_id) / lr.MANIFEST_NAME
        manifest.write_text("not json", encoding="utf-8", newline="\n")
        self.assert_refused(laj.RefusalKind.UNREADABLE)

    def test_an_unknown_entry_refuses_the_album(self) -> None:
        (self.album / lr.RUN_FOLDER_NAME / "notes.txt").write_text(
            "x", encoding="utf-8", newline="\n"
        )
        self.assert_refused(laj.RefusalKind.UNKNOWN)

    def assert_refused(self, kind: laj.RefusalKind) -> None:
        """Accept with apply refuses for *kind*, touching nothing and recording nothing."""
        before = tree_hashes(self.album)
        report = self.accept(apply=True)
        self.assertIn(kind, {r.kind for r in report.refusals})
        self.assertTrue(report.refused)
        self.assertEqual(tree_hashes(self.album), before)
        self.assertEqual(self.records_folders(), [])

    def test_a_held_lock_refuses_to_start(self) -> None:
        lock = run_lock.acquire(self.album / lr.RUN_FOLDER_NAME / "lock")
        assert lock is not None
        self.addCleanup(lock.release)
        with self.assertRaises(LockHeldError):
            self.accept(apply=True)
        self.assertTrue((self.album / lr.RUN_FOLDER_NAME).is_dir())


class RiskOrderTest(fixtures.IsolatedCase):
    """At-risk rows sort by track, then run order; any other name after every run."""

    def test_a_name_that_is_no_run_id_sorts_after_the_runs(self) -> None:
        def row(run_id: str) -> laj.AtRisk:
            return laj.AtRisk("01.mp3", run_id, laj.AtRiskReason.REPLACED, "replaced")

        names = ["notes", "20261001T000000Z-10", "20261001T000000Z-2", "a-run"]
        ordered = sorted((row(n) for n in names), key=lam._risk_order)
        self.assertEqual(
            [r.run_id for r in ordered],
            ["20261001T000000Z-2", "20261001T000000Z-10", "a-run", "notes"],
        )


class FormatTest(AcceptCase):
    """The report: at-risk tracks, the run table, the records, the counts, the hint."""

    def text(self, report: lam.AcceptReport) -> str:
        """*report* as printed for this test's album."""
        return lam.format_accept(report, album=self.album, prog=_PROG)

    def test_a_report_only_pass_in_its_order(self) -> None:
        text = self.text(self.accept(apply=False))
        lines = text.splitlines()
        self.assertEqual(lines[0], "album accept: 1 run folder under album")
        order = [
            text.index("01.mp3"),
            text.index(self.run_id),
            # Two at-risk rows, replaced and check flags, but one track.
            text.index("would remove 1 · at risk 1 · errors 0"),
            text.index(lam.HINT),
        ]
        self.assertEqual(order, sorted(order))
        self.assertTrue(text.endswith(f"{lam.HINT}\n"))
        self.assertIn("replaced", text)
        self.assertIn("check flags", text)

    def test_hint_false_leaves_out_only_the_hint(self) -> None:
        report = self.accept(apply=False)
        text = lam.format_accept(report, album=self.album, prog=_PROG, hint=False)
        self.assertEqual(f"{text}{lam.HINT}\n", self.text(report))

    def test_an_applied_pass_names_the_records_before_the_counts(self) -> None:
        report = self.accept(apply=True)
        text = self.text(report)
        self.assertLess(
            text.index(f"records: {report.records}"), text.index("removed 1")
        )
        self.assertTrue(text.endswith("removed 1 · at risk 1 · errors 0\n"))
        self.assertNotIn(lam.HINT, text)

    def test_a_refusal_names_each_reason_and_removes_nothing(self) -> None:
        self.made_run(5, record=False)
        text = self.text(self.accept(apply=True))
        self.assertTrue(text.startswith("album accept: 2 run folders under album\n"))
        self.assertIn("refused: unfinished run: ", text)
        self.assertIn("is unfinished: undo it, or run the album again, first", text)
        self.assertTrue(text.endswith("refused: nothing was removed\n"))

    def test_no_run_says_so(self) -> None:
        report = lam.AcceptReport(apply=False, found=False)
        self.assertEqual(self.text(report), "album accept: album has no run\n")

    def test_a_leftover_reads_left_by_an_earlier_accept(self) -> None:
        self.accept(apply=True, delete=failing_delete)
        text = self.text(self.accept(apply=True))
        self.assertIn(laj.ACCEPTING_NAME, text)
        self.assertIn(f"removed: {lam.LEFT_BY}", text)
        self.assertTrue(text.endswith("removed 0 · at risk 0 · errors 0\n"))

    def test_at_risk_counts_tracks_and_lists_every_row(self) -> None:
        self.made_run(5)
        report = self.accept(apply=False)
        self.assertEqual(len({r.track for r in report.at_risk}), 1)
        self.assertEqual(len(report.at_risk), 2)
        self.assertEqual(report.tracks_at_risk, 1)
        text = self.text(report)
        self.assertIn(f"{len(report.at_risk)}  01.mp3", text)
        self.assertIn("at risk 1 · ", text)

    def test_a_partial_records_folder_is_named_before_the_error(self) -> None:
        partial = self.tmp / "partial"
        report = dataclasses.replace(
            self.accept(apply=False), apply=True, partial=partial, error="boom"
        )
        text = self.text(report)
        self.assertLess(
            text.index(f"partial records: {partial}"), text.index("ERROR: boom")
        )

    def test_an_error_is_said_before_the_counts(self) -> None:
        report = dataclasses.replace(self.accept(apply=False), apply=True, error="boom")
        text = self.text(report)
        self.assertLess(text.index("ERROR: boom"), text.index("errors 1"))

    def test_a_stateless_row_shows_a_dash(self) -> None:
        held = laj.RunHeld("20261014T153012Z", None, 0, 0, 0)
        row = lam.AcceptRow(held.run_id, held, lam.WOULD_REMOVE)
        report = lam.AcceptReport(apply=False, rows=(row,))
        cells = [line.split() for line in self.text(report).splitlines()]
        self.assertIn(["1", held.run_id, "-", "0", "0", "0", "would", "remove"], cells)


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
