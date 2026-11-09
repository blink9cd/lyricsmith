"""transcribe's shared names: label constants, row types, options, line and path helpers.

Every module of the tool takes its labels, suffixes and exit codes from here,
so the parallel parts of the tool agree on one spelling of each; this module imports
none of the others.
Nothing here reads a file, runs a process or prints.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Final

#: The second header line of every .lrc this tool writes, and the mark of "ours".
MACHINE_BY_LINE = "[by:machine transcript]"

#: A timed .lrc line: [M:SS], [MM:SS.C], up to [MMM:SS.CCC].
TIMESTAMP_LINE = re.compile(r"^\[\d{1,3}:\d{2}(?:\.\d{1,3})?\]")

LRC_SUFFIX = ".lrc"
TXT_SUFFIX = ".txt"

#: The program named in the [re:] line.
TOOL_LABEL: Final = "lyricsmith transcribe"
#: The name prefix of the per-file temporary folder; the walk skips it as hidden.
TEMP_PREFIX: Final = ".lyricsmith-"

#: whisper-cli -of <workdir>/out writes out.lrc and out.txt.
ENGINE_STEM = "out"

EXIT_OK, EXIT_FILE_FAILED, EXIT_SETUP = 0, 1, 2


class SetupError(Exception):
    """A problem found before any file is touched; the run exits 2."""


class Action(enum.Enum):
    """What the plan will do with one audio file, as its row prints it."""

    TRANSCRIBE = "transcribe"
    OVERWRITE = "overwrite (ours)"
    SKIPPED = "skipped (has sidecar)"
    REFUSED = "refused (not ours)"


class Outcome(enum.Enum):
    """What happened to one audio file, as its result row prints it."""

    TRANSCRIBED = "transcribed"
    OVERWRITTEN = "overwritten"
    SKIPPED = "skipped (has sidecar)"
    REFUSED = "refused (not ours)"
    FAILED = "failed"
    # Last, so the count line's earlier words keep their places.
    LINKED = "skipped (linked)"


@dataclass(frozen=True)
class Planned:
    """One audio file, the two files it would get, and the planned action."""

    audio: Path
    lrc: Path
    txt: Path
    action: Action


@dataclass(frozen=True)
class FileResult:
    """One audio file's outcome, with the lines explaining a failure."""

    audio: Path
    outcome: Outcome
    detail: tuple[str, ...] = ()


@dataclass(frozen=True)
class Options:
    """The validated options a mode runs with.

    Built by the entry point's option layer; here so the modes, in their own module,
    can read it without importing the entry point.
    """

    mode: str  # "transcribe" or "fetch-models"
    path: Path | None
    extensions: frozenset[str]
    output_dir: Path | None
    overwrite: bool
    dry_run: bool
    language: str
    model: Path
    vad_model: Path
    model_dir: Path
    whisper_cli: Path | None  # None: find it on PATH
    #: The pinned whisper model whose run's files fetch-models fetches.
    whisper_model: str


@dataclass(frozen=True)
class Label:
    """The values the .lrc's [re:] line names, each read at run time."""

    tool_version: str
    engine_version: str
    model_name: str


def split_lines(text: str) -> list[str]:
    """Split on LF (and CRLF) only, dropping the empty string after a final newline.

    str.splitlines also splits on characters a lyric line may hold (U+2028, form
    feed), which would move them into a line of their own.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    return lines[:-1] if lines and lines[-1] == "" else lines


def has_timestamp(text: str) -> bool:
    """Whether any line of a .lrc starts with a [MM:SS.CC] timestamp."""
    return any(TIMESTAMP_LINE.match(line) for line in split_lines(text))


def shown_path(audio: PurePath, root: PurePath) -> str:
    """The path relative to the walk root, or whole when it is not under it.

    A relative path reads with ``/`` on every host, as the album tool's manifests name
    a track; a whole one stays as the host names it, so it can be pasted back.
    """
    if audio.is_relative_to(root):
        return audio.relative_to(root).as_posix()
    return str(audio)
