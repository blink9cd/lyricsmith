"""The check judges the frame that served a track, not the one ``--lang`` ranks first.

A track holds two SYLTs, in French and in German. A first run under ``--lang fra``
serves it from the French frame and writes the ``.lrc`` it lacked from them. A
second run under ``--lang deu`` finds that ``.lrc`` holding the French frame's lyrics,
so the French frame serves again and the track is unchanged: its check judges the
French frame, whatever the German one holds, and so does a later ``--mode check`` of
that run. Each test drives the entry point with its real wiring; only the clock and
the provider table are faked, and every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_embedded import KEEPER  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_second_frames_kept import SecondRunFramesKeptCase  # noqa: E402 - import follows the sys.path bootstrap above

#: Invented French lines, stored in time order.
_FRA_CLEAN = tag_builders.sylt(
    (1000, "Une lampe au palier"), (4500, "et l'escalier se tait"), lang="fra"
)
#: The same lines, stored out of order.
_FRA_DISORDERED = tag_builders.sylt(
    (4500, "et l'escalier se tait"), (1000, "Une lampe au palier"), lang="fra"
)
#: Invented German lines, stored in time order.
_DEU_CLEAN = tag_builders.sylt(
    (1000, "Ein Licht im Flur"), (4500, "und die Treppe schweigt"), lang="deu"
)
#: The same lines, stored out of order.
_DEU_DISORDERED = tag_builders.sylt(
    (4500, "und die Treppe schweigt"), (1000, "Ein Licht im Flur"), lang="deu"
)


class ServingFrameIsJudgedTest(SecondRunFramesKeptCase):
    """A frames-served track's check, in the run and in ``--mode check``."""

    def second_under_deu(self, *frames: lm.FrameRecord) -> lr.CheckResult:
        """The keeper with *frames*, run under fra then deu: the deu run's check.

        Asserts the second run left the keeper unchanged, served from its frames.
        """
        mp3 = self.keeper(*frames)[0]
        self.run_ok("--lang", "fra")
        self.assertIn("01 Keeper.lrc", {p.name for p in self.album.iterdir()})
        check = self.second_run(mp3, "--lang", "deu").check
        assert check is not None
        return check

    def recheck_row(self) -> list[str]:
        """The keeper's row in ``--mode check`` of the newest run, split on spaces."""
        result = self.run_album("--mode", "check")
        (row,) = [line for line in result.stdout.splitlines() if KEEPER in line]
        return row.split()

    def test_a_disordered_frame_that_did_not_serve_is_not_flagged(self) -> None:
        check = self.second_under_deu(_FRA_CLEAN, _DEU_DISORDERED)
        self.assertEqual(check.verdict, lr.CheckVerdict.PASS)
        self.assertEqual(check.flags, ())

    def test_a_disordered_frame_that_served_is_flagged(self) -> None:
        check = self.second_under_deu(_FRA_DISORDERED, _DEU_CLEAN)
        self.assertEqual(check.verdict, lr.CheckVerdict.FLAG)
        rules = [flag.rule for flag in check.flags]
        self.assertEqual(rules, [lr.CheckRule.OUT_OF_ORDER])

    def test_the_recheck_agrees_with_the_run_on_a_clean_serving_frame(self) -> None:
        self.second_under_deu(_FRA_CLEAN, _DEU_DISORDERED)
        self.assertEqual(self.recheck_row()[3:], ["PASS"])

    def test_the_recheck_agrees_with_the_run_on_a_disordered_serving_frame(
        self,
    ) -> None:
        self.second_under_deu(_FRA_DISORDERED, _DEU_CLEAN)
        row = self.recheck_row()
        self.assertEqual(row[3:7], ["FLAG:", "out", "of", "order"])
