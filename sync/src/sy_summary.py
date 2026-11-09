"""The summary: one row per track, the counts, and the commands and notes the footer gives.

Pure: it builds lines; the modes print them.
"""

from __future__ import annotations

import shlex
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import sy_located  # noqa: F401 - puts album's modules on sys.path
from lyrics_heard import MIN_HEARD_PAIRS
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
#: The album options that embed a folder's new .lrc files, after the album tool's words.
EMBED_OPTIONS: Final = ("--offline", "--album")
BY_HAND: Final = (
    "A low-confidence track gets no .lrc: time it by hand in a tap-along LRC editor run"
    " locally, such as lrc-maker."
)
NOT_SUNG: Final = (
    "A text not sung gets no .lrc: whisper-cli heard the vocals, and too few of the"
    " word pairs it heard are the text's, so the .txt may hold another song's lyrics."
    " Check it against the track and correct it; under --by-hand-list, remove the"
    " track's row to time it again."
)


def shown_path(audio: Path, root: Path) -> str:
    """*audio* relative to *root*, or its name when it is *root* itself or outside it.

    The relative path reads with ``/`` on every host, as the album tool's manifests
    name a track, so a row reads the same from a Windows run as from any other.
    """
    if audio != root and audio.is_relative_to(root):
        return audio.relative_to(root).as_posix()
    return audio.name


def _row(label: str, audio: Path, root: Path) -> str:
    """One row: the label padded to the shared width, then the path."""
    return f"{label:<{LABEL_WIDTH + LABEL_GAP}}{shown_path(audio, root)}"


def plan_lines(planned: Planned, root: Path) -> list[str]:
    """The dry run's row for *planned*, then its refusal's detail as its own line.

    Each is printed alone: a line break inside one printed line is spelled as an escape.
    """
    row = _row(planned.action.value, planned.audio, root)
    return [row, f"{DETAIL_INDENT}{planned.detail}"] if planned.detail else [row]


def result_lines(result: TrackResult, root: Path) -> list[str]:
    """*result*'s row, then its detail, then each note: first line, unaligned, little heard."""
    lines = [_row(result.outcome.value, result.audio, root)]
    lines += [f"{DETAIL_INDENT}{detail}" for detail in result.detail]
    if result.first_line is not None:
        name = result.audio.with_suffix(LRC_SUFFIX).name
        lines.append(f"{DETAIL_INDENT}check the first line: {name} {result.first_line}")
    if result.unaligned:
        lines.append(
            f"{DETAIL_INDENT}{result.unaligned} line(s) with nothing to align"
            " took the next line's stamp, or after the last sung line its end"
        )
    if result.little_heard is not None:
        lines.append(
            f"{DETAIL_INDENT}only {result.little_heard} word pairs heard, under"
            f" {MIN_HEARD_PAIRS}: its text was not checked against the vocals"
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


def _embed_words(album: Sequence[str]) -> str:
    """The album tool named by *album*'s words, then the embedding options, quoted."""
    return shlex.join([*album, *EMBED_OPTIONS])


def first_line_fix(album: Sequence[str]) -> str:
    """The note on a flagged first line, naming the album tool by *album*'s words."""
    return (
        "A flagged first line can start early, stretched back over the intro: edit its"
        " stamp in the .lrc, keep the [by:machine timed] line, then embed the corrected"
        f" time with {_embed_words(album)} <the album folder> (in a library, the"
        " library's Artist/Album folder); for a track in no album folder, add --track"
        " <the track>. A second sync run does not embed it."
    )


def album_command(album: Sequence[str], folder: Path, track: Path | None = None) -> str:
    """The album command for *folder*, or for one *track* in it, quoted for a shell.

    *album* is the album tool's words as this run names it: ``lyricsmith album`` when
    dispatched, its entry file's name when run alone. Both paths are absolute, so the
    command runs from any folder: album reads --track against the working directory.
    """
    command = f"{_embed_words(album)} {shlex.quote(str(folder.absolute()))}"
    if track is None:
        return command
    return f"{command} --track {shlex.quote(str(track.absolute()))}"


def embed_lines(embeds: Sequence[EmbedResult], album: Sequence[str]) -> list[str]:
    """One line per --embed album run: done, or not done with the command to run.

    *album* names the album tool in that command (`album_command`).
    """
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
        command = album_command(album, embed.folder, embed.track)
        lines.append(f"embed not done ({why}): {named}; run: {command}")
    return lines


def footer_lines(
    results: Sequence[TrackResult],
    *,
    album: Sequence[str],
    units: Sequence[Path] = (),
    loose: Sequence[Path] = (),
    sidecars: Sequence[Path] = (),
) -> list[str]:
    """The counts; album's command for each unit and loose track; the notes.

    A written track album cannot embed into, being no MP3, is named with its .lrc.
    *album* names the album tool in each command (`album_command`).
    """
    lines = [count_line(results)]
    lines += [f"embed them: {album_command(album, folder)}" for folder in units]
    lines += [
        f"embed it: {album_command(album, track.parent, track)}" for track in loose
    ]
    lines += [
        f"stays a sidecar: {track.with_suffix(LRC_SUFFIX)} (album embeds MP3s only)"
        for track in sidecars
    ]
    if any(result.first_line is not None for result in results):
        lines.append(first_line_fix(album))
    if any(result.outcome is Outcome.LOW_CONFIDENCE for result in results):
        lines.append(BY_HAND)
    if any(result.outcome is Outcome.NOT_SUNG for result in results):
        lines.append(NOT_SUNG)
    return lines


def by_hand_lines(added: int, target: Path | None) -> list[str]:
    """The footer's line naming the --by-hand-list file, when this run added rows to it."""
    if not added or target is None:
        return []
    return [f"listed for timing by hand: {added} row(s) added to {target}"]


def interrupted_line(left: int) -> str:
    """The line a Ctrl-C during timing prints: how many tracks it left untimed."""
    return (
        f"interrupted: timing stopped with {left} track(s) left, and nothing was"
        " embedded; a second run times the rest"
    )


def exit_code(results: Sequence[TrackResult], embeds: Sequence[EmbedResult]) -> int:
    """1 when any track failed or any --embed album run did not exit 0; else 0."""
    failed = any(result.outcome in FAILED for result in results)
    return EXIT_FAILED if failed or not all(embed.ok for embed in embeds) else EXIT_OK
