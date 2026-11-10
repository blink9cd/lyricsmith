"""The records the album tool keeps about a run: key, check verdict, outcome, manifest.

The second half of the shared interface begun in `lyrics_model`: the masked Genius key
and its resolution, the check's verdict, one track's outcome (the manifest's ``after``
block), what the strip stage removed from a track, the manifest itself with its schema,
and the undo report. `is_mp3_name` is the one test of an MP3's name, for discovery and
for the manifest's track paths alike.

Pure: no I/O, no printing. The run folder's name and `run_folder` are re-exported from
`lyrics_run_folders`, so every reader of them here names the same folder, and a refusal's
record, `ProviderRefusal`, from `lyrics_refusal`, beside its class and facts; that module
alone reads a third-party one, ``requests``'s error types. The runtime floor is Python 3.12.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

from lyrics_model import (
    FrameKind,
    LyricsKind,
    ManifestSchemaError,
    QueryMarker,
    SidecarRename,
    Source,
    TrackNotInRunError,
)
from lyrics_provider_states import BandcampState, CoolingState, SilenceCheck
from lyrics_cap import MAX_LYRICS_CHARS as MAX_LYRICS_CHARS
from lyrics_run_folders import MANIFEST_NAME as MANIFEST_NAME
from lyrics_run_folders import RUN_FOLDER_NAME as RUN_FOLDER_NAME
from lyrics_run_folders import run_folder as run_folder

# A refusal's record lives with its class and facts; re-exported, so readers keep this name.
from lyrics_refusal import MAX_REFUSAL_DETAIL_CHARS as MAX_REFUSAL_DETAIL_CHARS
from lyrics_refusal import ProviderRefusal as ProviderRefusal
from lyrics_retried import RetriedCounts

#: The manifest layout this code reads and writes. A manifest naming any other is refused.
MANIFEST_SCHEMA = 1
#: The suffix an MP3's name ends in, compared without case.
MP3_SUFFIX = ".mp3"


def is_mp3_name(name: str) -> bool:
    """True when *name*, a file name or a POSIX path, ends in the MP3 suffix, any case.

    A name that is the suffix alone, as ``CD1/.mp3``, is a hidden file with no suffix,
    not an MP3.
    """
    return PurePosixPath(name).suffix.casefold() == MP3_SUFFIX


# --- Genius key --------------------------------------------------------------------


class Secret:
    """A credential whose value never shows in a repr, a str or a format."""

    __slots__ = ("_value",)
    _MASK = "Secret(****)"

    def __init__(self, value: str) -> None:
        """Hold *value*; only `reveal` returns it."""
        self._value = value

    def reveal(self) -> str:
        """The raw value, for the one client constructor that needs it."""
        return self._value

    def __repr__(self) -> str:
        """The mask, never the value."""
        return self._MASK

    __str__ = __repr__


class KeyOrigin(StrEnum):
    """Where a Genius key was found, or that none was."""

    ARGUMENT = "--config"
    ENVIRONMENT = "environment"
    CONFIG_FILE = "config file"
    ABSENT = "absent"


@dataclass(frozen=True)
class KeyResolution:
    """The resolved Genius key and where it came from.

    *searched* is the config file path looked at, for the notice when no key
    resolved. *warnings* are returned, never printed: a config file readable by
    others, for one.
    """

    secret: Secret | None
    origin: KeyOrigin
    searched: Path | None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a key without an origin, or an origin without a key."""
        if (self.secret is None) != (self.origin is KeyOrigin.ABSENT):
            raise ValueError("a key resolves exactly when its origin is not absent")


# --- The check -----------------------------------------------------------------------


class CheckVerdict(StrEnum):
    """The check's verdict on one track."""

    PASS = "PASS"
    FLAG = "FLAG"
    SKIP = "SKIP"


class CheckRule(StrEnum):
    """One row of the check; values are the summary's words.

    `NO_LYRICS` is a FLAG: the check runs only on tracks whose lyrics the run wrote,
    so one that now holds none has lost them since (removed by hand, or by another
    tool), and that is a problem to surface, not a row to skip.

    `UNTIMED` is no row of the check: it is why a fresh run declined an untimed
    answer for a track whose lyrics were already synced, recorded as the declined
    answer's FLAG beside any unlike-backup one.
    """

    PAST_END = "past end"
    ENDS_SHORT = "ends short"
    OUT_OF_ORDER = "out of order"
    TOO_MANY_WORDS = "too many words"
    UNLIKE_BACKUP = "unlike backup"
    DURATION_UNREADABLE = "duration unreadable"
    NO_LYRICS = "no lyrics embedded"
    #: The embedded lyrics are a stand-in, not lyrics (`lyrics_placeholder`).
    PLACEHOLDER = "placeholder lyrics"
    UNTIMED = "untimed"
    #: A track tagged with a no-lyrics genre that holds lyrics: no row of the check,
    #: recorded as a FLAG by the run (lyrics_outcomes), never a reason to decline.
    NO_LYRICS_GENRE = "lyrics on a no-lyrics genre"


@dataclass(frozen=True)
class CheckReason:
    """Why a rule flagged or skipped, with the measured values in *detail*."""

    rule: CheckRule
    detail: str

    def describe(self) -> str:
        """The rule and its values, as the summary prints them."""
        return f"{self.rule} ({self.detail})"


@dataclass(frozen=True)
class CheckResult:
    """The check's findings on one track: FLAGs, and rows it had to SKIP."""

    flags: tuple[CheckReason, ...]
    skips: tuple[CheckReason, ...]

    @property
    def verdict(self) -> CheckVerdict:
        """FLAG when anything flagged, else SKIP when a row was skipped, else PASS."""
        if self.flags:
            return CheckVerdict.FLAG
        return CheckVerdict.SKIP if self.skips else CheckVerdict.PASS

    def summary(self) -> str:
        """``PASS``, ``FLAG: <reasons>`` or ``SKIP: <reasons>``."""
        reasons = self.flags or self.skips
        if not reasons:
            return str(CheckVerdict.PASS)
        return f"{self.verdict}: " + "; ".join(reason.describe() for reason in reasons)


# --- Outcomes and the manifest -------------------------------------------------------


class ErrorStage(StrEnum):
    """The stage a track's error happened in."""

    BACKUP = "backup"
    SIDECAR = "sidecar"
    FETCH = "fetch"
    EMBED = "embed"
    CHECK = "check"
    STRIP = "strip"
    UNDO = "undo"


@dataclass(frozen=True)
class StageError:
    """One track's error: the stage, the exception type's name, and its message."""

    stage: ErrorStage
    exc_type: str
    message: str

    def summary(self) -> str:
        """``ERROR <stage>: <message>``, as the summary prints it."""
        return f"ERROR {self.stage}: {self.message}"


@dataclass(frozen=True)
class SidecarHash:
    """A sidecar a run left beside an MP3: its file name and the SHA-256 of its bytes.

    Undo removes or replaces a sidecar without keeping a copy only when its bytes
    still hash to *sha256*, as lowercase hex: proof it is the run's, unchanged since.
    """

    name: str
    sha256: str


class HeldBefore(StrEnum):
    """The strongest real lyrics a track held before the run (`lyrics_held`)."""

    NOTHING = "nothing"
    UNTIMED = "untimed"
    SYNCED = "synced"


class OutcomeStatus(StrEnum):
    """How a run ended for one track."""

    WRITTEN = "written"
    #: Lyrics accepted, and the track already held them: nothing was saved or written.
    UNCHANGED = "unchanged"
    NOT_FOUND = "not found"
    #: Nothing found for a track whose genre no lyrics source holds (lyrics_genre).
    NO_LYRICS_EXPECTED = "no lyrics expected"
    #: Nothing found for a track whose audio is silent (lyrics_silence).
    SILENT = "silent"
    KEPT_EXISTING = "kept existing"
    #: A run found nothing, and removed the track's implausible old lyrics: a
    #: refetch's, or a stored placeholder (lyrics_refetch.placeholder_target).
    REMOVED = "removed"
    STRIPPED = "stripped"
    ERROR = "error"


#: The origin of a text ``--mode paste`` wrote: copied from a browser, never fetched.
PASTED_BY_HAND = "pasted by hand"


@dataclass(frozen=True)
class TrackOutcome:
    """The manifest's ``after`` block: what the run did to one track.

    Sidecar fields are file names beside the MP3. *sidecars_renamed* lets undo remove
    a rename's result even when the run stopped before writing its own sidecar.
    *sidecar_hashes* holds each sidecar the run wrote or renamed into place, hashed
    from the bytes the run wrote or read, never re-read after. *frames_written* is True
    when the run saved the track's lyrics frames: new ones written, or a strip's
    removal of old ones. *sidecar_links_ignored* names each sidecar link the run
    never read because it leads out of the album. *refusals* holds each provider call
    of the fetch that refused or failed instead of answering, in the order asked.
    *refetch_untimed* is True when a ``--refetch-untimed`` run asked the providers
    again for the track, whose lyrics were untimed only; *sidecar_unreadable* then
    says why its old ``.txt``, left for the strip to remove, could not be read, and
    *implausible* why the serving lyrics of its old sidecars or frames, each side
    judged apart, were too implausible to keep, which a track the refetch found
    nothing for is then `OutcomeStatus.REMOVED` for, with each such side removed whole.
    *refetch_implausible* is True when a ``--refetch-implausible`` run asked the
    providers again for the track, whose serving lyrics were implausible, or either
    refetch option did for a track holding a lyrics frame over the cap; *implausible*
    then says why, as for a refetch. Any run sets *implausible* alone,
    ``placeholder lyrics``, on a track whose only lyrics are placeholders, which it
    removes the same way when nothing answers.
    """

    status: OutcomeStatus
    source: Source | None = None
    kind: LyricsKind | None = None
    query: tuple[QueryMarker, ...] = ()
    sidecar_written: str | None = None
    sidecars_removed: tuple[str, ...] = ()
    sidecars_renamed: tuple[SidecarRename, ...] = ()
    sidecar_hashes: tuple[SidecarHash, ...] = ()
    frames_written: bool = False
    check: CheckResult | None = None
    error: StageError | None = None
    sidecar_links_ignored: tuple[str, ...] = ()
    refusals: tuple[ProviderRefusal, ...] = ()
    refetch_untimed: bool = False
    sidecar_unreadable: str | None = None
    implausible: str | None = None
    refetch_implausible: bool = False
    #: A silent track's measured whole-track peak, in dBFS.
    peak_dbfs: float | None = None
    #: Why a missed track's silence check failed, when it did.
    silence_failed: str | None = None
    #: What the track held before the run changed anything, None when not read.
    held: HeldBefore | None = None
    #: The album-relative path of the track a fix took the written text from, or
    #: `PASTED_BY_HAND` for a text ``--mode paste`` wrote.
    origin: str | None = None
    #: Why a fix kept a wrong text it would have removed: the album looks spoken
    #: (`lyrics_spoken`), so its transcripts are speech no text is sung in.
    withheld: str | None = None


@dataclass(frozen=True)
class StripResult:
    """What the strip stage removed from one track.

    *frames_removed* is True when the strip removed a lyrics frame the track held;
    *sidecars_removed* names each sidecar removed, links included, in name order.
    """

    frames_removed: bool
    sidecars_removed: tuple[str, ...]


@dataclass(frozen=True)
class FrameExport:
    """A backed-up frame in the manifest: its fields and where its export sits.

    *file* is the run-folder-relative ``.lrc`` / ``.txt`` export; *exact* is the
    ``.frames.json`` holding the frame verbatim when that export could not round-trip.
    """

    kind: FrameKind
    lang: str
    desc: str
    encoding: int
    format: int | None
    type: int | None
    file: str
    exact: str | None = None


@dataclass(frozen=True)
class SidecarCopy:
    """A sidecar that existed before the run: its name, and its copy or its link.

    A file is recorded by *file*, its byte-for-byte copy relative to the run folder. A
    symbolic link is recorded by *link*, its target as the link stored it, and nothing
    is copied: the bytes it leads to, if any, are not the track's. Exactly one of the
    two is set.
    """

    name: str
    file: str | None
    link: str | None = None

    def __post_init__(self) -> None:
        """Refuse a record that is both a copy and a link, or neither."""
        if (self.file is None) == (self.link is None):
            raise ValueError(
                "a sidecar record is a file copy or a link: not both, and not neither"
            )


@dataclass(frozen=True)
class TrackBefore:
    """The manifest's ``before`` block: what undo restores."""

    frames: tuple[FrameExport, ...]
    sidecars: tuple[SidecarCopy, ...]


@dataclass(frozen=True)
class ManifestTrack:
    """One track in a run: album-relative POSIX path, tag version, before and after.

    *before* is None when the track's backup failed; such a track is never written.
    *id3_version* is the version the track had before the run. None means it had no
    ID3v2 header, so no lyrics frame to put back: undo removes what the run added
    through `StripLyrics` (a write gave the file a v2.4 header, which it keeps) and
    never calls `RestoreFrames`, which takes a version.

    *displaced* lists, by album-relative path, every copy an undo of this track set
    aside in the run folder, an undo that then failed included, so each copy's origin
    stays on record.
    """

    path: str
    id3_version: tuple[int, int] | None
    before: TrackBefore | None
    after: TrackOutcome | None = None
    undone: bool = False
    displaced: tuple[str, ...] = ()


class RunState(StrEnum):
    """Where a run stands; each change is an atomic replace of the manifest."""

    BACKED_UP = "backed_up"
    COMPLETE = "complete"
    UNDONE = "undone"


class GeniusState(StrEnum):
    """Whether the run had a Genius key; the manifest never holds the key itself."""

    CONFIGURED = "configured"
    ABSENT = "absent"


@dataclass(frozen=True)
class Manifest:
    """A run's ``manifest.json``. Paths are album-relative, so *album* is always ``.``."""

    run_id: str
    lang: str
    state: RunState
    genius: GeniusState
    tracks: tuple[ManifestTrack, ...]
    album: str = "."
    schema: int = field(default=MANIFEST_SCHEMA)
    bandcamp: BandcampState = BandcampState.OFF
    youtube_music: CoolingState = CoolingState.OFF
    tekstowo: CoolingState = CoolingState.OFF
    silence_check: SilenceCheck = SilenceCheck.OFF
    #: The refusals a retry got past, by provider and reason (`lyrics_retried`).
    retried: RetriedCounts = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a schema this code does not know."""
        if self.schema != MANIFEST_SCHEMA:
            raise ManifestSchemaError(f"unknown manifest schema: {self.schema!r}")

    def selected(self, track: str | None) -> tuple[ManifestTrack, ...]:
        """Every track, in manifest order, or the one at album-relative path *track*.

        Raise `TrackNotInRunError` when the run does not list *track*.
        """
        if track is None:
            return self.tracks
        chosen = tuple(entry for entry in self.tracks if entry.path == track)
        if not chosen:
            raise TrackNotInRunError(f"{track} is not a track of run {self.run_id}")
        return chosen


class UndoStatus(StrEnum):
    """How undo ended for one track."""

    UNDONE = "undone"
    ALREADY_UNDONE = "already undone"
    ERROR = "error"


@dataclass(frozen=True)
class UndoTrackResult:
    """One track's undo: its album-relative path, status, and error if any.

    *error* is recorded against `ErrorStage.UNDO`, the shape a run's and a check's
    errors take, so every report prints one the same way.

    *displaced* names, by album-relative path, each sidecar copy this undo set aside
    in the run folder, on an error as well: a copy made before a later step failed
    is still there. *saved* is True when the undo saved the MP3, or may have before
    it failed: what ``--changed-list`` lists.
    """

    path: str
    status: UndoStatus
    error: StageError | None = None
    displaced: tuple[str, ...] = ()
    saved: bool = False


@dataclass(frozen=True)
class UndoReport:
    """An undo of one run; *newest* is False when a later run exists (warned on).

    *record_error* says why the manifest could not be written after a track: undo
    stopped there, and *tracks* holds every track it reached, that one included.
    *interrupted* says a Ctrl-C stopped it after the track it was restoring, and
    *not_reached* names, by album path, each selected track it then never began.
    """

    run_id: str
    newest: bool
    tracks: tuple[UndoTrackResult, ...]
    record_error: str | None = None
    interrupted: bool = False
    not_reached: tuple[str, ...] = ()

    @property
    def has_errors(self) -> bool:
        """True when any track's undo errored, as `RunReport` and `CheckReport` say it."""
        return any(track.status is UndoStatus.ERROR for track in self.tracks)
