"""A run over a labeled transcript keeps its .lrc, and other content replaces it unlabeled.

A sidecar whose header labels it a machine transcript, when it serves, is embedded and
left byte for byte, header lines included, and a second run still credits it rather than
the frames the first run wrote from it. A synced answer, or synced frames holding other
lyrics, replace it with a sidecar that carries no label, and the run's backup keeps the
transcript. Each test drives the entry point with its real wiring; only the clock and
the provider table are faked, and every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar as lsc  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_unchanged import (  # noqa: E402 - import follows the sys.path bootstrap above
    UnchangedCase,
)

MT = lm.Source.MACHINE_TRANSCRIPT
#: Synced frames holding other lyrics than the transcript.
OTHER_SYLT = tag_builders.sylt((2000, "A lamp swings on the landing"))


class RunLabelTest(UnchangedCase):
    """A served transcript stays byte for byte; other content never carries the label."""

    def transcript_keeper(self, *frames: lm.FrameRecord) -> tuple[Path, bytes]:
        """The keeper holding the transcript and *frames*; its .lrc's bytes."""
        mp3 = self.keeper(*frames, lrc=fixtures.MACHINE_LRC)[0]
        return mp3, mp3.with_suffix(".lrc").read_bytes()

    def backed_up_lrc(self) -> bytes:
        """The keeper's .lrc as the newest run's backup holds it."""
        run_dir = lyrics_backup.select_run(self.album, None)
        return (run_dir / "sidecars" / "01 Keeper.lrc").read_bytes()

    def test_a_served_transcript_is_embedded_and_its_lrc_kept(self) -> None:
        mp3, before = self.transcript_keeper()
        result = self.run_ok()
        keeper = self.keeper_after()
        self.assertEqual((keeper.source, keeper.sidecar_written), (MT, None))
        self.assertEqual(mp3.with_suffix(".lrc").read_bytes(), before)
        self.assertEqual(self.embedded(mp3), lm.parse_lrc(fixtures.MACHINE_LRC))
        (row,) = [line for line in result.stdout.splitlines() if "Keeper.mp3" in line]
        self.assertEqual(row.split()[3:6], ["machine", "transcript", "synced"])

    def test_a_second_run_still_credits_the_transcript(self) -> None:
        mp3, before = self.transcript_keeper()
        self.run_ok()
        self.run_ok()
        keeper = self.keeper_after()
        self.assert_unchanged(keeper)
        self.assertIs(keeper.source, MT)
        self.assertEqual(mp3.with_suffix(".lrc").read_bytes(), before)

    def test_a_synced_answer_replaces_it_unlabeled_and_the_backup_keeps_it(
        self,
    ) -> None:
        self.answer_steps(synced={"The Keeper": fixtures.LIGHTHOUSE_LRC}, plain={})
        mp3, before = self.transcript_keeper()
        self.run_ok()
        self.assertNotIn(self.keeper_after().source, (MT, lm.Source.SIDECAR, None))
        after = mp3.with_suffix(".lrc").read_text(encoding="utf-8")
        self.assertIsNone(lsc.machine_label(after))
        self.assertEqual(self.backed_up_lrc(), before)
        self.run_ok("--mode", "undo")
        self.assertEqual(mp3.with_suffix(".lrc").read_bytes(), before)

    def test_other_synced_frames_serve_and_rewrite_it_unlabeled(self) -> None:
        mp3, before = self.transcript_keeper(OTHER_SYLT)
        self.run_ok()
        self.assertIs(self.keeper_after().source, lm.Source.FRAMES)
        after = mp3.with_suffix(".lrc").read_text(encoding="utf-8")
        self.assertIsNone(lsc.machine_label(after))
        self.assertEqual(self.backed_up_lrc(), before)

    def test_the_check_accepts_a_served_transcript(self) -> None:
        self.transcript_keeper()
        self.run_ok()
        result = self.run_ok("--mode", "check")
        rows = [line.split() for line in result.stdout.splitlines()]
        self.assertIn(["1", "01", "Keeper.mp3", "PASS"], rows)
