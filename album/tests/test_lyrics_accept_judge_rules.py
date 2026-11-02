"""The accept judgment's pure rules: what is at risk, over every run, and what each holds.

Each manifest here is built as a dataclass, never read from disk, so a test names the
one field its rule turns on. Every title and lyric is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_judge as laj  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_categories as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_run_folders import run_order  # noqa: E402 - import follows the sys.path bootstrap above

S = lr.OutcomeStatus
R = laj.AtRiskReason
RUN = "20261014T153012Z"
TXT = "01.txt"


def frame(file: str = "frames/01.uslt.1.txt") -> lr.FrameExport:
    """A backed-up USLT frame exported to *file*."""
    return lr.FrameExport(lm.FrameKind.USLT, "eng", "", 3, None, None, file)


def before(
    frames: int = 0, files: tuple[str, ...] = (TXT,), links: tuple[str, ...] = ()
) -> lr.TrackBefore:
    """A backup of *frames* frames, a copy of each of *files* and a link at *links*."""
    copies = tuple(lr.SidecarCopy(name, f"sidecars/{name}") for name in files)
    linked = tuple(lr.SidecarCopy(name, None, "elsewhere.txt") for name in links)
    return lr.TrackBefore(
        tuple(frame(f"frames/{i}.txt") for i in range(frames)), copies + linked
    )


def after(
    status: lr.OutcomeStatus,
    *,
    sidecar_written: str | None = TXT,
    sidecars_removed: tuple[str, ...] = (),
    sidecars_renamed: tuple[lm.SidecarRename, ...] = (),
    frames_written: bool = False,
    implausible: str | None = None,
) -> lr.TrackOutcome:
    """An outcome of *status*; by default one that rewrote the ``.txt`` and no more."""
    return lr.TrackOutcome(
        status,
        sidecar_written=sidecar_written,
        sidecars_removed=sidecars_removed,
        sidecars_renamed=sidecars_renamed,
        frames_written=frames_written,
        implausible=implausible,
    )


def track(
    path: str = "01.mp3",
    held: lr.TrackBefore | None = None,
    outcome: lr.TrackOutcome | None = None,
    *,
    undone: bool = False,
    displaced: tuple[str, ...] = (),
) -> lr.ManifestTrack:
    """One manifest track; *held* None is a backup of the ``.txt`` alone."""
    held = before() if held is None else held
    return lr.ManifestTrack(path, (2, 4), held, outcome, undone, displaced)


def manifest(
    run_id: str = RUN,
    *tracks: lr.ManifestTrack,
    state: lr.RunState = lr.RunState.COMPLETE,
) -> lr.Manifest:
    """A run *run_id* of *tracks*."""
    return lr.Manifest(run_id, "eng", state, lr.GeniusState.ABSENT, tracks)


def entry(run: lr.Manifest, undo: tuple[str, ...] = ()) -> laj.RunEntry:
    """*run* as a read run folder whose ``undo`` folder holds *undo*."""
    order = run_order(run.run_id)
    assert order is not None
    return laj.RunEntry(run.run_id, order, lp.Kind.RUN, run, undo_files=undo)


def judge(*runs: laj.RunEntry) -> laj.Judgment:
    """The judgment of a run folder holding *runs*."""
    return laj.judge_contents(laj.Contents(runs=runs))


def risks(*tracks: lr.ManifestTrack) -> list[tuple[str, laj.AtRiskReason, str]]:
    """Each at-risk track of one run of *tracks*: its path, reason and detail."""
    found = judge(entry(manifest(RUN, *tracks))).at_risk
    return [(r.track, r.reason, r.detail) for r in found]


class ReasonsTest(fixtures.IsolatedCase):
    """One reason per landed outcome, as the at-risk table in `album.md` maps them."""

    def test_removed_carries_each_landed_reason(self) -> None:
        texts = {
            lc.RemovedReason.PLACEHOLDER: "placeholder lyrics",
            lc.RemovedReason.PAST_END: "last line 4:10, track 3:55",
            lc.RemovedReason.TOO_MANY_WORDS: "lyrics: 9.1 words per second: 4 max",
            lc.RemovedReason.WRONG_TEXT: "wrong text removed",
            lc.RemovedReason.OTHER: "an invented odd reason",
        }
        for reason, text in texts.items():
            with self.subTest(reason=reason):
                outcome = after(S.REMOVED, sidecars_removed=(TXT,), implausible=text)
                self.assertEqual(
                    risks(track(outcome=outcome)), [("01.mp3", R.REMOVED, reason.value)]
                )

    def test_stripped(self) -> None:
        outcome = after(S.STRIPPED, sidecar_written=None, frames_written=True)
        self.assertEqual(
            risks(track(held=before(frames=1), outcome=outcome)),
            [("01.mp3", R.STRIPPED, "stripped")],
        )

    def test_written_replaces(self) -> None:
        self.assertEqual(
            risks(track(outcome=after(S.WRITTEN))),
            [("01.mp3", R.REPLACED, "written")],
        )

    def test_an_error_over_a_backup_is_at_risk_whatever_it_recorded(self) -> None:
        # A write can change the file and raise before the outcome records it, so an
        # error with every recorded field empty still leaves the backup the only copy.
        helds = {"sidecar": before(), "frames": before(frames=1, files=())}
        for name, held in helds.items():
            with self.subTest(backup=name):
                outcome = after(S.ERROR, sidecar_written=None)
                self.assertEqual(
                    risks(track(held=held, outcome=outcome)),
                    [("01.mp3", R.REPLACED, "error")],
                )

    def test_an_error_over_no_backup_file_is_not_at_risk(self) -> None:
        for held in (before(files=()), before(files=(), links=(TXT,))):
            with self.subTest(held=held):
                outcome = after(S.ERROR, sidecar_written=None)
                self.assertEqual(risks(track(held=held, outcome=outcome)), [])

    def test_a_rename_onto_a_backed_up_name_touches_it(self) -> None:
        rename = (lm.SidecarRename("01.lrc", TXT),)
        outcome = after(S.KEPT_EXISTING, sidecar_written=None, sidecars_renamed=rename)
        self.assertEqual(
            risks(track(outcome=outcome)),
            [("01.mp3", R.REPLACED, "kept existing")],
        )

    def test_written_over_backed_up_frames(self) -> None:
        outcome = after(S.WRITTEN, sidecar_written=None, frames_written=True)
        self.assertEqual(
            [
                r[1]
                for r in risks(track(held=before(frames=2, files=()), outcome=outcome))
            ],
            [R.REPLACED],
        )

    def test_frames_written_over_no_frames_is_not_at_risk(self) -> None:
        outcome = after(S.WRITTEN, sidecar_written="01.lrc", frames_written=True)
        self.assertEqual(risks(track(outcome=outcome)), [])

    def test_a_kept_outcome_replaces_only_a_touched_sidecar(self) -> None:
        kept = (S.KEPT_EXISTING, S.NOT_FOUND, S.SILENT, S.NO_LYRICS_EXPECTED)
        for status in kept:
            with self.subTest(status=status, touched="removed"):
                outcome = after(status, sidecar_written=None, sidecars_removed=(TXT,))
                self.assertEqual(
                    risks(track(outcome=outcome)),
                    [("01.mp3", R.REPLACED, status.value)],
                )
            with self.subTest(status=status, touched="renamed"):
                rename = (lm.SidecarRename(TXT, "01.lrc"),)
                outcome = after(status, sidecar_written=None, sidecars_renamed=rename)
                self.assertEqual(
                    [r[1] for r in risks(track(outcome=outcome))], [R.REPLACED]
                )
            with self.subTest(status=status, touched="nothing"):
                outcome = after(
                    status, sidecar_written=None, sidecars_removed=("x.lrc",)
                )
                self.assertEqual(risks(track(outcome=outcome)), [])

    def test_unchanged_is_not_at_risk(self) -> None:
        self.assertEqual(risks(track(outcome=after(S.UNCHANGED))), [])

    def test_a_link_only_sidecar_is_not_at_risk(self) -> None:
        held = before(files=(), links=(TXT,))
        outcome = after(S.WRITTEN, sidecars_removed=(TXT,))
        self.assertEqual(risks(track(held=held, outcome=outcome)), [])

    def test_an_undone_track_or_one_never_backed_up_is_not_at_risk(self) -> None:
        undone = track(outcome=after(S.WRITTEN), undone=True)
        failed = lr.ManifestTrack(
            "02.mp3", None, None, after(S.ERROR, sidecar_written=None)
        )
        self.assertEqual(risks(undone, failed), [])

    def test_no_outcome_yet_is_unfinished(self) -> None:
        self.assertEqual(
            risks(track()), [("01.mp3", R.UNFINISHED, "no outcome recorded")]
        )


class SetAsideTest(fixtures.IsolatedCase):
    """Set-aside sidecars, undone or not, and every file in a run's ``undo`` folder."""

    def test_displaced_and_its_undo_file_are_one_entry(self) -> None:
        copy = f".lyricsmith/{RUN}/undo/01.txt"
        undone = track(outcome=after(S.WRITTEN), undone=True, displaced=(copy,))
        found = judge(entry(manifest(RUN, undone), undo=(copy,))).at_risk
        self.assertEqual(
            [(r.track, r.run_id, r.reason, r.detail) for r in found],
            [("01.mp3", RUN, R.SET_ASIDE, copy)],
        )

    def test_an_undo_file_no_track_names_is_listed_under_its_path(self) -> None:
        stray = f".lyricsmith/{RUN}/undo/stray.txt"
        found = judge(entry(manifest(RUN), undo=(stray,))).at_risk
        self.assertEqual(
            [(r.track, r.reason, r.detail) for r in found],
            [(stray, R.SET_ASIDE, "not listed by any track")],
        )

    def test_a_track_both_set_aside_and_replaced_lists_both(self) -> None:
        copy = f".lyricsmith/{RUN}/undo/01.txt"
        kept = track(outcome=after(S.WRITTEN), displaced=(copy,))
        self.assertEqual([r[1] for r in risks(kept)], [R.REPLACED, R.SET_ASIDE])


class EveryRunTest(fixtures.IsolatedCase):
    """Every run counts, and the list is sorted by track, then run, oldest first."""

    def test_each_run_that_changed_a_track_is_listed_oldest_first(self) -> None:
        runs = [f"{RUN}-10", RUN, f"{RUN}-2"]
        judged = judge(
            *(entry(manifest(r, track(outcome=after(S.WRITTEN)))) for r in runs)
        )
        self.assertEqual(
            [r.run_id for r in judged.at_risk], [RUN, f"{RUN}-2", f"{RUN}-10"]
        )
        self.assertEqual(
            [r.run_id for r in judged.runs], [RUN, f"{RUN}-2", f"{RUN}-10"]
        )

    def test_sorted_by_track_first(self) -> None:
        older = manifest(RUN, track("b.mp3", outcome=after(S.WRITTEN)))
        newer = manifest(f"{RUN}-2", track("a.mp3", outcome=after(S.WRITTEN)))
        found = judge(entry(newer), entry(older)).at_risk
        self.assertEqual(
            [(r.track, r.run_id) for r in found],
            [("a.mp3", f"{RUN}-2"), ("b.mp3", RUN)],
        )


class HeldTest(fixtures.IsolatedCase):
    """Each run's held counts: frame exports, sidecar copies and set-aside files."""

    def test_counts(self) -> None:
        shared = f".lyricsmith/{RUN}/undo/01.txt"
        own = f".lyricsmith/{RUN}/undo/01.lrc"
        first = track(
            held=before(frames=2, files=(TXT, "01.lrc"), links=("01.md",)),
            outcome=after(S.WRITTEN),
            displaced=(shared, own),
        )
        second = track(
            "02.mp3", held=before(frames=1, files=()), outcome=after(S.UNCHANGED)
        )
        failed = lr.ManifestTrack("03.mp3", None, None)
        stray = f".lyricsmith/{RUN}/undo/stray.txt"
        run = manifest(RUN, first, second, failed)
        held = judge(entry(run, undo=(shared, stray))).runs
        self.assertEqual(held, (laj.RunHeld(RUN, lr.RunState.COMPLETE, 3, 2, 3),))

    def test_a_folder_with_no_manifest_holds_nothing_counted(self) -> None:
        order = run_order(RUN)
        assert order is not None
        bare = laj.RunEntry(RUN, order, lp.Kind.NO_MANIFEST)
        judged = judge(bare)
        self.assertEqual(judged.runs, (laj.RunHeld(RUN, None, 0, 0, 0),))
        self.assertEqual((judged.refusals, judged.at_risk), ((), ()))


if __name__ == "__main__":
    unittest.main()
