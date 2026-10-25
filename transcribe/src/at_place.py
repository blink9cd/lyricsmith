"""Moving one file's finished pair into place with atomic renames.

Each audio file gets a temporary dot-folder inside its destination folder, so the
walk skips it as hidden and every rename stays on one filesystem. The labeled .lrc
is renamed into place first and the .txt second: a failure between them leaves an
ours .lrc that --overwrite can repair, never a .txt alone, which nothing may replace.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from at_types import ENGINE_STEM, TEMP_PREFIX, TXT_SUFFIX, Planned

#: The labeled .lrc is staged under this name before its rename.
STAGED_LRC = "labeled.lrc"


def missing_folders(dest_dir: Path) -> tuple[Path, ...]:
    """The folders at and above *dest_dir* that do not exist yet, deepest first.

    make_workdir creates them; a file that then fails removes them again with
    `prune_folders`, so a failure under --output-dir leaves no empty mirror folder.
    """
    missing = []
    for folder in (dest_dir, *dest_dir.parents):
        if os.path.lexists(folder):
            break
        missing.append(folder)
    return tuple(missing)


def prune_folders(folders: tuple[Path, ...]) -> None:
    """Remove each of *folders*, deepest first, while it is empty.

    os.rmdir removes only an empty folder, so one that holds anything (an ours .lrc
    placed before a later failure) stays, and so does every folder above it.
    """
    for folder in folders:
        try:
            folder.rmdir()
        except OSError:
            return


def make_workdir(dest_dir: Path) -> Path:
    """A temporary folder inside the destination, so each rename stays on one filesystem."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=dest_dir))


def place_pair(workdir: Path, lrc_text: str, planned: Planned) -> None:
    """Stage the labeled .lrc, then rename the .lrc and then the .txt into place."""
    staged = workdir / STAGED_LRC
    staged.write_text(lrc_text, encoding="utf-8", newline="\n")
    os.replace(staged, planned.lrc)
    os.replace(workdir / f"{ENGINE_STEM}{TXT_SUFFIX}", planned.txt)


def remove_workdir(workdir: Path) -> None:
    """Remove the file's temporary folder and whatever the engine left in it."""
    shutil.rmtree(workdir)
