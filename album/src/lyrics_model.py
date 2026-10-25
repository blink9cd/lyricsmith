"""The shared types of the album tool, and the pure LRC and frame conversions.

Every other module here builds against what this one fixes: the errors one module
raises and another acts on (held in `lyrics_errors`, re-exported here), the lyrics
values, one ID3 lyrics frame held verbatim, a track's tags, and the fetch chain's
provider seam, result and sidecar state. Its two siblings complete the interface:
`lyrics_records` (the Genius key, the check's verdict, the per-track outcome, the run
manifest) and `lyrics_stages` (a `typing.Protocol` per stage function, so the type
checker holds every implementation to one signature).

Pure: no I/O, no printing, no third-party import. The runtime floor is Python 3.12.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias

from lyrics_errors import LyricsError as LyricsError
from lyrics_errors import ConfigError as ConfigError
from lyrics_errors import FrameTooLargeError as FrameTooLargeError
from lyrics_errors import ManifestSchemaError as ManifestSchemaError
from lyrics_errors import NoRunError as NoRunError
from lyrics_errors import ReadBackError as ReadBackError
from lyrics_errors import SidecarDecodeError as SidecarDecodeError
from lyrics_errors import TagVersionError as TagVersionError
from lyrics_errors import TrackNotInRunError as TrackNotInRunError

from lyrics_cap import refuse_oversized_frames

_MS_PER_SECOND = 1000
_MS_PER_MINUTE = 60 * _MS_PER_SECOND
#: The most digits a stamp's minutes may have. Nine is far past the latest time a SYLT
#: frame holds (2**32 - 1 ms is 71582 minutes), so every real stamp reads back; a longer
#: run is no stamp, since thousands of digits pass CPython's int() digit limit and raise.
#: A provider can still give a start past it (YouTube Music's start text allows 15
#: digits of milliseconds); such a line reads back as untimed and is recorded so.
MAX_STAMP_MINUTE_DIGITS = 9
#: One LRC time stamp, optionally preceded by whitespace: minutes, seconds 0-59, and a
#: 1-3 digit fraction after "." or ":".
_STAMP = re.compile(
    rf"\s*\[(\d{{1,{MAX_STAMP_MINUTE_DIGITS}}}):([0-5]?\d)(?:[.:](\d{{1,3}}))?\]"
)
#: Milliseconds per unit of a fraction, by its digit count: tenths, hundredths, ms.
_FRACTION_SCALE = {0: 0, 1: 100, 2: 10, 3: 1}


# --- Lyrics values ---------------------------------------------------------------


class LyricsKind(StrEnum):
    """Whether lyrics carry time stamps."""

    SYNCED = "synced"
    UNSYNCED = "unsynced"


@dataclass(frozen=True)
class SyncedLine:
    """One timed line: its start in milliseconds and its text."""

    ms: int
    text: str

    def __post_init__(self) -> None:
        """Refuse a negative time, which no frame or LRC stamp can hold."""
        if self.ms < 0:
            raise ValueError(f"negative time stamp: {self.ms} ms")


@dataclass(frozen=True)
class SyncedLyrics:
    """Lyrics with at least one timed line."""

    lines: tuple[SyncedLine, ...]

    def __post_init__(self) -> None:
        """Refuse an empty line list: synced lyrics with no line are no lyrics."""
        if not self.lines:
            raise ValueError("synced lyrics need at least one line")

    @property
    def kind(self) -> LyricsKind:
        """Always `LyricsKind.SYNCED`."""
        return LyricsKind.SYNCED

    def plain_text(self) -> str:
        """The lines' text with the time stamps stripped, one line each."""
        return "\n".join(line.text for line in self.lines)


@dataclass(frozen=True)
class PlainLyrics:
    """Lyrics with no time stamps, kept verbatim."""

    text: str

    @property
    def kind(self) -> LyricsKind:
        """Always `LyricsKind.UNSYNCED`."""
        return LyricsKind.UNSYNCED

    def plain_text(self) -> str:
        """The text itself."""
        return self.text


Lyrics: TypeAlias = SyncedLyrics | PlainLyrics


def _stamp_ms(match: re.Match[str]) -> int:
    """The milliseconds one `_STAMP` match denotes."""
    minutes, seconds, fraction = match.group(1), match.group(2), match.group(3) or ""
    scaled = int(fraction or "0") * _FRACTION_SCALE[len(fraction)]
    return int(minutes) * _MS_PER_MINUTE + int(seconds) * _MS_PER_SECOND + scaled


def _parse_lrc_line(line: str) -> list[SyncedLine]:
    """Every timed line one LRC text line holds: one per leading stamp, else none."""
    stamps: list[int] = []
    position = 0
    while match := _STAMP.match(line, position):
        stamps.append(_stamp_ms(match))
        position = match.end()
    text = line[position:].strip()
    return [SyncedLine(ms, text) for ms in stamps]


# fuzz-target
def parse_lrc(text: str) -> Lyrics:
    """Read LRC *text* as synced lyrics, or as plain lyrics when no line is timed.

    Each line's leading stamps (``[mm:ss]``, ``[mm:ss.x]``, ``[mm:ss.xx]``,
    ``[mm:ss.xxx]``, or ``:`` before the fraction) give one timed line each, so a
    repeated chorus written once with several stamps expands in place. Lines with no
    stamp (``[ar:...]`` tags, blank lines, seconds past 59, minutes past
    `MAX_STAMP_MINUTE_DIGITS` digits) are skipped; an ``[offset:]``
    tag is too, so it never shifts the timing. The result is in time order, ties in file
    order. A text with no timed line comes back verbatim as `PlainLyrics`.
    """
    lines = [timed for raw in text.splitlines() for timed in _parse_lrc_line(raw)]
    if not lines:
        return PlainLyrics(text)
    return SyncedLyrics(tuple(sorted(lines, key=lambda line: line.ms)))


def _format_stamp(ms: int) -> str:
    """One millisecond LRC stamp, ``[mm:ss.mmm]``; minutes widen past 99."""
    minutes, rest = divmod(ms, _MS_PER_MINUTE)
    seconds, millis = divmod(rest, _MS_PER_SECOND)
    return f"[{minutes:02d}:{seconds:02d}.{millis:03d}]"


def format_lrc(lines: Sequence[SyncedLine]) -> str:
    """LRC text for *lines*: one millisecond-stamped line each, newline-terminated.

    `parse_lrc` reads the result back unchanged when the lines are in time order and
    each text is a single line with no surrounding whitespace that does not open with
    ``[``, and each time's minutes fit in `MAX_STAMP_MINUTE_DIGITS` digits.
    A line outside that shape (a frame entry holding a newline, say) does not
    survive the trip, which is why a caller that must be lossless compares the two.
    """
    return "".join(f"{_format_stamp(line.ms)}{line.text}\n" for line in lines)


# --- Tags and frames -------------------------------------------------------------


class FrameKind(StrEnum):
    """The two ID3 lyrics frames."""

    SYLT = "SYLT"
    USLT = "USLT"


@dataclass(frozen=True)
class SyltEntry:
    """One `SYLT` entry as stored: its text and its time, in the frame's own units."""

    text: str
    time: int

    def __post_init__(self) -> None:
        """Refuse a negative time: ID3 stores a SYLT time stamp unsigned."""
        if self.time < 0:
            raise ValueError(f"a SYLT time cannot be negative: {self.time}")


@dataclass(frozen=True)
class FrameRecord:
    """One `SYLT` or `USLT` frame, verbatim, as read from a file or restored to one.

    *format* (1 = MPEG frames, 2 = milliseconds) and *type* (1 = lyrics) belong to
    `SYLT` only and are None on a `USLT`. *payload* is the `USLT` text, or the `SYLT`
    entries in stored order.
    """

    kind: FrameKind
    lang: str
    desc: str
    encoding: int
    format: int | None
    type: int | None
    payload: str | tuple[SyltEntry, ...]

    def __post_init__(self) -> None:
        """Refuse a frame whose fields contradict its kind."""
        if self.kind is FrameKind.USLT:
            consistent = isinstance(self.payload, str) and self.format is None
            consistent = consistent and self.type is None
        else:
            consistent = isinstance(self.payload, tuple)
            consistent = consistent and None not in (self.format, self.type)
        if not consistent:
            raise ValueError(f"inconsistent {self.kind} frame: {self!r}")


@dataclass(frozen=True)
class TrackTags:
    """What a track's tags say: the query fields, the duration, and every lyrics frame.

    *id3_version* is ``(2, minor)`` from the loaded tag, or None for a file with no
    ID3v2 header, which therefore holds no lyrics frame. *duration_s* is None when
    the audio length cannot be read. *album* is None when absent or blank, like the
    artist and title.
    """

    artist: str | None
    title: str | None
    duration_s: float | None
    id3_version: tuple[int, int] | None
    frames: tuple[FrameRecord, ...]
    album: str | None = None
    #: Every genre of the TCON frame, ID3v1 references named; empty without one.
    genres: tuple[str, ...] = ()


#: The SYLT time-stamp format in milliseconds; format 1 counts MPEG frames instead.
SYLT_MILLISECONDS = 2


def frame_rank(frame: FrameRecord, lang: str) -> tuple[bool, bool]:
    """Sort key putting *lang* first, then an empty description: what this tool writes."""
    return (frame.lang != lang, frame.desc != "")


def _best_frame(frames: Sequence[FrameRecord], lang: str) -> FrameRecord | None:
    """The best-ranked of *frames*, the first stored winning a tie, or None if empty."""
    return min(frames, key=lambda frame: frame_rank(frame, lang), default=None)


def _entries(frame: FrameRecord) -> tuple[SyltEntry, ...]:
    """A SYLT frame's entries; a USLT has none."""
    return frame.payload if isinstance(frame.payload, tuple) else ()


def lyrics_from_frames(frames: Sequence[FrameRecord], lang: str) -> Lyrics | None:
    """The lyrics a track's SYLT / USLT *frames* hold, or None when they hold none.

    The one conversion both sides of the check go through: frames read back from a
    file, and frames restored from a run's backup. The rules, in order:

    - A SYLT in milliseconds with at least one entry wins over any USLT, as synced
      lyrics. Its entries keep their STORED order and their text verbatim: nothing is
      sorted, because the check's out-of-order row looks for exactly the disorder a
      time sort would hide.
    - Otherwise a USLT whose text is not blank, verbatim, as plain lyrics.
    - Otherwise any SYLT whose timestamp format is not milliseconds, as plain lyrics:
      its entries' text, one per line. Its times are never read as milliseconds;
      converting them would need the audio's frame rate, and a guess would feed the
      timing rows nonsense.
    - Among several frames at the same step, the one in *lang* with an empty
      description wins (what this tool writes), language before description, and the
      first stored breaks a tie. The SYLT content type is not consulted: taggers
      disagree on it, and the words are lyrics whichever type they were filed under.

    Any of *frames* over the lyrics cap raises `FrameTooLargeError` (`lyrics_cap`).
    """
    refuse_oversized_frames(frames)
    sylt = [f for f in frames if f.kind is FrameKind.SYLT and _entries(f)]
    timed = _best_frame([f for f in sylt if f.format == SYLT_MILLISECONDS], lang)
    if timed is not None:
        entries = _entries(timed)
        return SyncedLyrics(tuple(SyncedLine(e.time, e.text) for e in entries))
    uslt = [f for f in frames if isinstance(f.payload, str) and f.payload.strip()]
    plain = _best_frame(uslt, lang)
    if plain is not None:
        return PlainLyrics(str(plain.payload))
    untimed = _best_frame(sylt, lang)
    if untimed is None:
        return None
    return PlainLyrics("\n".join(entry.text for entry in _entries(untimed)))


# --- The fetch chain -------------------------------------------------------------


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
SOURCE_LABELS: Mapping[str, str] = MappingProxyType({"Lrclib": "LRCLIB"})


class QueryMarker(StrEnum):
    """A way the search query departed from the track's own artist and title tags."""

    FILENAME = "filename"
    STRIPPED = "stripped"


@dataclass(frozen=True)
class TrackQuery:
    """The artist and title a track is searched by, with its album and length.

    *from_filename* is set when a tag was missing and the file stem stood in;
    *stripped* when the title lost its trailing bracketed groups for the retry.
    *album* and *duration_s* are None when unknown: LRCLIB's exact lookup needs both,
    and the matcher compares a search hit's length with *duration_s*.
    """

    artist: str
    title: str
    from_filename: bool = False
    stripped: bool = False
    album: str | None = None
    duration_s: float | None = None

    @property
    def markers(self) -> tuple[QueryMarker, ...]:
        """The summary's ``query:`` markers for this query, in a fixed order."""
        flags = (
            (self.from_filename, QueryMarker.FILENAME),
            (self.stripped, QueryMarker.STRIPPED),
        )
        return tuple(marker for is_set, marker in flags if is_set)


@dataclass(frozen=True)
class ProviderKey:
    """One step of the chain: a provider asked for one kind of lyrics."""

    source: Source
    kind: LyricsKind


#: A provider call: the query in, the provider's raw text out, or None for "nothing".
#: Raising is a provider error; the chain moves on to the next step either way.
Provider: TypeAlias = Callable[[TrackQuery], str | None]
#: The providers a fetch may use. A step whose key is absent is skipped, which is how
#: a run without a Genius key, or an offline run, leaves providers out.
ProviderTable: TypeAlias = Mapping[ProviderKey, Provider]


class AttemptOutcome(StrEnum):
    """How one provider call ended."""

    ANSWERED = "answered"
    EMPTY = "empty"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class ProviderAttempt:
    """One provider call: which step, on which query, and how it ended."""

    key: ProviderKey
    query_stripped: bool
    outcome: AttemptOutcome
    detail: str


#: The attempt outcomes that are a provider's failure, not its answer: a track's
#: outcome records them as refusals, and compare and the tally count them as failed.
REFUSED_OUTCOMES = frozenset({AttemptOutcome.ERROR, AttemptOutcome.TIMEOUT})


@dataclass(frozen=True)
class FetchResult:
    """What the chain found for one track, with every attempt it made.

    *query* is the query the summary reports: the stripped retry's when that retry
    found lyrics, else the track's own, even when a retry ran. Each attempt records
    which pass it belonged to.
    """

    lyrics: Lyrics | None
    source: Source | None
    query: TrackQuery
    attempts: tuple[ProviderAttempt, ...]

    def __post_init__(self) -> None:
        """Refuse lyrics without a source, or a source without lyrics."""
        if (self.lyrics is None) != (self.source is None):
            raise ValueError("lyrics and their source come together or not at all")

    @property
    def found(self) -> bool:
        """True when the chain accepted an answer."""
        return self.lyrics is not None

    @property
    def every_provider_failed(self) -> bool:
        """True when nothing was found and every attempt raised or timed out.

        That is the track's fetch error. "Every provider answered, none had lyrics"
        is not found, which is not an error.
        """
        if self.found or not self.attempts:
            return False
        return all(attempt.outcome in REFUSED_OUTCOMES for attempt in self.attempts)


@dataclass(frozen=True)
class SidecarRename:
    """A sidecar renamed beside its MP3, by file name."""

    old: str
    new: str


@dataclass(frozen=True)
class SidecarState:
    """A track's sidecars after normalization, and what normalizing changed.

    *lyrics* is the sidecar's content: synced from a timed ``.lrc``, unsynced from a
    ``.txt``, or None when neither exists. Computed without applying (a dry run), it
    describes the same outcome: what would be renamed and removed, and the lyrics the
    surviving sidecar would serve.
    """

    lyrics: Lyrics | None
    renamed: tuple[SidecarRename, ...] = ()
    removed: tuple[str, ...] = ()
    #: Each renamed file's new name and the SHA-256 (hex) of the bytes read from it.
    hashes: tuple[tuple[str, str], ...] = ()
    #: Each sidecar name holding a link that leads out of the album, so never read:
    #: the reports name it, or it would look like a link that leads nowhere.
    links_ignored: tuple[str, ...] = ()
    #: The source the ``.lrc``'s header labels it with (`machine_label`), else None.
    label: Source | None = None


@dataclass(frozen=True)
class SidecarWrite:
    """The sidecar written beside an MP3, its bytes' SHA-256 (hex), and any removed."""

    written: str
    sha256: str
    removed: tuple[str, ...] = ()
