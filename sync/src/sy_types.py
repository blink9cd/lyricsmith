"""Shared names for the sync tool: labels, actions, outcomes, records and exit codes.

Every other module here imports from this one. Pure: nothing here reads or writes a file.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

#: The header line that marks a .lrc as this tool's own. This tool only writes it: album's
#: `machine_label`, which compares it stripped at both ends, is its one reader.
MACHINE_BY_LINE: Final = "[by:machine timed]"
#: The tool's name as its [re:] line gives it.
TOOL_LABEL: Final = "lyricsmith sync"
#: The prefix of the temporary folder each track's child process works in.
TEMP_PREFIX: Final = ".lyricsmith-"
LRC_SUFFIX: Final = ".lrc"
TXT_SUFFIX: Final = ".txt"
#: The wav2vec2 weights, under the name torchaudio's loader looks for in a model folder.
WAV2VEC2_FILE: Final = "wav2vec2_fairseq_base_ls960_asr_ls960.pth"
#: The htdemucs bag: a YAML naming its one model's signature, and that model's weights.
DEMUCS_BAG: Final = "htdemucs.yaml"
DEMUCS_WEIGHTS: Final = "955717e8.safetensors"

EXIT_OK, EXIT_FAILED, EXIT_SETUP = 0, 1, 2


class SetupError(Exception):
    """A problem found before any track is touched; the run exits 2."""


class Action(enum.Enum):
    """What the plan does with one track; the dry run prints the value."""

    TIME = "time"
    RETIME = "re-time"
    SKIP_TIMED = "skip: already timed"
    SKIP_NO_TEXT = "skip: no untimed text"
    SKIP_UNTIMED_LRC = "skip: untimed .lrc (run album first)"
    ERROR_TEXT = "ERROR text"


class Outcome(enum.Enum):
    """How one track ended; its summary row prints the value."""

    TIMED = "timed"
    RETIMED = "re-timed"
    SKIP_TIMED = Action.SKIP_TIMED.value
    SKIP_NO_TEXT = Action.SKIP_NO_TEXT.value
    SKIP_UNTIMED_LRC = Action.SKIP_UNTIMED_LRC.value
    TOO_LONG = "skip: text longer than the track"
    LOW_CONFIDENCE = "low confidence: time by hand"
    ERROR_TEXT = Action.ERROR_TEXT.value
    ERROR_ALIGN = "ERROR align"
    ERROR_WRITE = "ERROR write"


#: Each planned action that times nothing, and the outcome it ends as.
UNTIMED_OUTCOMES: Final[Mapping[Action, Outcome]] = MappingProxyType(
    {
        Action.SKIP_TIMED: Outcome.SKIP_TIMED,
        Action.SKIP_NO_TEXT: Outcome.SKIP_NO_TEXT,
        Action.SKIP_UNTIMED_LRC: Outcome.SKIP_UNTIMED_LRC,
        Action.ERROR_TEXT: Outcome.ERROR_TEXT,
    }
)
#: The outcomes that wrote a .lrc.
WRITTEN: Final = frozenset({Outcome.TIMED, Outcome.RETIMED})
#: The outcomes that make the run exit 1. A low-confidence track is a planned outcome.
FAILED: Final = frozenset(
    {Outcome.ERROR_TEXT, Outcome.ERROR_ALIGN, Outcome.ERROR_WRITE}
)


@dataclass(frozen=True)
class Planned:
    """One track's planned action, the lines it would time, and why it is refused."""

    audio: Path
    action: Action
    lines: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class TrackResult:
    """How one track ended, with what its row says beneath it."""

    audio: Path
    outcome: Outcome
    detail: tuple[str, ...] = ()
    #: The first line's stamp, when that line is stretched and should be checked by ear.
    first_line: str | None = None
    #: How many lines had nothing to align and took a neighboring line's stamp.
    unaligned: int = 0


@dataclass(frozen=True)
class Options:
    """The parsed command line."""

    mode: str
    path: Path | None
    extensions: frozenset[str]
    embed: bool
    overwrite: bool
    dry_run: bool
    model_dir: Path


@dataclass(frozen=True)
class Label:
    """The versions the [re:] line names, read at run time."""

    tool: str
    demucs: str
    torchaudio: str


class EmbedStop(enum.Enum):
    """Why an --embed folder has no album exit code to go by; its line prints the value."""

    NOT_STARTED = "did not start"
    TIMED_OUT = "timed out"
    INTERRUPTED = "interrupted"
    NOT_REAPED = "would not stop when killed, and may still be running"
    NOT_RUN = "not run: an earlier album run was interrupted"
    LOOSE = "in a library but not in an Artist/Album folder"


@dataclass(frozen=True)
class EmbedResult:
    """One folder's album run under --embed: its exit code, or why it has none to trust.

    A run that timed out or was interrupted is not done whatever it exited with: album
    stopped part way through the folder.
    """

    folder: Path
    code: int | EmbedStop
    #: The one track to embed, when the folder holds others no run here may take.
    track: Path | None = None

    @property
    def ok(self) -> bool:
        """True when the album run finished by itself and exited 0."""
        return self.code == EXIT_OK
