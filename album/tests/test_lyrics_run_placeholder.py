"""A placeholder a track stores: asked afresh, replaced by an answer, removed without one.

A track whose only lyrics are placeholders (an LRC header tag such as ``[au:comedy]``,
NetEase's notice, music-note glyphs, or timed lines with no words), in a sidecar, a
frame or both, reads as holding no lyrics: every run asks the providers as for a
track with none, and an answer replaces the placeholder. When nothing answers, the
placeholder goes, as a refetch's implausible old lyrics go, recorded ``removed`` and
undone byte for byte. A placeholder beside real lyrics never serves: the real
ones do. Under ``--refetch-untimed`` and ``--fresh`` it is the same, and a fresh run
never declines an answer for being unlike a placeholder backup. ``--mode check`` flags
an accepted track that holds a placeholder. Each test drives the entry point with
its real wiring; only the clock and the provider table are faked, and every lyric is
invented.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_refetch import (  # noqa: E402 - import follows the sys.path bootstrap above
    KEEPER,
    PLAIN_KEY,
    REFETCH,
    SYNCED_KEY,
    WRONG_USLT,
)
from test_lyrics_run_refetch_remove import (  # noqa: E402 - import follows the sys.path bootstrap above
    REMOVED,
    RemoveCase,
)

#: LRCLIB's stand-in for a stand-up track's words, as a ``.txt`` holds it.
TAG_TXT = "[au:comedy]\n"
#: The same, held in a USLT frame.
TAG_USLT = (tag_builders.uslt("[au:instrumental]"),)
#: NetEase's instrumental notice ("pure music, please enjoy"), held in a USLT frame.
NOTICE = "\u7eaf\u97f3\u4e50\uff0c\u8bf7\u6b23\u8d4f"
NOTICE_USLT = (tag_builders.uslt(NOTICE),)
#: The same notice as a synced frame's one line.
NOTICE_SYLT = (tag_builders.sylt((1000, NOTICE)),)
#: Real untimed lyrics under another description, so the notice ranks before them.
REAL_ALT_USLT = (tag_builders.uslt(fixtures.ORCHARD_PLAIN, desc="alt"),)
#: A second placeholder frame beside the notice: every worded frame a placeholder.
TAG_ALT_USLT = (tag_builders.uslt("[au:instrumental]", desc="alt"),)
#: Untimed frames of the answer's own song, so like it.
LIKE_USLT = (tag_builders.uslt(fixtures.LIGHTHOUSE_EDITED),)
#: A timed sidecar whose only line is a music-note glyph.
GLYPH_LRC = "[00:01.00]♪\n"
#: Why a stored placeholder no provider replaced was removed.
PLACEHOLDER = "placeholder lyrics"


class PlaceholderCase(RemoveCase):
    """The keeper with a stored placeholder, run with the options a test passes."""

    def run_row(self, *extra: str) -> tuple[str, lr.TrackOutcome]:
        """Run with *extra*: the keeper's summary row and its outcome."""
        result = self.run_album(*extra)
        self.assertIn(
            result.code,
            (album.EXIT_OK, album.EXIT_TRACK_ERRORS),
            result.stderr,
        )
        self.stderr = result.stderr
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        keeper, _mill = self.afters()
        assert keeper is not None
        return row, keeper

    def assert_placeholder_removed(
        self, paths: list[Path], *extra: str, left: tuple[str, ...] = ()
    ) -> lr.TrackOutcome:
        """Run with *extra*: the keeper's placeholder removed, only *left* beside it."""
        row, keeper = self.run_row(*extra)
        self.assertEqual(keeper.status, lr.OutcomeStatus.REMOVED)
        self.assertEqual(keeper.implausible, PLACEHOLDER)
        self.assertTrue(row.endswith(f"{REMOVED} ({PLACEHOLDER})"), row)
        self.assertEqual(sorted(self.keeper_files(paths)), sorted([KEEPER, *left]))
        return keeper

    def written_txt(self, paths: list[Path]) -> str:
        """The keeper's ``.txt`` as the run left it."""
        return paths[0].with_suffix(".txt").read_text(encoding="utf-8")

    def flag_rules(self, keeper: lr.TrackOutcome) -> list[lr.CheckRule]:
        """The rule of each FLAG in the keeper's check."""
        assert keeper.check is not None
        return [flag.rule for flag in keeper.check.flags]


class StoredPlaceholderAnsweredTest(PlaceholderCase):
    """A stored placeholder is asked afresh and an answer replaces it."""

    def test_a_plain_answer_replaces_a_tag_txt(self) -> None:
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper(txt=TAG_TXT)
        _row, keeper = self.run_row()
        self.assertEqual(
            (keeper.status, keeper.source), (lr.OutcomeStatus.WRITTEN, lm.Source.LRCLIB)
        )
        self.assertEqual(self.written_txt(paths), fixtures.LIGHTHOUSE_PLAIN)
        self.assertFalse(keeper.refetch_untimed)

    def test_a_synced_answer_replaces_a_tag_txt_and_uslt(self) -> None:
        self.answer_both(synced={"The Keeper": fixtures.LIGHTHOUSE_LRC}, plain={})
        paths = self.keeper(TAG_USLT, txt=TAG_TXT)
        _row, keeper = self.run_row()
        self.assertEqual(keeper.kind, lm.LyricsKind.SYNCED)
        self.assertEqual(sorted(self.keeper_files(paths)), ["01 Keeper.lrc", KEEPER])
        self.assertNotIn(TAG_USLT[0], rb.lyrics_frames(paths[0]))

    def test_a_glyph_lrc_is_replaced(self) -> None:
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper(lrc=GLYPH_LRC)
        _row, keeper = self.run_row()
        self.assertEqual(keeper.source, lm.Source.LRCLIB)
        self.assertEqual(sorted(self.keeper_files(paths)), [KEEPER, "01 Keeper.txt"])

    def test_worded_frames_serve_over_a_tag_txt_and_rewrite_it(self) -> None:
        paths = self.keeper(WRONG_USLT, txt=TAG_TXT)
        _row, keeper = self.run_row()
        self.assertEqual(keeper.source, lm.Source.FRAMES)
        self.assertEqual(self.written_txt(paths), fixtures.ORCHARD_PLAIN)

    def test_a_fresh_run_takes_an_answer_unlike_a_placeholder_backup(self) -> None:
        # The notice holds words, so the unlike-backup row would decline the answer.
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper(NOTICE_USLT, txt=TAG_TXT)
        row, keeper = self.run_row("--fresh")
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN, row)
        self.assertEqual(self.written_txt(paths), fixtures.LIGHTHOUSE_PLAIN)

    def test_a_fresh_run_takes_an_untimed_answer_over_a_wordless_lrc(self) -> None:
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper(TAG_USLT, lrc=GLYPH_LRC)
        row, keeper = self.run_row("--fresh")
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN, row)
        self.assertEqual(self.written_txt(paths), fixtures.LIGHTHOUSE_PLAIN)


class StoredPlaceholderRemovedTest(PlaceholderCase):
    """A stored placeholder nothing replaces is removed and undone."""

    def test_a_tag_txt_is_removed_and_undone(self) -> None:
        paths = self.keeper(txt=TAG_TXT)
        self.keep_before(paths)
        keeper = self.assert_placeholder_removed(paths)
        self.assertEqual(keeper.sidecars_removed, ("01 Keeper.txt",))
        self.assertIn(f"01 Keeper.mp3: {REMOVED} ({PLACEHOLDER})", self.stderr)
        self.assert_undone(paths)

    def test_a_tag_uslt_and_txt_are_removed_and_undone(self) -> None:
        paths = self.keeper(TAG_USLT, txt=TAG_TXT)
        self.keep_before(paths)
        keeper = self.assert_placeholder_removed(paths)
        self.assertEqual(
            (keeper.frames_written, keeper.sidecars_removed),
            (True, ("01 Keeper.txt",)),
        )
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        self.assert_undone(paths)

    def test_a_glyph_lrc_is_removed(self) -> None:
        paths = self.keeper(lrc=GLYPH_LRC)
        self.keep_before(paths)
        keeper = self.assert_placeholder_removed(paths)
        self.assertEqual(keeper.sidecars_removed, ("01 Keeper.lrc",))
        self.assert_undone(paths)

    def test_the_summary_counts_it_removed(self) -> None:
        self.keeper(txt=TAG_TXT)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertIn("kept existing 0 · removed 1 · errors 0", result.stdout)

    def test_a_refetch_removes_it_as_a_track_with_no_lyrics(self) -> None:
        paths = self.keeper(txt=TAG_TXT)
        self.keep_before(paths)
        self.assert_placeholder_removed(paths, REFETCH)
        self.assert_undone(paths)

    def test_a_refetch_removes_a_tag_txt_and_keeps_worded_frames(self) -> None:
        paths = self.keeper(WRONG_USLT, txt=TAG_TXT)
        self.keep_before(paths)
        keeper = self.assert_placeholder_removed(paths, REFETCH)
        self.assertEqual(
            (keeper.frames_written, keeper.sidecars_removed),
            (False, ("01 Keeper.txt",)),
        )
        self.assertEqual(rb.lyrics_frames(paths[0]), self.frames)
        self.assert_undone(paths)

    def test_a_refetch_keeps_a_real_uslt_ranked_below_a_notice(self) -> None:
        # Regression: the notice, ranked first, once judged every frame a placeholder,
        # so a miss stripped the real USLT beside it too.
        paths = self.keeper((*NOTICE_USLT, *REAL_ALT_USLT))
        self.keep_before(paths)
        row, keeper = self.run_row(REFETCH)
        self.assertTrue(keeper.refetch_untimed, row)
        self.assertNotEqual(keeper.status, lr.OutcomeStatus.REMOVED, row)
        self.assertIsNone(keeper.implausible, row)
        self.assertEqual(rb.lyrics_frames(paths[0]), self.frames)

    def test_a_refetch_removes_placeholder_frames_beside_a_real_txt(self) -> None:
        paths = self.keeper((*NOTICE_USLT, *TAG_ALT_USLT), txt=fixtures.ORCHARD_PLAIN)
        self.keep_before(paths)
        keeper = self.assert_placeholder_removed(
            paths, REFETCH, left=("01 Keeper.txt",)
        )
        self.assertEqual((keeper.frames_written, keeper.sidecars_removed), (True, ()))
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        self.assertEqual(self.written_txt(paths), fixtures.ORCHARD_PLAIN)
        self.assert_undone(paths)

    def test_a_fresh_run_removes_it_too(self) -> None:
        paths = self.keeper(TAG_USLT)
        self.keep_before(paths)
        self.assert_placeholder_removed(paths, "--fresh")
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        self.assert_undone(paths)

    def test_every_provider_failing_removes_nothing(self) -> None:
        failing = fixtures.raising(OSError("refused"))
        self.providers = {SYNCED_KEY: failing, PLAIN_KEY: failing}
        paths = self.keeper(TAG_USLT, txt=TAG_TXT)
        before = self.keeper_files(paths)
        row, keeper = self.run_row()
        self.assertEqual(self.keeper_files(paths), before)
        self.assertEqual(keeper.status, lr.OutcomeStatus.ERROR)
        self.assertIn("ERROR fetch", row)

    def test_a_dry_run_plans_the_removal(self) -> None:
        paths = self.keeper(txt=TAG_TXT)
        before = rb.snapshots(paths)
        result = self.run_album("--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertEqual(rb.snapshots(paths), before)
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        plan = f"{lyrics_modes.PLAN_REMOVE} (implausible: {PLACEHOLDER})"
        self.assertTrue(row.endswith(f"  {plan}"), row)


class PlaceholderCheckTest(PlaceholderCase):
    """``--mode check`` flags an accepted track whose frames now hold a placeholder."""

    def test_a_placeholder_frame_is_flagged(self) -> None:
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper()
        self.run_row()
        # As a run before the rule would have left it: the placeholder embedded.
        tag_builders.plant_frames(paths[0], TAG_USLT)
        result = self.run_album("--mode", "check")
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        self.assertIn("FLAG: placeholder lyrics (", row)
        self.assertIn("run the album again", row)
        self.assertIn("flagged 1", result.stdout)
        self.assertIn(run_fakes.RUN_ID, result.stdout)


class PlaceholderBackupTest(PlaceholderCase):
    """A placeholder in the backup is no lyrics: never synced, never compared with."""

    def setUp(self) -> None:
        """Untimed lighthouse lyrics answer the keeper."""
        super().setUp()
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})

    def test_a_fresh_run_takes_an_untimed_answer_over_a_placeholder_sylt(self) -> None:
        paths = self.keeper(NOTICE_SYLT)
        row, keeper = self.run_row("--fresh")
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN, row)
        self.assertEqual(self.written_txt(paths), fixtures.LIGHTHOUSE_PLAIN)

    def test_a_placeholder_sylt_beside_a_real_uslt_is_not_synced(self) -> None:
        paths = self.keeper((*NOTICE_SYLT, *LIKE_USLT))
        row, keeper = self.run_row("--fresh")
        self.assertNotIn("existing lyrics synced", row)
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN, row)
        self.assertEqual(self.written_txt(paths), fixtures.LIGHTHOUSE_PLAIN)

    def test_an_unlike_answer_is_compared_with_the_real_uslt(self) -> None:
        # The placeholder SYLT ranks first, but the backup is the real USLT beside it.
        self.keeper((*NOTICE_SYLT, *WRONG_USLT))
        row, keeper = self.run_row("--fresh")
        self.assertEqual(keeper.status, lr.OutcomeStatus.KEPT_EXISTING, row)
        self.assertEqual(self.flag_rules(keeper), [lr.CheckRule.UNLIKE_BACKUP])

    def test_an_answer_replacing_a_notice_uslt_passes_its_check(self) -> None:
        self.keeper(NOTICE_USLT)
        row, keeper = self.run_row()
        assert keeper.check is not None
        self.assertEqual(keeper.check.verdict, lr.CheckVerdict.PASS, row)
        result = self.run_album("--mode", "check")
        self.assertNotIn("unlike backup", result.stdout)
        self.assertIn("flagged 0", result.stdout)

    def test_an_answer_replacing_a_notice_txt_passes_its_check(self) -> None:
        # No lyrics frames: the check would compare with the backed-up .txt.
        self.keeper(txt=NOTICE + "\n")
        row, keeper = self.run_row()
        assert keeper.check is not None
        self.assertEqual(keeper.check.verdict, lr.CheckVerdict.PASS, row)
        result = self.run_album("--mode", "check")
        self.assertNotIn("unlike backup", result.stdout)

    def test_a_refetch_asks_again_over_a_placeholder_sylt_and_real_uslt(self) -> None:
        # The notice holds a word, but a placeholder is no synced lyrics held, so the
        # real USLT beside it makes the track untimed-only.
        paths = self.keeper((*NOTICE_SYLT, *WRONG_USLT))
        row, keeper = self.run_row(REFETCH)
        self.assertTrue(keeper.refetch_untimed, row)
        self.assertEqual(
            (keeper.status, keeper.source), (lr.OutcomeStatus.WRITTEN, lm.Source.LRCLIB)
        )
        self.assertEqual(self.written_txt(paths), fixtures.LIGHTHOUSE_PLAIN)
