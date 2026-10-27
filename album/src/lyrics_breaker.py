"""A provider's breaker: when to stop asking a provider that keeps refusing, and its file.

The policy is pure. `OPEN_AFTER` overloaded, unreachable or rate-limited
refusals in a row (`COUNTED`) open the breaker for `FIRST_WINDOW`. Once the window has
ended one track may ask again (half-open): an answer, or a not found, closes it
(`after_answer`); a refusal reopens it at double the window, capped at `MAX_WINDOW`
(`after_refusal`). One blocked refusal (a ``401``, ``403``, ``407`` or ``451``, a
challenge page) opens it at once at the cap. An unrecognized refusal, a reply of a
shape this tool does not know or a client error of one request such as a ``400``, says
nothing about the provider's health, so it neither counts toward opening nor resets
the count. Every moment is aware UTC, kept to the second, as the file keeps it.

The state file is one per guarded provider (`BREAKER_FILES`), beside the
cool-down files (`lyrics_cooldown.state_folder`), so a run reads where the last one
left off: across a sweep's album processes, and across a resume. It holds four
fields: ``run``, the refusals counted in a row; ``reason``, the last counted or
blocked one's class; ``window_s``, the current window in seconds; and ``open_until``,
when it ends, in `lyrics_cooldown.format_utc`'s form. The last two are null while
the breaker is closed. It is written and read with the cool-down file's own
discipline (`lyrics_cooldown.write_record`, `load_record`, `read_state`): atomically
and confirmed, and a file that cannot be read, or does not hold a state this policy
can make, is a warning and no breaker, replaced on the next write. An edited window
ending further off than its own length is refused the same way, so no edit can switch
a provider off for good. A half-open breaker whose window ended more than `MAX_WINDOW`
before it is read is read as closed (`decayed`): a provider left alone that long starts
over, rather than a week-old window reopening at double on one refusal. Nothing here
prints.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from lyrics_cooldown import (
    format_utc,
    load_record,
    parse_utc,
    read_state,
    state_folder,
    write_record,
)
from lyrics_model import Source
from lyrics_refusal import Refusal

#: How many counted refusals in a row open a closed breaker.
OPEN_AFTER = 3
#: How long a breaker first stays open, and the longest it ever stays open.
FIRST_WINDOW = timedelta(minutes=5)
MAX_WINDOW = timedelta(minutes=60)
#: The classes that count toward opening: a provider too busy or too far to answer.
COUNTED = frozenset({Refusal.RATE_LIMITED, Refusal.OVERLOADED, Refusal.UNREACHABLE})
#: The classes an open breaker may hold as its reason.
_OPENING = COUNTED | {Refusal.BLOCKED}
#: Every window the policy makes: the first, doubled, up to the cap.
WINDOWS: frozenset[timedelta] = frozenset(
    min(FIRST_WINDOW * 2**doubling, MAX_WINDOW) for doubling in range(5)
)
#: Each guarded provider's state file, beside the cool-down files. MusicBrainz rides
#: on Bandcamp's step, so Bandcamp's breaker guards it too.
BREAKER_FILES: Mapping[Source, str] = MappingProxyType(
    {
        Source.LRCLIB: "lrclib_breaker.json",
        Source.NETEASE: "netease_breaker.json",
        Source.BANDCAMP: "bandcamp_breaker.json",
        Source.GENIUS: "genius_breaker.json",
    }
)
#: The providers a run guards with a breaker, in the chain's order.
GUARDED_SOURCES: tuple[Source, ...] = tuple(BREAKER_FILES)
#: The file's fields, exactly.
_FIELDS = frozenset({"run", "reason", "window_s", "open_until"})
#: The largest file read: a real one is ~100 bytes; the cap keeps deep nesting out.
MAX_BREAKER_BYTES = 1024
#: How a skipped step's detail names the window's end.
_CLOCK_FORM = "%H:%M:%S"


@dataclass(frozen=True)
class BreakerState:
    """A breaker: its count in a row, its last reason, and its window while open.

    *window* and *open_until* are both None while it is closed; once it has opened,
    *open_until* past is half-open: the next track may ask.
    """

    run: int = 0
    reason: Refusal | None = None
    window: timedelta | None = None
    open_until: datetime | None = None

    def is_open(self, now: datetime) -> bool:
        """Whether a step is skipped at *now*: open, its window not yet ended."""
        return self.open_until is not None and now < self.open_until


#: A closed breaker with nothing counted: where every provider starts.
CLOSED = BreakerState()


def after_answer(state: BreakerState) -> BreakerState:
    """*state* after an answer or a not found: closed, the count reset."""
    del state  # any answer closes it, whatever it was
    return CLOSED


def _opened(
    state: BreakerState, reason: Refusal, window: timedelta, now: datetime
) -> BreakerState:
    """*state* opened at *now* for *window*, for *reason*; *now* kept to the second."""
    start = now.astimezone(UTC).replace(microsecond=0)
    return dataclasses.replace(
        state, reason=reason, window=window, open_until=start + window
    )


def after_refusal(state: BreakerState, reason: Refusal, now: datetime) -> BreakerState:
    """*state* after a refusal of *reason* at *now*.

    Blocked opens it at the cap. A counted refusal of a breaker still open changes
    nothing: its window already holds. Of one whose window has ended (half-open), it
    reopens it at double the window, capped; of a closed one, it counts, and opens it
    at `OPEN_AFTER`. Any other class changes nothing.
    """
    if reason is Refusal.BLOCKED:
        return _opened(state, reason, MAX_WINDOW, now)
    if reason not in COUNTED or state.is_open(now):
        return state
    run = state.run + 1
    if state.window is not None:
        doubled = min(state.window * 2, MAX_WINDOW)
        return _opened(dataclasses.replace(state, run=run), reason, doubled, now)
    if run >= OPEN_AFTER:
        return _opened(BreakerState(run), reason, FIRST_WINDOW, now)
    return BreakerState(run, reason)


def open_detail(state: BreakerState) -> str:
    """The detail a skipped step records: ``breaker open until 09:35:00 UTC``.

    Words for a person only: the reports tell a skipped step by its refusal's
    ``unsent`` fact (`lyrics_breakers.BreakerGuard.shut`), never by this text. A
    breaker that has never opened has no end to name: a ValueError.
    """
    if state.open_until is None:
        raise ValueError("a closed breaker has no end")
    return f"breaker open until {state.open_until.strftime(_CLOCK_FORM)} UTC"


# --- The state file ----------------------------------------------------------------


@dataclass(frozen=True)
class BreakerRead:
    """The file's breaker, closed when none, and why a file was ignored."""

    state: BreakerState = CLOSED
    warning: str | None = None


def breaker_path(environ: Mapping[str, str], source: Source) -> Path:
    """*source*'s breaker file, beside the cool-down files; KeyError for another source."""
    return state_folder(environ) / BREAKER_FILES[source]


def _document(state: BreakerState) -> dict[str, object]:
    """*state* as its file's JSON object."""
    window, until = state.window, state.open_until
    return {
        "run": state.run,
        "reason": None if state.reason is None else state.reason.value,
        "window_s": None if window is None else int(window.total_seconds()),
        "open_until": None if until is None else format_utc(until),
    }


def breaker_bytes(state: BreakerState) -> bytes:
    """*state* as the bytes its file holds."""
    return json.dumps(_document(state)).encode()


def _count(value: object) -> int:
    """*value* as a count in a row: an integer, never a boolean, none below zero."""
    if type(value) is not int or value < 0:
        raise ValueError("a breaker's run is not a count")
    return value


def _reason(value: object) -> Refusal | None:
    """*value* as a reason that opens a breaker, or None."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in _OPENING:
        raise ValueError("a breaker's reason is not one that opens it")
    return Refusal(value)


def _window(value: object) -> timedelta | None:
    """*value* as one of the policy's windows in whole seconds, or None."""
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError("a breaker's window is not whole seconds")
    # Bounded before the timedelta is built: one past its range overflows instead.
    if not 0 < value <= MAX_WINDOW.total_seconds():
        raise ValueError("a breaker's window is not one the policy makes")
    window = timedelta(seconds=value)
    if window not in WINDOWS:
        raise ValueError("a breaker's window is not one the policy makes")
    return window


def _check_closed(state: BreakerState) -> None:
    """Refuse a closed *state* the policy cannot make: a count past opening, a block."""
    if state.run >= OPEN_AFTER:
        raise ValueError("a closed breaker counted past opening")
    if (state.reason is None) != (state.run == 0):
        raise ValueError("a closed breaker's count and reason disagree")
    if state.reason is Refusal.BLOCKED:
        raise ValueError("a closed breaker blocked")


def _check_open(state: BreakerState, now: datetime) -> None:
    """Refuse an opened *state* with no reason, or a window ending further off than it lasts."""
    assert state.window is not None and state.open_until is not None
    if state.reason is None:
        raise ValueError("an open breaker with no reason")
    if state.open_until > now + state.window:
        raise ValueError("a breaker open further off than its window")


def decayed(state: BreakerState, now: datetime) -> BreakerState:
    """*state* as read at *now*: closed once its window ended over `MAX_WINDOW` before.

    A breaker still open, or half-open for no longer than the longest window, is kept
    as it is, so a probe soon after the window still doubles it.
    """
    if state.open_until is not None and now - state.open_until > MAX_WINDOW:
        return CLOSED
    return state


# fuzz-target
def parse_breaker(data: bytes, now: datetime) -> BreakerState:
    """The breaker *data* holds, checked against *now*; ValueError when malformed.

    Malformed is anything over `MAX_BREAKER_BYTES`, anything but the four fields in
    their forms, a window with no end or an end with no window, or a state the policy
    cannot make (`_check_closed`, `_check_open`). A well-formed state is then read as
    `decayed` gives it at *now*.
    """
    document = load_record(data, _FIELDS, max_bytes=MAX_BREAKER_BYTES, what="breaker")
    run, reason = _count(document["run"]), _reason(document["reason"])
    window = _window(document["window_s"])
    until_value = document["open_until"]
    until = None if until_value is None else parse_utc(until_value)
    if (window is None) != (until is None):
        raise ValueError("a breaker's window and end disagree")
    state = BreakerState(run, reason, window, until)
    if window is None:
        _check_closed(state)
    else:
        _check_open(state, now)
    return decayed(state, now)


def read_breaker(path: Path, now: datetime) -> BreakerRead:
    """The breaker *path* holds; closed for a missing file, a warning for a broken one."""
    state, warning = read_state(
        path,
        lambda data: parse_breaker(data, now),
        "breaker",
        max_bytes=MAX_BREAKER_BYTES,
    )
    return BreakerRead(CLOSED if state is None else state, warning)


def write_breaker(path: Path, state: BreakerState) -> None:
    """Record *state* in *path*, atomically and confirmed.

    Raises `OSError` or `ReadBackError` when the file cannot be written.
    """
    write_record(path, _document(state))
