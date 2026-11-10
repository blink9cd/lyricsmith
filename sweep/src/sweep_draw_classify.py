"""The sample's classifier: which group a track is drawn in, or why it is left out.

Pure functions over what the library read found (`sweep_draw_library`): a track's
`Timing`, read from its sidecars and frames, its manifest entries newest first, and
whether its album is a wrong-text album. The draw (`sweep_draw`) and the hearing's
report both classify through `track_stratum`, so a group's size in the library and
its sample are counted by one rule.

**The order.** Timing decides first: only a provider-timed track, a timed ``.lrc``
no machine labeled, is drawn in any group, the wrong-text group included; a
machine-timed one (sync's own check heard it), a frames-only one, one not timed and
one that cannot be read are each left out by their own reason. Then the album: every
provider-timed track of a wrong-text album is that group's, never drawn again into a
provenance group. Then provenance (`provenance`).

**Provenance** is a walk of the track's own entries, newest first, of the mode's
own: `lyrics_provenance.recorded_sources` passes over a ``sidecar`` write by design,
which would hide the older-sidecars group.

| Entry | Decides |
| --- | --- |
| undone, or no ``after`` block | nothing |
| ``written`` / ``unchanged`` with synced lyrics from NetEase, YouTube Music, LRCLIB or the ``sidecar`` | that group |
| the same from any other source (the track's own frames, an old provider) | left out, "another source" |
| ``stripped`` / ``removed``, or an untimed ``written`` / ``unchanged`` | left out, "no manifest record": the ``.lrc`` came after it (writing the ``.txt`` unlinks the ``.lrc``, and a kept untimed text means the track held no timed one then) |
| anything else (kept existing, not found, an error) | nothing |

A track no entry decides has no manifest record.

**The wrong-text marks.** An album is a wrong-text album when any entry of any of its
runs records a ``wrong text removed`` outcome, a withheld removal (the spoken-album guard's),
or a fix's write of another track's text, one naming the track it came from
(`marks_wrong_text`), or when the ledger
holds a fix-text visit of it (read by `sweep_draw_library`). An undone entry still
marks: an undo puts the text back, not the finding away. The withheld mark ``--withheld`` draws by
(`withheld_mark`) counts only on an entry not undone. Nothing here reads or prints.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_model import LyricsKind, Source
from lyrics_outcomes import WRONG_TEXT
from lyrics_records import PASTED_BY_HAND, ManifestTrack, OutcomeStatus, TrackOutcome
from sweep_strata import StrataGroup


class Timing(StrEnum):
    """How a track's lyrics are timed, as its sidecars and frames hold them."""

    #: A timed ``.lrc`` no machine labeled: a provider's timing, or an older sidecar's.
    PROVIDER_TIMED = "provider-timed"
    #: A timed ``.lrc`` a machine labeled (`lyrics_sidecar.MACHINE_LABELS`).
    MACHINE_TIMED = "machine-timed"
    #: Timed frames and no timed ``.lrc``: the timing is in the frames alone.
    FRAMES_ONLY = "frames-only"
    #: No timed lyrics, and an untimed sidecar holding words.
    UNTIMED = "untimed"
    #: No timed lyrics, and no untimed sidecar: untimed frames, or no lyrics at all.
    NOT_TIMED = "not timed"
    #: A sidecar or tag that could not be read.
    UNREADABLE = "unreadable"


class LeftOut(StrEnum):
    """Why a track is in no group, each counted and printed by these words."""

    MACHINE_TIMED = "machine-timed"
    FRAMES_ONLY = "frames-only"
    NO_MANIFEST_RECORD = "no manifest record"
    ANOTHER_SOURCE = "another source"
    NOT_TIMED = "not timed"
    UNREADABLE = "unreadable"
    #: A path, or album name, the track list or the strata file cannot hold.
    UNLISTABLE = "a path the files cannot hold"
    #: `--withheld` only: a track marked withheld that no longer holds an untimed text.
    NO_LONGER_UNTIMED = "no longer untimed"


#: A track's place in the draw: its group, or why it has none.
Stratum = StrataGroup | LeftOut

#: The provenance groups, by the source whose synced write or `unchanged` decides them.
PROVENANCE_GROUPS: Final[Mapping[Source, StrataGroup]] = MappingProxyType(
    {
        Source.NETEASE: StrataGroup.NETEASE,
        Source.YOUTUBE_MUSIC: StrataGroup.YOUTUBE_MUSIC,
        Source.LRCLIB: StrataGroup.LRCLIB,
        Source.SIDECAR: StrataGroup.OLDER_SIDECARS,
    }
)
#: Why a track that is not provider-timed is left out, by its timing.
_LEFT_OUT_BY_TIMING: Final[Mapping[Timing, LeftOut]] = MappingProxyType(
    {
        Timing.MACHINE_TIMED: LeftOut.MACHINE_TIMED,
        Timing.FRAMES_ONLY: LeftOut.FRAMES_ONLY,
        Timing.UNTIMED: LeftOut.NOT_TIMED,
        Timing.NOT_TIMED: LeftOut.NOT_TIMED,
        Timing.UNREADABLE: LeftOut.UNREADABLE,
    }
)
_WRITES: Final = frozenset({OutcomeStatus.WRITTEN, OutcomeStatus.UNCHANGED})
_REMOVALS: Final = frozenset({OutcomeStatus.STRIPPED, OutcomeStatus.REMOVED})


def _synced_group(source: Source | None) -> Stratum:
    """The group a synced write from *source* decides; "another source" for the rest."""
    if source is None:
        return LeftOut.ANOTHER_SOURCE
    return PROVENANCE_GROUPS.get(source, LeftOut.ANOTHER_SOURCE)


def _untimed_text(after: TrackOutcome) -> bool:
    """True when *after* wrote or kept an untimed text, so the ``.lrc`` came after it.

    Writing the ``.txt`` unlinks the ``.lrc``; keeping one unchanged means the track
    held no timed text the run would have served instead.
    """
    return after.status in _WRITES and after.kind is LyricsKind.UNSYNCED


def provenance(entries: Iterable[ManifestTrack]) -> Stratum:
    """The group the newest deciding entry of *entries*, newest first, puts a track in.

    "No manifest record" when a removal or an untimed write or ``unchanged`` comes
    first, or nothing decides.
    """
    for entry in entries:
        after = entry.after
        if entry.undone or after is None:
            continue
        if after.status in _REMOVALS or _untimed_text(after):
            return LeftOut.NO_MANIFEST_RECORD
        if after.status in _WRITES and after.kind is LyricsKind.SYNCED:
            return _synced_group(after.source)
    return LeftOut.NO_MANIFEST_RECORD


def marks_wrong_text(entry: ManifestTrack) -> bool:
    """True when *entry* records a wrong-text removal, a withheld one, or a fixed text.

    A fixed text is a sibling's text written in its place, or a paste's write
    (`lyrics_records.PASTED_BY_HAND`), which follows only a not-sung finding. Undone or
    not: the finding stands either way.
    """
    after = entry.after
    if after is None:
        return False
    removed = after.status is OutcomeStatus.REMOVED and after.implausible == WRONG_TEXT
    withheld = (
        after.status is OutcomeStatus.KEPT_EXISTING and after.withheld is not None
    )
    # A paste's write names its own origin, and marks: the album tool takes a paste
    # only for a track a transcript found not sung.
    pasted = after.origin == PASTED_BY_HAND
    # A sibling's text is written under the provider a run recorded for that sibling,
    # so any other origin marks it; `Source.ALBUM_TEXT` alone marks one in a manifest
    # written before outcomes named the track a text came from.
    sibling = (
        after.origin is not None and not pasted
    ) or after.source is Source.ALBUM_TEXT
    return removed or withheld or sibling or pasted


def withheld_mark(entry: ManifestTrack) -> bool:
    """True when *entry*, not undone, records a removal a fix withheld."""
    after = entry.after
    return (
        not entry.undone
        and after is not None
        and after.status is OutcomeStatus.KEPT_EXISTING
        and after.withheld is not None
    )


def track_stratum(
    timing: Timing, entries: Iterable[ManifestTrack], *, wrong_text_album: bool
) -> Stratum:
    """A track's group, or why it is left out: by *timing*, its album, then *entries*.

    *entries* are the track's own manifest entries, newest first; *wrong_text_album*
    is whether its album is one.
    """
    if timing is not Timing.PROVIDER_TIMED:
        return _LEFT_OUT_BY_TIMING[timing]
    if wrong_text_album:
        return StrataGroup.WRONG_TEXT_ALBUMS
    return provenance(entries)
