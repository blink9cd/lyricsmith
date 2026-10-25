"""The `[by:machine timed]` label, and a labeled .lrc whose times changed since its embed.

The sync tool writes a timed .lrc labeled `[by:machine timed]`. It is taken as a machine
transcript is: the timed providers are still asked, and with no timed answer it serves and
is kept byte for byte. A labeled .lrc holding the embedded synced lyrics' lines at other
times, its stamps corrected by hand or re-timed, is embedded again rather than losing to
the frames, and undo puts the earlier timing back. Every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory as li  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar as lsc  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_unchanged import (  # noqa: E402 - import follows the sys.path bootstrap above
    UnchangedCase,
)

MTI = lm.Source.MACHINE_TIMED


def _synced(text: str) -> lm.SyncedLyrics:
    """*text* parsed, narrowed to the synced lyrics every caller here gives it."""
    lyrics = lm.parse_lrc(text)
    assert isinstance(lyrics, lm.SyncedLyrics)
    return lyrics


class LabelTest(fixtures.IsolatedCase):
    """The label reads as the `machine timed` source, and only exactly."""

    def test_the_by_line_labels_the_file_machine_timed(self) -> None:
        self.assertIs(lsc.machine_label(fixtures.MACHINE_TIMED_LRC), MTI)

    def test_an_edited_by_line_is_no_label(self) -> None:
        edited = fixtures.MACHINE_TIMED_LRC.replace("machine timed", "me")
        self.assertIsNone(lsc.machine_label(edited))

    def test_the_source_reads_machine_timed_and_is_never_a_chain_step(self) -> None:
        self.assertEqual((MTI.value, MTI.label), ("machine timed",) * 2)
        self.assertNotIn(MTI, {key.source for key in lf.CHAIN})

    def test_the_plan_names_the_labels_source(self) -> None:
        self.assertEqual(
            lyrics_modes.plan_labeled(MTI, offline=False),
            "machine timed (synced), providers asked",
        )
        self.assertEqual(
            lyrics_modes.plan_labeled(MTI, offline=True), "machine timed (synced)"
        )


class SameLinesTest(fixtures.IsolatedCase):
    """Frames outrank a labeled .lrc only when their lines differ from its lines."""

    TIMED = _synced(fixtures.MACHINE_TIMED_LRC)

    def test_the_same_lines_at_other_times_do_not_outrank(self) -> None:
        moved = _synced(
            "[00:02.500]A gull on the railing\n[00:06.000]counts the boats coming in\n"
        )
        self.assertTrue(li.same_lines(moved, self.TIMED))
        self.assertFalse(li.EmbeddedLyrics(moved, None).synced_besides(self.TIMED))

    def test_whitespace_at_a_lines_ends_is_no_difference(self) -> None:
        padded = _synced(
            "[00:02.000] A gull on the railing \n[00:05.000]counts the boats coming in\n"
        )
        self.assertTrue(li.same_lines(padded, self.TIMED))

    def test_another_line_count_outranks(self) -> None:
        shorter = _synced("[00:01.000]A gull on the railing\n")
        self.assertFalse(li.same_lines(shorter, self.TIMED))
        self.assertTrue(li.EmbeddedLyrics(shorter, None).synced_besides(self.TIMED))

    def test_one_line_of_other_text_outranks(self) -> None:
        other = _synced(
            "[00:01.000]A gull on the railing\n[00:04.000]counts the ferries in\n"
        )
        self.assertTrue(li.EmbeddedLyrics(other, None).synced_besides(self.TIMED))


class TimedRunTest(UnchangedCase):
    """A run over a labeled .lrc: served and kept, and re-embedded when its times change."""

    def test_a_served_timed_lrc_is_embedded_and_kept(self) -> None:
        mp3 = self.keeper(lrc=fixtures.MACHINE_TIMED_LRC)[0]
        before = mp3.with_suffix(".lrc").read_bytes()
        result = self.run_ok("--offline")
        self.assertIs(self.keeper_after().source, MTI)
        self.assertEqual(mp3.with_suffix(".lrc").read_bytes(), before)
        self.assertEqual(self.embedded(mp3), lm.parse_lrc(fixtures.MACHINE_TIMED_LRC))
        (row,) = [line for line in result.stdout.splitlines() if mp3.name in line]
        self.assertIn("machine timed", row)

    def _edit_then_undo(self, text: str) -> None:
        """Embed *text*, move its first stamp, embed again, then undo the second run."""
        mp3 = self.keeper(lrc=text)[0]
        lrc = mp3.with_suffix(".lrc")
        self.run_ok("--offline")
        first = self.embedded(mp3)
        edited = text.replace("[00:01.000]", "[00:01.500]")
        lrc.write_text(edited, encoding="utf-8", newline="")
        self.run_ok("--offline")
        self.assertEqual(self.keeper_after().status, lr.OutcomeStatus.WRITTEN)
        self.assertEqual(self.embedded(mp3), lm.parse_lrc(edited))
        self.assertEqual(lrc.read_text(encoding="utf-8"), edited)
        self.run_ok("--mode", "undo")
        self.assertEqual(self.embedded(mp3), first)

    def test_a_timed_lrc_with_a_stamp_edited_is_embedded_again_then_undone(
        self,
    ) -> None:
        self._edit_then_undo(fixtures.MACHINE_TIMED_LRC)

    def test_a_transcript_with_a_stamp_edited_is_embedded_again_then_undone(
        self,
    ) -> None:
        self._edit_then_undo(fixtures.MACHINE_LRC)
