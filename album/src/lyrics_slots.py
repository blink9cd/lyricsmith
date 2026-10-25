"""A track's sidecar slot: its two sidecar names, and what each one holds on disk.

A track's slot is ``<stem>.lrc`` and ``<stem>.txt`` beside its MP3, and every stage
that looks at a sidecar asks here what a slot name holds, read without following a link
(`slot_entry`): a file, a link, or nothing. A link is a sidecar in its own right,
whether it leads to a file, to a folder or nowhere: the backup records it as a link,
and strip, the sidecar stage and undo remove or replace the link itself, never the file
it points at.

A folder, a pipe or a device under a slot name is nothing any stage wrote or can put
back. Undo and strip refuse it: `slot_entry` raises `SlotNotAFileError`, the track's
error, and the caller checks both names before it changes anything, so the track is
left whole to a person. The backup and the sidecar stage leave it be instead
(`sidecar_entry` reads it as no sidecar), as they always have: the backup records
nothing for it and the sidecar stage never reads, moves or removes it.

Pure reads; nothing here changes a file or prints.
"""

from __future__ import annotations

import stat
from enum import Enum
from pathlib import Path

from lyrics_model import LyricsError

#: The synced sidecar's suffix.
LRC_SUFFIX = ".lrc"
#: The unsynced sidecar's suffix.
TXT_SUFFIX = ".txt"


class SlotNotAFileError(LyricsError):
    """A sidecar name beside a track holds a folder, a pipe or a device: that track's error."""


class Slot(Enum):
    """What a sidecar name beside an MP3 holds."""

    ABSENT = "absent"
    FILE = "file"
    LINK = "link"


def sidecar_paths(mp3: Path) -> tuple[Path, Path]:
    """The paths of *mp3*'s two sidecar names, ``.lrc`` then ``.txt``."""
    return mp3.with_suffix(LRC_SUFFIX), mp3.with_suffix(TXT_SUFFIX)


def sidecar_slot(mp3: Path) -> frozenset[str]:
    """The two names a sidecar of *mp3* can have."""
    return frozenset(path.name for path in sidecar_paths(mp3))


def slot_entry(target: Path) -> Slot:
    """What *target* holds, read without following a link; refuse anything else.

    A folder, a pipe or a device under a sidecar's name is nothing undo or strip
    wrote or can put back, so it raises `SlotNotAFileError` and the track is left to a
    person.
    """
    try:
        mode = target.lstat().st_mode
    except FileNotFoundError:
        return Slot.ABSENT
    if stat.S_ISLNK(mode):
        return Slot.LINK
    if not stat.S_ISREG(mode):
        raise SlotNotAFileError(f"{target.name} is not a file or a link; left as it is")
    return Slot.FILE


def check_slot(mp3: Path) -> None:
    """Refuse a track whose sidecar names hold something other than a file or a link."""
    for name in sorted(sidecar_slot(mp3)):
        slot_entry(mp3.parent / name)


def sidecar_entry(target: Path) -> Slot:
    """What *target* holds as a sidecar: `slot_entry`'s answer, anything else ABSENT.

    For the backup and the sidecar stage, which leave a folder, a pipe or a device under
    a sidecar's name alone rather than refuse the track.
    """
    try:
        return slot_entry(target)
    except SlotNotAFileError:
        return Slot.ABSENT
