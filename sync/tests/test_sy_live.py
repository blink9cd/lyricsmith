"""Live: the real child process and models time the public-domain jfk.wav, offline.

Skipped unless this interpreter has the sync install, ffmpeg is on PATH, the models are
fetched, both sync's and transcribe's, and whisper.cpp's bundled jfk.wav sits beside
whisper-cli. The pinned starts were measured on the pinned stack; each may move by up to
a second, but timing the clip twice gives the same stamps. The clip's 18 distinct word
pairs are under the sung check's floor, so a run times it and lists it as little heard;
the check's verdict on its vocal stem is read in this process, against its own text and
another. The model folders are read when this module loads, before the isolated case
points HOME elsewhere; each run writes only into the test's temporary folder.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import at_engine  # noqa: E402 - import follows the sys.path bootstrap above
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar as lsc  # noqa: E402 - import follows the sys.path bootstrap above
import sy_by_hand as bh  # noqa: E402 - import follows the sys.path bootstrap above
import sy_child  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard as lh  # noqa: E402 - import follows the sys.path bootstrap above
import sy_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_modes  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
import sync  # noqa: E402 - import follows the sys.path bootstrap above
import sy_whisper  # noqa: E402 - import follows the sys.path bootstrap above
from sy_text import tokenize  # noqa: E402 - import follows the sys.path bootstrap above

#: transcribe's own search for whisper.cpp's bundled jfk.wav, reused rather than copied.
SAMPLE = sy_cases.transcribe_test_module("test_live_whisper").bundled_sample()
MODELS = sy_models.default_model_dir(sys.platform, os.environ, Path.home())
WHISPER_MODELS = at_models.default_model_dir(sys.platform, os.environ, Path.home())
WHISPER_CLI = shutil.which(sy_modes.WHISPER_CLI)
READY = (
    SAMPLE is not None
    and all(importlib.util.find_spec(name) for name in sy_modes.REQUIRED_MODULES)
    and shutil.which(sy_modes.FFMPEG) is not None
    and WHISPER_CLI is not None
    and not sy_models.model_problems(MODELS)
    and not sy_models.model_problems(WHISPER_MODELS, at_models.MANIFEST)
)
TEXT = (
    "And so my fellow Americans,\n"
    "ask not what your country can do for you,\n"
    "ask what you can do for your country.\n"
)
#: An invented verse the clip does not say.
WRONG = (
    "red kites over the harbor wall\n"
    "seven lanterns in the rain\n"
    "a quiet engine hums all night\n"
)
#: Measured on the pinned stack, offline: each line's start in seconds.
STARTS = (0.42, 3.55, 8.40)
TOLERANCE_S = 1.0


def stamp_lines(text: str) -> list[str]:
    """*text*'s timed lines, the header left out."""
    return [
        line
        for line in text.splitlines()
        if isinstance(lm.parse_lrc(line), lm.SyncedLyrics)
    ]


@unittest.skipUnless(
    READY,
    "needs the sync install, ffmpeg, the fetched models and whisper.cpp's jfk.wav",
)
class LiveSyncTest(sy_cases.IsolatedCase):
    def time_jfk(self, folder: Path) -> str:
        """The .lrc this tool writes for jfk.wav and TEXT, timed inside *folder*."""
        assert SAMPLE is not None
        folder.mkdir()
        track = folder / "01 jfk.wav"
        shutil.copyfile(SAMPLE, track)
        track.with_suffix(".txt").write_text(TEXT, encoding="utf-8", newline="\n")
        argv = [
            "--path",
            str(folder),
            "--extensions",
            "wav",
            "--model-dir",
            str(MODELS),
            "--whisper-model-dir",
            str(WHISPER_MODELS),
            "--by-hand-list",
            str(folder / "by-hand.tsv"),
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync.main(argv), 0)
        (row,) = bh.read_by_hand_list(folder / "by-hand.tsv")
        self.assertEqual((row.path, row.reason), (str(track), bh.REASON_LITTLE))
        return track.with_suffix(".lrc").read_text(encoding="utf-8")

    def test_the_clips_vocals_are_heard_as_its_own_text_and_not_another(
        self,
    ) -> None:
        assert SAMPLE is not None and WHISPER_CLI is not None
        vocals, _ = sy_child._stems(sy_child._decode(SAMPLE.resolve()), MODELS)
        pcm = sy_child._pcm16(vocals)
        verdicts = []
        for text in (TEXT, WRONG):
            tokens = tokenize(text.splitlines())
            request = rq.Request(
                SAMPLE.resolve(),
                tokens.ids,
                MODELS,
                tokens.line_of,
                Path(WHISPER_CLI).absolute(),
                WHISPER_MODELS,
            )
            heard, _ = sy_whisper.heard_in(
                pcm, request, self.tmp, at_engine.run_bounded
            )
            verdicts.append(heard)
        right, wrong = verdicts
        self.assertEqual(right.pairs, wrong.pairs)
        self.assertTrue(lh.little_heard(right), right)
        self.assertGreaterEqual(right.share, 0.9)
        self.assertLess(wrong.share, lh.MIN_HEARD_SHARE)

    def test_jfk_is_timed_labeled_within_a_second_and_the_same_twice(self) -> None:
        text = self.time_jfk(self.tmp / "first")
        self.assertIs(lsc.machine_label(text), lm.Source.MACHINE_TIMED)
        lyrics = lm.parse_lrc(text)
        assert isinstance(lyrics, lm.SyncedLyrics)
        for line, expected in zip(lyrics.lines, STARTS, strict=True):
            self.assertAlmostEqual(line.ms / 1000, expected, delta=TOLERANCE_S)
        again = self.time_jfk(self.tmp / "second")
        self.assertEqual(stamp_lines(again), stamp_lines(text))
