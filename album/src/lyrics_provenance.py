"""Which provider a track's lyrics came from, learned from the album's run manifests.

Compare must not call a track's lyrics a disagreement with the very provider that wrote
them, so it asks here what the runs recorded. The walk takes the runs newest first
(`lyrics_backup.runs_newest_first`); for each track the first entry that decides it
wins, and older entries for it are ignored:

| Entry | Decides |
| --- | --- |
| ``undone``, or no ``after`` block | nothing |
| ``written`` / ``unchanged`` from a provider | that provider |
| ``written`` / ``unchanged`` from another track's text (a fix's) | unrecorded |
| ``stripped`` / ``removed`` | unrecorded: whatever the track holds came later |
| anything else (kept existing, not found, error, a sidecar or frames write) | nothing |

A track no run decides is unrecorded. A manifest that cannot be read is passed over,
and the run is kept in `SourceRecords.passed_over` with the error's type, so the
caller can say so: compare gives one notice for each. Nothing here writes, locks or
prints.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from lyrics_backup import runs_newest_first
from lyrics_model import ManifestSchemaError, Source
from lyrics_records import ManifestTrack, OutcomeStatus
from lyrics_stages import ReadManifest

#: The sources that name a provider, as opposed to the track's own sidecar or frames.
#: Every provider compare asks must be here, or its own answer would read as a second.
PROVIDER_SOURCES: frozenset[Source] = frozenset(
    {
        Source.LRCLIB,
        Source.NETEASE,
        Source.BANDCAMP,
        Source.YOUTUBE_MUSIC,
        Source.GENIUS,
        Source.TEKSTOWO,
        Source.MUSIXMATCH,
    }
)
_PROVIDER_WRITES = frozenset({OutcomeStatus.WRITTEN, OutcomeStatus.UNCHANGED})
_REMOVALS = frozenset({OutcomeStatus.STRIPPED, OutcomeStatus.REMOVED})


@dataclass(frozen=True)
class SourceRecords:
    """The tracks the runs decided, and the runs whose manifests could not be read.

    *decided* maps an album-relative POSIX path to its source, None if unrecorded.
    *passed_over* holds each run folder whose manifest was passed over, newest first,
    with the name of the error's type, never its message.
    """

    decided: Mapping[str, Source | None] = field(default_factory=dict)
    passed_over: tuple[tuple[Path, str], ...] = ()

    def source_of(self, track: str) -> Source | None:
        """The recorded source of *track*; None when unrecorded or never decided."""
        return self.decided.get(track)


def _decision(entry: ManifestTrack) -> tuple[bool, Source | None]:
    """Whether *entry* decides its track, and the source it decides (None: unrecorded)."""
    after = entry.after
    if entry.undone or after is None:
        return False, None
    if after.status in _PROVIDER_WRITES and after.source in PROVIDER_SOURCES:
        return True, after.source
    if after.status in _PROVIDER_WRITES and after.source is Source.ALBUM_TEXT:
        return True, None
    return after.status in _REMOVALS, None


def recorded_sources(album: Path, read: ReadManifest) -> SourceRecords:
    """Walk *album*'s runs newest first with *read*; return what they decided.

    Each run whose manifest *read* refuses (`ManifestSchemaError`, or an `OSError`) is
    passed over and kept in the answer's `passed_over`.
    """
    decided: dict[str, Source | None] = {}
    passed_over: list[tuple[Path, str]] = []
    for run_dir in runs_newest_first(album):
        try:
            manifest = read(run_dir)
        except (ManifestSchemaError, OSError) as error:
            # Compare is a report that runs on any album: an unreadable manifest is
            # passed over, so an older entry, or "unrecorded", decides instead, and
            # the run is kept so the caller can name it.
            passed_over.append((run_dir, type(error).__name__))
            continue
        for entry in manifest.tracks:
            if entry.path in decided:
                continue
            settled, source = _decision(entry)
            if settled:
                decided[entry.path] = source
    return SourceRecords(decided, tuple(passed_over))
