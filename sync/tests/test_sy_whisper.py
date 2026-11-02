"""The child's sung check: the vocal stem written as a WAV, heard by whisper-cli, judged.

whisper-cli is stood in by a runner that reads the WAV it was given and writes the
transcript where the fixed call names it; the live smoke test runs the real one.
"""

from __future__ import annotations

import dataclasses
import stat
import subprocess
import sys
import wave
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import at_engine  # noqa: E402 - import follows the sys.path bootstrap above
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_heard as hd  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import Heard  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
import sy_whisper  # noqa: E402 - import follows the sys.path bootstrap above
from sy_text import tokenize  # noqa: E402 - import follows the sys.path bootstrap above
from sy_types import MODEL_RATE  # noqa: E402 - import follows the sys.path bootstrap above

LINES = ("one two three", "four five")
#: Two seconds of invented 16-bit samples at the model's rate.
PCM = bytes(range(256)) * 250
PCM_SECONDS = 2.0
#: What the stand-in whisper-cli prints on its error stream.
WHISPER_STDERR = "loading the model\nits last words\n"


class FakeWhisper:
    """A whisper-cli stand-in: it records its call and the WAV, then writes *text*.

    With *text* None it writes no transcript; *raises* is raised once the call is seen.
    """

    def __init__(
        self, text: str | None = "", code: int = 0, raises: BaseException | None = None
    ) -> None:
        self.text, self.code, self.raises = text, code, raises
        self.argv: list[str] = []
        self.timeout = 0.0
        self.params: tuple[int, int, int] = (0, 0, 0)
        self.frames = b""
        self.workdir_mode = 0

    def __call__(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.argv, self.timeout = list(argv), timeout
        out = Path(argv[argv.index("-of") + 1])
        self.workdir_mode = stat.S_IMODE(out.parent.stat().st_mode)
        with wave.open(argv[argv.index("-f") + 1], "rb") as read:
            self.params = (
                read.getnchannels(),
                read.getsampwidth(),
                read.getframerate(),
            )
            self.frames = read.readframes(read.getnframes())
        if self.raises is not None:
            raise self.raises
        if self.text is not None:
            out.with_suffix(".txt").write_text(
                self.text, encoding="utf-8", newline="\n"
            )
        return subprocess.CompletedProcess(list(argv), self.code, "", WHISPER_STDERR)


class WhisperTest(sy_cases.IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.scratch = self.tmp / "work"
        self.scratch.mkdir()
        tokens = tokenize(LINES)
        self.request = rq.Request(
            self.tmp / "a.mp3",
            tokens.ids,
            self.tmp / "sync-models",
            tokens.line_of,
            self.tmp / "bin" / "whisper-cli",
            self.tmp / "whisper-models",
        )

    def heard(self, whisper: FakeWhisper) -> Heard:
        """The verdict `heard_in` gives on `PCM` through *whisper*."""
        heard, _ = sy_whisper.heard_in(PCM, self.request, self.scratch, whisper)
        return heard

    def test_the_transcript_comes_back_with_the_verdict(self) -> None:
        text = "One two\nthree four\nsix seven\n"
        heard, transcript = sy_whisper.heard_in(
            PCM, self.request, self.scratch, FakeWhisper(text)
        )
        self.assertEqual((heard, transcript), (Heard(5, 0.6), text))

    def test_a_kept_transcript_is_judged_with_no_whisper_run(self) -> None:
        kept = dataclasses.replace(self.request, transcript="one two three four")
        whisper = FakeWhisper(raises=AssertionError("whisper-cli was run"))
        heard, transcript = sy_whisper.heard_in(PCM, kept, self.scratch, whisper)
        self.assertEqual(heard, hd.judge(kept.ids, "one two three four"))
        self.assertEqual((transcript, whisper.argv), ("one two three four", []))
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_the_vocals_are_heard_as_16_bit_mono_at_the_models_rate(self) -> None:
        whisper = FakeWhisper()
        self.heard(whisper)
        self.assertEqual(whisper.params, (1, 2, MODEL_RATE))
        self.assertEqual(whisper.frames, PCM)

    def test_whisper_is_called_as_transcribe_calls_it_in_english(self) -> None:
        whisper = FakeWhisper()
        self.heard(whisper)
        audio = Path(whisper.argv[whisper.argv.index("-f") + 1])
        workdir = Path(whisper.argv[whisper.argv.index("-of") + 1]).parent
        models = self.request.whisper_model_dir
        expected = at_engine.build_argv(
            self.request.whisper_cli,
            models / at_models.WHISPER_MODEL_NAME,
            models / at_models.VAD_MODEL_NAME,
            "en",
            audio,
            workdir,
        )
        self.assertEqual(whisper.argv, expected)

    def test_the_transcript_is_judged_against_the_text(self) -> None:
        heard = self.heard(FakeWhisper("One two\nthree four\nsix seven\n"))
        self.assertEqual(
            heard, hd.judge(self.request.ids, "one two three four six seven")
        )
        self.assertEqual(heard, Heard(5, 0.6))

    def test_the_work_folder_is_private_inside_scratch_and_removed(self) -> None:
        whisper = FakeWhisper()
        self.heard(whisper)
        self.assertEqual(whisper.workdir_mode, 0o700)
        workdir = Path(whisper.argv[whisper.argv.index("-of") + 1]).parent
        self.assertEqual(workdir.parent, self.scratch)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_the_bound_is_a_minute_plus_half_the_length(self) -> None:
        self.assertEqual(sy_whisper.whisper_timeout(100.0), 110.0)
        whisper = FakeWhisper()
        self.heard(whisper)
        self.assertEqual(whisper.timeout, sy_whisper.whisper_timeout(PCM_SECONDS))

    def test_a_failed_run_is_an_error_never_a_pass(self) -> None:
        timeout = subprocess.TimeoutExpired(["whisper-cli"], 1.0, stderr=b"stuck\n")
        cases = (
            (FakeWhisper(code=3), ("whisper-cli exited 3", "its last words")),
            (FakeWhisper(raises=timeout), ("whisper-cli timed out", "stuck")),
            (FakeWhisper(raises=OSError("gone")), ("could not start: gone",)),
            (FakeWhisper(None), ("left no transcript", "its last words")),
        )
        for whisper, expected in cases:
            with self.subTest(expected=expected[0]):
                with self.assertRaises(sy_whisper.HeardError) as raised:
                    self.heard(whisper)
                for words in expected:
                    self.assertIn(words, str(raised.exception))
                self.assertEqual(list(self.scratch.iterdir()), [])
