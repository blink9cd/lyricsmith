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
Nothing here prints.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable

import lyrics_files
from lyrics_model import ProviderTable, ReadBackError, Source

#: The variable naming the XDG state folder; a relative value is ignored.
STATE_ENV = "XDG_STATE_HOME"
#: The home variable, read from the environment given before the process's own.
HOME_ENV = "HOME"
#: The state folder under the home folder when the variable gives none.
_HOME_STATE_DIR = Path(".local") / "state"
#: The folder inside the state folder that holds every source's file.
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


def _state_base(environ: Mapping[str, str]) -> Path:
    """The state folder: XDG_STATE_HOME when absolute, else ~/.local/state.

    A relative XDG_STATE_HOME is ignored, as the XDG convention asks, so the answer
    never depends on the working directory. HOME is read from *environ* too; the
    process's home folder stands in only when *environ* has none.
    """
    state = environ.get(STATE_ENV, "")
    if state and Path(state).is_absolute():
        return Path(state)
    home = environ.get(HOME_ENV)
    base = Path(home) if home else Path.home()
    return base / _HOME_STATE_DIR


def cooldown_path(environ: Mapping[str, str], source: Source) -> Path:
    """*source*'s cool-down file, in `STATE_DIR` under the state folder.

    A source with no entry in `STATE_FILES` is a `KeyError`: a programming error, not
    a run's.
    """
    return _state_base(environ) / STATE_DIR / STATE_FILES[source]


def format_utc(moment: datetime) -> str:
    """*moment* in UTC, to the second: ``2026-10-22T09:30:00Z``."""
    return moment.astimezone(UTC).strftime(_UTC_FORM)


def _parse_utc(value: object) -> datetime:
    """A time in exactly `format_utc`'s form, aware UTC; ValueError otherwise.

    ``strptime`` alone would take ``8:30`` for ``08:30``, so the text must also be
    what formatting the result gives back.
    """
    if not isinstance(value, str):
        raise ValueError("a cool-down time is not text")
    moment = datetime.strptime(value, _UTC_FORM).replace(tzinfo=UTC)
    if format_utc(moment) != value:
        raise ValueError("a cool-down time is not in the file's form")
    return moment


def _what(value: object) -> str:
    """A ``what`` of 1 to `_WHAT_MAX` printable characters; ValueError otherwise."""
    if not isinstance(value, str) or not 0 < len(value) <= _WHAT_MAX:
        raise ValueError("a cool-down's what is not short text")
    if not value.isprintable():
        raise ValueError("a cool-down's what is not printable")
    return value


# fuzz-target
def parse_cooldown(data: bytes, now: datetime) -> CoolDown:
    """The cool-down *data* holds, checked against *now*; ValueError when malformed.

    Malformed is anything over `MAX_COOLDOWN_BYTES`, anything but the three fields in
    their forms, a refusal later than *now*, or an end other than `COOL_DOWN` after the
    refusal. Undecodable bytes raise `UnicodeDecodeError`, a ValueError too.
    """
    if len(data) > MAX_COOLDOWN_BYTES:
        raise ValueError("a cool-down file too large")
    try:
        document: object = json.loads(data.decode("utf-8"))
    except RecursionError as error:  # nesting deeper than the decoder's stack
        raise ValueError("a cool-down file nested too deeply") from error
    if not isinstance(document, dict) or set(document) != _FIELDS:
        raise ValueError("not a cool-down record")
    refused_at = _parse_utc(document["refused_at"])
    until = _parse_utc(document["until"])
    what = _what(document["what"])
    # Checked before the sum, so the addition stays inside datetime's range.
    if refused_at > now:
        raise ValueError("a cool-down refused in the future")
    if until != refused_at + COOL_DOWN:
        raise ValueError("a cool-down whose end is not a day after its refusal")
    return CoolDown(refused_at, what, until)


def read_cooldown(path: Path, now: datetime) -> CoolDownRead:
    """The cool-down *path* holds; none for a missing file, a warning for a broken one."""
    try:
        return CoolDownRead(parse_cooldown(path.read_bytes(), now))
    except FileNotFoundError:
        return CoolDownRead(None)
    # A UnicodeDecodeError and a JSONDecodeError are both ValueErrors; each is named.
    except (OSError, ValueError) as error:
        warning = f"{path}: cool-down file ignored ({type(error).__name__})"
        return CoolDownRead(None, warning)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    lyrics_files.write_confirmed(path, json.dumps(document).encode())
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
