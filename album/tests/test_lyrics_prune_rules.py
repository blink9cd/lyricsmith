"""The prune rules, as pure functions: which run folder goes, which stays, and why.

Nothing here touches a file: each `Found` is what a scan of a run folder would read.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run_folders  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_records import RunState  # noqa: E402 - import follows the sys.path bootstrap above

OLD, MID, NEW = "20261001T000000Z", "20261002T000000Z", "20261003T000000Z"
#: The run folder every entry here is said to sit in: a name only, never created.
PARENT = Path(lyrics_run_folders.RUN_FOLDER_NAME)


def run(name: str, state: RunState, *, displaced: bool = False) -> lp.Found:
    """A readable run folder named *name* in *state*."""
    order = lyrics_backup.run_order(name)
    assert order is not None
    return lp.Found(name, order, lp.Kind.RUN, state, displaced, parent=PARENT)


def other(name: str, kind: lp.Kind, detail: str | None = None) -> lp.Found:
    """A run-id entry of *kind* that is not a readable run."""
    order = lyrics_backup.run_order(name.removesuffix(lp.PRUNING_SUFFIX))
    assert order is not None
    return lp.Found(name, order, kind, detail=detail, parent=PARENT)


class RunOrderTest(fixtures.IsolatedCase):
    """`run_order` parses a run-id name as `select_run` sorts it."""

    def test_a_plain_id_sorts_as_suffix_one(self) -> None:
        self.assertEqual(lyrics_backup.run_order(OLD), (OLD, 1))

    def test_a_suffix_sorts_as_a_number(self) -> None:
        two = lyrics_backup.run_order(f"{OLD}-2")
        ten = lyrics_backup.run_order(f"{OLD}-10")
        assert two is not None and ten is not None
        self.assertLess(two, ten)

    def test_any_other_name_is_none(self) -> None:
        for name in ("lock", f"{OLD}.pruning", f"{OLD}-1", "notes"):
            with self.subTest(name=name):
                self.assertIsNone(lyrics_backup.run_order(name))


class ClassifyTest(fixtures.IsolatedCase):
    """Each kind of entry, and each state of a run, gets its outcome."""

    def test_each_entry_gets_its_outcome(self) -> None:
        cases = (
            (run(OLD, RunState.UNDONE), lp.Outcome.REMOVE_UNDONE),
            (run(OLD, RunState.COMPLETE), lp.Outcome.KEEP_NOT_UNDONE),
            (run(OLD, RunState.BACKED_UP), lp.Outcome.KEEP_NOT_UNDONE),
            (run(OLD, RunState.UNDONE, displaced=True), lp.Outcome.KEEP_DISPLACED),
            (other(OLD, lp.Kind.NO_MANIFEST), lp.Outcome.REMOVE_UNFINISHED),
            (other(OLD, lp.Kind.NOT_FOLDER), lp.Outcome.KEEP_NOT_FOLDER),
            (other(f"{OLD}.pruning", lp.Kind.LEFTOVER), lp.Outcome.REMOVE_LEFTOVER),
            (other(OLD, lp.Kind.UNREADABLE, "malformed"), lp.Outcome.KEEP_UNREADABLE),
        )
        for found, outcome in cases:
            with self.subTest(found=found):
                self.assertIs(lp.classify(found).outcome, outcome)

    def test_an_unreadable_manifest_keeps_its_reason(self) -> None:
        verdict = lp.classify(other(OLD, lp.Kind.UNREADABLE, "malformed manifest: x"))
        self.assertEqual(verdict.detail, "malformed manifest: x")
        self.assertFalse(verdict.removes)

    def test_removes_and_leftover_follow_the_outcome(self) -> None:
        leftover = lp.classify(other(f"{OLD}.pruning", lp.Kind.LEFTOVER))
        self.assertTrue(leftover.removes and leftover.leftover)
        undone = lp.classify(run(OLD, RunState.UNDONE))
        self.assertTrue(undone.removes)
        self.assertFalse(undone.leftover)


class JudgeTest(fixtures.IsolatedCase):
    """The newest run goes only when every run left behind is undone."""

    def outcomes(self, *entries: lp.Found) -> list[lp.Outcome]:
        """The outcomes `judge` gives *entries*, oldest first."""
        return [v.outcome for v in lp.judge(entries)]

    def test_every_run_undone_removes_them_all(self) -> None:
        self.assertEqual(
            self.outcomes(run(NEW, RunState.UNDONE), run(OLD, RunState.UNDONE)),
            [lp.Outcome.REMOVE_UNDONE, lp.Outcome.REMOVE_UNDONE],
        )

    def test_an_older_kept_run_keeps_the_newest_but_not_the_middle(self) -> None:
        self.assertEqual(
            self.outcomes(
                run(OLD, RunState.COMPLETE),
                run(MID, RunState.UNDONE),
                run(NEW, RunState.UNDONE),
            ),
            [
                lp.Outcome.KEEP_NOT_UNDONE,
                lp.Outcome.REMOVE_UNDONE,
                lp.Outcome.KEEP_NEWEST,
            ],
        )

    def test_a_kept_displaced_run_is_undone_so_the_newest_still_goes(self) -> None:
        self.assertEqual(
            self.outcomes(
                run(OLD, RunState.UNDONE, displaced=True), run(NEW, RunState.UNDONE)
            ),
            [lp.Outcome.KEEP_DISPLACED, lp.Outcome.REMOVE_UNDONE],
        )

    def test_an_unreadable_older_run_keeps_the_newest(self) -> None:
        self.assertEqual(
            self.outcomes(
                other(OLD, lp.Kind.UNREADABLE, "malformed"), run(NEW, RunState.UNDONE)
            ),
            [lp.Outcome.KEEP_UNREADABLE, lp.Outcome.KEEP_NEWEST],
        )

    def test_an_unreadable_newer_run_never_stands_in_for_the_newest(self) -> None:
        # A plain undo skips a run it cannot read, so the newest readable run is the
        # one it lands on; the unreadable one is left behind and counts as not undone.
        self.assertEqual(
            self.outcomes(
                run(OLD, RunState.COMPLETE),
                run(MID, RunState.UNDONE),
                other(NEW, lp.Kind.UNREADABLE, "manifest is a dangling link"),
            ),
            [
                lp.Outcome.KEEP_NOT_UNDONE,
                lp.Outcome.KEEP_NEWEST,
                lp.Outcome.KEEP_UNREADABLE,
            ],
        )

    def test_an_unreadable_newer_run_alone_holds_the_newest_back(self) -> None:
        self.assertEqual(
            self.outcomes(
                run(OLD, RunState.UNDONE),
                other(NEW, lp.Kind.UNREADABLE, "malformed"),
            ),
            [lp.Outcome.KEEP_NEWEST, lp.Outcome.KEEP_UNREADABLE],
        )

    def test_the_newest_kept_label_never_says_older(self) -> None:
        # The run holding the newest back can be newer than it, as just above.
        self.assertEqual(
            str(lp.Outcome.KEEP_NEWEST), "kept: newest, another run is not undone"
        )

    def test_entries_that_are_not_runs_never_decide_the_newest(self) -> None:
        self.assertEqual(
            self.outcomes(
                run(OLD, RunState.UNDONE),
                other(NEW, lp.Kind.NO_MANIFEST),
                other(f"{MID}.pruning", lp.Kind.LEFTOVER),
                other(f"{NEW}-2", lp.Kind.NOT_FOLDER),
            ),
            [
                lp.Outcome.REMOVE_UNDONE,
                lp.Outcome.REMOVE_LEFTOVER,
                lp.Outcome.REMOVE_UNFINISHED,
                lp.Outcome.KEEP_NOT_FOLDER,
            ],
        )

    def test_no_entries_judge_to_nothing(self) -> None:
        self.assertEqual(lp.judge(()), ())


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
