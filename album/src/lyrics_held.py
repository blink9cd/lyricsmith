"""What a track held before the run: the inventory's rule, over real lyrics only.

`held_lyrics` names the strongest lyrics a track held, from the sidecar the sidecar
stage would serve and its lyrics frames, by `lyrics_inventory.lyrics_kind`, the one
rule the run uses to judge what a track holds: a SYLT timed in MPEG frames and a
``.lrc`` with no stamp read as untimed there. Placeholders (`lyrics_placeholder`) are
set aside first, so a placeholder never makes a track synced; a track holding only
placeholders held untimed lyrics, since a stand-in holds no timing worth keeping.

`read_held` is what the run calls before it changes anything, so the outcome records
it as ``after.held`` (`lyrics_categories` reads it). The sidecars are planned, never
normalized, so nothing on disk changes; a sidecar that cannot be read, a frame over
the lyrics cap, or a read the system refuses leaves it unknown, as the run's own stages
report that track's error themselves.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from lyrics_errors import FrameTooLargeError
from lyrics_inventory import TrackLyricsKind, embedded_lyrics, lyrics_kind
from lyrics_model import FrameRecord, Lyrics
from lyrics_placeholder import is_placeholder_lyrics, without_placeholder_frames
from lyrics_records import HeldBefore
from lyrics_sidecar import UNREADABLE
from lyrics_stages import Stages

#: Each inventory kind, as the record names it.
_AS_HELD = {
    TrackLyricsKind.NONE: HeldBefore.NOTHING,
    TrackLyricsKind.UNTIMED: HeldBefore.UNTIMED,
    TrackLyricsKind.SYNCED: HeldBefore.SYNCED,
}


def held_lyrics(
    sidecar: Lyrics | None, frames: Sequence[FrameRecord], lang: str
) -> HeldBefore:
    """The strongest real lyrics *sidecar* and *frames* hold; placeholders alone, untimed."""
    real_frames = without_placeholder_frames(frames)
    stand_in = sidecar is not None and is_placeholder_lyrics(sidecar)
    embedded = embedded_lyrics(real_frames, lang)
    real = lyrics_kind(None if stand_in else sidecar, embedded.synced, embedded.untimed)
    if real is TrackLyricsKind.NONE and (stand_in or len(real_frames) < len(frames)):
        return HeldBefore.UNTIMED
    return _AS_HELD[real]


def read_held(
    stages: Stages, album: Path, path: Path, frames: Sequence[FrameRecord], lang: str
) -> HeldBefore | None:
    """What the track at *path*, holding *frames*, held; None when it cannot be read.

    A sidecar the sidecar stage cannot read, a frame over the lyrics cap, or a sidecar
    whose bytes the system refuses to give (an in-album link to a file without read
    permission, say) leaves it unknown rather than failing here: a run's own stages
    report that track's error at their own stage, and a strip still removes such
    lyrics.
    """
    try:
        sidecar = stages.normalize_sidecars(path, album=album, apply=False).lyrics
        return held_lyrics(sidecar, frames, lang)
    # OSError: reading what held costs nothing a strip or a run needs, so a read the
    # system refuses is not this step's to report; the stage that needs it reports it.
    except (*UNREADABLE, FrameTooLargeError, OSError):
        return None
