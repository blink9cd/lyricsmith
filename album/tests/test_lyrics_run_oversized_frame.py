"""A lyrics frame over the cap is junk to a refetch: asked again, and removed unanswered.

Under ``--refetch-untimed`` or ``--refetch-implausible`` a track holding a lyrics frame
over `MAX_LYRICS_CHARS` is asked again as though it held nothing, as an oversized
``.txt`` is. When no provider answers, every lyrics frame goes through the strip, its
sidecars judged apart; undo restores both byte for byte. An answer replaces the frame,
and the check's unlike-backup row is a SKIP naming why the backup holds nothing to
compare. Every provider failing leaves the track alone as its fetch error, and a run
without either option still ends the track as its error. Each test drives the entry
point with its real wiring; only the clock and the provider table are faked, and every
lyric is invented.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_refetch import (  # noqa: E402 - import follows the sys.path bootstrap above
    KEEPER,
    MILL,
    PLAIN_KEY,
    REFETCH,
    SYNCED_KEY,
    WRONG_TXT,
)
from test_lyrics_run_refetch_remove import (  # noqa: E402 - import follows the sys.path bootstrap above
    OVER_CAP,
    TOO_LARGE,
    RemoveCase,
)

IMPLAUSIBLE = "--refetch-implausible"
#: A USLT one word past the cap, and why a refetch judges it junk.
BIG_USLT = tag_builders.uslt("w " * (lr.MAX_LYRICS_CHARS // 2 + 1))
BIG_FRAME = f"a USLT frame is too large: over {lr.MAX_LYRICS_CHARS} characters"
#: A SYLT whose entries together pass the cap, and why.
BIG_SYLT = tag_builders.sylt(*((ms, "w") for ms in range(lr.MAX_LYRICS_CHARS // 2 + 1)))
BIG_SYLT_FRAME = f"a SYLT frame is too large: over {lr.MAX_LYRICS_CHARS} characters"


class OversizedRemoveTest(RemoveCase):
    """An oversized frame no provider answers is removed; undo restores it."""

    def assert_frame_removed(self, paths: list[Path], reason: str, *extra: str) -> None:
        """Refetch with *extra*: every frame gone for *reason*, and undo restoring it."""
        keeper = self.assert_removed_under(paths, reason, *extra)
        self.assertEqual((keeper.frames_written, keeper.sidecars_removed), (True, ()))
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        self.assert_undone(paths)

    def assert_removed_under(
        self,
        paths: list[Path],
        reason: str,
        *extra: str,
        left: tuple[str, ...] = (),
    ) -> lr.TrackOutcome:
        """`RemoveCase.assert_removed`, the run taking *extra* in place of the option."""
        self.refetch_options = extra
        return super().assert_removed(paths, reason, left)

    def refetch(self, *extra: str) -> tuple[str, lr.TrackOutcome, lr.TrackOutcome]:
        """Run with this test's options alone, unless none were set."""
        options = getattr(self, "refetch_options", ())
        if not options:
            return super().refetch(*extra)
        result = self.run_album(*options, *extra)
        self.assertIn(result.code, (album.EXIT_OK, album.EXIT_TRACK_ERRORS))
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        keeper, mill = self.afters()
        assert keeper is not None and mill is not None
        return row, keeper, mill

    def test_refetch_untimed_removes_an_oversized_uslt(self) -> None:
        paths = self.keeper((BIG_USLT,))
        self.keep_before(paths)
        self.assert_frame_removed(paths, BIG_FRAME, REFETCH)

    def test_refetch_implausible_removes_an_oversized_uslt(self) -> None:
        paths = self.keeper((BIG_USLT,))
        self.keep_before(paths)
        self.assert_frame_removed(paths, BIG_FRAME, IMPLAUSIBLE)

    def test_refetch_implausible_removes_an_oversized_sylt(self) -> None:
        paths = self.keeper((BIG_SYLT,))
        self.keep_before(paths)
        self.assert_frame_removed(paths, BIG_SYLT_FRAME, IMPLAUSIBLE)

    def test_it_takes_a_frame_under_the_cap_with_it(self) -> None:
        paths = self.keeper((tag_builders.uslt("A lamp", lang="deu"), BIG_USLT))
        self.keep_before(paths)
        self.assert_frame_removed(paths, BIG_FRAME, IMPLAUSIBLE)

    def test_an_oversized_txt_beside_it_goes_too(self) -> None:
        # The rehearsal's shape: the same junk as a frame and as a sidecar.
        paths = self.keeper((BIG_USLT,), txt=OVER_CAP)
        self.keep_before(paths)
        keeper = self.assert_removed_under(
            paths, f"{TOO_LARGE}; {BIG_FRAME}", IMPLAUSIBLE
        )
        self.assertEqual(
            (keeper.frames_written, keeper.sidecars_removed),
            (True, ("01 Keeper.txt",)),
        )
        self.assert_undone(paths)

    def test_a_plausible_txt_beside_it_stays(self) -> None:
        paths = self.keeper((BIG_USLT,), txt=WRONG_TXT)
        self.keep_before(paths)
        keeper = self.assert_removed_under(
            paths, BIG_FRAME, IMPLAUSIBLE, left=("01 Keeper.txt",)
        )
        self.assertEqual(keeper.sidecars_removed, ())
        self.assert_undone(paths)

    def test_either_option_marks_it_an_implausible_pick(self) -> None:
        # Never untimed-only: a SYLT read as untimed would hide the synced it held.
        paths = self.keeper((BIG_SYLT,))
        keeper = self.assert_removed_under(paths, BIG_SYLT_FRAME, REFETCH)
        self.assertEqual(
            (keeper.refetch_untimed, keeper.refetch_implausible), (False, True)
        )


class OversizedGuardsTest(RemoveCase):
    """An answer replaces it; a failure, or no refetch option, leaves it alone."""

    def test_an_answer_replaces_it_and_the_check_skips_the_backup(self) -> None:
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper((BIG_USLT,))
        row, keeper, _mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN, row)
        assert keeper.check is not None
        self.assertEqual(
            keeper.check.skips,
            (lr.CheckReason(lr.CheckRule.UNLIKE_BACKUP, f"backed-up {BIG_FRAME}"),),
        )
        self.assertNotIn(BIG_USLT, rb.lyrics_frames(paths[0]))

    def test_every_provider_failing_removes_nothing(self) -> None:
        failing = fixtures.raising(OSError("refused"))
        self.providers = {SYNCED_KEY: failing, PLAIN_KEY: failing}
        paths = self.keeper((BIG_USLT,))
        before = self.keeper_files(paths)
        row, keeper, _mill = self.refetch()
        self.assertEqual(self.keeper_files(paths), before)
        self.assertEqual(keeper.status, lr.OutcomeStatus.ERROR)
        self.assertIn("ERROR fetch", row)

    def test_without_a_refetch_option_it_is_still_the_tracks_error(self) -> None:
        paths = self.keeper((BIG_USLT,))
        before = self.keeper_files(paths)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn(f"ERROR sidecar: {BIG_FRAME}", result.stdout)
        self.assertEqual(self.keeper_files(paths), before)

    def test_a_dry_run_plans_the_removal(self) -> None:
        paths = self.keeper((BIG_USLT,))
        before = rb.snapshots(paths)
        result = self.run_album(IMPLAUSIBLE, "--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertEqual(rb.snapshots(paths), before)
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        plan = f"{lyrics_modes.PLAN_REMOVE} (implausible: {BIG_FRAME})"
        self.assertTrue(row.endswith(plan), row)

    def test_a_dry_run_of_a_marked_track_plans_the_removal(self) -> None:
        # A no-lyrics genre asks whether a miss keeps lyrics; that read must not fail.
        track = TrackSpec(KEEPER, title="The Keeper", genres=("Spoken Word",))
        paths = fixtures.build_album(self.album, (track, MILL))
        tag_builders.plant_frames(paths[0], (BIG_USLT,))
        result = self.run_album(IMPLAUSIBLE, "--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertIn(
            f"{lyrics_modes.PLAN_REMOVE} (implausible: {BIG_FRAME})", result.stdout
        )
