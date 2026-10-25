"""The whisper-cli call: its argument list, its bounded run, its outcome and its version.

`build_argv` writes the one fixed call; `transcribe_one` runs it into a per-file
temporary folder and turns the exit code, a timeout and the files left behind into
an `EngineRun`; `read_engine_version` asks `whisper-cli --version` for the label.
Every process goes through the `Runner` seam, so the tests replace it wholesale.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from at_types import ENGINE_STEM, LRC_SUFFIX, TXT_SUFFIX, has_timestamp

#: A long recording on a CPU-only machine can run slower than real time; the standard
#: library cannot read an MP3's duration, so the bound is fixed rather than scaled to
#: the recording.
ENGINE_TIMEOUT_SECONDS = 3 * 60 * 60
VERSION_TIMEOUT_SECONDS = 30
STDERR_TAIL_LINES = 5
VERSION = re.compile(r"version:\s*([0-9][0-9A-Za-z.+-]*)")
#: The album tool's Genius key variable, never passed to whisper-cli, which decodes
#: untrusted audio. Spelled out here rather than imported because this tool stays free
#: of the album modules; the album tool defines it as album/src/lyrics_config.py's
#: GENIUS_TOKEN_ENV, and the two must name the same variable.
GENIUS_TOKEN_ENV = "GENIUS_ACCESS_TOKEN"


class Runner(Protocol):
    """Runs one command with a timeout and returns its decoded result."""

    def __call__(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """Run argv; raise subprocess.TimeoutExpired past timeout seconds."""
        ...


def child_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """A copy of *environ* without the Genius key variable, for a child process."""
    return {name: value for name, value in environ.items() if name != GENIUS_TOKEN_ENV}


def run_bounded(
    argv: Sequence[str], *, timeout: float
) -> subprocess.CompletedProcess[str]:
    """subprocess.run with a timeout, no stdin, both streams decoded, the key withheld.

    Not the shared bounded_run library on purpose: the exported tool stays free of a
    third vendored module, and whisper-cli starts no child process.
    """
    # reliability: ignore[RL005] whisper-cli has no child to hold the pipes; the docstring says why
    return subprocess.run(
        list(argv),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=child_environment(os.environ),
    )


@dataclass(frozen=True)
class EngineRun:
    """One engine call's result: the .lrc text on success, the failure lines otherwise."""

    ok: bool
    lrc_text: str
    detail: tuple[str, ...]


def build_argv(
    cli: Path, model: Path, vad_model: Path, language: str, audio: Path, workdir: Path
) -> list[str]:
    """The fixed call: -mc 0 and a padded --vad (transcribe.md, Fixed settings), -np quiet."""
    return [
        str(cli),
        "-m",
        str(model),
        "-vm",
        str(vad_model),
        "--vad",
        # More padding and a longer minimum pause than whisper.cpp's defaults (30, 100),
        # and a shorter minimum speech stretch (250), so a short line said into laughter
        # stays inside a speech stretch rather than falling between two or being dropped.
        "-vp",
        "400",
        "-vsd",
        "600",
        "-vspd",
        "100",
        "-mc",
        "0",
        "-l",
        language,
        "-np",
        "-f",
        str(audio),
        "-otxt",
        "-olrc",
        "-of",
        str(workdir / ENGINE_STEM),
    ]


def stderr_tail(text: str, lines: int = STDERR_TAIL_LINES) -> tuple[str, ...]:
    """The last non-blank lines of an error stream."""
    return tuple([line for line in text.split("\n") if line.strip()][-lines:])


def _decoded(stream: bytes | str | None) -> str:
    """A TimeoutExpired stream as text: it may be raw bytes, already text, or None."""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


def _failed(*detail: str) -> EngineRun:
    """A failed run carrying its explanation lines."""
    return EngineRun(False, "", tuple(detail))


def transcribe_one(
    argv: Sequence[str],
    workdir: Path,
    run: Runner = run_bounded,
    timeout: float = ENGINE_TIMEOUT_SECONDS,
) -> EngineRun:
    """Run whisper-cli into workdir; fail on a non-zero exit, a timeout or an untimed .lrc."""
    try:
        done = run(argv, timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        return _failed(
            f"whisper-cli timed out after {timeout:g} s",
            *stderr_tail(_decoded(expired.stderr)),
        )
    except OSError as error:
        return _failed(f"whisper-cli could not start: {error}")
    if done.returncode != 0:
        return _failed(
            f"whisper-cli exited {done.returncode}", *stderr_tail(done.stderr)
        )
    lrc = workdir / f"{ENGINE_STEM}{LRC_SUFFIX}"
    if not lrc.is_file() or not (workdir / f"{ENGINE_STEM}{TXT_SUFFIX}").is_file():
        return _failed("whisper-cli left no .lrc and .txt", *stderr_tail(done.stderr))
    text = lrc.read_text(encoding="utf-8", errors="replace")
    if not has_timestamp(text):
        return _failed("the .lrc has no timestamped line")
    return EngineRun(True, text, ())


# fuzz-target
def parse_engine_version(output: str) -> str | None:
    """'whisper.cpp version: 1.9.5' -> '1.9.5'; None when no version is printed."""
    match = VERSION.search(output)
    return match.group(1) if match else None


def read_engine_version(cli: Path, run: Runner = run_bounded) -> str | None:
    """whisper-cli --version, from either stream; None when it cannot be read."""
    try:
        done = run([str(cli), "--version"], timeout=VERSION_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if done.returncode != 0:
        return None
    return parse_engine_version(f"{done.stdout}\n{done.stderr}")
