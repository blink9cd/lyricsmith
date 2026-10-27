"""The child process that times one track: decode, separate the vocals, score, align.

`sy_engine` runs it as ``python sy_child.py --request <file> --reply <file>``, one track
a process, so the parent's timeout bounds a call PyTorch gives no timeout of its own, and
the memory, about 3 GB, goes back when it exits. Before loading anything it refuses the
network: both models are read from the model folder, and nothing of the track leaves the
machine. Each function that needs PyTorch is left out of the unit suites' coverage, which
run without it and test `main` with those functions stood in; the live smoke test
(tests/test_sy_live.py) runs them all.
"""

from __future__ import annotations

import argparse
import random
import socket
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The project's manifest reader, in its own folder under the project root: it puts the
# shared libraries the project's pyproject.toml names on sys.path, or fails naming the
# one that is missing (lyricsmith_project/lyricsmith_project.md).
_PROJECT_DIR = Path(__file__).resolve().parents[2]
_READER_DIR = str(_PROJECT_DIR / "lyricsmith_project")
# Moved first on sys.path, so no same-named module can stand in for the reader.
sys.path[:] = [_READER_DIR, *(entry for entry in sys.path if entry != _READER_DIR)]
import lyricsmith_project  # noqa: E402 - import follows the sys.path bootstrap above

# Run as a script, a failure exits 2 naming it; imported, it raises.
lyricsmith_project.add_shared_libraries(exit_on_failure=__name__ == "__main__")

import stdio_utf8  # noqa: E402 - follows the sys.path insert above
from sy_lines import Span, frames_needed  # noqa: E402 - follows the sys.path insert above
from sy_request import (  # noqa: E402 - follows the sys.path insert above
    STATUS_OK,
    STATUS_TOO_LONG,
    Reply,
    dump_reply,
    load_request,
)
from sy_text import BLANK, LABELS  # noqa: E402 - follows the sys.path insert above
from sy_types import (  # noqa: E402 - follows the sys.path insert above
    DEMUCS_BAG,
    DEMUXERS,
    MAX_TRACK_S,
    WAV2VEC2_FILE,
)

#: htdemucs's sample rate, stereo.
MIX_RATE: Final = 44100
#: wav2vec2's sample rate, mono.
MODEL_RATE: Final = 16000
#: wav2vec2's attention memory grows with the square of its input's length: a whole song
#: in one pass does not fit in 16 GB, so the vocals are scored 20 s at a time and the
#: windows' scores joined before the one alignment over the whole text.
WINDOW_SAMPLES: Final = 20 * MODEL_RATE
#: The feature extractor's shortest input; a shorter tail window is dropped.
MIN_WINDOW_SAMPLES: Final = 400
#: Decoding even a long track takes seconds; this bounds a stuck ffmpeg.
DECODE_TIMEOUT_S: Final = 300
#: ffmpeg's error lines a failed decode's message keeps. The parent shows the last five
#: lines of this process's error output, one of them the exception's own first line, and
#: a whitelist refusal takes four: its reason, then three lines on the failed open.
FFMPEG_ERROR_LINES: Final = 4
#: Keeps a silent track's normalization from dividing by zero.
MIN_STD: Final = 1e-8
#: demucs shifts the mix by a random offset before separating, drawn from Python's module
#: RNG; unseeded, the same track's stamps moved between runs. The seed fixes the draw, so
#: a run repeats. The draw itself moves a track's timing (on one test track, the lines more
#: than 3 s off ranged from 6 to 9 across five seeds), and 0 is a fixed choice, set before
#: any seed was measured, not a tuned one.
SHIFT_SEED: Final = 0


def build_parser() -> argparse.ArgumentParser:
    """The child's two options, both required."""
    parser = argparse.ArgumentParser(description="Time one track for the sync tool.")
    parser.add_argument("--request", required=True, help="the request file to read")
    parser.add_argument("--reply", required=True, help="the reply file to write")
    return parser


def _refuse_network() -> None:
    """Make every socket connection, datagram send and name lookup in this process fail."""

    def refused(*_args: object, **_kwargs: object) -> NoReturn:
        raise OSError("the sync tool's child process never uses the network")

    for target, name in (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket.socket, "sendto"),
        (socket.socket, "sendmsg"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
    ):
        setattr(target, name, refused)


def decode_argv(audio: Path) -> list[str]:
    """The ffmpeg command line decoding *audio*, absolute, to raw stereo at `MIX_RATE`.

    ffmpeg reads a name such as ``http:x.mp3`` or ``pipe:0`` as a protocol, so the track
    is named ``file:`` plus its absolute path, and only the file protocol is allowed.
    Only the audio demuxers `DEMUXERS` names may open it, so a track whose bytes are an
    ffconcat or HLS playlist is refused rather than opening the files it names.
    ``-t`` decodes no more than `MAX_TRACK_S`, whatever the tags claim, since the whole
    decoded track is held in memory.
    """
    if not audio.is_absolute():
        raise ValueError(f"the track must be named by an absolute path: {audio}")
    return [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        ",".join(sorted(set(DEMUXERS.values()))),
        "-t",
        str(MAX_TRACK_S),
        "-i",
        f"file:{audio}",
        "-f",
        "f32le",
        "-ac",
        "2",
        "-ar",
        str(MIX_RATE),
        "-",
    ]


class DecodeError(RuntimeError):
    """ffmpeg could not decode a track; the message ends with ffmpeg's own last lines."""


def decoded_bytes(audio: Path) -> bytes:
    """*audio* decoded by ffmpeg to raw stereo float samples at `MIX_RATE`.

    A failure raises `DecodeError` carrying ffmpeg's last error lines, so the operator
    reads ffmpeg's reason (``Format not on whitelist``, a corrupt frame) and not only
    an exit code.
    """
    try:
        # reliability: ignore[RL005] ffmpeg starts no child to hold the pipes open
        done = subprocess.run(
            decode_argv(audio),
            capture_output=True,
            check=True,
            timeout=DECODE_TIMEOUT_S,
        )
    except subprocess.CalledProcessError as failed:
        raise DecodeError(_failure_message(failed)) from None
    return done.stdout


def _failure_message(failed: subprocess.CalledProcessError) -> str:
    """The exit code ffmpeg gave, then its last `FFMPEG_ERROR_LINES` error lines."""
    text = (failed.stderr or b"").decode("utf-8", errors="replace")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return f"ffmpeg exit {failed.returncode} decoding the track, no error output"
    tail = "\n".join(lines[-FFMPEG_ERROR_LINES:])
    return f"ffmpeg exit {failed.returncode} decoding the track:\n{tail}"


def _decode(
    audio: Path,
) -> Any:  # pragma: no cover - needs PyTorch; the live test runs it
    """*audio* as a (2, samples) float tensor at `MIX_RATE`, decoded by ffmpeg."""
    import numpy
    import torch

    raw = decoded_bytes(audio)
    return (
        torch.from_numpy(numpy.frombuffer(raw, dtype=numpy.float32).copy())
        .view(-1, 2)
        .T
    )


def _seed_shift() -> None:
    """Seed the module RNG demucs draws its shift from, so a track separates the same."""
    random.seed(SHIFT_SEED)


def _vocals(
    mix: Any, model_dir: Path
) -> Any:  # pragma: no cover - needs PyTorch; the live test runs it
    """The vocal stem of *mix*, separated by htdemucs loaded from *model_dir* alone."""
    import torch
    import yaml
    from demucs.apply import BagOfModels, apply_model
    from demucs.hf import load_safetensors_model

    bag = yaml.safe_load((model_dir / DEMUCS_BAG).read_text(encoding="utf-8"))
    models = [
        load_safetensors_model(model_dir / f"{sig}.safetensors")
        for sig in bag["models"]
    ]
    separator = BagOfModels(models, bag.get("weights"), bag.get("segment"))
    separator.eval()
    reference = mix.mean(0)
    mean, std = reference.mean(), reference.std().clamp_min(MIN_STD)
    with torch.inference_mode():
        _seed_shift()
        stems = apply_model(
            separator, ((mix - mean) / std)[None], device="cpu", progress=False
        )[0]
    return stems[separator.sources.index("vocals")] * std + mean


def _emissions(
    vocals: Any, model_dir: Path
) -> tuple[Any, float]:  # pragma: no cover - needs PyTorch; the live test runs it
    """wav2vec2's log-probabilities for *vocals*, window by window, and their seconds."""
    import torch
    import torchaudio

    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    if tuple(bundle.get_labels()) != LABELS:
        raise RuntimeError(
            "the model's labels are not the ones this tool tokenizes for"
        )
    if not (model_dir / WAV2VEC2_FILE).is_file():
        raise FileNotFoundError(model_dir / WAV2VEC2_FILE)
    model = bundle.get_model(
        dl_kwargs={"model_dir": str(model_dir), "progress": False}
    ).eval()
    mono = torchaudio.functional.resample(vocals.mean(0), MIX_RATE, MODEL_RATE)
    pieces = []
    with torch.inference_mode():
        for start in range(0, int(mono.shape[0]), WINDOW_SAMPLES):
            window = mono[start : start + WINDOW_SAMPLES]
            if window.shape[0] >= MIN_WINDOW_SAMPLES:
                scores, _ = model(window[None])
                pieces.append(torch.log_softmax(scores, dim=-1)[0])
    return torch.cat(pieces), float(mono.shape[0]) / MODEL_RATE


def _align(
    emissions: Any, ids: Sequence[int]
) -> list[Span]:  # pragma: no cover - needs PyTorch; the live test runs it
    """One CTC forced alignment of *ids* over *emissions*: one span per token."""
    import torch
    from torchaudio.functional import forced_align, merge_tokens

    targets = torch.tensor([list(ids)], dtype=torch.int32)
    path, scores = forced_align(emissions[None], targets, blank=BLANK)
    return [
        Span(s.start, s.end, float(s.score))
        for s in merge_tokens(path[0], scores[0].exp())
    ]


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Read the request, time the track, write the reply; exit 0, or crash for the parent."""
    # The parent decodes this process's error output as UTF-8.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    args = build_parser().parse_args(argv)
    _refuse_network()
    request = load_request(Path(args.request).read_text(encoding="utf-8"))
    vocals = _vocals(_decode(request.audio), request.model_dir)
    emissions, seconds = _emissions(vocals, request.model_dir)
    frames = int(emissions.shape[0])
    if frames_needed(request.ids) > frames:
        reply = Reply(STATUS_TOO_LONG, frames, seconds / frames, ())
    else:
        reply = Reply(
            STATUS_OK, frames, seconds / frames, tuple(_align(emissions, request.ids))
        )
    Path(args.reply).write_text(dump_reply(reply), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - run as a child process
    sys.exit(main())
