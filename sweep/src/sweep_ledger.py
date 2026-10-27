"""The sweep's ledger: one JSON line per album visited, kept outside the library.

**Where.** ``$XDG_STATE_HOME/lyricsmith/sweep/`` when that variable holds an absolute
path, else ``~/.local/state/lyricsmith/sweep/``, by the album tool's
`lyrics_state.state_root`, the rule every lyricsmith state file follows. The library
itself is never written by the sweep, only by the album tool. One ledger per library,
named by a hash of the library's resolved path (`ledger_path`), so two libraries never
share one and a library named by another spelling of its path finds its own.

**What.** Each line is a JSON object. An album's entry (``"kind": "album"``) holds the
album relative to the library, its run id (null when no run was found, none being
looked for after a child never reaped or one that could not run, or when its manifest
could not be read; kept, if already found, when a Ctrl-C cut the read short),
the album tool's exit code (null for a child never reaped, or an album the sweep could not
run the album tool on), how the visit ended (`Outcome`), the counts tallied from its
manifest (null whenever no tally was made), the refusing tracks and providers, the
providers it owes (`owed_providers`), the providers whose challenge its run met
(`challenged_providers`, `sweep_tally.AlbumTally.challenged`), the tracks each
provider refused by reason (`refusals`, a lookup an open breaker left unsent counted as
``breaker open``, apart from every reason) and the manifest's retries by provider and
reason (`retried`), the language the album tool ran in, the sweep's pass (`SweepPass`),
and the time. An entry written before the language was recorded reads as the album
tool's default language, one written before the pass was recorded as the normal pass,
and one written before a later field (`sweep_ledger_fields`) as that field's default:
nothing owed (its `owed_recorded` False, so `sweep_earlier` can tell), no challenge, no
refusals or retries, no removals or categories counted. Providers are kept by the
manifest's own values (``Lrclib``), reasons and categories by their words. A restart mark
(``"kind": "restart"``) records a ``--restart`` and its pass: entries of that pass
before it, in every language, no longer count as done (`done_albums`).

**Rewritten whole, atomically.** An append reads the file, adds one line, and replaces
the file through the album tool's confirmed atomic write, so a reader never sees half a
line and a write that did not land is an error. The bytes already there are kept as
they were, a line that cannot be read included, for a person to see. The ledger is
written readable by the user alone (0o600), and each folder made for it 0o700
(`lyrics_state.write_state_file`); a folder already there keeps its mode.

**Read whole, but bounded.** Every read goes through the album tool's
`lyrics_files.read_regular`, never following a link and never past `MAX_LEDGER_BYTES`
(`ledger_bytes`, through `read_unlinked`): a FIFO pipe, a device, a link or a larger
file is an `OSError` at once, the ledger's own error, never a wait, a read of somewhere
else, or a write through a link.

**Read tolerantly.** The ledger is a file a person may edit, so `parse_record` is a
parser of untrusted input: any shape it does not expect raises `LedgerLineError` and
nothing else, and `read_ledger` reports such a line by number and skips it, never ending
the sweep over it. Nothing here prints.
"""

from __future__ import annotations

import errno
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from album import DEFAULT_LANG
from lyrics_files import NotRegularFileError, read_regular
from lyrics_model import Source

# The album tool's one state-folder rule, re-exported: the silence pass's list reads it
# from here, so the ledger and the list never disagree about where the sweep keeps state.
from lyrics_state import state_root as state_root
from lyrics_state import write_state_file
from sweep_ledger_fields import (
    ByKey,
    ByReason,
    LedgerLineError,
    by_key,
    by_reason,
    challenged,
    count,
    counts,
    member,
    optional,
    owed,
)
from sweep_ledger_fields import field as _field
from sweep_tally import Counts

#: The folder under the state directory that holds every ledger.
LEDGER_DIR_NAME = Path("lyricsmith") / "sweep"
#: The largest ledger read. A line is one album's visit, about a kilobyte, and the
#: ledger keeps every visit of every pass, so even a large library swept many times
#: over stays far under it; past it, a whole rewrite per album would already crawl.
#: A bound on what a file swapped in can make the sweep hold in memory.
MAX_LEDGER_BYTES = 256 * 1024 * 1024
#: How many hex digits of the library's hash name its ledger: plenty to keep two
#: libraries apart, short enough to read.
_HASH_DIGITS = 16
_KIND_ALBUM, _KIND_RESTART = "album", "restart"
#: The pass's key in a line: ``pass`` is a Python keyword, so the field is ``pass_``.
_PASS_KEY, _PASS_FIELD = "pass", "pass_"
#: Whether a line held its owed providers: read from the line, never written to it.
_OWED_KEY, _RECORDED_FIELD = "owed_providers", "owed_recorded"
_ENCODING = "utf-8"


class SweepPass(StrEnum):
    """Which pass a sweep makes. Each keeps its own done set in the one ledger."""

    #: The album tool run as it runs by default.
    NORMAL = "normal"
    #: The album tool run with ``--refetch-untimed``, re-asking untimed-only tracks.
    REFETCH_UNTIMED = "refetch-untimed"


class Outcome(StrEnum):
    """How an album's visit ended. Only ``done`` keeps a resumed sweep from retrying it."""

    #: The album tool finished (exit 0 or 1), its manifest was read, and it owes
    #: no provider.
    DONE = "done"
    #: The album tool finished, but its providers only refused (`sweep_tally`).
    REFUSED = "refused"
    #: The album tool finished, a provider answered, but the album still owes answers
    #: from others that refused or that a cool-down or stop left unasked
    #: (`sweep_tally`): a resume asks only those again.
    OWED = "owed"
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
    line as ``pass``. *owed_providers* are the providers its tally owes, in chain
    order; only an `Outcome.OWED` entry's are asked on a revisit (`owed_albums`).
    *challenged_providers* are those whose challenge its run met, in chain order.
    *owed_recorded* is False for an entry read from a line written before
    ``owed_providers`` was recorded, which says nothing of what it owes
    (`sweep_earlier`); it is never written, since every line written now holds it.
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
    owed_providers: tuple[Source, ...] = ()
    challenged_providers: tuple[Source, ...] = ()
    refusals: ByKey = field(default_factory=dict)
    retried: ByReason = field(default_factory=dict)
    owed_recorded: bool = True


@dataclass(frozen=True)
class RestartMark:
    """A ``--restart``: every entry of its pass before it no longer counts as done."""

    time: str
    pass_: SweepPass = SweepPass.NORMAL


#: One line of the ledger, either kind.
LedgerRecord = LedgerEntry | RestartMark


@dataclass(frozen=True)
class LedgerRead:
    """Every record read, in file order, and a ``line N: why`` for each line skipped."""

    records: tuple[LedgerRecord, ...]
    problems: tuple[str, ...]


# --- Where -----------------------------------------------------------------------------


def ledger_path(library: Path, environ: Mapping[str, str]) -> Path:
    """The ledger file of *library*, under the state directory *environ* names."""
    digest = hashlib.sha256(str(library.resolve()).encode(_ENCODING)).hexdigest()
    return (
        state_root(environ) / LEDGER_DIR_NAME / f"ledger-{digest[:_HASH_DIGITS]}.jsonl"
    )


# --- Writing ---------------------------------------------------------------------------


def encode_record(record: LedgerRecord) -> str:
    """*record* as one ledger line, without its newline; ASCII, so any text survives."""
    if isinstance(record, RestartMark):
        document: dict[str, object] = {"kind": _KIND_RESTART, "time": record.time}
    else:
        document = {"kind": _KIND_ALBUM, **asdict(record)}
        del document[_PASS_FIELD]
        del document[_RECORDED_FIELD]
        document["outcome"] = record.outcome.value
        document["refusing_providers"] = list(record.refusing_providers)
        document["owed_providers"] = list(record.owed_providers)
        document["challenged_providers"] = list(record.challenged_providers)
    document[_PASS_KEY] = record.pass_.value
    return json.dumps(document, ensure_ascii=True, sort_keys=True)


class LedgerTooLargeError(OSError):
    """A ledger past `MAX_LEDGER_BYTES`: refused, as one that cannot be read is."""


def read_unlinked(path: Path, limit: int) -> bytes:
    """At most *limit* bytes of the regular file at *path*, never through a link.

    `lyrics_files.read_regular` without following a link. The open's own refusal of
    a link at *path* is an ``ELOOP`` whose text names a loop, not a link, so it is
    raised as `NotRegularFileError` saying *path* is a symbolic link instead. Any
    other ``ELOOP`` (a loop in a folder on the way) keeps the OS's own error.
    """
    try:
        return read_regular(path, limit, follow=False)
    except OSError as error:
        if error.errno == errno.ELOOP and path.is_symlink():
            raise NotRegularFileError(f"{path.name} is a symbolic link") from error
        raise


def ledger_bytes(path: Path) -> bytes:
    """The bytes of the ledger at *path*; none when there is no ledger.

    A link, a FIFO pipe or a device raises `NotRegularFileError` (an `OSError`)
    naming it, and a ledger past `MAX_LEDGER_BYTES` `LedgerTooLargeError`, before
    more than the cap is read.
    """
    try:
        data = read_unlinked(path, MAX_LEDGER_BYTES + 1)
    except (FileNotFoundError, NotADirectoryError):
        return b""
    if len(data) > MAX_LEDGER_BYTES:
        raise LedgerTooLargeError(f"{path.name} is more than {MAX_LEDGER_BYTES} bytes")
    return data


def append_record(path: Path, record: LedgerRecord) -> None:
    """Add *record* to the ledger at *path*, rewriting the file atomically.

    Missing folders are created, and every byte already there is kept as it was.
    """
    existing = ledger_bytes(path)
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    write_state_file(path, existing + encode_record(record).encode(_ENCODING) + b"\n")


def append_unless_last(path: Path, record: LedgerRecord) -> None:
    """Add *record* to the ledger at *path*, unless it is already the file's last line.

    So an append retried after a Ctrl-C cut the first short writes the record once,
    whether or not the first attempt's atomic replace had landed.
    """
    line = encode_record(record).encode(_ENCODING) + b"\n"
    if ledger_bytes(path).endswith(line):
        return
    append_record(path, record)


# --- Reading ---------------------------------------------------------------------------


def _album(value: str) -> str:
    """*value* when it names a folder inside a library, else refused."""
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise LedgerLineError("album is not a path inside the library")
    return value


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
    return member(_field(document, _PASS_KEY, str), SweepPass)


def _entry(document: dict[str, object]) -> LedgerEntry:
    """An album's entry from its decoded line."""
    return LedgerEntry(
        album=_album(_field(document, "album", str)),
        run_id=optional(document, "run_id", str),
        exit_code=optional(document, "exit_code", int),
        outcome=member(_field(document, "outcome", str), Outcome),
        time=_field(document, "time", str),
        counts=counts(document),
        refusing_tracks=count(document, "refusing_tracks"),
        refusing_providers=_providers(document),
        lang=_lang(document),
        pass_=_pass(document),
        owed_providers=owed(document),
        owed_recorded=_OWED_KEY in document,
        challenged_providers=challenged(document),
        refusals=by_key(document, "refusals"),
        retried=by_reason(document, "retried"),
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
    reported by its number. A ledger that cannot be read at all (`ledger_bytes`)
    raises `OSError`.
    """
    records: list[LedgerRecord] = []
    problems: list[str] = []
    for number, raw in enumerate(ledger_bytes(path).split(b"\n"), start=1):
        if not raw.strip():
            continue
        try:
            records.append(parse_record(_decode(raw)))
        except LedgerLineError as error:
            problems.append(f"line {number}: {error}")
    return LedgerRead(tuple(records), tuple(problems))


def read_records(
    path: Path, progress: Callable[[str], None]
) -> tuple[LedgerRecord, ...]:
    """The records of the ledger at *path* (`read_ledger`), each line skipped said.

    Each line that cannot be read is said through *progress*, by its number.
    """
    read = read_ledger(path)
    for problem in read.problems:
        progress(f"ledger {problem}; skipped")
    return read.records


def newest_entries(
    records: Iterable[LedgerRecord], lang: str, pass_: SweepPass
) -> dict[str, LedgerEntry]:
    """Each album's newest *lang* entry of *pass_* since that pass's last restart."""
    newest: dict[str, LedgerEntry] = {}
    for record in records:
        if record.pass_ != pass_:
            continue
        if isinstance(record, RestartMark):
            newest.clear()
        elif record.lang == lang:
            newest[record.album] = record
    return newest


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
    newest = newest_entries(records, lang, pass_)
    return frozenset(
        album for album, entry in newest.items() if entry.outcome is Outcome.DONE
    )


def owed_albums(
    records: Iterable[LedgerRecord], lang: str, pass_: SweepPass
) -> dict[str, tuple[Source, ...]]:
    """Each album whose newest entry is `OWED`, with its debt.

    The newest entry is read as `done_albums` reads it (`newest_entries`). Any other
    newest entry, a refused or interrupted visit say, owes nothing here, so the album's
    next visit asks every provider: never fewer than it owes.
    """
    newest = newest_entries(records, lang, pass_)
    return {
        album: entry.owed_providers
        for album, entry in newest.items()
        if entry.outcome is Outcome.OWED
    }
