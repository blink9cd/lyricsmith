"""The engine call: its argument list, its outcomes, its timeout, its version."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from unittest import mock

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import at_engine
from tests._isolation import IsolatedCase

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")


class FakeRunner:
    """Stands in for run_bounded: records each call, then does what the test set."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        error: BaseException | None = None,
        effect: Callable[[Sequence[str]], None] | None = None,
    ) -> None:
        self.calls: list[tuple[list[str], float]] = []
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
        self.error, self.effect = error, effect

    def __call__(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), timeout))
        if self.error is not None:
            raise self.error
        if self.effect is not None:
            self.effect(argv)
        return subprocess.CompletedProcess(
            list(argv), self.returncode, self.stdout, self.stderr
        )


def writes(
    lrc: str | None, txt: str | None = " x\n"
) -> Callable[[Sequence[str]], None]:
    """An effect that writes the engine's two files where -of points."""

    def effect(argv: Sequence[str]) -> None:
        stem = Path(argv[list(argv).index("-of") + 1])
        if lrc is not None:
            stem.with_suffix(".lrc").write_text(lrc, encoding="utf-8", newline="\n")
        if txt is not None:
            stem.with_suffix(".txt").write_text(txt, encoding="utf-8", newline="\n")

    return effect


class EngineTest(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.workdir = self.root / "work"
        self.workdir.mkdir()
        self.argv = at_engine.build_argv(
            Path("cli"), Path("m.bin"), Path("v.bin"), "en", Path("a.mp3"), self.workdir
        )

    def test_build_argv_is_the_whisper_cli_call(self) -> None:
        self.assertEqual(
            self.argv,
            [
                "cli",
                "-m",
                "m.bin",
                "-vm",
                "v.bin",
                "--vad",
                "-vp",
                "400",
                "-vsd",
                "600",
                "-vspd",
                "100",
                "-mc",
                "0",
                "-l",
                "en",
                "-np",
                "-f",
                "a.mp3",
                "-otxt",
                "-olrc",
                "-of",
                str(self.workdir / "out"),
            ],
        )

    def test_success_returns_the_lrc_text_and_passes_the_timeout(self) -> None:
        runner = FakeRunner(effect=writes("[by:whisper.cpp]\n[00:00.26] x\n"))
        run = at_engine.transcribe_one(self.argv, self.workdir, run=runner)
        self.assertTrue(run.ok)
        self.assertEqual(run.lrc_text, "[by:whisper.cpp]\n[00:00.26] x\n")
        self.assertEqual(runner.calls, [(self.argv, at_engine.ENGINE_TIMEOUT_SECONDS)])
        self.assertEqual(at_engine.ENGINE_TIMEOUT_SECONDS, 3 * 60 * 60)

    def test_failures(self) -> None:
        cases = [
            (
                FakeRunner(returncode=3, stderr="a\n\nb\nc\nd\ne\nf\n"),
                ("whisper-cli exited 3", "b", "c", "d", "e", "f"),
            ),
            (
                FakeRunner(error=subprocess.TimeoutExpired("cli", 10800)),
                ("whisper-cli timed out after 10800 s",),
            ),
            (
                FakeRunner(error=FileNotFoundError(2, "No such file")),
                ("whisper-cli could not start: [Errno 2] No such file",),
            ),
            (FakeRunner(effect=writes(None)), ("whisper-cli left no .lrc and .txt",)),
            (
                FakeRunner(effect=writes("[00:00.26] x\n", None)),
                ("whisper-cli left no .lrc and .txt",),
            ),
            (
                FakeRunner(effect=writes("[by:whisper.cpp]\nuntimed\n")),
                ("the .lrc has no timestamped line",),
            ),
        ]
        for runner, detail in cases:
            with self.subTest(detail=detail[0]):
                run = at_engine.transcribe_one(self.argv, self.workdir, run=runner)
                self.assertFalse(run.ok)
                self.assertEqual(run.detail, detail)
                for leftover in self.workdir.iterdir():
                    leftover.unlink()

    def test_a_missing_output_and_a_timeout_carry_the_stderr_tail(self) -> None:
        cases = [
            (
                FakeRunner(effect=writes(None), stderr="loading\n\nerror: no space\n"),
                ("whisper-cli left no .lrc and .txt", "loading", "error: no space"),
            ),
            (
                FakeRunner(
                    error=subprocess.TimeoutExpired("cli", 5, stderr=b"busy\n\xff\n")
                ),
                ("whisper-cli timed out after 5 s", "busy", "\ufffd"),
            ),
            (
                FakeRunner(error=subprocess.TimeoutExpired("cli", 5, stderr="text\n")),
                ("whisper-cli timed out after 5 s", "text"),
            ),
        ]
        for runner, detail in cases:
            with self.subTest(detail=detail[0]):
                run = at_engine.transcribe_one(
                    self.argv, self.workdir, run=runner, timeout=5
                )
                self.assertEqual(run.detail, detail)

    def test_stderr_tail_drops_blank_lines(self) -> None:
        self.assertEqual(at_engine.stderr_tail("\n a \n\n b\n"), (" a ", " b"))

    def test_parse_engine_version(self) -> None:
        self.assertEqual(
            at_engine.parse_engine_version("whisper.cpp version: 1.9.5\n"), "1.9.5"
        )
        self.assertIsNone(
            at_engine.parse_engine_version("usage: whisper-cli [options]\n")
        )

    @given(st.from_regex(r"[0-9][0-9A-Za-z.+-]{0,10}", fullmatch=True), st.text())
    def test_fuzz_parse_engine_version(self, version: str, tail: str) -> None:
        printed = f"whisper.cpp version: {version}\n{tail}"
        self.assertEqual(at_engine.parse_engine_version(printed), version)

    def test_read_engine_version_reads_either_stream(self) -> None:
        for runner in (
            FakeRunner(stderr="whisper.cpp version: 1.9.5\n"),
            FakeRunner(stdout="whisper.cpp version: 1.9.5\n"),
        ):
            self.assertEqual(
                at_engine.read_engine_version(Path("cli"), run=runner), "1.9.5"
            )
            self.assertEqual(
                runner.calls,
                [(["cli", "--version"], at_engine.VERSION_TIMEOUT_SECONDS)],
            )

    def test_read_engine_version_failures_give_none(self) -> None:
        for runner in (
            FakeRunner(returncode=1, stderr="whisper.cpp version: 1.9.5\n"),
            FakeRunner(stdout="no version here\n"),
            FakeRunner(error=subprocess.TimeoutExpired("cli", 30)),
            FakeRunner(error=PermissionError(13, "denied")),
        ):
            self.assertIsNone(at_engine.read_engine_version(Path("cli"), run=runner))

    def test_run_bounded_kills_a_real_process_at_its_timeout(self) -> None:
        with self.assertRaises(subprocess.TimeoutExpired):
            at_engine.run_bounded(
                [sys.executable, "-c", "import time; time.sleep(60)"], timeout=1
            )

    def test_run_bounded_decodes_both_streams(self) -> None:
        done = at_engine.run_bounded(
            [
                sys.executable,
                "-c",
                "import sys; print('out'); print('err', file=sys.stderr)",
            ],
            timeout=30,
        )
        self.assertEqual(
            (done.returncode, done.stdout.strip(), done.stderr.strip()),
            (0, "out", "err"),
        )


class ChildEnvironmentTest(IsolatedCase):
    """whisper-cli decodes untrusted audio, so it never sees the album tool's key."""

    def test_the_key_variable_is_dropped_and_the_rest_kept(self) -> None:
        environ = {at_engine.GENIUS_TOKEN_ENV: "fake-key", "PATH": "/bin", "LANG": "C"}
        self.assertEqual(
            at_engine.child_environment(environ), {"PATH": "/bin", "LANG": "C"}
        )
        self.assertIn(at_engine.GENIUS_TOKEN_ENV, environ)

    def test_the_variable_is_the_album_tools(self) -> None:
        self.assertEqual(at_engine.GENIUS_TOKEN_ENV, "GENIUS_ACCESS_TOKEN")

    def test_run_bounded_hides_an_exported_key_from_the_child(self) -> None:
        name = "GENIUS_ACCESS_TOKEN"  # the album tool's, spelled out
        with mock.patch.dict(os.environ, {name: "fake-key", "AT_KEPT": "kept"}):
            done = at_engine.run_bounded(
                [
                    sys.executable,
                    "-c",
                    "import os, sys;"
                    " print(os.environ.get('AT_KEPT'), os.environ.get(sys.argv[1]))",
                    name,
                ],
                timeout=30,
            )
        self.assertEqual(done.stdout.split(), ["kept", "None"])
