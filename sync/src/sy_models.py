"""The pinned models this tool reads, the folder they live in, and the check before a run.

The fetch itself is the transcribe tool's (`at_models.fetch_models`): a download into a
temporary file beside the target, hashed as it is written and kept only when its size and
SHA-256 match. A run never downloads; it checks every file here first.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

import at_models
from at_models import ModelFile
from sy_types import DEMUCS_BAG, DEMUCS_WEIGHTS, WAV2VEC2_FILE

#: This tool's folder inside lyricsmith's model folder.
SUBFOLDER: Final = "sync"
#: The demucs files are pinned to one Hugging Face commit, never a branch, so the
#: address can never start serving other weights.
_HTDEMUCS: Final = (
    "https://huggingface.co/adefossez/HTDemucs/resolve/"
    "cbc8a9b1a87023b7fd74e7b3412e6321c0eab003/"
)
MANIFEST: Final[tuple[ModelFile, ...]] = (
    ModelFile(
        WAV2VEC2_FILE,
        f"https://download.pytorch.org/torchaudio/models/{WAV2VEC2_FILE}",
        377664473,
        "488fd4f16de84438ffc945334278c1b9fb9b7159a806c1080b16111a958c945d",
    ),
    ModelFile(
        DEMUCS_BAG,
        f"{_HTDEMUCS}{DEMUCS_BAG}",
        21,
        "239c445d0b14454d541ad8bd9bb271c9e536d267e8a4625208744cbb2e7bb66c",
    ),
    ModelFile(
        DEMUCS_WEIGHTS,
        f"{_HTDEMUCS}{DEMUCS_WEIGHTS}",
        84025440,
        "d9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd",
    ),
)


def default_model_dir(platform: str, env: Mapping[str, str], home: Path) -> Path:
    """This tool's model folder: lyricsmith's per-user model folder, then ``sync``."""
    return at_models.default_model_dir(platform, env, home) / SUBFOLDER


def model_problems(
    model_dir: Path, manifest: Sequence[ModelFile] = MANIFEST
) -> list[str]:
    """Each model missing from *model_dir*, or not matching its pinned size and digest."""
    problems = []
    for pinned in manifest:
        path = model_dir / pinned.name
        if not path.is_file():
            problems.append(f"missing model {pinned.name}")
        elif (
            path.stat().st_size != pinned.size
            or at_models.file_digest(path) != pinned.sha256
        ):
            problems.append(f"model {pinned.name} does not match its pinned digest")
    return problems
