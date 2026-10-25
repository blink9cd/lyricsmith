"""The records the run keeps: the check's verdict, the run manifest's types, the Genius key.

These are `lyrics_records`' types: the check result and its per-track statuses, the
manifest's entries and schema guard, and the `Secret` that keeps the key out of every
repr. Every value here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above


class CheckResultTest(fixtures.IsolatedCase):
    """CheckResult's verdict and its summary wording."""

    def test_no_reason_is_a_pass(self) -> None:
        result = lr.CheckResult((), ())
        self.assertIs(result.verdict, lr.CheckVerdict.PASS)
        self.assertEqual(result.summary(), "PASS")

    def test_any_flag_wins_and_lists_every_flag_with_its_values(self) -> None:
        short = lr.CheckReason(lr.CheckRule.ENDS_SHORT, "last 1:02 of 4:10")
        unlike = lr.CheckReason(lr.CheckRule.UNLIKE_BACKUP, "0.41")
        skip = lr.CheckReason(lr.CheckRule.DURATION_UNREADABLE, "no audio frames")
        result = lr.CheckResult((short, unlike), (skip,))
        self.assertIs(result.verdict, lr.CheckVerdict.FLAG)
        self.assertEqual(
            result.summary(),
            "FLAG: ends short (last 1:02 of 4:10); unlike backup (0.41)",
        )

    def test_skips_alone_are_a_skip(self) -> None:
        skip = lr.CheckReason(lr.CheckRule.DURATION_UNREADABLE, "no audio frames")
        result = lr.CheckResult((), (skip,))
        self.assertIs(result.verdict, lr.CheckVerdict.SKIP)
        self.assertEqual(
            result.summary(), "SKIP: duration unreadable (no audio frames)"
        )

    def test_rule_words_are_the_summary_words(self) -> None:
        self.assertEqual(
            [rule.value for rule in lr.CheckRule],
            [
                "past end",
                "ends short",
                "out of order",
                "too many words",
                "unlike backup",
                "duration unreadable",
                "no lyrics embedded",
                "placeholder lyrics",
                "untimed",
                "lyrics on a no-lyrics genre",
            ],
        )

    def test_a_written_track_now_without_lyrics_is_a_flag(self) -> None:
        gone = lr.CheckReason(lr.CheckRule.NO_LYRICS, "no SYLT or USLT frame")
        result = lr.CheckResult((gone,), ())
        self.assertIs(result.verdict, lr.CheckVerdict.FLAG)
        self.assertEqual(
            result.summary(), "FLAG: no lyrics embedded (no SYLT or USLT frame)"
        )


class ManifestTypesTest(fixtures.IsolatedCase):
    """The manifest dataclasses, the outcome block, and the run folder's address."""

    def test_a_manifest_defaults_to_schema_one_and_an_album_relative_root(self) -> None:
        frame = lr.FrameExport(
            lm.FrameKind.SYLT, "eng", "", 3, 2, 1, "frames/01 A.sylt.1.lrc", None
        )
        before = lr.TrackBefore(
            (frame,), (lr.SidecarCopy("01 A.txt", "sidecars/01 A.txt"),)
        )
        track = lr.ManifestTrack("01 A.mp3", (2, 4), before)
        manifest = lr.Manifest(
            "20261014T153012Z",
            "eng",
            lr.RunState.BACKED_UP,
            lr.GeniusState.ABSENT,
            (track,),
        )
        self.assertEqual(manifest.schema, 1)
        self.assertEqual(manifest.schema, lr.MANIFEST_SCHEMA)
        self.assertEqual(manifest.album, ".")
        self.assertIsNone(track.after)
        self.assertFalse(track.undone)

    def test_an_unknown_schema_is_refused(self) -> None:
        with self.assertRaises(lm.ManifestSchemaError):
            lr.Manifest(
                "r",
                "eng",
                lr.RunState.COMPLETE,
                lr.GeniusState.CONFIGURED,
                (),
                schema=2,
            )

    def test_state_and_genius_values_are_the_manifest_words(self) -> None:
        self.assertEqual(
            [state.value for state in lr.RunState], ["backed_up", "complete", "undone"]
        )
        self.assertEqual(
            [state.value for state in lr.GeniusState], ["configured", "absent"]
        )

    def test_an_outcome_records_what_the_run_did_to_one_track(self) -> None:
        error = lr.StageError(
            lr.ErrorStage.FETCH, "TimeoutError", "every provider failed"
        )
        outcome = lr.TrackOutcome(lr.OutcomeStatus.ERROR, error=error)
        self.assertIsNone(outcome.source)
        self.assertEqual(outcome.sidecars_removed, ())
        self.assertFalse(outcome.frames_written)
        self.assertEqual(error.summary(), "ERROR fetch: every provider failed")
        self.assertEqual(
            [stage.value for stage in lr.ErrorStage],
            ["backup", "sidecar", "fetch", "embed", "check", "strip", "undo"],
        )
        self.assertEqual(
            [status.value for status in lr.OutcomeStatus],
            [
                "written",
                "unchanged",
                "not found",
                "no lyrics expected",
                "silent",
                "kept existing",
                "removed",
                "stripped",
                "error",
            ],
        )

    def test_the_run_folder_sits_hidden_under_the_album(self) -> None:
        album = Path("album")
        self.assertEqual(
            lr.run_folder(album, "20261014T153012Z"),
            album / ".lyricsmith" / "20261014T153012Z",
        )
        self.assertEqual(lr.MANIFEST_NAME, "manifest.json")

    def test_an_undo_report_has_errors_when_a_track_errored(self) -> None:
        done = lr.UndoTrackResult("01 A.mp3", lr.UndoStatus.UNDONE)
        again = lr.UndoTrackResult("02 B.mp3", lr.UndoStatus.ALREADY_UNDONE)
        missing = lr.StageError(lr.ErrorStage.UNDO, "TrackRefusedError", "missing")
        lost = lr.UndoTrackResult("03 C.mp3", lr.UndoStatus.ERROR, missing)
        self.assertFalse(lr.UndoReport("r", True, (done, again)).has_errors)
        self.assertTrue(lr.UndoReport("r", False, (done, lost)).has_errors)
        self.assertFalse(hasattr(lr.UndoReport("r", True, ()), "ok"))
        self.assertEqual(lost.error and lost.error.summary(), "ERROR undo: missing")


class SecretTest(fixtures.IsolatedCase):
    """Secret: the value never shows in a repr, a str or a format."""

    def test_the_value_is_masked_everywhere_but_reveal(self) -> None:
        secret = lr.Secret("fake-token-for-tests")
        for shown in (repr(secret), str(secret), f"{secret}", repr([secret])):
            with self.subTest(shown=shown):
                self.assertNotIn("fake-token-for-tests", shown)
                self.assertIn("Secret(****)", shown)
        self.assertEqual(secret.reveal(), "fake-token-for-tests")

    def test_a_key_resolution_has_a_secret_exactly_when_one_resolved(self) -> None:
        searched = Path("config.toml")
        lr.KeyResolution(None, lr.KeyOrigin.ABSENT, searched)
        lr.KeyResolution(
            lr.Secret("fake-token-for-tests"), lr.KeyOrigin.ENVIRONMENT, None
        )
        with self.assertRaises(ValueError):
            lr.KeyResolution(None, lr.KeyOrigin.ENVIRONMENT, None)
        with self.assertRaises(ValueError):
            lr.KeyResolution(
                lr.Secret("fake-token-for-tests"), lr.KeyOrigin.ABSENT, None
            )
