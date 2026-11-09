"""The hearing log: what each hearing of a track found, one record per track and model.

sync's hearing mode keeps here every verdict it reaches, sung or not, and sweep reads it
back to give each group of a sample its rate. It is a store of its own, kept apart from
the transcript store (`lyrics_heard_store`) on purpose: that store holds only tracks
heard not sung, and the spoken-album guard and the sweep's fix pass count every record
in it as one, so a sung transcript there would mislead both.

The log is a folder named on the command line, one file a track and model, named by the
SHA-256 of the track's absolute path, a NUL, and the model's file name
(``<hex>.json``), so two models' hearings of one track are kept apart. A file holds a
JSON object of exactly eight fields: ``path``; ``fingerprint``, the
`lyrics_heard.text_fingerprint` of the text heard, which the caller computes; ``kind``
(`HeardKind`); ``model``; ``pairs`` and ``share``, as `lyrics_heard.judge_words` gave
them; ``verdict`` (`HeardVerdict`); and ``transcript``. A record's verdict is taken as
written: the rule that reached it is `lyrics_heard`'s alone, never restated here.

**The path contract** is the transcript store's: a caller passes each track path as its
walk of the library lists it, made absolute with `Path.absolute()` and never
`resolve()`d, since the record is named by those bytes.

**Private and bounded**, as the transcript store is, and with its limits: each file is
written 0o600, atomically and read back (`lyrics_state.write_state_file`), in a folder
made 0o700. A transcript over `MAX_TRANSCRIPT_BYTES` in UTF-8 is never stored; a file is
read no further than `MAX_RECORD_BYTES` and one byte, never through a link or anything
but a regular file (`lyrics_files.read_regular`). What is read is checked field by
field, its path and model against the file's own name too, so a malformed, oversized
or misplaced file is a `RecordRefusedError` (a `ValueError`) the caller reports, never a
record it trusts. Nothing here prints, and no error quotes a file's content.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, TypeGuard

from lyrics_cooldown import load_record
from lyrics_heard_store import (
    ESCAPE_GROWTH,
    FINGERPRINT,
    MAX_PATH_BYTES,
    MAX_TRANSCRIPT_BYTES,
    NUL,
    RECORD_SUFFIX,
    STORE_ERRORS,
    RecordRefusedError,
    read_named,
    scan_records,
    transcript_fits,
)
from lyrics_state import write_state_file

__all__ = [
    "MAX_MODEL_BYTES",
    "MAX_PATH_BYTES",
    "MAX_RECORD_BYTES",
    "MAX_TRANSCRIPT_BYTES",
    "STORE_ERRORS",
    "HearingListing",
    "HearingRecord",
    "HeardKind",
    "HeardVerdict",
    "RecordRefusedError",
    "hearing_path",
    "list_hearings",
    "read_hearing",
    "write_hearing",
]

#: The longest model file name kept, in the file system's bytes: the longest file name
#: the platforms the tools run on allow.
MAX_MODEL_BYTES: Final = 255
#: The most pairs a record holds. Pairs are the transcript's distinct adjacent word
#: pairs, fewer than its words, and a word takes a byte at least, so no hearing of a
#: kept transcript has more; the bound keeps every count written readable back.
_MAX_PAIRS: Final = MAX_TRANSCRIPT_BYTES
#: Room for the field names, the punctuation, the fingerprint, the enums and the numbers.
_ENVELOPE_BYTES: Final = 512
#: The largest file a record can be, so the largest one read.
MAX_RECORD_BYTES: Final = (
    ESCAPE_GROWTH * (MAX_TRANSCRIPT_BYTES + MAX_PATH_BYTES + MAX_MODEL_BYTES)
    + _ENVELOPE_BYTES
)
_FIELDS: Final = frozenset(
    {"path", "fingerprint", "kind", "model", "pairs", "share", "verdict", "transcript"}
)
#: What a model file name may not hold or be: it names a file, never a folder.
_MODEL_SEPARATORS: Final = ("/", "\\", "\x00")
_MODEL_NOT_NAMES: Final = frozenset({"", ".", ".."})
#: What the log's files are called in an error.
_WHAT: Final = "hearing"


class HeardKind(StrEnum):
    """What timing the heard text had: a provider's, sync's own, or none."""

    PROVIDER_TIMED = "provider-timed"
    MACHINE_TIMED = "machine-timed"
    UNTIMED = "untimed"


class HeardVerdict(StrEnum):
    """What the hearing found, by `lyrics_heard.judge_words`'s rule."""

    SUNG = "sung"
    NOT_SUNG = "not sung"
    LITTLE_HEARD = "little heard"


@dataclass(frozen=True)
class HearingRecord:
    """One hearing of a track's text by one model, and what it found."""

    path: str
    fingerprint: str
    kind: HeardKind
    model: str
    pairs: int
    share: float
    verdict: HeardVerdict
    transcript: str


@dataclass(frozen=True)
class HearingListing:
    """Every record the log holds, in path then model order, and each unreadable file."""

    records: tuple[HearingRecord, ...]
    problems: tuple[str, ...]


def _check_model(model: str) -> bytes:
    """*model*'s file system bytes; `RecordRefusedError` unless it is a plain file name."""
    if model in _MODEL_NOT_NAMES or any(part in model for part in _MODEL_SEPARATORS):
        raise RecordRefusedError(f"a {_WHAT} record's model is not a file name")
    encoded = os.fsencode(model)
    if len(encoded) > MAX_MODEL_BYTES:
        raise RecordRefusedError(
            f"a {_WHAT} record's model over {MAX_MODEL_BYTES} bytes"
        )
    return encoded


def _check_path(path: str) -> bytes:
    """*path*'s file system bytes; `RecordRefusedError` unless it can name a track."""
    encoded = os.fsencode(path)
    if len(encoded) > MAX_PATH_BYTES:
        raise RecordRefusedError(f"a {_WHAT} record's path over {MAX_PATH_BYTES} bytes")
    if NUL in encoded:
        raise RecordRefusedError(f"a {_WHAT} record's path holds a NUL byte")
    if not Path(path).is_absolute():
        raise RecordRefusedError(f"a {_WHAT} record's track path is not absolute")
    return encoded


def hearing_path(folder: Path, path: str, model: str) -> Path:
    """Where the hearing of the track *path* by *model* goes in the log *folder*.

    A relative *path*, one holding a NUL or over `MAX_PATH_BYTES`, and a *model* that is
    not a plain file name of at most `MAX_MODEL_BYTES` raise `RecordRefusedError`.
    """
    digest = hashlib.sha256(_check_path(path) + NUL + _check_model(model))
    return folder / f"{digest.hexdigest()}{RECORD_SUFFIX}"


def _is_count(value: object) -> TypeGuard[int]:
    """Whether *value* is a count of pairs: an int, never a bool, 0 to `_MAX_PAIRS`."""
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return 0 <= value <= _MAX_PAIRS


def _is_share(value: object) -> TypeGuard[int | float]:
    """Whether *value* is a share: a number from 0 to 1, never a bool or a NaN."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value) and 0 <= value <= 1


def _checked(record: HearingRecord) -> HearingRecord:
    """*record*, unless a field is out of its form; `RecordRefusedError` naming which."""
    hearing_path(Path(), record.path, record.model)
    if not FINGERPRINT.fullmatch(record.fingerprint):
        raise RecordRefusedError(f"a {_WHAT} record's fingerprint is not a SHA-256")
    if type(record.kind) is not HeardKind or type(record.verdict) is not HeardVerdict:
        raise RecordRefusedError(f"a {_WHAT} record's kind or verdict is unknown")
    if not _is_count(record.pairs) or not _is_share(record.share):
        raise RecordRefusedError(f"a {_WHAT} record's pairs or share is out of range")
    if not transcript_fits(record.transcript):
        raise RecordRefusedError(f"a transcript over {MAX_TRANSCRIPT_BYTES} bytes")
    return record


def write_hearing(folder: Path, record: HearingRecord) -> None:
    """Keep *record* in the log *folder*, replacing that track's earlier one by its model.

    Checked first, so a record out of its form raises `RecordRefusedError` with nothing
    written. Then written atomically, 0o600, read back. Raises one of `STORE_ERRORS`.
    """
    checked = _checked(record)
    document = {
        "path": checked.path,
        "fingerprint": checked.fingerprint,
        "kind": str(checked.kind),
        "model": checked.model,
        "pairs": checked.pairs,
        "share": float(checked.share),
        "verdict": str(checked.verdict),
        "transcript": checked.transcript,
    }
    target = hearing_path(folder, checked.path, checked.model)
    # ASCII JSON: a lone surrogate from an undecodable path name is escaped, not lost.
    write_state_file(target, json.dumps(document).encode("ascii"))


def _text_fields(document: dict[str, object]) -> tuple[str, ...]:
    """The record's six text fields, in schema order; `RecordRefusedError` if one is not."""
    names = ("path", "fingerprint", "kind", "model", "verdict", "transcript")
    values = tuple(document[name] for name in names)
    if not all(isinstance(value, str) for value in values):
        raise RecordRefusedError(f"a {_WHAT} record's field is not text")
    return tuple(str(value) for value in values)


def _numbers(document: dict[str, object]) -> tuple[int, float]:
    """The record's pairs and share; `RecordRefusedError` when either is out of form.

    A whole share JSON wrote as an integer (``1``) reads as the float it stands for.
    """
    pairs, share = document["pairs"], document["share"]
    if not _is_count(pairs):
        raise RecordRefusedError(f"a {_WHAT} record's pairs is not a count")
    if not _is_share(share):
        raise RecordRefusedError(f"a {_WHAT} record's share is out of range")
    return pairs, float(share)


def _parsed(data: bytes, name: str) -> HearingRecord:
    """The record *data* holds, kept in a file called *name*; `ValueError` when not one."""
    document = load_record(data, _FIELDS, max_bytes=MAX_RECORD_BYTES, what=_WHAT)
    path, fingerprint, kind, model, verdict, transcript = _text_fields(document)
    pairs, share = _numbers(document)
    try:
        enums = HeardKind(kind), HeardVerdict(verdict)
    except ValueError as error:
        raise RecordRefusedError(
            f"a {_WHAT} record's kind or verdict is unknown"
        ) from error
    record = _checked(
        HearingRecord(
            path, fingerprint, enums[0], model, pairs, share, enums[1], transcript
        )
    )
    if hearing_path(Path(), path, model).name != name:
        raise RecordRefusedError(
            f"a {_WHAT} record under another track's or model's name"
        )
    return record


def read_hearing(folder: Path, path: str, model: str) -> HearingRecord | None:
    """The hearing of the track *path* by *model* in the log *folder*; none when unheard.

    Raises one of `STORE_ERRORS` for a file that cannot be read, is not a regular file
    (a link under its name included), is over `MAX_RECORD_BYTES`, or is not a record of
    *path* by *model* in every field.
    """
    return read_named(hearing_path(folder, path, model), MAX_RECORD_BYTES, _parsed)


def list_hearings(folder: Path) -> HearingListing:
    """Every record in the log *folder*, in path then model order; none with no folder.

    Only a name of a record's form (sixty-four hex digits and ``.json``) is read, so a
    write's leftover temporary file is not a record. Such a file that cannot be read as
    a record (truncated, oversized, failing its schema) is a problem naming it and the
    error's type, and the rest are still listed. A *folder* that is not a folder raises
    its `OSError`.
    """
    records, problems = scan_records(folder, MAX_RECORD_BYTES, _parsed, _WHAT)
    records.sort(key=lambda record: (record.path, record.model))
    return HearingListing(tuple(records), tuple(problems))
