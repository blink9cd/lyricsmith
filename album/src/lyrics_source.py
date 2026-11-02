"""Where a track's lyrics came from: the `Source` enum and its display labels.

The values are what a run's manifest records; what a person reads is each member's
`label`. `lyrics_model` re-exports both names, so every importer reads them from
there; this module imports nothing of the tool's own, so the re-export makes no
import cycle.

Pure: no I/O, no printing.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class Source(StrEnum):
    """Where a track's lyrics came from.

    The values are what a run's manifest records, kept as they are so every manifest,
    old or new, still reads and undoes. What a person reads is the `label`: ``str()``
    and formatting both give it, so a summary, a progress line or an error message
    names each provider as the provider writes its own name.
    """

    SIDECAR = "sidecar"
    #: The track's own lyrics frames, served like a sidecar of the same kind.
    FRAMES = "frames"
    #: No step asks it any more; kept so a manifest an earlier run wrote still reads.
    MUSIXMATCH = "Musixmatch"
    LRCLIB = "Lrclib"
    NETEASE = "NetEase"
    GENIUS = "Genius"
    #: An album's Bandcamp page, found through MusicBrainz.
    BANDCAMP = "Bandcamp"
    #: YouTube Music's anonymous web route.
    YOUTUBE_MUSIC = "YouTube Music"
    #: Tekstowo.pl's plain search and song pages.
    TEKSTOWO = "Tekstowo"
    #: A timed ``.lrc`` the transcribe tool made, labeled so in its header (`machine_label`).
    MACHINE_TRANSCRIPT = "machine transcript"
    #: A timed ``.lrc`` the sync tool made, labeled so in its header (`machine_label`).
    MACHINE_TIMED = "machine timed"
    #: Another track's stored untimed text, which a fix wrote where no run recorded
    #: the provider it came from (`lyrics_fix_text`); the track is the outcome's origin.
    ALBUM_TEXT = "album text"

    @property
    def label(self) -> str:
        """The name shown to a person: the value, unless `SOURCE_LABELS` gives another."""
        return SOURCE_LABELS.get(self.value, self.value)

    def __str__(self) -> str:
        """The `label`. The manifest stores the value: JSON encodes the string itself."""
        return self.label

    def __format__(self, format_spec: str) -> str:
        """The `label`, formatted by *format_spec*: a str enum would format its value."""
        return format(self.label, format_spec)


#: Display labels that differ from the value, keyed by the value. Kept outside the
#: class, where the enum would read an assignment as one more member.
SOURCE_LABELS: Mapping[str, str] = MappingProxyType(
    {"Lrclib": "LRCLIB", "album text": "another track's text"}
)
