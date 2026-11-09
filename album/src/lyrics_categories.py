"""Outcome categories: what a written track held and got, why removed lyrics went.

The album summary's ``written:``, ``removed:`` and ``removal withheld:`` lines, and the
sweep's totals, count the same way from a `ManifestTrack` alone:

- What a track held is the run's own record of it, ``after.held`` (`lyrics_held`),
  read from the files before anything changed. Where it is unknown (a sidecar that
  could not be read, a frame over the lyrics cap, a track that errored before it was
  read, or a manifest written before the field existed), the categories fall back to
  `held_before`, an approximation.
- `held_before` names what a track held before the run, from its ``before`` block:
  synced when it held a `SYLT` frame timed in milliseconds or a ``.lrc`` copy, else
  untimed when it held any other lyrics frame or a ``.txt`` copy, else nothing. It
  reads the backup's records, never the files, so a ``.lrc`` holding no time stamp,
  or a placeholder `SYLT` of stamps alone, still counts as synced here; outside a
  ``--refetch-untimed`` run, nothing on the record tells them apart. A sidecar
  recorded as a link was never copied and is never read, so it holds nothing.
- `written_category` names a written track's category. The track's own files (a
  sidecar, its frames, a machine-labeled ``.lrc``) are `WrittenCategory.EMBEDDED`
  whatever the kinds; otherwise the held kind and the kind written decide, another
  track's text a fix wrote (`Source.ALBUM_TEXT`) included: it is no file of the
  track's own, so over an untimed text it is `WrittenCategory.UNTIMED_REPLACED`. A
  ``--refetch-untimed`` track held untimed lyrics, whatever its backup records. A track
  whose only lyrics were placeholders, which a run records as ``placeholder lyrics``
  on its outcome outside a ``--refetch-implausible`` pick, held untimed lyrics: a
  stand-in holds no timing worth keeping. A track with no ``before``, or a written
  record with no kind, which no run writes, is `WrittenCategory.BEFORE_UNKNOWN`.
- `removed_reason` reads why a removed track's old lyrics went from its recorded
  ``implausible`` text: the refetch's placeholder words, or the check's past-end or
  too-many-words detail, recognized by the stable words those details are built from,
  or a fix's ``wrong text removed``, counted as ``wrong text``.
  With both sides removed apart, the first reason that names a rule decides.
- A fix's kept track whose removal it withheld (`TrackOutcome.withheld`) counts on
  the ``removal withheld:`` line, so it is never read as a plain ``kept existing``:
  as ``held for Genius`` when a fix given ``--hold-for-genius`` held it
  (`lyrics_outcomes.HELD_FOR_GENIUS`), else as ``spoken word``, the album looking
  spoken. The held are counted apart (`OutcomeCategories.held`) and among the
  withheld too, so a count from before the hold existed reads as spoken word alone.

Pure: no I/O, nothing printed. The runtime floor is Python 3.12.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from lyrics_check import LAST_LINE_DETAIL, WORD_RATE_DETAIL
from lyrics_model import SYLT_MILLISECONDS, FrameKind, LyricsKind, Source
from lyrics_records import (
    CheckRule,
    FrameExport,
    ManifestTrack,
    OutcomeStatus,
    TrackBefore,
    TrackOutcome,
)
from lyrics_outcomes import HELD_FOR_GENIUS, REMOVAL_WITHHELD
from lyrics_outcomes import WRONG_TEXT as WRONG_TEXT_REMOVED
from lyrics_refetch import PLACEHOLDER, REASON_JOINER
from lyrics_sidecar import MACHINE_LABELS
from lyrics_slots import LRC_SUFFIX, TXT_SUFFIX
from lyrics_spoken import SPOKEN_WORD

# The held kind is a record's field; re-exported, so the categories' readers name it here.
from lyrics_records import HeldBefore as HeldBefore


class WrittenCategory(StrEnum):
    """A written track's category; the values are the summary's words."""

    UPGRADED = "upgraded"
    NEW_SYNCED = "new synced"
    EMBEDDED = "embedded"
    SYNCED_REPLACED = "synced replaced"
    UNTIMED_REPLACED = "untimed replaced"
    NEW_UNTIMED = "new untimed"
    REPLACED_BY_UNTIMED = "replaced by untimed"
    BEFORE_UNKNOWN = "before unknown"


class RemovedReason(StrEnum):
    """Why a removed track's old lyrics went; the values are the summary's words."""

    PLACEHOLDER = "placeholder"
    PAST_END = CheckRule.PAST_END.value
    TOO_MANY_WORDS = CheckRule.TOO_MANY_WORDS.value
    #: A fix's removal: the transcript sang no text tried (`lyrics_fix_step`). The
    #: summary's word drops the recorded reason's ``removed``, which the line says.
    WRONG_TEXT = "wrong text"
    OTHER = "other"


#: The ``written:`` line's order: gains first, then replacements, then the unknown.
WRITTEN_ORDER: tuple[WrittenCategory, ...] = tuple(WrittenCategory)
#: The ``removed:`` line's order.
REMOVED_ORDER: tuple[RemovedReason, ...] = tuple(RemovedReason)
#: The sources that are the track's own files: what a written track embedded.
OWN_FILES = frozenset({Source.SIDECAR, Source.FRAMES, *MACHINE_LABELS.values()})
#: Each held kind and kind written, and its category.
_BY_KINDS: Mapping[tuple[HeldBefore, LyricsKind], WrittenCategory] = {
    (HeldBefore.NOTHING, LyricsKind.SYNCED): WrittenCategory.NEW_SYNCED,
    (HeldBefore.UNTIMED, LyricsKind.SYNCED): WrittenCategory.UPGRADED,
    (HeldBefore.SYNCED, LyricsKind.SYNCED): WrittenCategory.SYNCED_REPLACED,
    (HeldBefore.SYNCED, LyricsKind.UNSYNCED): WrittenCategory.REPLACED_BY_UNTIMED,
    (HeldBefore.NOTHING, LyricsKind.UNSYNCED): WrittenCategory.NEW_UNTIMED,
    (HeldBefore.UNTIMED, LyricsKind.UNSYNCED): WrittenCategory.UNTIMED_REPLACED,
}
#: How each line opens, and what parts its entries.
_WRITTEN_LABEL, _REMOVED_LABEL, _GAP = "written:", "removed:", ", "
_WITHHELD_LABEL = f"{REMOVAL_WITHHELD}:"


@dataclass(frozen=True)
class OutcomeCategories:
    """Written tracks by category, removed tracks by reason, and removals withheld.

    *withheld* counts every withheld removal; *held*, those of them held for Genius.
    """

    written: dict[WrittenCategory, int] = field(default_factory=dict)
    removed: dict[RemovedReason, int] = field(default_factory=dict)
    withheld: int = 0
    held: int = 0


def _copied(before: TrackBefore, suffix: str) -> bool:
    """True when *before* holds a copied sidecar ending in *suffix*; never a link's."""
    return any(
        copy.file is not None and copy.name.endswith(suffix) for copy in before.sidecars
    )


def _timed(frame: FrameExport) -> bool:
    """True for a SYLT timed in milliseconds; one timed in MPEG frames reads untimed."""
    return frame.kind is FrameKind.SYLT and frame.format == SYLT_MILLISECONDS


def held_before(before: TrackBefore | None) -> HeldBefore | None:
    """What *before* held, by its frames and its sidecar copies; None when absent."""
    if before is None:
        return None
    if any(_timed(frame) for frame in before.frames) or _copied(before, LRC_SUFFIX):
        return HeldBefore.SYNCED
    if before.frames or _copied(before, TXT_SUFFIX):
        return HeldBefore.UNTIMED
    return HeldBefore.NOTHING


def _held(track: ManifestTrack, after: TrackOutcome) -> HeldBefore | None:
    """What *track* held: as the run recorded it, else read from its backup's record.

    The record (``after.held``, `lyrics_held`) is the run's own reading of the files.
    Where it is unknown (the cases the module docstring names), the backup's record is
    the approximation: a ``--refetch-untimed`` run marks only a track holding no real
    synced lyrics (`lyrics_refetch.refetch_target`), though a placeholder SYLT beside
    real untimed lyrics, a SYLT of time stamps alone, or a ``.lrc`` with no stamp backs
    up as synced. A run records ``placeholder lyrics`` alone on a track whose only
    lyrics were placeholders (`lyrics_refetch.placeholder_target`); a
    ``--refetch-implausible`` pick may record it for one side while the other held
    real lyrics, so it is not read as the whole track's.
    """
    if after.held is not None:
        return after.held
    held = held_before(track.before)
    only_placeholders = (
        after.implausible == PLACEHOLDER and not after.refetch_implausible
    )
    if held is HeldBefore.SYNCED and (after.refetch_untimed or only_placeholders):
        return HeldBefore.UNTIMED
    return held


def written_category(track: ManifestTrack) -> WrittenCategory | None:
    """*track*'s category when the run wrote it; None for any other outcome."""
    after = track.after
    if after is None or after.status is not OutcomeStatus.WRITTEN:
        return None
    if after.source in OWN_FILES:
        return WrittenCategory.EMBEDDED
    held = _held(track, after)
    if held is None or after.kind is None:
        return WrittenCategory.BEFORE_UNKNOWN
    return _BY_KINDS[held, after.kind]


def _named_reason(part: str) -> RemovedReason | None:
    """The rule one recorded reason names, or None when it names none."""
    if part == PLACEHOLDER:
        return RemovedReason.PLACEHOLDER
    if part == WRONG_TEXT_REMOVED:
        return RemovedReason.WRONG_TEXT
    if part.startswith(LAST_LINE_DETAIL):
        return RemovedReason.PAST_END
    if WORD_RATE_DETAIL in part:
        return RemovedReason.TOO_MANY_WORDS
    return None


def removed_reason(outcome: TrackOutcome) -> RemovedReason:
    """Why *outcome*'s old lyrics went: the first recorded reason naming a rule."""
    parts = (outcome.implausible or "").split(REASON_JOINER)
    named = (_named_reason(part) for part in parts)
    return next((reason for reason in named if reason is not None), RemovedReason.OTHER)


def count_categories(tracks: Iterable[ManifestTrack]) -> OutcomeCategories:
    """Written tracks by category, removed tracks by reason, removals withheld."""
    written: dict[WrittenCategory, int] = {}
    removed: dict[RemovedReason, int] = {}
    withheld = held = 0
    for track in tracks:
        category = written_category(track)
        if category is not None:
            written[category] = written.get(category, 0) + 1
        if track.after is not None and track.after.status is OutcomeStatus.REMOVED:
            reason = removed_reason(track.after)
            removed[reason] = removed.get(reason, 0) + 1
        if track.after is not None and track.after.withheld is not None:
            withheld += 1
            held += track.after.withheld == HELD_FOR_GENIUS
    return OutcomeCategories(written, removed, withheld, held)


def _line[Key: StrEnum](
    label: str, order: tuple[Key, ...], counts: Mapping[Key, int]
) -> str | None:
    """``<label> <name> <n>, ...`` in *order*, non-zero counts only; None when empty."""
    parts = [f"{key} {counts[key]}" for key in order if counts.get(key, 0)]
    return f"{label} {_GAP.join(parts)}" if parts else None


def format_written(counts: OutcomeCategories) -> str | None:
    """The summary's ``written:`` line, or None when no category counts a track."""
    return _line(_WRITTEN_LABEL, WRITTEN_ORDER, counts.written)


def format_removed(counts: OutcomeCategories) -> str | None:
    """The summary's ``removed:`` line, or None when no track was removed."""
    return _line(_REMOVED_LABEL, REMOVED_ORDER, counts.removed)


def format_withheld(counts: OutcomeCategories) -> str | None:
    """The ``removal withheld:`` line, or None when none was withheld.

    ``spoken word <n>``, then ``held for Genius <n>``, each only when it counts one.
    """
    if not counts.withheld:
        return None
    parts = (
        (SPOKEN_WORD, counts.withheld - counts.held),
        (HELD_FOR_GENIUS, counts.held),
    )
    shown = _GAP.join(f"{reason} {count}" for reason, count in parts if count)
    return f"{_WITHHELD_LABEL} {shown}"
