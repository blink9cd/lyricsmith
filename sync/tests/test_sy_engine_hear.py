"""A hearing child: run, bounded with no alignment budget, its reply checked; each model.

Also the whisper model a run hears with: its own time bound, the argv naming it, and the
setup check reading only the models that run needs.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_engine  # noqa: E402 - import follows the sys.path bootstrap above
import sy_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_modes  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
import sy_whisper  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import Heard  # noqa: E402 - import follows the sys.path bootstrap above
from sy_cases import fake_child, reply_for  # noqa: E402 - follows the bootstrap above
from sy_types import MAX_TRACK_S, Options  # noqa: E402 - follows the bootstrap above

REQUEST = rq.Request(
    Path("a.mp3"),
    (2, 3),
    Path("models"),
    (0, 0),
    Path("whisper-cli"),
    Path("w"),
    align=False,
)
TURBO, LARGE = at_models.WHISPER_MODEL_NAMES
UNSUNG = rq.heard_only(Heard(30, 0.1), "what was heard")
SUNG = rq.heard_only(Heard(30, 0.9), "what was heard")


class RecordingChild:
    """A fake child writing *reply*, recording the bound it was run under."""

    def __init__(self, reply: str) -> None:
        self.run = fake_child(reply)
        self.timeout = 0.0

    def __call__(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.timeout = timeout
        return self.run(argv, timeout=timeout)


class HearEngineTest(sy_cases.IsolatedCase):
    def test_each_hearing_comes_back(self) -> None:
        for reply in (SUNG, UNSUNG):
            with self.subTest(reply=reply):
                back = sy_engine.hear(
                    REQUEST, self.tmp, fake_child(rq.dump_reply(reply)), 10.0
                )
                self.assertEqual((back.reply, back.detail), (reply, ()))

    def test_the_hearing_request_is_written_where_the_child_reads_it(self) -> None:
        sy_engine.hear(REQUEST, self.tmp, fake_child(None, code=1), 10.0)
        written = (self.tmp / sy_engine.REQUEST_NAME).read_text(encoding="utf-8")
        self.assertEqual(rq.load_request(written), REQUEST)

    def test_a_crash_a_timeout_and_a_failed_start_are_each_said(self) -> None:
        timeout = subprocess.TimeoutExpired(["x"], 1.0)
        cases = (
            (fake_child(None, code=3), "exit 3"),
            (fake_child(None, raises=timeout), "timed out"),
            (fake_child(None, raises=OSError("no")), "could not start: no"),
        )
        for run, said in cases:
            with self.subTest(said=said):
                back = sy_engine.hear(REQUEST, self.tmp, run, 1.0)
                self.assertIsNone(back.reply)
                self.assertEqual(back.detail[0], said)

    def test_an_aligned_or_malformed_reply_is_unreadable_here(self) -> None:
        for reply in (reply_for(("la la",)), "{}", None):
            with self.subTest(reply=reply):
                back = sy_engine.hear(REQUEST, self.tmp, fake_child(reply), 1.0)
                self.assertIsNone(back.reply)
                self.assertTrue(back.detail[0].startswith("unreadable reply"))
                (self.tmp / sy_engine.REPLY_NAME).unlink(missing_ok=True)

    def test_a_hearing_is_bounded_with_no_alignment_budget(self) -> None:
        child = RecordingChild(rq.dump_reply(SUNG))
        sy_engine.hear(REQUEST, self.tmp, child, 100.0)
        # A minute to load htdemucs, the length again to separate, then
        # whisper-cli's own bound: a minute and half the length.
        self.assertEqual(child.timeout, 60.0 + 100.0 + 110.0)
        self.assertEqual(sy_engine.child_timeout(100.0, align=False), child.timeout)
        self.assertLess(child.timeout, sy_engine.child_timeout(100.0))

    def test_a_hearing_never_trusts_a_length_past_the_longest_track(self) -> None:
        longest = sy_engine.child_timeout(MAX_TRACK_S, align=False)
        self.assertEqual(sy_engine.child_timeout(1e9, align=False), longest)
        self.assertEqual(sy_engine.child_timeout(None, align=False), longest)

    def test_the_bound_takes_the_whisper_models_own_factor(self) -> None:
        factors = {TURBO: 0.5, LARGE: 2.0}
        with mock.patch.object(sy_whisper, "WHISPER_PER_SECOND_BY_MODEL", factors):
            self.assertEqual(sy_whisper.whisper_timeout(100.0, LARGE), 260.0)
            self.assertEqual(
                sy_engine.child_timeout(100.0, align=False, model=LARGE),
                60.0 + 100.0 + 260.0,
            )
            large = dataclasses.replace(REQUEST, whisper_model=LARGE)
            child = RecordingChild(rq.dump_reply(SUNG))
            sy_engine.hear(large, self.tmp, child, 100.0)
            self.assertEqual(child.timeout, 420.0)


class WhisperModelTest(sy_cases.IsolatedCase):
    def test_each_pinned_whisper_model_has_its_factor_large_v3_twice_the_turbos(
        self,
    ) -> None:
        factors = sy_whisper.WHISPER_PER_SECOND_BY_MODEL
        self.assertEqual(set(factors), set(at_models.WHISPER_MODEL_NAMES))
        self.assertEqual(factors[TURBO], 0.5)
        self.assertEqual(factors[LARGE], 1.0)
        self.assertEqual(sy_whisper.whisper_timeout(100.0, LARGE), 160.0)
        self.assertEqual(sy_whisper.whisper_timeout(100.0), 110.0)

    def test_the_vocals_are_heard_with_the_model_the_request_names(self) -> None:
        called: list[str] = []

        def whisper(
            argv: Sequence[str], *, timeout: float
        ) -> subprocess.CompletedProcess[str]:
            called.extend(argv)
            out = Path(argv[argv.index("-of") + 1])
            out.with_suffix(".txt").write_text("", encoding="utf-8", newline="\n")
            return subprocess.CompletedProcess(list(argv), 0, "", "")

        large = dataclasses.replace(REQUEST, whisper_model=LARGE)
        sy_whisper.heard_in(b"\0\0" * 160, large, self.tmp, whisper)
        self.assertEqual(Path(called[called.index("-m") + 1]), Path("w") / LARGE)

    def test_the_setup_check_reads_only_the_models_its_run_hears_with(self) -> None:
        options = Options(
            mode="hear",
            path=self.tmp,
            extensions=frozenset({"mp3"}),
            embed=False,
            overwrite=False,
            dry_run=False,
            model_dir=self.tmp,
            whisper_model_dir=self.tmp / "whisper",
            whisper_model=LARGE,
        )
        for model, expected in ((None, TURBO), (LARGE, LARGE)):
            with self.subTest(model=model):
                checked = mock.Mock(return_value=[])
                chosen = dataclasses.replace(options, whisper_model=model)
                with mock.patch.object(sy_models, "model_problems", checked):
                    sy_modes._whisper_problems(chosen, Path("whisper-cli"), ("sync",))
                (manifest,) = (call.args[1] for call in checked.call_args_list)
                self.assertEqual(manifest, at_models.run_models(expected))
