"""The ledger's field readers: each field of a decoded line checked, or refused.

`sweep_ledger.parse_record` reads every line through these. A line is text a person
may edit, so each reader refuses any shape the tool does not write with a
`LedgerLineError`, and nothing else: a value of the wrong type (a boolean is never a
number), a count below 0, a provider, reason or category the tool does not know. The
refusals' reasons may also be ``breaker open`` (`lyrics_refusal_tally.Unsent`), which an
older line never holds; the retries' never are, since a skipped step sent nothing. A
field added after the ledger was first written reads as its default from a line that
predates it; one present must hold a value of its own kind, never null. Nothing here
reads a file or prints.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import fields
from typing import TypeVar

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_categories import RemovedReason, WrittenCategory
from lyrics_model import Source
from lyrics_refusal import Refusal
from lyrics_refusal_tally import RefusalCounts, refusal_key
from lyrics_tally import PROVIDERS
from sweep_tally import Counts

_T = TypeVar("_T")
_K = TypeVar("_K")
#: A count per provider and reason: the retries.
ByReason = dict[Source, dict[Refusal, int]]
#: A count per provider and key: the refusals, a lookup left unsent apart.
ByKey = RefusalCounts
#: The ``counts`` numbers added after the ledger was first written, each read as 0
#: from a line that predates it.
_LATER_COUNTS = frozenset({"no_lyrics_expected", "silent", "removed", "withheld"})
#: The ``counts`` maps added later, each read as empty from a line that predates it.
_LATER_MAPS = frozenset({"written_by", "removed_by"})
#: The providers an owed album may name: those ``--only-providers`` can.
_ASKABLE = frozenset(PROVIDERS)
#: A fix entry's records digest: a SHA-256 in lower-case hex.
_DIGEST = re.compile(r"[0-9a-f]{64}")
#: The records digest's key in a line.
DIGEST_KEY = "records_digest"


class LedgerLineError(ValueError):
    """A ledger line that is not one this tool writes."""


def field(document: Mapping[str, object], name: str, kind: type[_T]) -> _T:
    """*document*'s *name*, refused unless it is a *kind*; a boolean is never a number."""
    value = document.get(name)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise LedgerLineError(f"{name} is not {kind.__name__}")
    return value


def optional(document: Mapping[str, object], name: str, kind: type[_T]) -> _T | None:
    """*document*'s *name* when it is not null, refused unless it is a *kind*."""
    return None if document.get(name) is None else field(document, name, kind)


def member(value: str, enum: Callable[[str], _T]) -> _T:
    """*value* as a member of *enum*, else refused."""
    try:
        return enum(value)
    except ValueError as error:
        raise LedgerLineError(f"unknown value {value!r}") from error


def count(document: Mapping[str, object], name: str) -> int:
    """*document*'s *name*, refused unless it is a whole number of 0 or more."""
    value = field(document, name, int)
    if value < 0:
        raise LedgerLineError(f"{name} is below 0")
    return value


def _tally(raw: object, name: str, enum: Callable[[str], _K]) -> dict[_K, int]:
    """*raw* as a count per member of *enum*; *name* says which field it is."""
    if not isinstance(raw, dict):
        raise LedgerLineError(f"{name} is not dict")
    return {member(key, enum): count(raw, key) for key in raw}


def _later_tally(
    raw: Mapping[str, object], name: str, enum: Callable[[str], _K]
) -> dict[_K, int]:
    """*raw*'s *name*, a count per member of *enum*; empty when an older line lacks it."""
    return _tally(raw[name], name, enum) if name in raw else {}


def counts(document: Mapping[str, object]) -> Counts | None:
    """The ``counts`` object as `Counts`, or None when it is null.

    A count every line has held since the first is required; a later one missing
    from an older line is left out, for `Counts` to default.
    """
    raw = optional(document, "counts", dict)
    if raw is None:
        return None
    numbers = {
        f.name: count(raw, f.name)
        for f in fields(Counts)
        if f.name not in _LATER_MAPS and (f.name in raw or f.name not in _LATER_COUNTS)
    }
    return Counts(
        **numbers,
        written_by=_later_tally(raw, "written_by", WrittenCategory),
        removed_by=_later_tally(raw, "removed_by", RemovedReason),
    )


def by_reason(document: Mapping[str, object], name: str) -> ByReason:
    """*document*'s *name*, a count per provider and reason; empty when absent."""
    return _by_provider(document, name, Refusal)


def by_key(document: Mapping[str, object], name: str) -> ByKey:
    """*document*'s *name*, a count per provider and refusal key; empty when absent."""
    return _by_provider(document, name, refusal_key)


def _by_provider(
    document: Mapping[str, object], name: str, key: Callable[[str], _K]
) -> dict[Source, dict[_K, int]]:
    """*document*'s *name*, a count per provider and *key*; empty when absent."""
    if name not in document:
        return {}
    raw = field(document, name, dict)
    counted = {
        member(provider, Source): _tally(reasons, name, key)
        for provider, reasons in raw.items()
    }
    # The tool lists a provider only under a reason it counted, never with none.
    if not all(counted.values()):
        raise LedgerLineError(f"{name} lists a provider with no reason")
    return counted


def _askable(document: Mapping[str, object], name: str) -> tuple[Source, ...]:
    """*document*'s *name*, a list of providers; none from a line written before it.

    Each must be a provider ``--only-providers`` can name, and named once.
    """
    if name not in document:
        return ()
    names = field(document, name, list)
    if not all(isinstance(value, str) for value in names):
        raise LedgerLineError(f"{name} holds a value that is not text")
    providers = tuple(member(value, Source) for value in names)
    if not _ASKABLE.issuperset(providers) or len(set(providers)) != len(providers):
        raise LedgerLineError(f"{name} names a provider no revisit can ask")
    return providers


def owed(document: Mapping[str, object]) -> tuple[Source, ...]:
    """The ``owed_providers`` list; none from a line written before it was recorded.

    A revisit hands them to the album tool, which refuses any provider
    ``--only-providers`` cannot name.
    """
    return _askable(document, "owed_providers")


def challenged(document: Mapping[str, object]) -> tuple[Source, ...]:
    """The ``challenged_providers`` list; none from a line written before it was recorded."""
    return _askable(document, "challenged_providers")


def records_digest(document: Mapping[str, object]) -> str | None:
    """The ``records_digest`` field; none from a line without it, a fix's before it.

    Present, it must be a SHA-256 in lower-case hex, never null: only a fix entry
    holds one (`sweep_fix_text.records_digest`).
    """
    if DIGEST_KEY not in document:
        return None
    value = field(document, DIGEST_KEY, str)
    if not _DIGEST.fullmatch(value):
        raise LedgerLineError(f"{DIGEST_KEY} is not a SHA-256")
    return value
