"""Each cooling source's cool-down file: the 24 hours after a refusal in which it is not asked.

When a cooling source refuses or challenges a request, its client stops asking it for
the rest of the run and records the refusal here (`record_stop`), so the next runs leave
it alone for `COOL_DOWN` too. Each source has its own file (`cooldown_path`), under
``$XDG_STATE_HOME`` when absolute, else ``~/.local/state``: state, not settings, so apart
from the config file, and the machine's relation with one service, so in no run folder.
The folder is ``lyricsmith``.
A table's provider reports its stop through `ReportsStop`, read by `table_stop`.

It holds three fields, ``refused_at``, ``what`` and ``until``, each time in
`format_utc`'s form, ``until`` exactly `COOL_DOWN` after ``refused_at``. It is written
atomically and read back (`lyrics_files.write_confirmed`), so a reader never sees half a
file. A file that cannot be read, or does not hold exactly that, is a warning and is
ignored rather than an error: a broken file must not stop a run, and an edited one, with
a refusal in the future or an end further off, must not switch its source off for good.
No warning quotes the file's content. No option clears a cool-down; deleting the file does.
The discipline is shared: the breakers' files (`lyrics_breaker`) sit in the same folder
(`state_folder`) and are parsed, read and written through the same helpers
(`load_record`, `read_state`, `write_record`, `parse_utc`). Nothing here prints.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, TypeVar, runtime_checkable

import lyrics_files
from lyrics_model import ProviderTable, ReadBackError, Source
from lyrics_state import state_root, write_state_file

#: The folder inside the state folder (`lyrics_state.state_root`) that holds every
#: source's file.
STATE_DIR = Path("lyricsmith")
#: Each source's cool-down file, beside the others in the state folder.
STATE_FILES: Mapping[Source, str] = MappingProxyType(
    {Source.YOUTUBE_MUSIC: "youtube_music.json", Source.TEKSTOWO: "tekstowo.json"}
)
#: How long a source is left alone after a refusal.
COOL_DOWN = timedelta(hours=24)
#: The file's time form: ISO 8601 to the second, in UTC, marked ``Z``.
_UTC_FORM = "%Y-%m-%dT%H:%M:%SZ"
#: The file's fields, exactly.
_FIELDS = frozenset({"refused_at", "what", "until"})
#: The longest ``what`` the file may hold.
_WHAT_MAX = 100
#: The largest file read: a real one is ~100 bytes; the cap keeps deep nesting out.
MAX_COOLDOWN_BYTES = 1024

_T = TypeVar("_T")


@dataclass(frozen=True)
class CoolDown:
    """A refusal's record: when it came, what it was, and when the cool-down ends."""

    refused_at: datetime  # UTC
    what: str  # "HTTP 429", "redirect to consent"
    until: datetime  # refused_at + COOL_DOWN


@dataclass(frozen=True)
class CoolDownRead:
    """The file's cool-down, if any, and why a file was ignored."""

    cool_down: CoolDown | None
    warning: str | None = None


def utc_now() -> datetime:
    """Now, aware, in UTC: the tool's one clock, which the run's wiring imports."""
    return datetime.now(UTC)


def state_folder(environ: Mapping[str, str]) -> Path:
    """`STATE_DIR` under the state folder *environ* names: where every source's file sits.

    The state folder is `lyrics_state.state_root`'s. The breakers' files
    (`lyrics_breaker`) sit here too, beside the cool-down files.
    """
    return state_root(environ) / STATE_DIR


def cooldown_path(environ: Mapping[str, str], source: Source) -> Path:
    """*source*'s cool-down file, in `STATE_DIR` under the state folder.

    A source with no entry in `STATE_FILES` is a `KeyError`: a programming error, not
    a run's.
    """
    return state_folder(environ) / STATE_FILES[source]


def format_utc(moment: datetime) -> str:
    """*moment* in UTC, to the second: ``2026-10-22T09:30:00Z``."""
    return moment.astimezone(UTC).strftime(_UTC_FORM)


def parse_utc(value: object) -> datetime:
    """A time in exactly `format_utc`'s form, aware UTC; ValueError otherwise.

    ``strptime`` alone would take ``8:30`` for ``08:30``, so the text must also be
    what formatting the result gives back.
    """
    if not isinstance(value, str):
        raise ValueError("a state file's time is not text")
    moment = datetime.strptime(value, _UTC_FORM).replace(tzinfo=UTC)
    if format_utc(moment) != value:
        raise ValueError("a state file's time is not in the file's form")
    return moment


def _what(value: object) -> str:
    """A ``what`` of 1 to `_WHAT_MAX` printable characters; ValueError otherwise."""
    if not isinstance(value, str) or not 0 < len(value) <= _WHAT_MAX:
        raise ValueError("a cool-down's what is not short text")
    if not value.isprintable():
        raise ValueError("a cool-down's what is not printable")
    return value


def load_record(
    data: bytes, fields: frozenset[str], *, max_bytes: int, what: str
) -> dict[str, object]:
    """The JSON object *data* holds, with exactly *fields*; ValueError otherwise.

    The shared first half of every state file's parser: anything over *max_bytes* is
    refused unread, nesting deeper than the decoder's stack is a ValueError, and so are
    undecodable bytes (`UnicodeDecodeError`). *what* names the file in the error.
    """
    if len(data) > max_bytes:
        raise ValueError(f"a {what} file too large")
    try:
        document: object = json.loads(data.decode("utf-8"))
    except RecursionError as error:  # nesting deeper than the decoder's stack
        raise ValueError(f"a {what} file nested too deeply") from error
    if not isinstance(document, dict) or set(document) != fields:
        raise ValueError(f"not a {what} record")
    return document


# fuzz-target
def parse_cooldown(data: bytes, now: datetime) -> CoolDown:
    """The cool-down *data* holds, checked against *now*; ValueError when malformed.

    Malformed is anything over `MAX_COOLDOWN_BYTES`, anything but the three fields in
    their forms, a refusal later than *now*, or an end other than `COOL_DOWN` after the
    refusal. Undecodable bytes raise `UnicodeDecodeError`, a ValueError too.
    """
    document = load_record(
        data, _FIELDS, max_bytes=MAX_COOLDOWN_BYTES, what="cool-down"
    )
    refused_at = parse_utc(document["refused_at"])
    until = parse_utc(document["until"])
    what = _what(document["what"])
    # Checked before the sum, so the addition stays inside datetime's range.
    if refused_at > now:
        raise ValueError("a cool-down refused in the future")
    if until != refused_at + COOL_DOWN:
        raise ValueError("a cool-down whose end is not a day after its refusal")
    return CoolDown(refused_at, what, until)


def read_state(
    path: Path, parse: Callable[[bytes], _T], what: str, *, max_bytes: int
) -> tuple[_T | None, str | None]:
    """What *parse* reads from *path*, and a warning; none for a missing file.

    A file that cannot be read, or that *parse* refuses, is no state and a warning
    naming the file and the error's type, never quoting the content. It is read
    through `lyrics_files.read_regular`, so a FIFO pipe or a folder under the name is
    such a file at once, never waited on. No more than *max_bytes* and one byte is
    read, so *parse*, given *parse*'s own cap as *max_bytes*, refuses an oversized
    file by that byte without it being read whole. *what* names the kind of file in
    the warning.
    """
    try:
        return parse(lyrics_files.read_regular(path, max_bytes + 1)), None
    except FileNotFoundError:
        return None, None
    # A UnicodeDecodeError and a JSONDecodeError are both ValueErrors; each is named.
    except (OSError, ValueError) as error:
        return None, f"{path}: {what} file ignored ({type(error).__name__})"


def read_cooldown(path: Path, now: datetime) -> CoolDownRead:
    """The cool-down *path* holds; none for a missing file, a warning for a broken one."""
    cool_down, warning = read_state(
        path,
        lambda data: parse_cooldown(data, now),
        "cool-down",
        max_bytes=MAX_COOLDOWN_BYTES,
    )
    return CoolDownRead(cool_down, warning)


def write_record(path: Path, document: Mapping[str, object]) -> None:
    """Write *document* as JSON to *path*, atomically and confirmed, its folder made.

    A state file: written 0o600, each folder made for it 0o700
    (`lyrics_state.write_state_file`). Raises `OSError` or `ReadBackError` when the
    file cannot be written.
    """
    write_state_file(path, json.dumps(document).encode())


def write_cooldown(path: Path, refused_at: datetime, what: str) -> CoolDown:
    """Record a refusal at *refused_at* in *path*, atomically and confirmed.

    The time is kept to the second, as the file stores it, so the record returned is
    the one a later read gives back. Raises `OSError` or `ReadBackError` when the file
    cannot be written.
    """
    moment = refused_at.astimezone(UTC).replace(microsecond=0)
    cool_down = CoolDown(moment, what, moment + COOL_DOWN)
    document = {
        "refused_at": format_utc(cool_down.refused_at),
        "what": cool_down.what,
        "until": format_utc(cool_down.until),
    }
    write_record(path, document)
    return cool_down


def is_cooling(cool_down: CoolDown | None, now: datetime) -> bool:
    """Whether *cool_down* still holds at *now*: it ends at its ``until``, not after."""
    return cool_down is not None and now < cool_down.until


@dataclass(frozen=True)
class Stop:
    """What stopped a source for the run, the cool-down's end, and a failed write."""

    what: str
    until: datetime
    write_error: str | None = None


def record_stop(path: Path, now: datetime, what: str) -> Stop:
    """Write *what*'s cool-down to *path*; a write that fails still stops, and says why."""
    try:
        cool_down = write_cooldown(path, now, what)
    except (OSError, ReadBackError) as error:
        return Stop(what, now + COOL_DOWN, write_error=str(error))
    return Stop(what, cool_down.until)


@dataclass(frozen=True)
class CoolingSetup:
    """What the table builds a cooling source with: where its cool-down is written."""

    state_path: Path
    now: Callable[[], datetime] = utc_now


@runtime_checkable
class ReportsStop(Protocol):
    """A step that can say what stopped it for the run."""

    def provider_stop(self) -> Stop | None:
        """What stopped the step's source, or None while it still runs."""


def table_stop(table: ProviderTable, source: Source) -> Stop | None:
    """*source*'s stop in *table*, or `None` when it is absent or has not stopped."""
    for key, provider in table.items():
        if key.source is source and isinstance(provider, ReportsStop):
            return provider.provider_stop()
    return None
