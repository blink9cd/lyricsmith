"""The child asked to hear only: it separates and hears, replies the verdict, aligns nothing.

PyTorch's steps are stood in as `test_sy_child` stands them in; the live suites run
the real ones.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_child  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import Heard  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_hearing_log import HeardVerdict  # noqa: E402 - follows the bootstrap above
from sy_text import tokenize  # noqa: E402 - import follows the sys.path bootstrap above

#: The text sent, invented: 22 distinct word pairs, so a transcript of it is judged.
LINES = (
    "the morning ferry leaves at nine with seven sleepy riders",
    "a gull is shouting from the mast while the captain hums a tune",
)
SUNG_TEXT = " ".join(LINES)
#: An invented song of 21 distinct word pairs, none of them the text's.
WRONG_SONG = (
    "red kites over the harbor wall seven lanterns in the rain a quiet engine hums"
    " all night and nobody comes home again"
)


def whisper_hears(text: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    """A whisper-cli stand-in that writes *text* as its transcript and exits 0."""

    def run(argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        out = Path(argv[argv.index("-of") + 1])
        out.with_suffix(".txt").write_text(text, encoding="utf-8", newline="\n")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    return run


class ChildHearTest(sy_cases.IsolatedCase):
    def hear(self, heard: str) -> rq.HeardOnly:
        """The child's main on a hearing request for `LINES`; its checked reply.

        whisper-cli hears *heard*; the scoring, which a hearing never reaches, raises.
        """
        work = self.tmp / "work"
        work.mkdir(exist_ok=True)
        request, reply = work / "request.json", work / "reply.json"
        tokens = tokenize(LINES)
        asked = rq.Request(
            self.tmp / "a.mp3",
            tokens.ids,
            self.tmp,
            tokens.line_of,
            Path("/w"),
            Path("/wm"),
            align=False,
        )
        request.write_text(rq.dump_request(asked), encoding="utf-8", newline="\n")
        never = AssertionError("a hearing scored the vocals")
        with (
            mock.patch.object(sy_child, "_refuse_network"),
            mock.patch.object(sy_child, "_decode"),
            mock.patch.object(sy_child, "_stems", return_value=(None, ())),
            mock.patch.object(sy_child, "_pcm16", return_value=b"\0\0" * 16000),
            mock.patch.object(sy_child.at_engine, "run_bounded", whisper_hears(heard)),
            mock.patch.object(sy_child, "_emissions", side_effect=never),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = sy_child.main(["--request", str(request), "--reply", str(reply)])
        self.assertEqual(code, 0)
        self.assertEqual(
            sorted(p.name for p in work.iterdir()), ["reply.json", "request.json"]
        )
        return rq.load_heard(reply.read_text(encoding="utf-8"))

    def test_a_text_heard_sung_replies_its_verdict_and_transcript(self) -> None:
        reply = self.hear(SUNG_TEXT)
        self.assertEqual(reply.verdict, HeardVerdict.SUNG)
        self.assertEqual(reply.heard, Heard(22, 1.0))
        self.assertEqual(reply.transcript, SUNG_TEXT)

    def test_a_text_not_sung_replies_its_verdict_and_transcript(self) -> None:
        reply = self.hear(WRONG_SONG)
        expected = rq.HeardOnly(HeardVerdict.NOT_SUNG, Heard(21, 0.0), WRONG_SONG)
        self.assertEqual(reply, expected)

    def test_a_text_too_little_heard_replies_so(self) -> None:
        reply = self.hear("et et et")
        self.assertEqual(
            reply, rq.HeardOnly(HeardVerdict.LITTLE_HEARD, Heard(1, 0.0), "et et et")
        )

    def test_a_transcript_too_long_to_keep_is_sent_as_null(self) -> None:
        heard = " ".join([WRONG_SONG] * 600)
        reply = self.hear(heard)
        self.assertEqual(
            dataclasses.replace(reply, transcript=""),
            rq.HeardOnly(HeardVerdict.NOT_SUNG, Heard(22, 0.0), ""),
        )
        self.assertIsNone(reply.transcript)
