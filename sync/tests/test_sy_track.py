"""One track, timed or skipped: each reply the child can give, and the .lrc it leaves."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_lrc  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_model import Source  # noqa: E402 - follows the bootstrap above
from lyrics_sidecar import machine_label  # noqa: E402 - follows the bootstrap
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
from at_engine import Runner  # noqa: E402 - import follows the sys.path bootstrap above
from sy_cases import FRAME_SECONDS, fake_child, reply_for  # noqa: E402 - follows the bootstrap
import sy_track  # noqa: E402 - import follows the sys.path bootstrap above
from sy_track import time_or_skip  # noqa: E402 - import follows the sys.path bootstrap above
from sy_types import (  # noqa: E402 - import follows the sys.path bootstrap above
    MAX_TRACK_S,
    Action,
    Label,
    Outcome,
    Planned,
    TrackResult,
)

LINES = ("la la", "lo lo", "li li")
LABEL = Label("1.2.3", "4.5.6", "7.8.9")


class TrackTest(sy_cases.IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.audio = self.tmp / "01 A.mp3"
        self.lrc = self.audio.with_suffix(".lrc")

    def time(
        self, run: Runner, action: Action = Action.TIME, lines: tuple[str, ...] = LINES
    ) -> TrackResult:
        """*self.audio* planned as *action* with *lines*, run through *run*."""
        planned = Planned(self.audio, action, lines=lines)
        return time_or_skip(planned, self.tmp / "models", LABEL, run)

    def test_a_skip_keeps_its_outcome_and_detail(self) -> None:
        planned = Planned(self.audio, Action.ERROR_TEXT, detail="not UTF-8")
        result = time_or_skip(planned, self.tmp, LABEL, fake_child(None, code=9))
        self.assertEqual(
            (result.outcome, result.detail), (Outcome.ERROR_TEXT, ("not UTF-8",))
        )

    def test_a_confident_reply_is_written_with_the_label(self) -> None:
        result = self.time(fake_child(reply_for(LINES)))
        self.assertEqual((result.outcome, result.first_line), (Outcome.TIMED, None))
        text = self.lrc.read_text(encoding="utf-8")
        self.assertIs(machine_label(text), Source.MACHINE_TIMED)
        self.assertIn(sy_lrc.re_line(LABEL), text)

    def test_a_re_time_ends_re_timed(self) -> None:
        result = self.time(fake_child(reply_for(LINES)), Action.RETIME)
        self.assertIs(result.outcome, Outcome.RETIMED)

    def test_a_low_confidence_reply_writes_nothing_and_names_the_median(self) -> None:
        result = self.time(fake_child(reply_for(LINES, score=0.05)))
        self.assertIs(result.outcome, Outcome.LOW_CONFIDENCE)
        self.assertEqual(result.detail, ("median line confidence 0.05",))
        self.assertFalse(self.lrc.exists())

    def test_text_longer_than_the_track_writes_nothing(self) -> None:
        reply = rq.dump_reply(rq.Reply(rq.STATUS_TOO_LONG, 3, FRAME_SECONDS, ()))
        self.assertIs(self.time(fake_child(reply)).outcome, Outcome.TOO_LONG)
        self.assertFalse(self.lrc.exists())

    def test_a_track_over_the_longest_length_runs_no_child(self) -> None:
        over = float(MAX_TRACK_S + 1)
        with mock.patch.object(sy_track.sy_engine, "track_seconds", return_value=over):
            result = self.time(fake_child(None, raises=AssertionError("a child ran")))
        self.assertEqual(
            (result.outcome, result.detail),
            (Outcome.SKIP_LONG_TRACK, (f"{over:.0f} s, over the {MAX_TRACK_S} s cap",)),
        )
        self.assertFalse(self.lrc.exists())

    def test_a_track_at_the_longest_length_is_timed(self) -> None:
        at_cap = float(MAX_TRACK_S)
        with mock.patch.object(
            sy_track.sy_engine, "track_seconds", return_value=at_cap
        ):
            result = self.time(fake_child(reply_for(LINES)))
        self.assertIs(result.outcome, Outcome.TIMED)

    def test_a_crash_is_an_align_error_with_its_exit(self) -> None:
        result = self.time(fake_child(None, code=1))
        self.assertIs(result.outcome, Outcome.ERROR_ALIGN)
        self.assertEqual(result.detail[0], "exit 1")

    def test_a_stretched_first_line_is_flagged_with_its_stamp(self) -> None:
        result = self.time(fake_child(reply_for(LINES, widths=(100, 10, 10))))
        self.assertIs(result.outcome, Outcome.TIMED)
        self.assertEqual(result.first_line, sy_lrc.stamp(0.0))

    def test_a_refused_write_is_a_write_error(self) -> None:
        with mock.patch.object(sy_lrc, "write_lrc", side_effect=OSError("full")):
            result = self.time(fake_child(reply_for(LINES)))
        self.assertEqual(
            (result.outcome, result.detail), (Outcome.ERROR_WRITE, ("full",))
        )

    def test_a_header_line_takes_a_neighbors_stamp(self) -> None:
        lines = ("[Chorus]", "la")
        result = self.time(fake_child(reply_for(lines)), lines=lines)
        self.assertEqual((result.outcome, result.unaligned), (Outcome.TIMED, 1))
