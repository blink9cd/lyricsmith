"""`--refetch-untimed` with no answer: implausible old lyrics are removed, the rest kept.

A track a refetch asked again, that no provider answered, keeps its old untimed lyrics
when they are plausible. Its sidecars and its frames are judged apart, each by the
lyrics on that side that would serve: a side whose lyrics are implausible, a ``.txt``
too large to read or more words a second than the check's `too many words` row passes,
is removed whole through the strip, the other side stays when plausible, and undo
restores them byte for byte. An answer still replaces them as ever, and every provider
failing still leaves the track alone as its fetch error. Each test drives the entry
point with its real wiring; only the clock and the provider table are faked, and every
lyric is invented.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_refetch import (  # noqa: E402 - import follows the sys.path bootstrap above
    KEEPER,
    PLAIN_KEY,
    REFETCH,
    SYNCED_KEY,
    WRONG_TXT,
    WRONG_USLT,
    RefetchCase,
)

RUN_ID = run_fakes.RUN_ID
#: A saved page under the size cap: 2000 words in a 30-second track, whose length
#: the tag reader measures from its silent frames as just under 30 seconds.
PAGE = "la " * 2000
PAGE_RATE = "66.692 words per second: 2000 words in 29.989 s"
#: More characters than any sidecar may hold, in bytes a backup still copies.
OVER_CAP = "la " * (lr.MAX_LYRICS_CHARS // 3 + 1)
TOO_LARGE = f"01 Keeper.txt is too large: over {lr.MAX_LYRICS_CHARS} characters"
#: How a removed track's row opens.
REMOVED = "removed: implausible old lyrics"


class RemoveCase(RefetchCase):
    """A refetch expected to remove the keeper's old lyrics, and undo to restore them."""

    #: The album's snapshots and files before the refetch, for `assert_undone`.
    before: tuple[dict[Path, rb.TrackSnapshot], dict[str, bytes]]
    #: The keeper's lyrics frames before the refetch.
    frames: Counter[lm.FrameRecord]

    def assert_removed(
        self, paths: list[Path], reason: str, left: tuple[str, ...] = ()
    ) -> lr.TrackOutcome:
        """Refetch: the keeper removed for *reason*, with only *left* beside it."""
        row, keeper, mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.REMOVED)
        self.assertEqual(keeper.implausible, reason)
        self.assertTrue(row.endswith(f"{REMOVED} ({reason})"), row)
        self.assertNotIn("ERROR", row)
        self.assertEqual(sorted(self.keeper_files(paths)), ["01 Keeper.mp3", *left])
        self.assertEqual(mill.status, lr.OutcomeStatus.NOT_FOUND)
        return keeper

    def assert_undone(self, paths: list[Path]) -> None:
        """Undo the refetch: every frame and file of the album back as it was."""
        before, files = self.before
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertEqual(rb.snapshots(paths), before)
        self.assertEqual(raw(paths), files)

    def keep_before(self, paths: list[Path]) -> None:
        """Remember the album's frames and files, for `assert_undone`."""
        self.before = (rb.snapshots(paths), raw(paths))
        self.frames = rb.lyrics_frames(paths[0])


class RefetchRemoveTest(RemoveCase):
    """Implausible old lyrics are removed when nothing answers; undo restores them."""

    def test_an_over_cap_txt_is_removed_and_a_plausible_uslt_kept(self) -> None:
        paths = self.keeper(WRONG_USLT, txt=OVER_CAP)
        self.keep_before(paths)
        keeper = self.assert_removed(paths, TOO_LARGE)
        self.assertEqual(
            (keeper.frames_written, keeper.sidecars_removed),
            (False, ("01 Keeper.txt",)),
        )
        self.assertEqual(rb.lyrics_frames(paths[0]), self.frames)
        self.assert_undone(paths)

    def test_a_page_txt_goes_and_a_plausible_uslt_stays(self) -> None:
        # Regression: an implausible .txt once took a plausible USLT beside it too.
        paths = self.keeper(WRONG_USLT, txt=PAGE)
        self.keep_before(paths)
        keeper = self.assert_removed(paths, PAGE_RATE)
        self.assertEqual(
            (keeper.frames_written, keeper.sidecars_removed),
            (False, ("01 Keeper.txt",)),
        )
        self.assertEqual(rb.lyrics_frames(paths[0]), self.frames)
        self.assert_undone(paths)

    def test_a_page_uslt_goes_and_a_plausible_txt_stays(self) -> None:
        paths = self.keeper((tag_builders.uslt(PAGE),), txt=WRONG_TXT)
        self.keep_before(paths)
        keeper = self.assert_removed(paths, PAGE_RATE, ("01 Keeper.txt",))
        self.assertEqual((keeper.frames_written, keeper.sidecars_removed), (True, ()))
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        self.assert_undone(paths)

    def test_a_page_under_the_cap_is_removed_by_its_rate(self) -> None:
        paths = self.keeper(txt=PAGE)
        self.keep_before(paths)
        keeper = self.assert_removed(paths, PAGE_RATE)
        self.assertEqual(
            (keeper.frames_written, keeper.sidecars_removed),
            (False, ("01 Keeper.txt",)),
        )
        self.assert_undone(paths)

    def test_a_page_in_a_uslt_alone_is_removed(self) -> None:
        paths = self.keeper((tag_builders.uslt(PAGE),))
        self.keep_before(paths)
        keeper = self.assert_removed(paths, PAGE_RATE)
        self.assertEqual((keeper.frames_written, keeper.sidecars_removed), (True, ()))
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        self.assert_undone(paths)

    def test_a_page_in_an_untimed_lrc_is_removed(self) -> None:
        paths = self.keeper(lrc=PAGE)
        keeper = self.assert_removed(paths, PAGE_RATE)
        self.assertEqual(keeper.sidecars_removed, ("01 Keeper.lrc",))

    def test_the_summary_counts_it_removed(self) -> None:
        self.keeper(txt=PAGE)
        result = self.run_album(REFETCH)
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertIn("kept existing 0 · removed 1 · errors 0", result.stdout)
        self.assertIn(f"01 Keeper.mp3: {REMOVED} ({PAGE_RATE})", result.stderr)


class RefetchRemoveWholeTest(RemoveCase):
    """What serves is judged, and an implausible one takes every frame or sidecar."""

    def test_a_page_uslt_takes_every_frame_with_it(self) -> None:
        # The --lang USLT serves and is judged; a plausible USLT in another language
        # and a wordless SYLT go with it, and undo brings all three back.
        other = tag_builders.uslt(fixtures.ORCHARD_PLAIN, lang="fra")
        blank = tag_builders.sylt((1000, ""), (4000, " "))
        paths = self.keeper((tag_builders.uslt(PAGE), other, blank))
        self.keep_before(paths)
        self.assertEqual(sum(self.frames.values()), 3)
        keeper = self.assert_removed(paths, PAGE_RATE)
        self.assertEqual((keeper.frames_written, keeper.sidecars_removed), (True, ()))
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        self.assert_undone(paths)

    def test_a_page_txt_takes_a_stamps_only_lrc_with_it(self) -> None:
        paths = self.keeper(txt=PAGE, lrc="[00:01.00]\n[00:05.00]\n")
        self.keep_before(paths)
        keeper = self.assert_removed(paths, PAGE_RATE)
        self.assertEqual(keeper.sidecars_removed, ("01 Keeper.lrc", "01 Keeper.txt"))
        self.assert_undone(paths)

    def test_some_providers_failing_and_the_rest_silent_still_removes(self) -> None:
        self.providers = {
            SYNCED_KEY: fixtures.raising(OSError("refused")),
            PLAIN_KEY: run_fakes.TitleProvider({}),
        }
        paths = self.keeper((tag_builders.uslt(PAGE),))
        self.keep_before(paths)
        keeper = self.assert_removed(paths, PAGE_RATE)
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        self.assertTrue(keeper.refusals)
        self.assert_undone(paths)

    def test_a_removal_names_an_undecodable_txt_it_leaves(self) -> None:
        paths = self.keeper((tag_builders.uslt(PAGE),))
        paths[0].with_suffix(".txt").write_bytes(b"caf\xe9 by the quay\n")
        self.keep_before(paths)
        row, keeper, _mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.REMOVED)
        self.assertEqual(sorted(self.keeper_files(paths)), [KEEPER, "01 Keeper.txt"])
        self.assertIn(f"{REMOVED} ({PAGE_RATE}); 01 Keeper.txt is not UTF-8", row)
        self.assertEqual(keeper.sidecar_unreadable, row.split("; ", 1)[1])
        self.assert_undone(paths)


class RefetchRemoveGuardsTest(RefetchCase):
    """Removal never preempts an answer, follows a failure, or starts a refused write."""

    def test_an_answer_replaces_a_page_as_ever(self) -> None:
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper(txt=PAGE)
        _row, keeper, _mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        text = paths[0].with_suffix(".txt").read_text(encoding="utf-8")
        self.assertEqual(text, fixtures.LIGHTHOUSE_PLAIN)

    def test_every_provider_failing_removes_nothing(self) -> None:
        failing = fixtures.raising(OSError("refused"))
        self.providers = {SYNCED_KEY: failing, PLAIN_KEY: failing}
        paths = self.keeper((tag_builders.uslt(PAGE),), txt=OVER_CAP)
        before = self.keeper_files(paths)
        row, keeper, _mill = self.refetch()
        self.assertEqual(self.keeper_files(paths), before)
        self.assertEqual(keeper.status, lr.OutcomeStatus.ERROR)
        self.assertIn("ERROR fetch", row)

    def test_plausible_lyrics_are_still_kept(self) -> None:
        paths = self.keeper(WRONG_USLT, txt=WRONG_TXT)
        before = self.keeper_files(paths)
        row, keeper, _mill = self.refetch()
        self.assertEqual(self.keeper_files(paths), before)
        self.assertEqual(keeper.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertIsNone(keeper.implausible)
        self.assertNotIn("removed", row)

    def test_an_mp3_it_cannot_save_is_refused_before_anything_is_removed(
        self,
    ) -> None:
        paths = self.keeper((tag_builders.uslt(PAGE),), txt=PAGE)
        mode = paths[0].stat().st_mode
        os.chmod(paths[0], mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        self.addCleanup(os.chmod, paths[0], mode)
        before = self.keeper_files(paths)
        row, keeper, _mill = self.refetch()
        self.assertEqual(self.keeper_files(paths), before)
        assert keeper.error is not None
        self.assertEqual(keeper.error.stage, lr.ErrorStage.STRIP)
        self.assertIn("ERROR strip", row)


class RefetchRemoveRecordTest(RefetchCase):
    """The removal's record, its check and its dry run."""

    def test_the_manifest_records_the_removal(self) -> None:
        self.keeper(txt=PAGE)
        self.refetch()
        run_dir = lr.run_folder(self.album, RUN_ID)
        document = json.loads((run_dir / lr.MANIFEST_NAME).read_text(encoding="utf-8"))
        keeper, mill = (track["after"] for track in document["tracks"])
        self.assertEqual(
            (keeper["status"], keeper["implausible"], keeper["sidecars_removed"]),
            ("removed", PAGE_RATE, ["01 Keeper.txt"]),
        )
        self.assertIs(keeper["refetch_untimed"], True)
        self.assertIsNone(mill["implausible"])

    def test_a_manifest_without_the_field_reads_as_none(self) -> None:
        self.keeper(txt=PAGE)
        self.refetch()
        run_dir = lr.run_folder(self.album, RUN_ID)
        document = json.loads((run_dir / lr.MANIFEST_NAME).read_text(encoding="utf-8"))
        for track in document["tracks"]:
            del track["after"]["implausible"]
        manifest = lyrics_manifest.parse_manifest(json.dumps(document).encode())
        for track in manifest.tracks:
            assert track.after is not None
            self.assertIsNone(track.after.implausible)

    def test_the_check_does_not_judge_a_removed_track(self) -> None:
        self.keeper(txt=PAGE)
        self.refetch()
        result = self.run_album("--mode", "check")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        self.assertTrue(row.endswith("  -"), row)
        self.assertIn("flagged 0", result.stdout)

    def test_a_dry_run_plans_the_removal(self) -> None:
        cases = {
            "page": (PAGE, f"{lyrics_modes.PLAN_REMOVE} (implausible: {PAGE_RATE})"),
            "over cap": (
                OVER_CAP,
                f"{lyrics_modes.PLAN_REMOVE} (implausible: {TOO_LARGE})",
            ),
            "plausible": (WRONG_TXT, lyrics_modes.PLAN_REFETCH),
        }
        for name, (txt, plan) in cases.items():
            with self.subTest(name):
                for old in self.album.glob("*"):
                    old.unlink()
                paths = self.keeper(txt=txt)
                before = rb.snapshots(paths)
                result = self.run_album(REFETCH, "--dry-run")
                self.assertEqual(result.code, album.EXIT_OK, result.stdout)
                self.assertEqual(rb.snapshots(paths), before)
                lines = result.stdout.splitlines()
                (row,) = [line for line in lines if "01 Keeper" in line]
                self.assertTrue(row.endswith(f"  {plan}"), row)


class RemovedStatusTest(fixtures.IsolatedCase):
    """A removed track's status reads whole even from a record that lacks the why."""

    def test_a_removal_without_its_reason_says_removed(self) -> None:
        bare = lr.TrackOutcome(lr.OutcomeStatus.REMOVED)
        self.assertEqual(lyrics_outcomes.describe_status(bare), REMOVED)
