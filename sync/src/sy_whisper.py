"""The child's sung check: whisper-cli hears the vocal stem, and `sy_heard` judges it.

The child separates the vocals, then, before wav2vec2 loads, hands them here as 16-bit
samples. A request carrying a transcript kept from an earlier run is judged on that, and
whisper-cli is not run: nothing is transcribed twice. Otherwise the samples are written
as a WAV in a private folder inside the parent's work folder and transcribed by
whisper-cli exactly as the transcribe tool calls it, in English. A run that fails, times
out or leaves no transcript raises `HeardError`, so the child fails and the track is an
error: a check that could not run is never a pass. The run goes through a `Runner`, so
the tests stand in for whisper-cli.
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
import at_engine
from at_models import VAD_MODEL_NAME, WHISPER_MODEL_NAME
from at_types import ENGINE_STEM
from lyrics_heard import Heard
from sy_heard import judge
from sy_request import Request
from sy_types import (
    MODEL_RATE,
    TEMP_PREFIX,
    TXT_SUFFIX,
    WHISPER_BASE_S,
    WHISPER_PER_SECOND,
)

#: whisper-cli hears the vocals in English, the alignment model's one language: a song
#: in another still scored 0.34 or more against its own text, over the cutoff.
WHISPER_LANGUAGE: Final = "en"
#: The vocal stem as whisper-cli reads it: 16-bit mono at `MODEL_RATE`, as measured.
WAV_NAME: Final = "vocals.wav"
PCM_BYTES: Final = 2
#: whisper-cli's error lines a failed run's message keeps, as ffmpeg's are kept.
WHISPER_ERROR_LINES: Final = 4


class HeardError(RuntimeError):
    """whisper-cli could not hear the vocals: never a pass, so the child fails."""


def whisper_timeout(seconds: float) -> float:
    """whisper-cli's bound on *seconds* of vocals: a minute plus half their length."""
    return WHISPER_BASE_S + WHISPER_PER_SECOND * seconds


def write_wav(path: Path, pcm: bytes) -> None:
    """Write *pcm*, 16-bit mono samples at `MODEL_RATE`, as a WAV file at *path*."""
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(PCM_BYTES)
        out.setframerate(MODEL_RATE)
        out.writeframes(pcm)


def _whisper_failure(head: str, stderr: str | bytes | None) -> HeardError:
    """*head*, then whisper-cli's last `WHISPER_ERROR_LINES` error lines."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    tail = at_engine.stderr_tail(stderr or "", WHISPER_ERROR_LINES)
    return HeardError("\n".join((f"{head}:" if tail else head, *tail)))


def _transcript(
    argv: Sequence[str], text: Path, timeout: float, run: at_engine.Runner
) -> str:
    """Run whisper-cli by *argv* within *timeout*; the transcript it left at *text*."""
    try:
        done = run(argv, timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        head = f"whisper-cli timed out after {timeout:g} s"
        raise _whisper_failure(head, expired.stderr) from None
    except OSError as error:
        raise HeardError(f"whisper-cli could not start: {error}") from None
    if done.returncode != 0:
        head = f"whisper-cli exited {done.returncode}"
        raise _whisper_failure(head, done.stderr)
    try:
        return text.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise _whisper_failure("whisper-cli left no transcript", done.stderr) from None


def heard_in(
    pcm: bytes, request: Request, scratch: Path, run: at_engine.Runner
) -> tuple[Heard, str]:
    """What whisper-cli, run by *run*, hears of *request*'s text in the vocals *pcm*.

    The verdict comes back with the transcript it was judged on. A *request* carrying
    a kept transcript is judged on that, and whisper-cli is not run.

    The vocals are written as a WAV in a private folder made inside *scratch*, the
    parent's work folder, so a child killed at its bound leaves nothing the parent
    does not remove; the folder goes when the transcript is read. whisper-cli is
    called exactly as the transcribe tool calls it (`at_engine.build_argv`), in
    `WHISPER_LANGUAGE`, by the runner that withholds the Genius key from it.
    """
    if request.transcript is not None:
        return judge(request.ids, request.transcript), request.transcript
    seconds = len(pcm) / (PCM_BYTES * MODEL_RATE)
    models = request.whisper_model_dir
    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX, dir=scratch) as work:
        workdir = Path(work)
        write_wav(workdir / WAV_NAME, pcm)
        argv = at_engine.build_argv(
            request.whisper_cli,
            models / WHISPER_MODEL_NAME,
            models / VAD_MODEL_NAME,
            WHISPER_LANGUAGE,
            workdir / WAV_NAME,
            workdir,
        )
        text = workdir / f"{ENGINE_STEM}{TXT_SUFFIX}"
        transcript = _transcript(argv, text, whisper_timeout(seconds), run)
    return judge(request.ids, transcript), transcript
