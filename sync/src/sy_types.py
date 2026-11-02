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

#: The header line that marks a .lrc as this tool's own. This tool only writes it and reads
#: it back only through album's `machine_label`, which compares it stripped at both ends.
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
#: wav2vec2's sample rate, mono: the rate the vocal stem is scored and heard at.
MODEL_RATE: Final = 16000
#: The longest track timed, in seconds. The child holds the whole decoded track in memory,
#: about 705 KB a second as 32-bit stereo and twice that while separating, so a longer
#: track is skipped before any child starts, and ffmpeg decodes no more than this. It is
#: also the length the child's bound assumes when a track's own cannot be read.
MAX_TRACK_S: Final = 3600
#: whisper-cli's bound in the child, a minute plus half the track's decoded length. It
#: ran a median 7.8 s a track on the test library, about 0.036 of the track's length,
#: model load included: half the length leaves over ten times that. The parent's bound
#: on the whole child adds the same on top of its own.
WHISPER_BASE_S: Final = 60.0
WHISPER_PER_SECOND: Final = 0.5
#: --extensions when none is given: comma-separated, as the option takes them.
DEFAULT_EXTENSIONS: Final = "mp3"
#: Each extension --extensions may name, and the ffmpeg demuxer that reads it. ffmpeg
#: opens a track only with one of these, whatever its bytes say: probed freely, a track
#: holding an ffconcat or HLS playlist would open the files that playlist names. So no
#: demuxer that reads another file (concat, hls, image2, any playlist) is ever listed.
DEMUXERS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "aac": "aac",
        "aif": "aiff",
        "aifc": "aiff",
        "aiff": "aiff",
        "flac": "flac",
        "m4a": "mov",
        "m4b": "mov",
        "mka": "matroska",
        "mp2": "mp3",
        "mp3": "mp3",
        "oga": "ogg",
        "ogg": "ogg",
        "opus": "ogg",
        "wav": "wav",
        "wma": "asf",
        "wv": "wv",
    }
)

EXIT_OK, EXIT_FAILED, EXIT_SETUP = 0, 1, 2
#: A Ctrl-C while timing: the shell's code for a run stopped by SIGINT.
EXIT_INTERRUPTED: Final = 130


class SetupError(Exception):
    """A problem found before any track is touched; the run exits 2."""


class Action(enum.Enum):
    """What the plan does with one track; the dry run prints the value."""

    TIME = "time"
    RETIME = "re-time"
    SKIP_TIMED = "skip: already timed"
    SKIP_NO_TEXT = "skip: no untimed text"
    SKIP_UNTIMED_LRC = "skip: untimed .lrc (run album first)"
    SKIP_LINKED = "skip: linked"
    SKIP_BY_HAND = "skip: listed for timing by hand"
    SKIP_NOT_SUNG = "skip: listed as not sung"
    ERROR_TEXT = "ERROR text"
    ERROR_AUDIO = "ERROR audio"


class Outcome(enum.Enum):
    """How one track ended; its summary row prints the value."""

    TIMED = "timed"
    RETIMED = "re-timed"
    SKIP_TIMED = Action.SKIP_TIMED.value
    SKIP_NO_TEXT = Action.SKIP_NO_TEXT.value
    SKIP_UNTIMED_LRC = Action.SKIP_UNTIMED_LRC.value
    SKIP_LINKED = Action.SKIP_LINKED.value
    SKIP_BY_HAND = Action.SKIP_BY_HAND.value
    SKIP_NOT_SUNG = Action.SKIP_NOT_SUNG.value
    SKIP_LONG_TRACK = "skip: track too long"
    TOO_LONG = "skip: text longer than the track"
    LOW_CONFIDENCE = "low confidence: time by hand"
    #: whisper-cli heard the vocals, and too little of what it heard is the text.
    NOT_SUNG = "not sung: left untimed"
    ERROR_TEXT = Action.ERROR_TEXT.value
    ERROR_AUDIO = Action.ERROR_AUDIO.value
    ERROR_ALIGN = "ERROR align"
    ERROR_WRITE = "ERROR write"


#: Each planned action that times nothing, and the outcome it ends as.
UNTIMED_OUTCOMES: Final[Mapping[Action, Outcome]] = MappingProxyType(
    {
        Action.SKIP_TIMED: Outcome.SKIP_TIMED,
        Action.SKIP_NO_TEXT: Outcome.SKIP_NO_TEXT,
        Action.SKIP_UNTIMED_LRC: Outcome.SKIP_UNTIMED_LRC,
        Action.SKIP_LINKED: Outcome.SKIP_LINKED,
        Action.SKIP_BY_HAND: Outcome.SKIP_BY_HAND,
        Action.SKIP_NOT_SUNG: Outcome.SKIP_NOT_SUNG,
        Action.ERROR_TEXT: Outcome.ERROR_TEXT,
        Action.ERROR_AUDIO: Outcome.ERROR_AUDIO,
    }
)
#: The outcomes that wrote a .lrc.
WRITTEN: Final = frozenset({Outcome.TIMED, Outcome.RETIMED})
#: The outcomes that make the run exit 1. A low-confidence track is a planned outcome,
#: and so is a text not sung.
FAILED: Final = frozenset(
    {Outcome.ERROR_TEXT, Outcome.ERROR_AUDIO, Outcome.ERROR_ALIGN, Outcome.ERROR_WRITE}
)


@dataclass(frozen=True)
class Planned:
    """One track's planned action, the lines it would time, and why it is refused."""

    audio: Path
    action: Action
    lines: tuple[str, ...] = ()
    detail: str = ""
    #: What whisper-cli heard of the track when its earlier text was found not sung,
    #: kept so a changed text is judged against it rather than heard again.
    transcript: str | None = None


@dataclass(frozen=True)
class TrackResult:
    """How one track ended, with what its row says beneath it."""

    audio: Path
    outcome: Outcome
    detail: tuple[str, ...] = ()
    #: The first line's stamp, when that line is stretched and should be checked by ear.
    first_line: str | None = None
    #: How many non-blank lines had nothing to align and took a neighboring line's stamp.
    unaligned: int = 0
    #: A low-confidence track's median line confidence, as its row prints it.
    confidence: str | None = None
    #: A text not sung: the share of what was heard that is the text's, as printed.
    share: str | None = None
    #: The distinct word pairs whisper-cli heard, when too few to judge the text.
    little_heard: int | None = None
    #: A text not sung: what whisper-cli heard, which --heard-dir keeps.
    transcript: str | None = None


@dataclass(frozen=True)
class Whisper:
    """What the child hears a track's vocals with: whisper-cli, and its models' folder."""

    cli: Path
    model_dir: Path


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
    #: The folder holding the transcribe tool's whisper models, absolute.
    whisper_model_dir: Path
    #: The --by-hand-list file, absolute; None when the option is not given.
    by_hand_list: Path | None = None
    #: The --heard-dir folder, absolute; None when the option is not given.
    heard_dir: Path | None = None


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
