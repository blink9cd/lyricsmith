"""A track's text and what timing it had, read from its sidecars by album's own rules.

album's `plan_sidecars` decides what a track's sidecars hold, as `sy_plan` reads them,
so this tool and album never disagree about a track. A timed text with no label is a
provider's (or a person's: the two cannot be told apart, and both are heard as one),
one carrying this tool's ``[by:machine timed]`` line is machine-timed, and an untimed
text, from a ``.txt`` or an untimed ``.lrc`` album has yet to rename, is untimed. A
track whose sidecars hold no text has no kind: any lyrics it has are in its frames,
which this tool never reads. Nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
from lyrics_hearing_log import HeardKind
from lyrics_model import PlainLyrics, Source, SyncedLyrics
from lyrics_sidecar import plan_sidecars
from sy_text import text_lines


@dataclass(frozen=True)
class KindedText:
    """A track's text, its lines as a timing run reads them, and what timing it had."""

    kind: HeardKind
    lines: tuple[str, ...]


def text_kind(audio: Path) -> KindedText | None:
    """*audio*'s text and its kind; None when its sidecars hold none.

    A sidecar that cannot be read raises as `plan_sidecars` raises: one of
    `lyrics_sidecar.UNREADABLE`, or the `OSError` of one that cannot be opened.
    """
    state = plan_sidecars(audio, audio.parent)
    lyrics = state.lyrics
    if isinstance(lyrics, SyncedLyrics):
        machine = state.label is Source.MACHINE_TIMED
        kind = HeardKind.MACHINE_TIMED if machine else HeardKind.PROVIDER_TIMED
        text = "\n".join(line.text for line in lyrics.lines)
        return KindedText(kind, text_lines(text))
    if isinstance(lyrics, PlainLyrics):
        return KindedText(HeardKind.UNTIMED, text_lines(lyrics.text))
    return None
