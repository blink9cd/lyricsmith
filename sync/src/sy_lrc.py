"""The .lrc this tool writes, and the atomic write beside a track.

The stamps are album's own format (`format_lrc`), so album reads the file back as written.
The header line ``[by:machine timed]`` marks the file as this tool's. Only album reads
that label (`machine_label`, through `plan_sidecars`), for both tools, so they never
disagree: any edit to the line makes the file a person's, and this tool never re-times it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

from lyrics_files import replace_atomically
from lyrics_model import SyncedLine, format_lrc
from sy_types import LRC_SUFFIX, MACHINE_BY_LINE, TOOL_LABEL, Label

MS_PER_SECOND: Final = 1000


def re_line(label: Label) -> str:
    """The [re:] line naming this tool's, demucs's and torchaudio's versions."""
    return (
        f"[re:{TOOL_LABEL} {label.tool}; demucs {label.demucs} htdemucs; "
        f"torchaudio {label.torchaudio} WAV2VEC2_ASR_BASE_960H]"
    )


def _ms(seconds: float) -> int:
    """*seconds* as whole milliseconds."""
    return round(seconds * MS_PER_SECOND)


def lrc_text(label: Label, lines: Sequence[str], starts: Sequence[float]) -> str:
    """The labeled .lrc: the two header lines, then each line at its start."""
    timed = [
        SyncedLine(_ms(start), line) for line, start in zip(lines, starts, strict=True)
    ]
    return f"{re_line(label)}\n{MACHINE_BY_LINE}\n{format_lrc(timed)}"


def stamp(seconds: float) -> str:
    """*seconds* as the stamp the .lrc gives it, such as ``[01:01.235]``."""
    return format_lrc([SyncedLine(_ms(seconds), "")]).rstrip("\n")


def write_lrc(audio: Path, text: str) -> None:
    """Replace *audio*'s .lrc with *text*, atomically: album's fsynced temp and replace."""
    replace_atomically(audio.with_suffix(LRC_SUFFIX), text.encode("utf-8"))
