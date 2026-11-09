"""`--mode fix-text` on provider-timed tracks: each winner's write, the removal, undo.

A provider-timed track whose ``.lrc`` text sync heard not sung is fixed as an untimed
one is, from the album's other texts (timed ones too) and then the provider chain, its
synced steps before its untimed ones. A sibling's timing is never copied: its words are
written untimed for sync to time against this track. A synced provider answer keeps its
stamps, blank stamped lines included. With none sung the text and its timing go, as
``wrong text removed``, and undo restores the track byte for byte. Each test drives the
entry point with its real wiring; every title and lyric is invented.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    FOUNDRY,
    HARBOR,
    MEADOW,
    QUARRY,
    TITLES,
    song_track,
    words,
)
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above
from timed_fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    MACHINE_HEADER,
    TimedFixCase,
    frame_kinds,
    lrc_of,
    planned_sidecar,
    timed_track,
)

SYLT, USLT = lm.FrameKind.SYLT, lm.FrameKind.USLT
WRONG_TEXT = "wrong text removed"


class _Case(TimedFixCase):
    """`TimedFixCase`, with the checks the winner paths share."""

    def assert_untimed(self, path: Path, song: str) -> None:
        """*path* holds *song* untimed alone: a ``USLT`` and a ``.txt``, no timing.

        That is the sidecar state sync's planner plans ``TIME`` from: a worded
        untimed text, no ``.lrc`` at all, and so no link at its name.
        """
        self.assertEqual(self.text_of(path), words(song))
        self.assertNotIn("[", path.with_suffix(".txt").read_text("utf-8"))
        self.assertFalse(path.with_suffix(".lrc").exists())
        self.assertFalse(path.with_suffix(".lrc").is_symlink())
        self.assertEqual(frame_kinds(path), [USLT])
        planned = planned_sidecar(path)
        self.assertIsInstance(planned.lyrics, lm.PlainLyrics)
        self.assertEqual(planned.renamed, ())

    def assert_record_kept_for_sync(self, path: Path, record: object) -> None:
        """*path*'s record is kept unchanged, and no longer matches its text.

        sync's planner (`sy_plan.listed_by_hand`) skips a ``not sung`` row only while
        the record's fingerprint is the text's; a changed one carries the kept
        transcript into a ``TIME`` plan, so the new text is judged without a second
        hearing.
        """
        kept = self.record_of(path)
        self.assertIsNotNone(record)
        self.assertEqual(kept, record)
        assert kept is not None
        text = path.with_suffix(".txt").read_text("utf-8")
        self.assertNotEqual(kept.fingerprint, lyrics_heard.text_fingerprint(text))


class SiblingWinnerTest(_Case):
    """A sibling's text wins: its words are written untimed, never its stamps."""

    def test_a_shifted_timed_album_takes_each_text_untimed_and_undoes(self) -> None:
        paths = self.build_timed(
            timed_track(1, HARBOR, MEADOW), timed_track(2, MEADOW, HARBOR)
        )
        self.hear(paths[0], HARBOR, held=MEADOW)
        self.hear(paths[1], MEADOW, held=HARBOR)
        records = [self.record_of(path) for path in paths]
        self.answer_both({TITLES[HARBOR]: lrc_of(HARBOR)}, {})
        before = (rb.snapshots(paths), raw(paths))
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        for path, song, record, origin in zip(
            paths, (HARBOR, MEADOW), records, (paths[1], paths[0]), strict=True
        ):
            self.assert_untimed(path, song)
            self.assert_record_kept_for_sync(path, record)
            outcome = self.outcome(path.name)
            self.assertIs(outcome.status, lr.OutcomeStatus.WRITTEN)
            self.assertIs(outcome.kind, lm.LyricsKind.UNSYNCED)
            self.assertEqual(outcome.origin, origin.name)
        # No provider is asked once a sibling's text has won.
        self.assertEqual(self.order, [])
        undone = self.run_album("--mode", "undo")
        self.assertEqual(undone.code, album.EXIT_OK, undone.stdout + undone.stderr)
        self.assertEqual((rb.snapshots(paths), raw(paths)), before)

    def test_an_untimed_sibling_wins_for_a_timed_track(self) -> None:
        paths = self.build_timed(
            timed_track(1, HARBOR, QUARRY), song_track(2, MEADOW, txt=HARBOR)
        )
        self.hear(paths[0], HARBOR, held=QUARRY)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assert_untimed(paths[0], HARBOR)
        self.assertEqual(self.outcome(paths[0].name).origin, paths[1].name)

    def test_a_timed_sibling_is_no_candidate_for_an_untimed_track(self) -> None:
        # The untimed fix is unchanged: an untimed track draws untimed texts alone.
        paths = self.build_timed(
            song_track(1, HARBOR, txt=QUARRY), timed_track(2, MEADOW, HARBOR)
        )
        self.hear(paths[0], HARBOR)
        self.answer({TITLES[HARBOR]: HARBOR})
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertEqual(self.text_of(paths[0]), words(HARBOR))
        self.assertIs(self.outcome(paths[0].name).source, lm.Source.LRCLIB)
        self.assertIsNone(self.outcome(paths[0].name).origin)

    def test_a_dry_run_names_the_timed_sibling_it_would_take(self) -> None:
        paths = self.build_timed(
            timed_track(1, HARBOR, MEADOW), timed_track(2, MEADOW, HARBOR)
        )
        self.hear(paths[0], HARBOR, held=MEADOW)
        before = raw(paths)
        result = self.fix("--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        row = cli_fakes.row_cells(result.stdout, paths[0].name)[-1]
        self.assertEqual(row, f"take {paths[1].name}'s text")
        self.assertEqual(raw(paths), before)


class ProviderWinnerTest(_Case):
    """No sibling is sung: the chain's synced steps, then its untimed ones."""

    def test_a_synced_answer_keeps_its_stamps_and_its_blank_lines(self) -> None:
        paths = self.build_timed(timed_track(1, HARBOR, QUARRY))
        self.hear(paths[0], HARBOR, held=QUARRY)
        record = self.record_of(paths[0])
        answer = lrc_of(HARBOR)
        self.answer_both({TITLES[HARBOR]: answer}, {TITLES[HARBOR]: HARBOR})
        before = (rb.snapshots(paths), raw(paths))
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        lrc = paths[0].with_suffix(".lrc").read_text("utf-8")
        self.assertEqual(lm.parse_lrc(lrc), lm.parse_lrc(answer))
        # Unlabeled, millisecond-stamped, the blank stamped line kept.
        self.assertNotIn("[by:", lrc)
        written = lm.parse_lrc(lrc)
        assert isinstance(written, lm.SyncedLyrics)
        self.assertIn("", [line.text for line in written.lines])
        self.assertFalse(paths[0].with_suffix(".txt").exists())
        self.assertEqual(frame_kinds(paths[0]), [SYLT, USLT])
        outcome = self.outcome(paths[0].name)
        self.assertEqual(
            (outcome.status, outcome.source, outcome.kind),
            (lr.OutcomeStatus.WRITTEN, lm.Source.LRCLIB, lm.LyricsKind.SYNCED),
        )
        # The synced step answered first, so the untimed one was never asked.
        self.assertEqual(self.order, [lm.LyricsKind.SYNCED])
        self.assertEqual(self.record_of(paths[0]), record)
        undone = self.run_album("--mode", "undo")
        self.assertEqual(undone.code, album.EXIT_OK, undone.stdout + undone.stderr)
        self.assertEqual((rb.snapshots(paths), raw(paths)), before)

    def test_an_untimed_answer_is_written_for_sync_to_time(self) -> None:
        paths = self.build_timed(timed_track(1, HARBOR, QUARRY))
        self.hear(paths[0], HARBOR, held=QUARRY)
        record = self.record_of(paths[0])
        self.answer_both({}, {TITLES[HARBOR]: HARBOR})
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assert_untimed(paths[0], HARBOR)
        self.assert_record_kept_for_sync(paths[0], record)
        self.assertEqual(self.order, [lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED])
        outcome = self.outcome(paths[0].name)
        self.assertEqual(
            (outcome.source, outcome.kind), (lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED)
        )

    def test_an_answer_of_the_refused_words_is_passed_over(self) -> None:
        paths = self.build_timed(timed_track(1, HARBOR, QUARRY))
        self.hear(paths[0], HARBOR, held=QUARRY)
        self.answer_both({TITLES[HARBOR]: lrc_of(QUARRY)}, {TITLES[HARBOR]: HARBOR})
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assert_untimed(paths[0], HARBOR)


class RemovalTest(_Case):
    """Nothing sung: the text and its timing go, flagged, and undo brings them back."""

    def test_none_sung_removes_the_lrc_and_every_frame_and_undoes(self) -> None:
        paths = self.build_timed(
            timed_track(1, HARBOR, QUARRY), song_track(2, MEADOW, txt=MEADOW)
        )
        self.hear(paths[0], HARBOR, held=QUARRY)
        record = self.record_of(paths[0])
        self.answer_both({TITLES[HARBOR]: lrc_of(FOUNDRY)}, {TITLES[HARBOR]: FOUNDRY})
        before = (rb.snapshots(paths), raw(paths))
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertEqual(
            cli_fakes.row_cells(result.stdout, paths[0].name)[-1], WRONG_TEXT
        )
        for suffix in (".lrc", ".txt"):
            self.assertFalse(paths[0].with_suffix(suffix).exists())
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        outcome = self.outcome(paths[0].name)
        self.assertEqual(
            (outcome.status, outcome.implausible),
            (lr.OutcomeStatus.REMOVED, WRONG_TEXT),
        )
        self.assertEqual(self.record_of(paths[0]), record)
        undone = self.run_album("--mode", "undo")
        self.assertEqual(undone.code, album.EXIT_OK, undone.stdout + undone.stderr)
        self.assertEqual((rb.snapshots(paths), raw(paths)), before)


class SkipTest(_Case):
    """A timed track the fix does not take is skipped, nothing asked or written."""

    def assert_skipped(self, paths: list[Path], reason: str) -> None:
        """The fix skipped the first track for *reason* and changed nothing."""
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIn(f"skipped {paths[0].name}: {reason}", result.stderr)
        self.assertIn("nothing to fix", result.stdout)
        self.assertEqual(raw(paths), before)
        self.assertEqual(self.order, [])
        self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())

    def test_a_timed_text_changed_since_its_hearing_is_skipped(self) -> None:
        paths = self.build_timed(timed_track(1, HARBOR, QUARRY))
        self.hear(paths[0], HARBOR, held=FOUNDRY)
        self.assert_skipped(paths, "its text changed since it was heard")

    def test_a_machine_timed_track_is_skipped(self) -> None:
        paths = self.build_timed(timed_track(1, HARBOR, QUARRY, label=MACHINE_HEADER))
        self.hear(paths[0], HARBOR, held=QUARRY)
        self.assert_skipped(paths, "its timed lyrics are machine made")

    def test_a_timed_text_the_transcript_sings_is_skipped(self) -> None:
        paths = self.build_timed(timed_track(1, HARBOR, HARBOR))
        self.hear(paths[0], HARBOR, held=HARBOR)
        self.assert_skipped(paths, "the transcript sings its text")
