"""The sweep itself: order, limit, pause, ledger, resume, the stop rule and Ctrl-C.

The album tool is played by `sweep_fixtures.FakeAlbumTool`, which writes each run's
manifest through the album tool's own writer, and the pause by a sleep that only records,
so nothing here waits or reaches a provider.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_made_run  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run as sr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_model import (  # noqa: E402 - import follows the sys.path bootstrap above
    ReadBackError,
)
from sweep_tally import Counts  # noqa: E402 - follows the sys.path bootstrap

_REFUSED = sf.Script(outcomes=sf.REFUSED, exit_code=album.EXIT_TRACK_ERRORS)
_LOCKED = sf.Script(outcomes=(), exit_code=album.EXIT_CANNOT_RUN, manifest=False)


class OrderAndPaceTest(sf.SweepCase):
    """Albums run in walk order, one at a time, paused between, capped by --limit."""

    def test_every_album_runs_in_order_with_the_bound(self) -> None:
        report = self._sweep()
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A3", "A4", "A5"])
        self.assertEqual(self.fake.timeouts, [123.0] * 5)
        self.assertIs(report.stop, sr.StopReason.FINISHED)
        self.assertEqual(report.remaining, 0)
        self.assertTrue(all("--fresh" not in argv for argv in self.fake.calls))

    def test_the_pause_falls_between_albums_never_before_the_first(self) -> None:
        self._sweep()
        self.assertEqual(self.sleep.pauses, [7.0] * 4)

    def test_limit_caps_the_albums_visited(self) -> None:
        report = self._sweep(limit=2)
        self.assertEqual(self.fake.albums(), ["A1", "A2"])
        self.assertIs(report.stop, sr.StopReason.LIMIT)
        self.assertEqual(report.remaining, 3)

    def test_a_limit_past_the_albums_left_finishes(self) -> None:
        report = self._sweep(limit=9)
        self.assertIs(report.stop, sr.StopReason.FINISHED)

    def test_progress_names_each_album_as_it_starts(self) -> None:
        self._sweep(limit=1)
        self.assertIn("[1/5] Artist/A1", self.notes)


class LedgerTest(sf.SweepCase):
    """One ledger line per album, and a resumed sweep skipping what is done."""

    def test_each_album_appends_one_entry_with_its_counts(self) -> None:
        self.fake.scripts["A2"] = _REFUSED
        report = self._sweep(limit=2)
        first, second = self._ledger()
        self.assertEqual(first.album, "Artist/A1")
        self.assertIs(first.outcome, sl.Outcome.DONE)
        self.assertEqual(first.exit_code, 0)
        assert first.counts is not None
        self.assertEqual((first.counts.written, first.counts.not_found), (1, 1))
        self.assertEqual(first.time, sf.fixed_now().isoformat())
        self.assertIsNotNone(first.run_id)
        self.assertIs(second.outcome, sl.Outcome.REFUSED)
        self.assertEqual(second.refusing_tracks, 2)
        self.assertEqual(
            second.refusing_providers, (lm.Source.LRCLIB.label, lm.Source.NETEASE.label)
        )
        self.assertEqual([v.entry for v in report.visited], [first, second])

    def test_a_resumed_sweep_skips_done_albums_and_retries_the_rest(self) -> None:
        self.fake.scripts["A2"] = _REFUSED
        self.fake.scripts["A3"] = _LOCKED
        self._sweep(limit=3)
        self.fake.scripts.clear()
        report = self._sweep()
        self.assertEqual(self.fake.albums()[3:], ["A2", "A3", "A4", "A5"])
        self.assertEqual(report.skipped_done, 1)

    def test_restart_runs_done_albums_again(self) -> None:
        self._sweep(limit=2)
        report = self._sweep(restart=True, limit=1)
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A1"])
        self.assertEqual(report.skipped_done, 0)
        # The restart is on record, so a resume after it skips only what it redid.
        self._sweep(limit=1)
        self.assertEqual(self.fake.albums()[3:], ["A2"])

    def test_malformed_ledger_lines_are_reported_and_the_sweep_goes_on(self) -> None:
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_text(
            "this is not a ledger line\n", encoding="utf-8", newline=""
        )
        self._sweep(limit=1)
        self.assertTrue(any("line 1" in note for note in self.notes))
        self.assertEqual(len(self._ledger()), 1)


class OfflineTest(sf.SweepCase):
    """An offline visit asks no provider, so it never makes an album done."""

    def test_an_offline_visit_is_recorded_as_offline_and_never_done(self) -> None:
        report = self._sweep(offline=True)
        self.assertEqual(
            {entry.outcome for entry in self._ledger()}, {sl.Outcome.OFFLINE}
        )
        self.assertEqual(sr.sweep_exit_code(report), sr.EXIT_OK)
        records = sl.read_ledger(self.ledger).records
        self.assertEqual(
            sl.done_albums(records, sr.DEFAULT_LANG, sl.SweepPass.NORMAL), frozenset()
        )

    def test_a_later_online_sweep_visits_every_album_an_offline_one_did(
        self,
    ) -> None:
        self._sweep(offline=True)
        report = self._sweep()
        self.assertEqual(self.fake.albums()[5:], ["A1", "A2", "A3", "A4", "A5"])
        self.assertEqual(report.skipped_done, 0)


class LangTest(sf.SweepCase):
    """Each entry records its language, and an album is done per language."""

    def test_each_entry_records_the_language_it_ran_in(self) -> None:
        self._sweep(limit=1)
        self._sweep(limit=1, lang="deu")
        self.assertEqual(
            [entry.lang for entry in self._ledger()], [sr.DEFAULT_LANG, "deu"]
        )

    def test_an_album_done_in_one_language_is_visited_in_another(self) -> None:
        self._sweep(limit=1)
        report = self._sweep(limit=1, lang="deu")
        self.assertEqual(self.fake.albums(), ["A1", "A1"])
        self.assertEqual(report.skipped_done, 0)
        # Back in the default language, A1 is done.
        self._sweep(limit=1)
        self.assertEqual(self.fake.albums()[2:], ["A2"])


class StopRuleTest(sf.SweepCase):
    """The sweep stops after --stop-after consecutive refused albums, and only then."""

    def test_exactly_n_consecutive_refused_albums_stop_the_sweep(self) -> None:
        for name in ("A1", "A2", "A3"):
            self.fake.scripts[name] = _REFUSED
        report = self._sweep()
        self.assertEqual(self.fake.albums(), ["A1", "A2"])
        self.assertIs(report.stop, sr.StopReason.REFUSALS)
        self.assertEqual(report.remaining, 3)

    def test_an_answered_album_in_between_resets_the_count(self) -> None:
        for name in ("A1", "A3", "A5"):
            self.fake.scripts[name] = _REFUSED
        report = self._sweep()
        self.assertIs(report.stop, sr.StopReason.FINISHED)

    def test_an_album_that_could_not_run_neither_counts_nor_resets(self) -> None:
        self.fake.scripts.update(A1=_REFUSED, A2=_LOCKED, A3=_REFUSED)
        report = self._sweep()
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A3"])
        self.assertIs(report.stop, sr.StopReason.REFUSALS)
        self.assertIs(self._ledger()[1].outcome, sl.Outcome.COULD_NOT_RUN)

    def test_a_timed_out_album_neither_counts_nor_resets(self) -> None:
        timed_out = sf.Script(exit_code=album.EXIT_INTERRUPTED, timed_out=True)
        self.fake.scripts.update(A1=_REFUSED, A2=timed_out, A3=_REFUSED)
        report = self._sweep()
        self.assertIs(self._ledger()[1].outcome, sl.Outcome.TIMED_OUT)
        self.assertIs(report.stop, sr.StopReason.REFUSALS)


class InterruptTest(sf.SweepCase):
    """Ctrl-C ends the sweep after the album in hand, which is recorded, not done."""

    def test_an_interrupted_album_is_recorded_and_the_sweep_stops(self) -> None:
        self.fake.scripts["A2"] = sf.Script(
            exit_code=album.EXIT_INTERRUPTED, interrupted=True
        )
        report = self._sweep()
        self.assertEqual(self.fake.albums(), ["A1", "A2"])
        self.assertIs(report.stop, sr.StopReason.INTERRUPTED)
        self.assertIs(self._ledger()[1].outcome, sl.Outcome.INTERRUPTED)
        self.assertEqual(report.remaining, 3)
        self.assertEqual(
            sl.done_albums(
                sl.read_ledger(self.ledger).records,
                sr.DEFAULT_LANG,
                sl.SweepPass.NORMAL,
            ),
            {"Artist/A1"},
        )

    def test_a_child_that_exits_interrupted_stops_the_sweep_too(self) -> None:
        self.fake.scripts["A1"] = sf.Script(exit_code=album.EXIT_INTERRUPTED)
        report = self._sweep()
        self.assertIs(report.stop, sr.StopReason.INTERRUPTED)

    def test_ctrl_c_during_the_pause_stops_before_the_next_album(self) -> None:
        def interrupted_sleep(_seconds: float) -> None:
            raise KeyboardInterrupt

        self.sleep_fn = interrupted_sleep
        report = self._sweep()
        self.assertEqual(self.fake.albums(), ["A1"])
        self.assertIs(report.stop, sr.StopReason.INTERRUPTED)
        self.assertEqual(report.remaining, 4)

    def test_ctrl_c_while_the_run_is_read_records_it_interrupted(self) -> None:
        with mock.patch.object(
            sweep_made_run, "tally_manifest", side_effect=KeyboardInterrupt
        ):
            report = self._sweep()
        self.assertEqual(self.fake.albums(), ["A1"])
        self.assertIs(report.stop, sr.StopReason.INTERRUPTED)
        (entry,) = self._ledger()
        self.assertEqual((entry.outcome, entry.exit_code), (sl.Outcome.INTERRUPTED, 0))
        self.assertIsNone(entry.counts)
        self.assertEqual(report.remaining, 4)

    def test_ctrl_c_after_the_manifest_is_read_keeps_its_run_id(self) -> None:
        with mock.patch.object(
            sweep_made_run, "tally_manifest", side_effect=KeyboardInterrupt
        ):
            report = self._sweep()
        (run_id,) = sweep_child.run_ids(self.albums[0])
        (entry,) = self._ledger()
        self.assertEqual(entry.run_id, run_id)
        self.assertIsNone(entry.counts)
        text = sweep_report.format_sweep(
            report, library=self.library, options=sf.OPTIONS, undo_prog=("al",)
        )
        undo = sweep_report.undo_command(("al",), self.albums[0], run_id)
        self.assertIn(f"Artist/A1  undo: {undo}", text)

    def test_ctrl_c_while_the_manifest_is_read_keeps_the_new_run_id(self) -> None:
        with mock.patch.object(sweep_child, "read_run", side_effect=KeyboardInterrupt):
            self._sweep()
        (run_id,) = sweep_child.run_ids(self.albums[0])
        (entry,) = self._ledger()
        self.assertEqual(
            (entry.outcome, entry.run_id), (sl.Outcome.INTERRUPTED, run_id)
        )
        self.assertIsNone(entry.counts)


class FinalTableTest(sf.IsolatedCase):
    """The final table's columns: removed, then no lyrics expected, beside not found."""

    def test_no_lyrics_expected_follows_not_found(self) -> None:
        counts = Counts(
            written=1, not_found=2, no_lyrics_expected=3, silent=5, kept=4, removed=6
        )
        entry = sl.LedgerEntry("A/B", None, 0, sl.Outcome.DONE, "t", counts=counts)
        report = sr.SweepReport(
            (sr.Visit(self.library, entry),), 0, 0, sr.StopReason.FINISHED
        )
        text = sweep_report.format_sweep(
            report, library=self.library, options=sf.OPTIONS, undo_prog=("al",)
        )
        header, row = text.splitlines()[1:3]
        self.assertIn(
            "  not found  removed  no lyrics expected  silent  kept  ", header
        )
        # written, unchanged, not found, removed, no lyrics expected, silent, kept,
        # errors, flagged, refusing: the count lands under its own column.
        self.assertEqual(
            row.split()[-10:], ["1", "0", "2", "6", "3", "5", "4", "0", "0", "0"]
        )


class SwappedForALinkTest(sf.SweepCase):
    """An album that became a link after the survey is dropped at its visit, not run."""

    def _swap_during_the_first_visit(self, index: int) -> None:
        """Swap album *index* for a link outside the library while A1 is visited."""
        outside = sf.needy_album(self.tmp / "outside", "Far Artist", "Far Album")

        def swapping(
            argv: Sequence[str],
            timeout_s: float,
            *,
            env: Mapping[str, str] | None = None,
        ) -> sweep_child.ChildResult:
            if not self.fake.calls:
                shutil.rmtree(self.albums[index])
                self.albums[index].symlink_to(outside)
            return self.fake(argv, timeout_s, env=env)

        self.run_child = swapping

    def test_an_album_swapped_for_an_outside_link_is_never_run(self) -> None:
        self._swap_during_the_first_visit(2)
        report = self._sweep()
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A4", "A5"])
        self.assertEqual(report.dropped, ("Artist/A3",))
        self.assertEqual(report.remaining, 0)
        self.assertIs(report.stop, sr.StopReason.FINISHED)
        self.assertNotIn("Artist/A3", [e.album for e in self._ledger()])
        self.assertTrue(any("Artist/A3" in note for note in self.notes))

    def test_a_dropped_album_takes_no_limit_slot(self) -> None:
        self._swap_during_the_first_visit(1)
        report = self._sweep(limit=2)
        self.assertEqual(self.fake.albums(), ["A1", "A3"])
        self.assertEqual(report.dropped, ("Artist/A2",))
        self.assertIs(report.stop, sr.StopReason.LIMIT)
        self.assertEqual(report.remaining, 2)

    def test_a_dropped_album_takes_no_pause(self) -> None:
        self._swap_during_the_first_visit(1)
        self._sweep()
        self.assertEqual(self.fake.albums(), ["A1", "A3", "A4", "A5"])
        self.assertEqual(self.sleep.pauses, [7.0] * 3)


class AbandonedTest(sf.SweepCase):
    """A child never reaped is still recorded, named by pid, and never done."""

    def test_a_child_left_running_is_recorded_as_abandoned_by_pid(self) -> None:
        self.fake.scripts["A1"] = sf.Script(exit_code=None, timed_out=True, pid=4242)
        report = self._sweep(limit=2)
        first = self._ledger()[0]
        self.assertIs(first.outcome, sl.Outcome.ABANDONED)
        self.assertIsNone(first.exit_code)
        self.assertTrue(any("4242" in note for note in self.notes))
        # A timed-out child, even one left running, does not stop the sweep.
        self.assertEqual(self.fake.albums(), ["A1", "A2"])
        self.assertEqual(sr.sweep_exit_code(report), sr.EXIT_ALBUM_ERRORS)

    def test_an_interrupted_child_left_running_stops_the_sweep(self) -> None:
        self.fake.scripts["A1"] = sf.Script(exit_code=None, interrupted=True, pid=7)
        report = self._sweep()
        self.assertIs(self._ledger()[0].outcome, sl.Outcome.ABANDONED)
        self.assertIs(report.stop, sr.StopReason.INTERRUPTED)
        self.assertEqual(sr.sweep_exit_code(report), sr.EXIT_INTERRUPTED)

    def test_ctrl_c_in_a_timed_out_albums_grace_stops_the_sweep(self) -> None:
        self.fake.scripts["A1"] = sf.Script(
            exit_code=-9, timed_out=True, interrupted=True
        )
        report = self._sweep()
        self.assertIs(self._ledger()[0].outcome, sl.Outcome.TIMED_OUT)
        self.assertIs(report.stop, sr.StopReason.INTERRUPTED)


class OutcomeTest(sf.SweepCase):
    """How each way a child can end is recorded."""

    def _only(self, script: sf.Script) -> sl.LedgerEntry:
        self.fake.scripts["A1"] = script
        self._sweep(limit=1)
        return self._ledger()[0]

    def test_a_run_that_left_no_manifest_is_no_record(self) -> None:
        entry = self._only(sf.Script(manifest=False))
        self.assertIs(entry.outcome, sl.Outcome.NO_RECORD)
        self.assertIsNone(entry.run_id)
        self.assertIsNone(entry.counts)

    def test_an_album_that_could_not_run_has_no_run_read(self) -> None:
        # The album tool exits 2 without making a run, so any new run beside it is
        # another process's, never this album's record.
        entry = self._only(sf.Script(exit_code=album.EXIT_CANNOT_RUN))
        self.assertIs(entry.outcome, sl.Outcome.COULD_NOT_RUN)
        self.assertIsNone(entry.run_id)
        self.assertIsNone(entry.counts)

    def test_two_new_runs_are_no_record_and_said_so(self) -> None:
        entry = self._only(sf.Script(other_runs=1))
        self.assertIs(entry.outcome, sl.Outcome.NO_RECORD)
        self.assertIsNone(entry.run_id)
        self.assertTrue(any("2 new runs" in note for note in self.notes))

    def test_a_new_run_behind_the_clock_is_still_found(self) -> None:
        self._sweep(limit=1)
        self.fake.clock -= timedelta(hours=1)
        report = self._sweep(restart=True, limit=1)
        entry = report.visited[0].entry
        self.assertIs(entry.outcome, sl.Outcome.DONE)
        assert entry.run_id is not None
        self.assertLess(entry.run_id, self._ledger()[0].run_id or "")

    def test_an_exit_code_the_album_tool_never_gives_is_a_failure(self) -> None:
        self.assertIs(self._only(sf.Script(exit_code=-9)).outcome, sl.Outcome.FAILED)

    def test_track_errors_with_a_manifest_are_done(self) -> None:
        entry = self._only(
            sf.Script(outcomes=(sf.fetch_error(),), exit_code=album.EXIT_TRACK_ERRORS)
        )
        self.assertIs(entry.outcome, sl.Outcome.DONE)


class LedgerFailureTest(sf.SweepCase):
    """A ledger that cannot be read or written ends the sweep, naming the file."""

    def _block_the_ledger_folder(self) -> None:
        """Put a file where the ledger's folder should be, so nothing can go in it."""
        self.ledger.parent.parent.mkdir(parents=True)
        self.ledger.parent.touch()

    def test_a_ledger_that_cannot_be_written_stops_after_the_album_in_hand(
        self,
    ) -> None:
        self._block_the_ledger_folder()
        report = self._sweep()
        self.assertEqual(self.fake.albums(), ["A1"])
        self.assertEqual([v.album.name for v in report.visited], ["A1"])
        self.assertIs(report.stop, sr.StopReason.LEDGER)
        self.assertEqual(report.remaining, 4)
        self.assertEqual(sr.sweep_exit_code(report), sr.EXIT_USAGE)
        self.assertTrue(any(str(self.ledger) in note for note in self.notes))

    def test_a_write_that_does_not_read_back_stops_the_sweep(self) -> None:
        failing = mock.patch.object(
            sr, "append_unless_last", side_effect=ReadBackError("did not read back")
        )
        with failing:
            report = self._sweep()
        self.assertIs(report.stop, sr.StopReason.LEDGER)
        self.assertEqual(len(report.visited), 1)

    def test_a_restart_mark_that_cannot_be_written_visits_nothing(self) -> None:
        self._block_the_ledger_folder()
        report = self._sweep(restart=True)
        self.assertEqual(self.fake.calls, [])
        self.assertIs(report.stop, sr.StopReason.LEDGER)
        self.assertEqual(report.remaining, 5)

    def test_a_ledger_that_cannot_be_read_visits_nothing(self) -> None:
        with mock.patch.object(
            sl, "read_ledger", side_effect=PermissionError("denied")
        ):
            report = self._sweep()
        self.assertEqual(self.fake.calls, [])
        self.assertIs(report.stop, sr.StopReason.LEDGER)
        self.assertTrue(any("denied" in note for note in self.notes))


class ExitCodeTest(sf.SweepCase):
    """The sweep's exit code, from its report."""

    def test_a_clean_sweep_exits_zero(self) -> None:
        self.assertEqual(sr.sweep_exit_code(self._sweep()), sr.EXIT_OK)

    def test_an_album_that_did_not_end_cleanly_exits_one(self) -> None:
        self.fake.scripts["A4"] = sf.Script(
            outcomes=(sf.fetch_error(),), exit_code=album.EXIT_TRACK_ERRORS
        )
        self.assertEqual(sr.sweep_exit_code(self._sweep()), sr.EXIT_ALBUM_ERRORS)

    def test_a_refusal_stop_exits_three(self) -> None:
        self.fake.scripts.update(A1=_REFUSED, A2=_REFUSED)
        self.assertEqual(sr.sweep_exit_code(self._sweep()), sr.EXIT_REFUSED)

    def test_an_interrupt_exits_130(self) -> None:
        self.fake.scripts["A1"] = sf.Script(
            exit_code=album.EXIT_INTERRUPTED, interrupted=True
        )
        self.assertEqual(sr.sweep_exit_code(self._sweep()), sr.EXIT_INTERRUPTED)

    def test_nothing_to_visit_exits_zero(self) -> None:
        self.albums = []
        report = self._sweep()
        self.assertEqual(report.visited, ())
        self.assertEqual(sr.sweep_exit_code(report), sr.EXIT_OK)
