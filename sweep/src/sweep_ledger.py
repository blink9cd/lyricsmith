"""The sweep's ledger: one JSON line per album visited, kept outside the library.

**Where.** ``$XDG_STATE_HOME/lyricsmith/sweep/`` when that variable holds an absolute
path, else ``~/.local/state/lyricsmith/sweep/``: the library itself is never written by
the sweep, only by the album tool. One ledger per library, named by a hash of the library's
resolved path (`ledger_path`), so two libraries never share one and a library named by
another spelling of its path finds its own.

**What.** Each line is a JSON object. An album's entry (``"kind": "album"``) holds the
album relative to the library, its run id (null when no run was found, none being
looked for after a child never reaped or one that could not run, or when its manifest
could not be read; kept, if already found, when a Ctrl-C cut the read short),
the album tool's exit code (null for a child never reaped, or an album the sweep could not
run the album tool on), how the visit ended (`Outcome`), the counts tallied from its
manifest (null whenever no tally was made), the refusing tracks and providers, the
language the album tool ran in, the sweep's pass (`SweepPass`), and the time. An entry
written before the language was recorded reads as the album tool's default language, and
one written before the pass was recorded as the normal pass. A restart mark
(``"kind": "restart"``) records a ``--restart`` and its pass: entries of that pass
before it, in every language, no longer count as done (`done_albums`).

**Rewritten whole, atomically.** An append reads the file, adds one line, and replaces
the file through the album tool's confirmed atomic write, so a reader never sees half a
line and a write that did not land is an error. The bytes already there are kept as
they were, a line that cannot be read included, for a person to see.

**Read tolerantly.** The ledger is a file a person may edit, so `parse_record` is a
parser of untrusted input: any shape it does not expect raises `LedgerLineError` and
nothing else, and `read_ledger` reports such a line by number and skips it, never ending
the sweep over it. Nothing here prints.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TypeVar

from album import DEFAULT_LANG
from lyrics_files import write_confirmed
from sweep_tally import Counts

#: The folder under the state directory that holds every ledger.
LEDGER_DIR_NAME = Path("lyricsmith") / "sweep"
#: The state directory variable, and the fallback under HOME when it is not usable.
_STATE_ENV = "XDG_STATE_HOME"
_STATE_FALLBACK = (".local", "state")
#: How many hex digits of the library's hash name its ledger: plenty to keep two
#: libraries apart, short enough to read.
_HASH_DIGITS = 16
_KIND_ALBUM, _KIND_RESTART = "album", "restart"
#: The pass's key in a line: ``pass`` is a Python keyword, so the field is ``pass_``.
_PASS_KEY, _PASS_FIELD = "pass", "pass_"
_ENCODING = "utf-8"
#: The ``counts`` fields added after the ledger was first written, each read as its
#: default from a line that predates it.
_LATER_COUNTS = frozenset({"no_lyrics_expected", "silent"})

_T = TypeVar("_T")


class SweepPass(StrEnum):
    """Which pass a sweep makes. Each keeps its own done set in the one ledger."""

    #: The album tool run as it runs by default.
    NORMAL = "normal"
    #: The album tool run with ``--refetch-untimed``, re-asking untimed-only tracks.
    REFETCH_UNTIMED = "refetch-untimed"


class Outcome(StrEnum):
    """How an album's visit ended. Only ``done`` keeps a resumed sweep from retrying it."""

    #: The album tool finished (exit 0 or 1) and its manifest was read.
    DONE = "done"
    #: The album tool finished, but its providers only refused (`sweep_tally`).
    REFUSED = "refused"
    #: The album tool could not run (exit 2): the album locked by another run, say. Or
    #: the sweep could not run it at all (its run folder unreadable): no exit code.
    COULD_NOT_RUN = "could not run"
    #: Ctrl-C stopped the album tool (exit 130), or reached the sweep while it read the
    #: run the album tool made, whatever the album tool exited with (0, say).
    INTERRUPTED = "interrupted"
    #: The album tool ran past the per-album bound and was stopped.
    TIMED_OUT = "timed out"
    #: The album tool finished, but no new run's manifest could be read.
    NO_RECORD = "no record"
    #: The album tool made its run but could not write its record, the manifest, or its
    #: --changed-list (exit 3): what manifest there is was read and tallied.
    RECORD_NOT_WRITTEN = "record not written"
    #: The album tool ended with an exit code it never gives.
    FAILED = "failed"
    #: The album tool finished (exit 0 or 1) offline, asking no provider: never done, so
    #: a later sweep that may ask the providers visits the album.
    OFFLINE = "offline"
    #: The album tool would not stop even when killed, so it was left, never reaped.
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class LedgerEntry:
    """One album's visit, as its ledger line holds it.

    *exit_code* is None for a child that was never reaped, or never started; *lang*
    is the language the album tool ran in, and *pass_* the sweep's pass, held in the
    line as ``pass``.
    """

    album: str
    run_id: str | None
    exit_code: int | None
    outcome: Outcome
    time: str
    counts: Counts | None = None
    refusing_tracks: int = 0
    refusing_providers: tuple[str, ...] = ()
    lang: str = DEFAULT_LANG
    pass_: SweepPass = SweepPass.NORMAL


@dataclass(frozen=True)
class RestartMark:
    """A ``--restart``: every entry of its pass before it no longer counts as done."""

    time: str
    pass_: SweepPass = SweepPass.NORMAL


#: One line of the ledger, either kind.
LedgerRecord = LedgerEntry | RestartMark


class LedgerLineError(ValueError):
    """A ledger line that is not one this tool writes."""


@dataclass(frozen=True)
class LedgerRead:
    """Every record read, in file order, and a ``line N: why`` for each line skipped."""

    records: tuple[LedgerRecord, ...]
    problems: tuple[str, ...]


# --- Where -----------------------------------------------------------------------------


def ledger_path(library: Path, environ: Mapping[str, str]) -> Path:
    """The ledger file of *library*, under the state directory *environ* names."""
    state = environ.get(_STATE_ENV, "")
    root = (
        Path(state) if os.path.isabs(state) else Path.home().joinpath(*_STATE_FALLBACK)
    )
    digest = hashlib.sha256(str(library.resolve()).encode(_ENCODING)).hexdigest()
    return root / LEDGER_DIR_NAME / f"ledger-{digest[:_HASH_DIGITS]}.jsonl"


# --- Writing ---------------------------------------------------------------------------


def encode_record(record: LedgerRecord) -> str:
    """*record* as one ledger line, without its newline; ASCII, so any text survives."""
    if isinstance(record, RestartMark):
        document: dict[str, object] = {"kind": _KIND_RESTART, "time": record.time}
    else:
        document = {"kind": _KIND_ALBUM, **asdict(record)}
        del document[_PASS_FIELD]
        document["outcome"] = record.outcome.value
        document["refusing_providers"] = list(record.refusing_providers)
    document[_PASS_KEY] = record.pass_.value
    return json.dumps(document, ensure_ascii=True, sort_keys=True)


def append_record(path: Path, record: LedgerRecord) -> None:
    """Add *record* to the ledger at *path*, rewriting the file atomically.

    Missing folders are created, and every byte already there is kept as it was.
    """
    existing = path.read_bytes() if path.is_file() else b""
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    write_confirmed(path, existing + encode_record(record).encode(_ENCODING) + b"\n")


def append_unless_last(path: Path, record: LedgerRecord) -> None:
    """Add *record* to the ledger at *path*, unless it is already the file's last line.

    So an append retried after a Ctrl-C cut the first short writes the record once,
    whether or not the first attempt's atomic replace had landed.
    """
    line = encode_record(record).encode(_ENCODING) + b"\n"
    if path.is_file() and path.read_bytes().endswith(line):
        return
    append_record(path, record)


# --- Reading ---------------------------------------------------------------------------


def _field(document: dict[str, object], name: str, kind: type[_T]) -> _T:
    """*document*'s *name*, refused unless it is a *kind*; a boolean is never a number."""
    value = document.get(name)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise LedgerLineError(f"{name} is not {kind.__name__}")
    return value


def _optional(document: dict[str, object], name: str, kind: type[_T]) -> _T | None:
    """*document*'s *name* when it is not null, refused unless it is a *kind*."""
    return None if document.get(name) is None else _field(document, name, kind)


def _album(value: str) -> str:
    """*value* when it names a folder inside a library, else refused."""
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise LedgerLineError("album is not a path inside the library")
    return value


def _member(value: str, enum: Callable[[str], _T]) -> _T:
    """*value* as a member of *enum*, else refused."""
    try:
        return enum(value)
    except ValueError as error:
        raise LedgerLineError(f"unknown value {value!r}") from error


def _counts(document: dict[str, object]) -> Counts | None:
    """The ``counts`` object as `Counts`, or None when it is null."""
    raw = _optional(document, "counts", dict)
    if raw is None:
        return None
    # A count added after a ledger line was written reads as its default, so an older
    # ledger still reads: one missing from *raw* is left out, for `Counts` to default.
    # A count every line has held since the first is required.
    values: dict[str, int] = {
        f.name: _field(raw, f.name, int)
        for f in fields(Counts)
        if f.name in raw or f.name not in _LATER_COUNTS
    }
    return Counts(**values)


def _providers(document: dict[str, object]) -> tuple[str, ...]:
    """The ``refusing_providers`` list, each a string."""
    names = _field(document, "refusing_providers", list)
    if not all(isinstance(name, str) for name in names):
        raise LedgerLineError("refusing_providers holds a value that is not text")
    return tuple(names)


def _lang(document: dict[str, object]) -> str:
    """The ``lang`` field; an entry from before it was recorded ran in the default."""
    if "lang" not in document:
        return DEFAULT_LANG
    return _field(document, "lang", str)


def _pass(document: dict[str, object]) -> SweepPass:
    """The ``pass`` field; a line from before it was recorded is the normal pass."""
    if _PASS_KEY not in document:
        return SweepPass.NORMAL
    return _member(_field(document, _PASS_KEY, str), SweepPass)


def _entry(document: dict[str, object]) -> LedgerEntry:
    """An album's entry from its decoded line."""
    return LedgerEntry(
        album=_album(_field(document, "album", str)),
        run_id=_optional(document, "run_id", str),
        exit_code=_optional(document, "exit_code", int),
        outcome=_member(_field(document, "outcome", str), Outcome),
        time=_field(document, "time", str),
        counts=_counts(document),
        refusing_tracks=_field(document, "refusing_tracks", int),
        refusing_providers=_providers(document),
        lang=_lang(document),
        pass_=_pass(document),
    )


# fuzz-target
def parse_record(line: str) -> LedgerRecord:
    """One ledger line as its record; raise `LedgerLineError` on anything else."""
    try:
        document = json.loads(line)
    except (ValueError, RecursionError) as error:
        raise LedgerLineError("not JSON") from error
    if not isinstance(document, dict):
        raise LedgerLineError("not a JSON object")
    kind = document.get("kind")
    if kind == _KIND_RESTART:
        return RestartMark(_field(document, "time", str), _pass(document))
    if kind == _KIND_ALBUM:
        return _entry(document)
    raise LedgerLineError("unknown kind")


def _decode(raw: bytes) -> str:
    """*raw* as text, refused unless it is UTF-8."""
    try:
        return raw.decode(_ENCODING)
    except UnicodeDecodeError as error:
        raise LedgerLineError("not UTF-8") from error


def read_ledger(path: Path) -> LedgerRead:
    """Every record of the ledger at *path*; a missing ledger is an empty one.

    A blank line is passed over; any other line that cannot be read is skipped and
    reported by its number.
    """
    if not path.is_file():
        return LedgerRead((), ())
    records: list[LedgerRecord] = []
    problems: list[str] = []
    for number, raw in enumerate(path.read_bytes().split(b"\n"), start=1):
        if not raw.strip():
            continue
        try:
            records.append(parse_record(_decode(raw)))
        except LedgerLineError as error:
            problems.append(f"line {number}: {error}")
    return LedgerRead(tuple(records), tuple(problems))


def done_albums(
    records: Iterable[LedgerRecord], lang: str, pass_: SweepPass
) -> frozenset[str]:
    """The albums whose newest *lang* entry of *pass_* since its last restart is `DONE`.

    An album done in one language is not done in another: each language's frames are
    their own. An album done by one pass is not done by the other either: the refetch
    pass re-asks tracks a normal pass left as they were. A restart mark of *pass_*
    forgets every language's entries of that pass before it, and a mark of the other
    pass forgets nothing here.
    """
    newest: dict[str, Outcome] = {}
    for record in records:
        if record.pass_ != pass_:
            continue
        if isinstance(record, RestartMark):
            newest.clear()
        elif record.lang == lang:
            newest[record.album] = record.outcome
    return frozenset(
        album for album, outcome in newest.items() if outcome is Outcome.DONE
    )
