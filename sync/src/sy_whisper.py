"""The child's sung check: whisper-cli hears the vocal stem, and `sy_heard` judges it.

The child separates the vocals, then, before wav2vec2 loads, hands them here as 16-bit
samples. A request carrying a transcript kept from an earlier run is judged on that, and
whisper-cli is not run: nothing is transcribed twice. Otherwise the samples are written
as a WAV in a private folder inside the parent's work folder and transcribed by
whisper-cli exactly as the transcribe tool calls it, in English, with the whisper model
the request names. A run that fails, times
out or leaves no transcript raises `HeardError`, so the child fails and the track is an
error: a check that could not run is never a pass. The run goes through a `Runner`, so
the tests stand in for whisper-cli.
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Final

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
import at_engine
from at_models import LARGE_MODEL_NAME, VAD_MODEL_NAME, WHISPER_MODEL_NAME
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
#: large-v3's seconds of bound a second of vocals, about twice the turbo's. On five
#: test-library tracks at low load its median whisper time was about 0.068 s a second
#: of audio against the turbo's about 0.040 (paired ratios 1.3 to 2.5, median about
#: 1.7), so 1.0 keeps about the turbo's headroom over its own measured time.
LARGE_WHISPER_PER_SECOND: Final = 1.0
#: Each pinned whisper model's seconds of bound a second of vocals, on top of
#: `WHISPER_BASE_S`: the turbo's (`WHISPER_PER_SECOND`) and large-v3's, each measured.
WHISPER_PER_SECOND_BY_MODEL: Final[Mapping[str, float]] = MappingProxyType(
    {
        WHISPER_MODEL_NAME: WHISPER_PER_SECOND,
        LARGE_MODEL_NAME: LARGE_WHISPER_PER_SECOND,
    }
)


class HeardError(RuntimeError):
    """whisper-cli could not hear the vocals: never a pass, so the child fails."""


def whisper_timeout(seconds: float, model: str = WHISPER_MODEL_NAME) -> float:
    """whisper-cli's bound on *seconds* of vocals heard with *model*.

    A minute plus the model's own share of their length: half of it for the turbo, all
    of it for large-v3 (`WHISPER_PER_SECOND_BY_MODEL`).
    """
    return WHISPER_BASE_S + WHISPER_PER_SECOND_BY_MODEL[model] * seconds


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
            models / request.whisper_model,
            models / VAD_MODEL_NAME,
            WHISPER_LANGUAGE,
            workdir / WAV_NAME,
            workdir,
        )
        text = workdir / f"{ENGINE_STEM}{TXT_SUFFIX}"
        bound = whisper_timeout(seconds, request.whisper_model)
        transcript = _transcript(argv, text, bound, run)
    return judge(request.ids, transcript), transcript
