"""The strip stage: remove a track's lyrics frames and both its sidecars.

`strip_track` takes one track back to no lyrics at all: every `SYLT` and `USLT` frame
goes, and so does whatever sits under its ``.lrc`` and ``.txt`` names. It runs in an
order that leaves a refused track whole:

1. Both sidecar names are checked first (`lyrics_slots.check_slot`). A folder, a pipe
   or a device under either raises `SlotNotAFileError` before anything changes, frames
   included.
2. The frames are removed through `strip_lyrics`, which keeps the ID3 version, leaves a
   track with no lyrics frame unwritten, and raises `TagVersionError` on an ID3v2.2 tag
   holding lyrics, again before any sidecar is touched.
3. Each sidecar present is removed. A link is removed itself, never the file it points
   at.

Nothing is copied aside here: the caller has already backed the track up, and undo
restores frames and sidecars from that backup. The caller holds the album lock, and
nothing here prints.
"""

from __future__ import annotations

from pathlib import Path

from lyrics_records import StripResult
from lyrics_slots import Slot, check_slot, sidecar_slot, slot_entry
from lyrics_stages import StripTrack
from lyrics_tags import strip_lyrics


def _remove_sidecars(mp3: Path) -> tuple[str, ...]:
    """Remove each sidecar beside *mp3*, a link itself; return the names removed, sorted."""
    removed: list[str] = []
    for name in sorted(sidecar_slot(mp3)):
        target = mp3.parent / name
        if slot_entry(target) is Slot.ABSENT:
            continue
        # unlink removes a link itself, never the file it points at.
        target.unlink()
        removed.append(name)
    return tuple(removed)


def strip_track(
    mp3: Path, *, frames: bool = True, sidecars: bool = True
) -> StripResult:
    """Remove *mp3*'s lyrics frames, then its sidecars; return what was removed.

    *frames* False keeps the frames, and *sidecars* False the sidecars, for a refetch
    removing only what it found implausible. Whether frames were removed is the
    strip's own answer, read from the one load it makes, not inferred afterward.
    Raise `SlotNotAFileError` when a sidecar name holds a folder, a pipe or a device,
    and `TagVersionError` on an ID3v2.2 tag holding lyrics: in both cases before
    anything of the track changes.
    """
    check_slot(mp3)
    had_frames = strip_lyrics(mp3) if frames else False
    return StripResult(had_frames, _remove_sidecars(mp3) if sidecars else ())


#: Bound to its protocol, so the type checker holds the signature.
STAGE: StripTrack = strip_track
