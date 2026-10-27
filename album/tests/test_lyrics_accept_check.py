"""Accept's check of the current tracks: every run whose lyrics a track may hold.

Each manifest is built as a dataclass; the stages that read a track and its backup are
fakes recording their calls, and the check answers a canned verdict by track, so a test
names the one verdict its rule turns on. Every title and lyric is invented.
"""

from __future__ import annotations

import shutil
import sys
import unittest
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_check as lac  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_judge as laj  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup as lb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages as ls  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_run_folders import run_folder, run_order  # noqa: E402 - import follows the sys.path bootstrap above

S = lr.OutcomeStatus
OLDER = "20261014T153012Z"
NEWER = "20261015T090000Z"
#: The duration every faked track reads as, in seconds.
DURATION_S = 180.0
PASS = lr.CheckResult((), ())
FLAG = lr.CheckResult((lr.CheckReason(lr.CheckRule.NO_LYRICS, "none embedded"),), ())
SKIP = lr.CheckResult((), (lr.CheckReason(lr.CheckRule.UNLIKE_BACKUP, "no backup"),))


@dataclass
class FakeTrackStages:
    """The track-reading stages and the check, answering by the track last read.

    *verdicts* maps an MP3's file name to its check result (PASS when absent);
    *broken* names files whose read raises. Every read and backup load is recorded.
    """

    verdicts: dict[str, lr.CheckResult] = field(default_factory=dict)
    broken: frozenset[str] = frozenset()
    reads: list[str] = field(default_factory=list)
    backups: list[Path] = field(default_factory=list)

    def read_tags(self, path: Path) -> lm.TrackTags:
        """No frames and a fixed duration; raise for a *broken* file."""
        if path.name in self.broken:
            raise OSError(f"cannot read {path.name}")
        self.reads.append(path.name)
        return lm.TrackTags("An Artist", "A Title", DURATION_S, (2, 4), ())

    def load_before(
        self, run_dir: Path, before: lr.TrackBefore
    ) -> tuple[lm.FrameRecord, ...]:
        """Record which run folder the backup is read from; it holds no frames."""
        self.backups.append(run_dir)
        return ()

    def load_before_sidecar(self, run_dir: Path, before: lr.TrackBefore) -> None:
        """No backed-up sidecar either."""
        return None

    def check(
        self,
        embedded: lm.Lyrics | None,
        backup: lm.Lyrics | None,
        duration: float | None,
    ) -> lr.CheckResult:
        """The canned verdict of the track last read."""
        return self.verdicts.get(self.reads[-1], PASS)

    def stages(self) -> ls.Stages:
        """The run stages with these fakes in place of the reads and the check."""
        return run_fakes.make_stages(
            run_fakes.FakeRunFolder(),
            read_tags=self.read_tags,
            load_before=self.load_before,
            load_before_sidecar=self.load_before_sidecar,
            check=self.check,
        )


def track(
    path: str,
    status: lr.OutcomeStatus | None = S.WRITTEN,
    *,
    undone: bool = False,
    after: lr.TrackOutcome | None = None,
    before: lr.TrackBefore | None = None,
) -> lr.ManifestTrack:
    """A backed-up track of outcome *status* (None: no outcome recorded).

    *after*, when given, is the whole outcome instead; *before* the backup instead of
    an empty one.
    """
    if after is None and status is not None:
        after = lr.TrackOutcome(status)
    backup = lr.TrackBefore((), ()) if before is None else before
    return lr.ManifestTrack(path, (2, 4), backup, after, undone)


def entry(run_id: str, *tracks: lr.ManifestTrack) -> laj.RunEntry:
    """A read run folder *run_id* holding *tracks*."""
    order = run_order(run_id)
    assert order is not None
    manifest = lr.Manifest(
        run_id, "eng", lr.RunState.COMPLETE, lr.GeniusState.ABSENT, tracks
    )
    return laj.RunEntry(run_id, order, lp.Kind.RUN, manifest)


class AcceptCheckCase(fixtures.IsolatedCase):
    """A fake album under the test's temp folder, and the check over its runs."""

    def setUp(self) -> None:
        """Fresh fakes per test."""
        super().setUp()
        self.fakes = FakeTrackStages()

    def flags(self, *runs: laj.RunEntry) -> tuple[laj.AtRisk, ...]:
        """The check's at-risk tracks over *runs*."""
        contents = laj.Contents(runs=runs)
        return lac.check_flags(self.tmp, contents, self.fakes.stages())


class VerdictTest(AcceptCheckCase):
    """A FLAG is listed with check's own reason; SKIP and PASS are not."""

    def test_a_flag_is_listed_with_the_checks_reason(self) -> None:
        self.fakes.verdicts["01.mp3"] = FLAG
        got = self.flags(entry(OLDER, track("01.mp3")))
        want = laj.AtRisk("01.mp3", OLDER, laj.AtRiskReason.CHECK_FLAG, FLAG.summary())
        self.assertEqual(got, (want,))

    def test_a_skip_is_not_listed(self) -> None:
        self.fakes.verdicts["01.mp3"] = SKIP
        self.assertEqual(self.flags(entry(OLDER, track("01.mp3"))), ())
        self.assertEqual(self.fakes.reads, ["01.mp3"])

    def test_a_pass_is_not_listed(self) -> None:
        self.assertEqual(self.flags(entry(OLDER, track("01.mp3"))), ())
        self.assertEqual(self.fakes.reads, ["01.mp3"])

    def test_a_check_that_errors_is_listed(self) -> None:
        self.fakes.broken = frozenset({"01.mp3"})
        (got,) = self.flags(entry(OLDER, track("01.mp3")))
        self.assertEqual(
            (got.track, got.run_id, got.reason),
            ("01.mp3", OLDER, laj.AtRiskReason.CHECK_FAILED),
        )
        self.assertEqual(got.detail, "cannot read 01.mp3")

    def test_a_run_folder_deleted_before_the_check_is_a_failed_check(self) -> None:
        # The judgment read the run; its folder, and so the backup, is gone by the check.
        run_dir = run_folder(self.tmp, OLDER)
        run_dir.mkdir(parents=True)
        before = lr.TrackBefore((), (lr.SidecarCopy("01.txt", "01.txt"),))
        shutil.rmtree(run_dir)
        stages = dataclasses.replace(
            self.fakes.stages(), load_before_sidecar=lb.load_before_sidecar
        )
        contents = laj.Contents(runs=(entry(OLDER, track("01.mp3", before=before)),))
        (got,) = lac.check_flags(self.tmp, contents, stages)
        self.assertEqual(got.reason, laj.AtRiskReason.CHECK_FAILED)
        self.assertIn("01.txt", got.detail)

    def test_results_are_sorted_by_track(self) -> None:
        self.fakes.verdicts.update({"01.mp3": FLAG, "02.mp3": FLAG})
        got = self.flags(entry(OLDER, track("02.mp3"), track("01.mp3")))
        self.assertEqual([r.track for r in got], ["01.mp3", "02.mp3"])


class EligibilityTest(AcceptCheckCase):
    """Only an entry check would check is checked: accepted, backed up, not undone."""

    def test_a_removed_track_is_not_checked(self) -> None:
        self.assertEqual(self.flags(entry(OLDER, track("01.mp3", S.REMOVED))), ())
        self.assertEqual(self.fakes.reads, [])

    def test_an_unchanged_track_is_checked(self) -> None:
        self.fakes.verdicts["01.mp3"] = FLAG
        self.assertEqual(len(self.flags(entry(OLDER, track("01.mp3", S.UNCHANGED)))), 1)

    def test_an_unreadable_run_is_skipped(self) -> None:
        order = run_order(OLDER)
        assert order is not None
        broken = laj.RunEntry(OLDER, order, lp.Kind.UNREADABLE, error="bad json")
        self.assertEqual(self.flags(broken), ())

    def test_an_empty_run_folder_checks_nothing(self) -> None:
        self.assertEqual(self.flags(), ())


class NewestRunTest(AcceptCheckCase):
    """Each track's runs are checked newest first, down to the last that changed it."""

    def test_only_the_newest_runs_entry_is_checked(self) -> None:
        self.fakes.verdicts["01.mp3"] = FLAG
        got = self.flags(entry(OLDER, track("01.mp3")), entry(NEWER, track("01.mp3")))
        self.assertEqual([(r.track, r.run_id) for r in got], [("01.mp3", NEWER)])
        self.assertEqual(self.fakes.reads, ["01.mp3"])

    def test_runs_are_taken_by_run_order_not_listing_order(self) -> None:
        self.fakes.verdicts["01.mp3"] = FLAG
        got = self.flags(entry(NEWER, track("01.mp3")), entry(OLDER, track("01.mp3")))
        self.assertEqual([r.run_id for r in got], [NEWER])

    def test_an_undone_newest_entry_falls_back_to_the_older_run(self) -> None:
        self.fakes.verdicts["01.mp3"] = FLAG
        newer = entry(NEWER, track("01.mp3", undone=True))
        got = self.flags(entry(OLDER, track("01.mp3")), newer)
        self.assertEqual([r.run_id for r in got], [OLDER])

    def test_a_newest_entry_with_no_outcome_falls_back(self) -> None:
        self.fakes.verdicts["01.mp3"] = FLAG
        newer = entry(NEWER, track("01.mp3", None))
        got = self.flags(entry(OLDER, track("01.mp3")), newer)
        self.assertEqual([r.run_id for r in got], [OLDER])

    def test_an_ineligible_newest_entry_is_not_replaced_by_an_older_one(self) -> None:
        # The newer run removed the lyrics: the older run's lyrics are gone too.
        newer = entry(NEWER, track("01.mp3", S.REMOVED))
        self.fakes.verdicts["01.mp3"] = FLAG
        self.assertEqual(self.flags(entry(OLDER, track("01.mp3")), newer), ())
        self.assertEqual(self.fakes.reads, [])

    def test_a_newer_not_found_leaves_the_older_lyrics_checked(self) -> None:
        self.fakes.verdicts["01.mp3"] = FLAG
        newer = entry(NEWER, track("01.mp3", S.NOT_FOUND))
        got = self.flags(entry(OLDER, track("01.mp3")), newer)
        self.assertEqual([r.run_id for r in got], [OLDER])
        self.assertEqual(self.fakes.backups, [run_folder(self.tmp, OLDER)])

    def test_a_newer_kept_existing_leaves_the_older_lyrics_checked(self) -> None:
        self.fakes.verdicts["01.mp3"] = FLAG
        newer = entry(NEWER, track("01.mp3", S.KEPT_EXISTING))
        got = self.flags(entry(OLDER, track("01.mp3")), newer)
        self.assertEqual([r.run_id for r in got], [OLDER])

    def test_a_newer_unchanged_checks_both_runs(self) -> None:
        # The newer backup holds the older run's lyrics; only the older holds the
        # original, so both comparisons are made.
        self.fakes.verdicts["01.mp3"] = FLAG
        newer = entry(NEWER, track("01.mp3", S.UNCHANGED))
        got = self.flags(entry(OLDER, track("01.mp3")), newer)
        self.assertEqual(
            [(r.track, r.run_id) for r in got], [("01.mp3", OLDER), ("01.mp3", NEWER)]
        )
        self.assertEqual(
            sorted(self.fakes.backups),
            sorted([run_folder(self.tmp, OLDER), run_folder(self.tmp, NEWER)]),
        )

    def test_a_newer_error_that_wrote_nothing_leaves_the_older_checked(self) -> None:
        self.fakes.verdicts["01.mp3"] = FLAG
        newer = entry(NEWER, track("01.mp3", S.ERROR))
        got = self.flags(entry(OLDER, track("01.mp3")), newer)
        self.assertEqual([r.run_id for r in got], [OLDER])

    def test_a_newer_error_that_wrote_frames_stops_the_walk(self) -> None:
        # The newer run changed the lyrics before it failed, and its outcome is not one
        # check judges: nothing is checked, and the older lyrics are not current.
        self.fakes.verdicts["01.mp3"] = FLAG
        wrote = lr.TrackOutcome(S.ERROR, frames_written=True)
        newer = entry(NEWER, track("01.mp3", after=wrote))
        self.assertEqual(self.flags(entry(OLDER, track("01.mp3")), newer), ())
        self.assertEqual(self.fakes.reads, [])

    def test_each_track_takes_its_own_newest_run(self) -> None:
        self.fakes.verdicts.update({"01.mp3": FLAG, "02.mp3": FLAG})
        older = entry(OLDER, track("01.mp3"), track("02.mp3"))
        got = self.flags(older, entry(NEWER, track("02.mp3")))
        self.assertEqual(
            [(r.track, r.run_id) for r in got],
            [("01.mp3", OLDER), ("02.mp3", NEWER)],
        )

    def test_check_reads_the_backup_of_the_run_about_to_be_deleted(self) -> None:
        self.flags(entry(OLDER, track("01.mp3")), entry(NEWER, track("01.mp3")))
        self.assertEqual(self.fakes.backups, [run_folder(self.tmp, NEWER)])


if __name__ == "__main__":
    unittest.main()
