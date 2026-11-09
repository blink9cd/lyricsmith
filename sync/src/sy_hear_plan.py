"""The hearing's plan: which tracks are heard, each with its text and kind, and why not.

A track is planned from its sidecars alone (`sy_kind`), as album reads them: a provider's
timed ``.lrc`` is provider-timed, one this tool labeled machine-timed, an untimed text
untimed. A track whose sidecars hold no text is skipped as frames only, since
this tool reads no tags: its lyrics, if any, are left unheard and counted. A
kind not asked for is skipped with its kind. A track the hearing log already holds a
hearing of, for its current text's fingerprint and this run's model, is skipped as
heard; a changed text is heard again. The walk is the timing run's, a link and a FIFO
judged as it judges them (`sy_modes.entry_row`), and ``--tracks-from`` narrows it to the
tracks its list names, read by album's own changed-list reader; a listed track the walk
does not find is an error row, never dropped. Nothing here writes.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Final

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
import at_discover
import sy_kind
import sy_modes
from lyrics_changed import parse_changed_list
from lyrics_heard import text_fingerprint
from lyrics_hearing_log import HeardKind, list_hearings
from lyrics_sidecar import UNREADABLE
from sweep_ledger import read_unlinked
from sy_text import tokenize
from sy_types import Action, SetupError

#: The options a hearing reads its two inputs by, as each line it adds starts.
LOG_OPTION: Final = "--hearing-log"
TRACKS_OPTION: Final = "--tracks-from"
#: The largest track list read: a line a track, well under a kilobyte, so a library of
#: a million tracks lists in well under this; it bounds what a swapped-in file costs.
MAX_TRACKS_FROM_BYTES = 64 * 1024 * 1024
#: The detail beneath a track with no sidecar text.
FRAMES_ONLY: Final = "any lyrics it has are in its frames, which sync does not read"
#: The detail beneath a listed track the walk did not find.
NOT_FOUND: Final = f"listed in {TRACKS_OPTION}, but not found under --path"


class HearAction(enum.Enum):
    """What the plan does with one track; the dry run prints the value."""

    HEAR = "hear"
    SKIP_HEARD = "skip: heard before"
    SKIP_KIND = "skip: kind not asked for"
    SKIP_FRAMES_ONLY = "skip: no .lrc or .txt"
    SKIP_NO_TEXT = "skip: no words to hear"
    SKIP_LINKED = Action.SKIP_LINKED.value
    ERROR_TEXT = Action.ERROR_TEXT.value
    ERROR_AUDIO = Action.ERROR_AUDIO.value


#: The timing run's rows for a name that is no track to time, as a hearing's.
_ENTRY_ACTIONS: Final[Mapping[Action, HearAction]] = MappingProxyType(
    {
        Action.SKIP_LINKED: HearAction.SKIP_LINKED,
        Action.ERROR_AUDIO: HearAction.ERROR_AUDIO,
        Action.ERROR_TEXT: HearAction.ERROR_TEXT,
    }
)


@dataclass(frozen=True)
class HearPlan:
    """One track's planned action, its text and kind when read, and why it is skipped."""

    audio: Path
    action: HearAction
    kind: HeardKind | None = None
    lines: tuple[str, ...] = ()
    detail: str = ""
    #: The track as ``--tracks-from`` spells it when the list matched it, else empty.
    listed_as: str = ""

    @property
    def log_key(self) -> str:
        """The path the hearing log keys this track by: the list's, else the walk's.

        The list's spelling is lexically normal and the walk's keeps any ``..`` in
        --path, so a listed track is logged as the list, and the rows read from it,
        name it.
        """
        return self.listed_as or str(self.audio)


@dataclass(frozen=True)
class Selection:
    """What a run hears: the *kinds* asked, with *model*, past what the log holds.

    *heard* maps each (track path, model) the log holds a hearing of to the fingerprint
    of the text heard. *listed* is the paths ``--tracks-from`` names, or None for every
    track the walk finds.
    """

    kinds: frozenset[HeardKind]
    model: str
    heard: Mapping[tuple[str, str], str]
    listed: frozenset[str] | None = None


@dataclass(frozen=True)
class LogRead:
    """The hearing log read: each hearing's fingerprint, its unreadable files, why not.

    *problems* are lines to print; *setup* is a log that cannot be read at all.
    """

    by_track: dict[tuple[str, str], str]
    problems: tuple[str, ...] = ()
    setup: tuple[str, ...] = ()


def read_tracks_from(target: Path) -> tuple[str, ...]:
    """The track paths the list at *target* names, in order.

    Read as the sweep reads its state files, never through a link and never past
    `MAX_TRACKS_FROM_BYTES`, and parsed by album's own strict reader. Raises
    `SetupError` naming *target* when it cannot be read or is not such a list.
    """
    try:
        data = read_unlinked(target, MAX_TRACKS_FROM_BYTES + 1)
    except OSError as error:
        raise SetupError(
            f"{TRACKS_OPTION} {target}: cannot be read ({error})"
        ) from None
    if len(data) > MAX_TRACKS_FROM_BYTES:
        raise SetupError(
            f"{TRACKS_OPTION} {target}: more than {MAX_TRACKS_FROM_BYTES} bytes"
        )
    paths = parse_changed_list(data)
    if paths is None:
        raise SetupError(
            f"{TRACKS_OPTION} {target}: not a track list (one absolute path a line,"
            " UTF-8, each line ended by a linefeed)"
        )
    return paths


def read_log(folder: Path) -> LogRead:
    """The hearing log in *folder*: what it holds, its unreadable files, why it cannot be read.

    No folder yet is an empty log. A name holding anything but a folder, or a folder
    that cannot be listed, is a setup problem; a file in it that is not a record this
    tool can read is a line to print, and its track is heard again.
    """
    if folder.exists() and not folder.is_dir():
        return LogRead({}, setup=(f"{LOG_OPTION} is not a folder: {folder}",))
    try:
        listing = list_hearings(folder)
    except OSError as error:
        return LogRead({}, setup=(f"{LOG_OPTION} {folder}: cannot be read ({error})",))
    by_track = {(r.path, r.model): r.fingerprint for r in listing.records}
    problems = tuple(f"{LOG_OPTION}: {problem}" for problem in listing.problems)
    return LogRead(by_track, problems)


def _open_error(audio: Path, error: OSError) -> str:
    """A sidecar that exists but cannot be opened: its name, then the system's reason."""
    named = Path(str(error.filename)).name if error.filename else audio.name
    return f"{named}: {error.strerror or error}"


def _plan_text(track: Path, key: str, selection: Selection) -> HearPlan:
    """*track*'s plan from its sidecars: heard, skipped, or its text refused.

    *key* is the path the hearing log keys the track by.
    """
    try:
        found = sy_kind.text_kind(track)
    except UNREADABLE as error:
        return HearPlan(track, HearAction.ERROR_TEXT, detail=str(error))
    except OSError as error:
        return HearPlan(track, HearAction.ERROR_TEXT, detail=_open_error(track, error))
    if found is None:
        return HearPlan(track, HearAction.SKIP_FRAMES_ONLY, detail=FRAMES_ONLY)
    kind, lines = found.kind, found.lines
    if kind not in selection.kinds:
        return HearPlan(track, HearAction.SKIP_KIND, kind, lines, str(kind))
    if not tokenize(lines).ids:
        return HearPlan(track, HearAction.SKIP_NO_TEXT, kind)
    heard = selection.heard.get((key, selection.model))
    if heard == text_fingerprint(lines):
        return HearPlan(track, HearAction.SKIP_HEARD, kind, lines)
    return HearPlan(track, HearAction.HEAR, kind, lines)


def _plan_one(
    track: Path, listed_as: str, path: Path, selection: Selection
) -> HearPlan:
    """*track*'s row when it is no track to hear, else its plan from its sidecars.

    *listed_as* is the track as ``--tracks-from`` spells it, or empty.
    """
    row = sy_modes.entry_row(track, named=track == path)
    if row is not None:
        plan = HearPlan(track, _ENTRY_ACTIONS[row.action], detail=row.detail)
    else:
        plan = _plan_text(track, listed_as or str(track), selection)
    return replace(plan, listed_as=listed_as)


def _spellings(track: Path, path: Path, resolved: Path) -> tuple[str, str]:
    """*track* as a list may name it: as walked, then below *path* *resolved*.

    Both lexically normal. sweep resolves ``--library``, so its draw names a track
    below the resolved folder even when ``--path`` reaches it through a link.
    """
    walked = os.path.normpath(str(track))
    return walked, os.path.normpath(str(resolved / track.relative_to(path)))


def _listed(
    audio: list[Path], path: Path, listed: frozenset[str]
) -> tuple[list[tuple[Path, str]], list[str]]:
    """Each walked track the list names, with the list's spelling; the names not found.

    A track the list names both ways is keyed by the walk's spelling.
    """
    resolved = path.resolve()
    chosen: list[tuple[Path, str]] = []
    matched: set[str] = set()
    for track in audio:
        named = [one for one in _spellings(track, path, resolved) if one in listed]
        if named:
            chosen.append((track, named[0]))
            matched.update(named)
    return chosen, sorted(listed - matched)


def plan_hearings(
    path: Path, extensions: frozenset[str], selection: Selection
) -> list[HearPlan]:
    """Each unreadable folder's row, then each track's plan, then each listed track not found.

    With ``--tracks-from``, only the tracks its list names are planned, matched by
    path made lexically normal, as the list's reader makes each it reads, either as
    walked or below ``--path`` resolved (`_spellings`); each carries the list's
    spelling as its `HearPlan.log_key`.
    """
    errors: list[OSError] = []
    root = at_discover.walk_root(path)
    audio = at_discover.find_audio(path, extensions, errors)
    missing: list[str] = []
    chosen = [(track, "") for track in audio]
    if selection.listed is not None:
        chosen, missing = _listed(audio, path, selection.listed)
    folders = []
    for error in errors:
        row = sy_modes.unreadable_folder(error, root)
        folders.append(HearPlan(row.audio, HearAction.ERROR_TEXT, detail=row.detail))
    plans = [_plan_one(track, key, path, selection) for track, key in chosen]
    lost = [
        HearPlan(Path(m), HearAction.ERROR_AUDIO, detail=NOT_FOUND) for m in missing
    ]
    return folders + plans + lost
