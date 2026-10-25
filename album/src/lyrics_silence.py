"""A track's sound level, measured locally with ffmpeg's volumedetect filter.

The album tool measures a track only when every provider missed it: a whole-track peak at or
below SILENT_PEAK_DBFS ends it ``silent``. ffmpeg is a program found on PATH, not a Python
dependency; without it the run's check is off. ffmpeg decodes the local file to a null sink:
nothing is sent anywhere.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

# The shared bounded runner sits directly under the project root, two folders above
# this one.
# See stdio_utf8/stdio_utf8.md, "How this project's tools import it", for why
# each is ONE fixed candidate rather than a search up the ancestors, and why a
# missing copy fails its import below by name.
sys.path.append(str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_config import GENIUS_TOKEN_ENV  # noqa: E402 - follows the bootstrap above

#: The executable looked up on PATH.
FFMPEG = "ffmpeg"
#: A whole-track peak at or below this is silent: about 30 dB under the quietest music,
#: and above the dither and hiss a silent gap carries.
SILENT_PEAK_DBFS = -60.0
#: Where ``-inf`` (an all-zero signal) is held, so the value stays a finite JSON number.
FLOOR_DBFS = -120.0
#: One measurement's bound: a 60-minute 320 kbps MP3 decoded in under 3 s on the build
#: machine on 2026-10-21, so this is a wide margin for a slow disk, not a tight fit.
SILENCE_TIMEOUT_S = 60.0

_MAX_VOLUME = re.compile(r"max_volume:\s*(\S+)\s*dB")
_NEG_INF = "-inf"

Runner = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class Peak:
    """A measured whole-track peak, in dBFS, never below FLOOR_DBFS."""

    dbfs: float


@dataclass(frozen=True)
class MeasureFailed:
    """Why a track could not be measured; the run carries on without the check."""

    reason: str


Level = Peak | MeasureFailed


def find_ffmpeg() -> str | None:
    """The ffmpeg on PATH, or None when there is none."""
    return shutil.which(FFMPEG)


def check_label(ffmpeg: str | None) -> str:
    """The summary's ``silence check:`` value for a run with *ffmpeg*."""
    return "on" if ffmpeg is not None else "off (no ffmpeg)"


def volumedetect_argv(ffmpeg: str, track: Path) -> list[str]:
    """The argument list measuring *track*'s first audio stream, writing nothing."""
    return [
        ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", str(track),
        "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-",
    ]  # fmt: skip


# fuzz-target
def parse_peak(stderr: str) -> float | None:
    """The last ``max_volume`` in volumedetect's *stderr*, or None when it holds none.

    ffmpeg can print an empty volumedetect block (no ``max_volume``) before the real
    one, so the last line wins. ``-inf`` and anything lower are held at FLOOR_DBFS.
    """
    found = _MAX_VOLUME.findall(stderr)
    if not found:
        return None
    if found[-1] == _NEG_INF:
        return FLOOR_DBFS
    try:
        value = float(found[-1])
    except ValueError:
        return None
    return max(value, FLOOR_DBFS) if math.isfinite(value) else None


def is_silent(peak: Peak) -> bool:
    """Whether *peak* is at or below the silence threshold."""
    return peak.dbfs <= SILENT_PEAK_DBFS


def measure_level(track: Path, ffmpeg: str, *, run: Runner | None = None) -> Level:
    """*track*'s whole-track peak, or why it could not be measured. Never raises.

    *run* is the process seam; None means the real one, bounded by bounded_run.
    """
    try:
        done = (run or _real_run)(volumedetect_argv(ffmpeg, track), SILENCE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return MeasureFailed("timed out")
    except OSError:
        return MeasureFailed("ffmpeg did not start")
    if done.returncode != 0:
        return MeasureFailed(f"ffmpeg exit {done.returncode}")
    peak = parse_peak(done.stderr)
    return MeasureFailed("no level in its answer") if peak is None else Peak(peak)


def child_environ(environ: Mapping[str, str]) -> dict[str, str]:
    """A copy of *environ* for ffmpeg, without the Genius key.

    ffmpeg needs no credential, so the one this tool may read from its environment
    is not handed on to a program that decodes untrusted files.
    """
    return {name: value for name, value in environ.items() if name != GENIUS_TOKEN_ENV}


# The real-process IO seam, excluded from unit coverage: the live suite runs it.
def _real_run(  # pragma: no cover
    argv: list[str], timeout: float
) -> subprocess.CompletedProcess[str]:
    """Run *argv* bounded by *timeout*, killing its whole tree on expiry (bounded_run).

    The child gets this process's environment less the Genius key (`child_environ`).
    """
    env = child_environ(os.environ)
    return bounded_run.run_bounded(argv, timeout=timeout, env=env)
