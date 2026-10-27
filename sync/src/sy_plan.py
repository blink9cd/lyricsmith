"""Each track's planned action, read from its sidecars by album's own rules.

album's `plan_sidecars` decides what a track's sidecars hold: the same names and
suffixes, a link followed only inside the track's folder, the same decode and size cap.
So this tool and album never disagree about a track. Two tracks sharing one .lrc are
caught here too: the first keeps its plan, each later one is refused. Nothing here writes.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
from lyrics_model import PlainLyrics, Source, SyncedLyrics
from lyrics_sidecar import UNREADABLE, plan_sidecars
from lyrics_slots import Slot, sidecar_entry
from sy_text import text_lines, tokenize
from sy_types import LRC_SUFFIX, TXT_SUFFIX, Action, Planned

#: A walked name's rows that are no track: a skipped link, or not a regular file.
UNCLAIMED = frozenset({Action.SKIP_LINKED, Action.ERROR_AUDIO})


def plan_track(audio: Path, *, overwrite: bool) -> Planned:
    """*audio*'s action: time it, re-time it, skip it, or refuse its unreadable text."""
    try:
        state = plan_sidecars(audio, audio.parent)
    except UNREADABLE as error:
        return Planned(audio, Action.ERROR_TEXT, detail=str(error))
    except OSError as error:
        return Planned(audio, Action.ERROR_TEXT, detail=_open_error(audio, error))
    lyrics = state.lyrics
    if isinstance(lyrics, SyncedLyrics):
        ours = state.label is Source.MACHINE_TIMED and _lrc_is_a_file(audio)
        return _plan_timed(audio, lyrics, ours=ours, overwrite=overwrite)
    if isinstance(lyrics, PlainLyrics):
        return _plan_untimed(audio, lyrics, renamed=bool(state.renamed))
    return Planned(audio, Action.SKIP_NO_TEXT)


def _open_error(audio: Path, error: OSError) -> str:
    """A sidecar that exists but cannot be opened: its name, then the system's reason.

    album's decode errors name the file themselves; an OSError names it only in
    *filename*, and when it carries none the track's .txt is the sidecar meant.
    """
    named = (
        Path(str(error.filename)) if error.filename else audio.with_suffix(TXT_SUFFIX)
    )
    return f"{named.name}: {error.strerror}"


def _lrc_is_a_file(audio: Path) -> bool:
    """True when *audio*'s .lrc name holds a file itself, not a link to one.

    album reads a link through to a file inside the folder, labeled or not, but a re-time
    writes over the name it reads, so a link would be replaced: it is left alone.
    """
    return sidecar_entry(audio.with_suffix(LRC_SUFFIX)) is Slot.FILE


def _plan_timed(
    audio: Path, lyrics: SyncedLyrics, *, ours: bool, overwrite: bool
) -> Planned:
    """A timed .lrc: re-timed from its own lines when it is *ours* and asked; else skipped.

    *ours* is album's own reading of the label, so the two tools never disagree on it.
    album removes the .txt once a timed .lrc is embedded, so a re-time takes its words
    from the .lrc.
    """
    if overwrite and ours:
        lines = text_lines("\n".join(line.text for line in lyrics.lines))
        if not tokenize(lines).ids:
            return Planned(audio, Action.SKIP_NO_TEXT)
        return Planned(audio, Action.RETIME, lines=lines)
    return Planned(audio, Action.SKIP_TIMED)


def _plan_untimed(audio: Path, lyrics: PlainLyrics, *, renamed: bool) -> Planned:
    """Untimed text: timed from a .txt; an untimed .lrc is left for album to rename.

    A link at the .lrc name is refused rather than written over.
    """
    if renamed:
        return Planned(audio, Action.SKIP_UNTIMED_LRC)
    lines = text_lines(lyrics.text)
    if not tokenize(lines).ids:
        return Planned(audio, Action.SKIP_NO_TEXT)
    lrc = audio.with_suffix(LRC_SUFFIX)
    if sidecar_entry(lrc) is Slot.LINK:
        # The atomic write would replace the link itself, as a re-time would.
        detail = f"{lrc.name} is a link: never replaced"
        return Planned(audio, Action.ERROR_TEXT, detail=detail)
    return Planned(audio, Action.TIME, lines=lines)


def claim_lrcs(plans: Sequence[Planned]) -> list[Planned]:
    """Keep the first plan for each .lrc; every later track sharing it is refused.

    01 A.mp3 and 01 A.flac read and write one 01 A.lrc, so only the first in sorted
    order may. File names are compared case-folded: in a folder that ignores case, A.mp3
    and a.flac share a .lrc too, and refusing such a pair where case does matter costs a
    row, never a file. Folders are compared exactly, so two folders whose names differ
    only by case, distinct on a disk that keeps case, never refuse each other's tracks.
    A name the walk found that is no track to time (`UNCLAIMED`) claims nothing.
    """
    claimed: set[tuple[str, str]] = set()
    kept: list[Planned] = []
    for planned in plans:
        if planned.action in UNCLAIMED:
            kept.append(planned)
            continue
        lrc = planned.audio.with_suffix(LRC_SUFFIX)
        key = (str(lrc.parent), lrc.name.casefold())
        if key in claimed:
            detail = f"another track already claims {lrc.name}"
            kept.append(Planned(planned.audio, Action.ERROR_TEXT, detail=detail))
            continue
        claimed.add(key)
        kept.append(planned)
    return kept
