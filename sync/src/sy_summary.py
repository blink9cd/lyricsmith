"""The summary: one row per track, the counts, and the commands and notes the footer gives.

Pure: it builds lines; the modes print them.
"""

from __future__ import annotations

import shlex
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from sy_types import (
    EXIT_FAILED,
    EXIT_OK,
    FAILED,
    LRC_SUFFIX,
    WRITTEN,
    Action,
    EmbedResult,
    EmbedStop,
    Outcome,
    Planned,
    TrackResult,
)

LABEL_GAP: Final = 2
DETAIL_INDENT: Final = "    "
#: The widest label any row can carry, so the paths line up.
LABEL_WIDTH: Final = max(len(label.value) for label in (*Action, *Outcome))
#: The album command that embeds a folder's new .lrc files, as a person runs it.
ALBUM_COMMAND: Final = "lyricsmith album --offline --album"
FIRST_LINE_FIX: Final = (
    "A flagged first line can start early, stretched back over the intro: edit its stamp"
    " in the .lrc, keep the [by:machine timed] line, then embed the corrected time with"
    f" {ALBUM_COMMAND} <the album folder> (in a library, the library's Artist/Album"
    " folder); for a track in no album folder, add --track <the track>. A second sync"
    " run does not embed it."
)
BY_HAND: Final = (
    "A low-confidence track gets no .lrc: time it by hand in a tap-along LRC editor run"
    " locally, such as lrc-maker."
)


def _shown(audio: Path, root: Path) -> str:
    """*audio* relative to *root*, or its name when it is *root* itself or outside it."""
    if audio != root and audio.is_relative_to(root):
        return str(audio.relative_to(root))
    return audio.name


def _row(label: str, audio: Path, root: Path) -> str:
    """One row: the label padded to the shared width, then the path."""
    return f"{label:<{LABEL_WIDTH + LABEL_GAP}}{_shown(audio, root)}"


def plan_line(planned: Planned, root: Path) -> str:
    """The dry run's row for *planned*, its refusal's detail beneath it."""
    row = _row(planned.action.value, planned.audio, root)
    return f"{row}\n{DETAIL_INDENT}{planned.detail}" if planned.detail else row


def result_lines(result: TrackResult, root: Path) -> list[str]:
    """*result*'s row, then its detail, the first-line flag and the unaligned count."""
    lines = [_row(result.outcome.value, result.audio, root)]
    lines += [f"{DETAIL_INDENT}{detail}" for detail in result.detail]
    if result.first_line is not None:
        name = result.audio.with_suffix(LRC_SUFFIX).name
        lines.append(f"{DETAIL_INDENT}check the first line: {name} {result.first_line}")
    if result.unaligned:
        lines.append(
            f"{DETAIL_INDENT}{result.unaligned} line(s) with nothing to align"
            " took the next line's stamp"
        )
    return lines


def count_line(results: Sequence[TrackResult]) -> str:
    """Each outcome seen and its count, in the order outcomes are declared."""
    counts = Counter(result.outcome for result in results)
    seen = [
        f"{outcome.value} {counts[outcome]}" for outcome in Outcome if counts[outcome]
    ]
    return ", ".join(seen) or "nothing to time"


def written_tracks(results: Sequence[TrackResult]) -> tuple[Path, ...]:
    """Each track that gained or changed a .lrc, in the order the run took them."""
    return tuple(result.audio for result in results if result.outcome in WRITTEN)


def album_command(folder: Path, track: Path | None = None) -> str:
    """The album command for *folder*, or for one *track* in it, quoted for a shell.

    Both paths are absolute, so the command runs from any folder: album reads --track
    against the working directory.
    """
    command = f"{ALBUM_COMMAND} {shlex.quote(str(folder.absolute()))}"
    if track is None:
        return command
    return f"{command} --track {shlex.quote(str(track.absolute()))}"


def embed_lines(embeds: Sequence[EmbedResult]) -> list[str]:
    """One line per --embed album run: done, or not done with the command to run."""
    lines = []
    for embed in embeds:
        if embed.ok:
            lines.append(f"embedded: {embed.folder}")
            continue
        why = (
            embed.code.value
            if isinstance(embed.code, EmbedStop)
            else f"album exit {embed.code}"
        )
        named = embed.folder if embed.track is None else embed.track
        command = album_command(embed.folder, embed.track)
        lines.append(f"embed not done ({why}): {named}; run: {command}")
    return lines


def footer_lines(
    results: Sequence[TrackResult],
    *,
    units: Sequence[Path] = (),
    loose: Sequence[Path] = (),
) -> list[str]:
    """The counts; album's command for each unit and loose track; the fix and the note."""
    lines = [count_line(results)]
    lines += [f"embed them: {album_command(folder)}" for folder in units]
    lines += [f"embed it: {album_command(track.parent, track)}" for track in loose]
    if any(result.first_line is not None for result in results):
        lines.append(FIRST_LINE_FIX)
    if any(result.outcome is Outcome.LOW_CONFIDENCE for result in results):
        lines.append(BY_HAND)
    return lines


def exit_code(results: Sequence[TrackResult], embeds: Sequence[EmbedResult]) -> int:
    """1 when any track failed or any --embed album run did not exit 0; else 0."""
    failed = any(result.outcome in FAILED for result in results)
    return EXIT_FAILED if failed or not all(embed.ok for embed in embeds) else EXIT_OK
