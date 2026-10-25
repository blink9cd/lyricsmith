"""The folder walk, what each entry it finds is, and the two output locations.

`find_audio` takes a named file as is and walks a folder for matching names;
`classify` says whether one is audio to transcribe, a link the walk skips, or not a
regular file at all; `output_paths` places the .lrc and .txt beside the audio or
mirrors them under an output folder. Nothing here reads a file's contents or writes
anything.
"""

from __future__ import annotations

import enum
import os
import stat
from pathlib import Path

from at_types import LRC_SUFFIX, TXT_SUFFIX


def walk_root(path: Path) -> Path:
    """The folder rows and mirrored outputs are relative to: --path, or a file's folder."""
    return path if path.is_dir() else path.parent


def find_audio(
    path: Path, extensions: frozenset[str], errors: list[OSError] | None = None
) -> list[Path]:
    """A named file as is; else every matching file below path, sorted.

    Names starting with "." are skipped, files and folders alike: hidden files, macOS
    "._" companions and this tool's own temporary folders. `os.walk(followlinks=False)`
    lists a symlinked folder in `dirs` but never descends into it, which is the whole
    of the rule that symlinked folders are not followed. Every other matching name is
    listed, a symlinked file or a FIFO among them: `classify` judges each, so none is
    dropped unreported. Extensions match case-insensitively, without the dot.

    A folder the walk cannot read is appended to *errors* when a list is given, so the
    caller can report it rather than drop it silently (the bulk rule).
    """
    if not path.is_dir():
        return [path]
    found: list[Path] = []
    onerror = None if errors is None else errors.append
    for folder, dirs, files in os.walk(path, onerror=onerror, followlinks=False):
        # Pruning in place is how os.walk is told not to descend into hidden folders.
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        found.extend(
            Path(folder) / name
            for name in files
            if not name.startswith(".")
            and Path(name).suffix.lower().lstrip(".") in extensions
        )
    return sorted(found)


class Entry(enum.Enum):
    """What one found name is, so each outcome is named and none is dropped."""

    AUDIO = "audio"  # a regular file: transcribe it
    LINKED = "linked"  # a symlink the walk found: skipped and reported
    NOT_REGULAR = "not a regular file"  # a FIFO, socket or device: never opened


def classify(path: Path, *, named: bool) -> Entry:
    """Whether *path* is audio, a link the walk skips, or not a regular file.

    A link the walk found is skipped, as lyricsmith album skips a linked MP3, whatever
    it points at, a missing target included. A file *named* by --path is the
    operator's choice, so its link is followed. Otherwise only a regular file, judged
    by `os.stat` (which follows a link), is audio: whisper-cli would block for its
    whole bound on a FIFO and read a device without end. An entry that cannot be
    stat'ed raises OSError, for the caller to report.
    """
    if not named and path.is_symlink():
        return Entry.LINKED
    return Entry.AUDIO if stat.S_ISREG(os.stat(path).st_mode) else Entry.NOT_REGULAR


def output_paths(audio: Path, root: Path, output_dir: Path | None) -> tuple[Path, Path]:
    """The .lrc and .txt locations: beside the audio, or mirrored under output_dir."""
    base = audio if output_dir is None else output_dir / audio.relative_to(root)
    return base.with_suffix(LRC_SUFFIX), base.with_suffix(TXT_SUFFIX)
