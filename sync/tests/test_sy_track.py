"""One track, timed or skipped: each reply the child can give, and the .lrc it leaves."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_lrc  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_model import Source, SyncedLyrics, parse_lrc  # noqa: E402 - follows the bootstrap
from lyrics_sidecar import machine_label  # noqa: E402 - follows the bootstrap
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
from at_engine import Runner  # noqa: E402 - import follows the sys.path bootstrap above
from sy_cases import (  # noqa: E402 - follows the bootstrap
    FRAME_SECONDS,
    HEARD,
    OTHER_SONG,
    fake_child,
    not_sung_reply,
    reply_for,
)
from lyrics_heard import Heard  # noqa: E402 - import follows the sys.path bootstrap above
import sy_track  # noqa: E402 - import follows the sys.path bootstrap above
from sy_text import tokenize  # noqa: E402 - import follows the sys.path bootstrap above
from sy_track import time_or_skip  # noqa: E402 - import follows the sys.path bootstrap above
from sy_types import (  # noqa: E402 - import follows the sys.path bootstrap above
    MAX_TRACK_S,
    Action,
    Label,
    Outcome,
    Planned,
    TrackResult,
    Whisper,
)

LINES = ("la la", "lo lo", "li li")
LABEL = Label("1.2.3", "4.5.6", "7.8.9")
WHISPER = Whisper(Path("/bin/whisper-cli"), Path("/whisper-models"))


def requesting(requests: list[rq.Request], reply: str) -> Runner:
    """A fake child answering *reply* that adds each request it is handed to *requests*."""
    child = fake_child(reply)

    def run(argv: Sequence[str], *, timeout: float) -> CompletedProcess[str]:
        text = Path(argv[argv.index("--request") + 1]).read_text(encoding="utf-8")
        requests.append(rq.load_request(text))
        return child(argv, timeout=timeout)

    return run


class TrackTest(sy_cases.IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.audio = self.tmp / "01 A.mp3"
        self.lrc = self.audio.with_suffix(".lrc")

    def time(
        self,
        run: Runner,
        action: Action = Action.TIME,
        lines: tuple[str, ...] = LINES,
        transcript: str | None = None,
    ) -> TrackResult:
        """*self.audio* planned as *action* with *lines*, run through *run*.

        The plan carries *transcript* as a kept one, when given.
        """
        planned = Planned(self.audio, action, lines=lines, transcript=transcript)
        return time_or_skip(planned, self.tmp / "models", LABEL, run, whisper=WHISPER)

    def test_a_skip_keeps_its_outcome_and_detail(self) -> None:
        planned = Planned(self.audio, Action.ERROR_TEXT, detail="not UTF-8")
        result = time_or_skip(
            planned, self.tmp, LABEL, fake_child(None, code=9), whisper=WHISPER
        )
        self.assertEqual(
            (result.outcome, result.detail), (Outcome.ERROR_TEXT, ("not UTF-8",))
        )

    def test_a_confident_reply_is_written_with_the_label(self) -> None:
        result = self.time(fake_child(reply_for(LINES)))
        self.assertEqual((result.outcome, result.first_line), (Outcome.TIMED, None))
        text = self.lrc.read_text(encoding="utf-8")
        self.assertIs(machine_label(text), Source.MACHINE_TIMED)
        self.assertIn(sy_lrc.re_line(LABEL), text)

    def test_the_request_carries_each_tokens_line(self) -> None:
        requests: list[rq.Request] = []
        run = requesting(requests, reply_for(LINES))
        self.assertIs(self.time(run).outcome, Outcome.TIMED)
        self.assertEqual([r.line_of for r in requests], [tokenize(LINES).line_of])
        named = [(r.whisper_cli, r.whisper_model_dir) for r in requests]
        self.assertEqual(named, [(WHISPER.cli, WHISPER.model_dir)])

    def test_a_text_not_sung_writes_nothing_and_names_the_share(self) -> None:
        result = self.time(fake_child(not_sung_reply(Heard(30, 0.04))))
        self.assertIs(result.outcome, Outcome.NOT_SUNG)
        self.assertEqual(
            result.detail, ("0.04 of the 30 word pairs heard are the text's",)
        )
        self.assertEqual((result.share, result.little_heard), ("0.04", None))
        self.assertFalse(self.lrc.exists())

    def test_a_text_not_sung_carries_what_was_heard(self) -> None:
        result = self.time(fake_child(not_sung_reply(transcript="heard words")))
        self.assertEqual(
            (result.outcome, result.transcript), (Outcome.NOT_SUNG, "heard words")
        )

    def test_a_text_not_sung_heard_too_long_to_keep_is_still_not_sung(self) -> None:
        result = self.time(fake_child(not_sung_reply(transcript=None)))
        self.assertEqual(
            (result.outcome, result.share, result.transcript),
            (Outcome.NOT_SUNG, "0.04", None),
        )

    def test_a_changed_text_still_not_sung_is_judged_with_no_child(self) -> None:
        child = fake_child(None, raises=AssertionError("a child ran"))
        result = self.time(child, transcript=OTHER_SONG)
        self.assertEqual(
            (result.outcome, result.share, result.transcript),
            (Outcome.NOT_SUNG, "0.00", OTHER_SONG),
        )
        self.assertEqual(
            result.detail,
            (
                "0.00 of the 30 word pairs heard are the text's",
                "judged against what was heard before",
            ),
        )
        self.assertFalse(self.lrc.exists())

    def test_a_changed_text_now_heard_hands_the_child_the_kept_transcript(
        self,
    ) -> None:
        requests: list[rq.Request] = []
        run = requesting(requests, reply_for(LINES))
        kept = " ".join(LINES)
        self.assertIs(self.time(run, transcript=kept).outcome, Outcome.TIMED)
        self.assertEqual([r.transcript for r in requests], [kept])

    def test_a_track_with_no_kept_transcript_asks_the_child_to_hear_it(self) -> None:
        requests: list[rq.Request] = []
        run = requesting(requests, reply_for(LINES))
        self.time(run)
        self.assertEqual([r.transcript for r in requests], [None])

    def test_a_text_too_little_heard_to_judge_is_timed_and_says_so(self) -> None:
        result = self.time(fake_child(reply_for(LINES, heard=Heard(12, 0.0))))
        self.assertEqual((result.outcome, result.little_heard), (Outcome.TIMED, 12))
        self.assertTrue(self.lrc.exists())

    def test_too_little_heard_still_meets_the_confidence_floor(self) -> None:
        reply = reply_for(LINES, score=0.05, heard=Heard(12, 0.0))
        result = self.time(fake_child(reply))
        self.assertEqual(
            (result.outcome, result.little_heard), (Outcome.LOW_CONFIDENCE, 12)
        )
        self.assertFalse(self.lrc.exists())

    def test_a_text_heard_enough_to_judge_carries_no_note(self) -> None:
        result = self.time(fake_child(reply_for(LINES, heard=Heard(20, 0.25))))
        self.assertEqual((result.outcome, result.little_heard), (Outcome.TIMED, None))

    def test_a_re_time_ends_re_timed(self) -> None:
        result = self.time(fake_child(reply_for(LINES)), Action.RETIME)
        self.assertIs(result.outcome, Outcome.RETIMED)

    def test_a_low_confidence_reply_writes_nothing_and_names_the_median(self) -> None:
        result = self.time(fake_child(reply_for(LINES, score=0.05)))
        self.assertIs(result.outcome, Outcome.LOW_CONFIDENCE)
        self.assertEqual(result.detail, ("median line confidence 0.05",))
        self.assertFalse(self.lrc.exists())

    def test_a_low_confidence_result_carries_the_median_as_its_row_prints_it(
        self,
    ) -> None:
        result = self.time(fake_child(reply_for(LINES, score=0.05)))
        self.assertEqual(result.confidence, "0.05")
        self.assertTrue(result.detail[0].endswith(" 0.05"), result.detail)

    def test_text_longer_than_the_track_writes_nothing(self) -> None:
        reply = rq.dump_reply(rq.Reply(rq.STATUS_TOO_LONG, 3, FRAME_SECONDS, (), HEARD))
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

    def test_blank_lines_are_written_empty_and_move_no_sung_lines_stamp(self) -> None:
        spaced = ("la la", "", "lo lo", "", "", "li li")
        result = self.time(fake_child(reply_for(spaced)), lines=spaced)
        self.assertEqual((result.outcome, result.unaligned), (Outcome.TIMED, 0))
        with_blanks = parse_lrc(self.lrc.read_text(encoding="utf-8"))
        self.time(fake_child(reply_for(LINES)))
        without = parse_lrc(self.lrc.read_text(encoding="utf-8"))
        assert isinstance(with_blanks, SyncedLyrics)  # a timed .lrc reads as synced
        assert isinstance(without, SyncedLyrics)  # a timed .lrc reads as synced
        self.assertEqual([line.text for line in with_blanks.lines], list(spaced))
        sung = tuple(line for line in with_blanks.lines if line.text)
        self.assertEqual(sung, without.lines)
        # Each blank line is stamped with the next sung line's start, written before it.
        ms = [line.ms for line in with_blanks.lines]
        self.assertEqual(ms[1:3], [ms[2]] * 2)
        self.assertEqual(ms[3:6], [ms[5]] * 3)
