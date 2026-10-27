"""The .lrc this tool writes, and the atomic write beside a track.

The stamps are album's own format (`format_lrc`), so album reads the file back as written.
The header line ``[by:machine timed]`` marks the file as this tool's. This tool reads that
label only through album (`machine_label`, by way of `plan_sidecars`), so the two never
disagree: any edit to the line makes the file a person's, and this tool never re-times it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
from lyrics_files import replace_atomically
from lyrics_model import SyncedLine, format_lrc
from stdio_utf8 import spell_unsafe
from sy_types import LRC_SUFFIX, MACHINE_BY_LINE, TOOL_LABEL, Label

MS_PER_SECOND: Final = 1000


def re_line(label: Label) -> str:
    """The [re:] line naming this tool's, demucs's and torchaudio's versions.

    Each version is escaped, as the transcribe tool escapes its label's: each is read
    at run time and may hold a line break, which would split the header and put a line
    of the value's before the by-line, so album would no longer read the file as ours.
    """
    tool, demucs, torchaudio = (
        spell_unsafe(version)
        for version in (label.tool, label.demucs, label.torchaudio)
    )
    return (
        f"[re:{TOOL_LABEL} {tool}; demucs {demucs} htdemucs; "
        f"torchaudio {torchaudio} WAV2VEC2_ASR_BASE_960H]"
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
