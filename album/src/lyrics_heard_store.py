"""The transcript store: what whisper-cli heard of each track whose text was not sung.

sync keeps a track's transcript here when it finds the track's untimed text not sung, so
that album can judge another text against it without transcribing the track again. The
store is a folder named on the command line, one file a track, named by the SHA-256 of
the track's absolute path (``<hex>.json``), holding a JSON object of exactly three
fields: ``path``, the track's absolute path; ``fingerprint``, the
`lyrics_heard.text_fingerprint` of the text the transcript was judged against; and
``transcript``.

**The path contract.** A record is named by the bytes of the path it was written under, so
a caller passes each track path as its own walk of the library lists it, made absolute
with `Path.absolute()` and never `resolve()`d: resolving would name one file two ways
depending on which walk wrote it. Another spelling of the same file (a link in the path,
a name in NFC rather than NFD) names another record, so `read_record` will not find it;
`records_for` matches stored records to tracks by file identity instead, for a caller
that must find a record however it was spelled.

**Private.** A transcript is what a track in the library sings: each file is written 0o600,
atomically and read back (`lyrics_state.write_state_file`), and a folder the store creates
is made 0o700; an existing store folder keeps its mode. Nothing here prints, and no error
quotes a file's content.

**Bounded both ways.** A transcript over `MAX_TRANSCRIPT_BYTES` in UTF-8 is never
stored (`transcript_fits` measures it as the store does): `write_record` raises `RecordRefusedError` before anything is written, and the
caller reports it as a failed write. A file is read no further than `MAX_RECORD_BYTES`
and one byte, the most a stored record's JSON can take, and never through a link, a
FIFO pipe or anything but a regular file (`lyrics_files.read_regular`). What is read is
checked field by field, its path against the file's own name too, so a malformed,
oversized or misplaced file is an error the caller reports, never a record it trusts.

`STORE_ERRORS` is everything a store call raises when it cannot keep or read a record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeVar

import lyrics_files
from lyrics_cooldown import load_record
from lyrics_model import ReadBackError
from lyrics_state import write_state_file

#: The longest transcript kept, in UTF-8 bytes; a longer one is refused, never cut.
MAX_TRANSCRIPT_BYTES: Final = 64 * 1024
#: The longest track path kept, in the file system's bytes: Linux's PATH_MAX, the
#: largest of the platforms the tools run on.
MAX_PATH_BYTES: Final = 4096
#: The most JSON one stored byte can grow to: a control character is ``\u0000``.
_ESCAPE_GROWTH: Final = 6
#: Room for the field names, the punctuation and the fingerprint.
_ENVELOPE_BYTES: Final = 256
#: The largest file a record can be, so the largest one read.
MAX_RECORD_BYTES: Final = (
    _ESCAPE_GROWTH * (MAX_TRANSCRIPT_BYTES + MAX_PATH_BYTES) + _ENVELOPE_BYTES
)
#: Everything a store call raises when it cannot keep or read a record: an OS error, a
#: write that did not read back, or a record refused (a `ValueError`, as a file that
#: is not JSON or not UTF-8 is).
STORE_ERRORS: Final = (OSError, ReadBackError, ValueError)
#: A record file's ending, and the whole of a record file's name.
RECORD_SUFFIX: Final = ".json"
_RECORD_NAME: Final = re.compile(r"[0-9a-f]{64}\.json")
#: A fingerprint: a SHA-256 in lower-case hex.
_FINGERPRINT: Final = re.compile(r"[0-9a-f]{64}")
_FIELDS: Final = frozenset({"path", "fingerprint", "transcript"})
#: The byte no file system path holds.
_NUL: Final = b"\x00"
#: What the store's files are called in an error.
_WHAT: Final = "transcript"
#: How a transcript is measured: a lone surrogate counts as the bytes it takes, never
#: an error.
_TRANSCRIPT_ERRORS: Final = "surrogatepass"


class RecordRefusedError(ValueError):
    """A record the store will not keep, or a file it will not trust as one."""


@dataclass(frozen=True)
class HeardRecord:
    """One track's transcript, and the fingerprint of the text it was judged against."""

    path: Path
    fingerprint: str
    transcript: str


@dataclass(frozen=True)
class Listing:
    """Every record the store holds, in path order, and each file it could not read."""

    records: tuple[HeardRecord, ...]
    problems: tuple[str, ...]


def record_path(folder: Path, track: Path) -> Path:
    """Where *track*'s record goes in the store *folder*; a relative *track* is refused.

    *track* is the path as the caller's walk of the library lists it, made absolute with
    `Path.absolute()` and never `resolve()`d: the record is named by those bytes, so
    another spelling of the same file names another record (`records_for` finds it).
    """
    if not track.is_absolute():
        raise RecordRefusedError("a transcript record's track path is not absolute")
    digest = hashlib.sha256(os.fsencode(track)).hexdigest()
    return folder / f"{digest}{RECORD_SUFFIX}"


def transcript_fits(text: str) -> bool:
    """Whether the store keeps *text* as a transcript: at most `MAX_TRANSCRIPT_BYTES`.

    Measured in UTF-8 as a record is checked, a lone surrogate counting as the three
    bytes it takes, so a caller can tell before transcribing more or writing.
    """
    return len(text.encode("utf-8", _TRANSCRIPT_ERRORS)) <= MAX_TRANSCRIPT_BYTES


def _checked(record: HeardRecord) -> HeardRecord:
    """*record*, unless a field is out of its form; `RecordRefusedError` naming which.

    A path holding a NUL byte is refused: no file system path holds one, so it names
    no track, and every look at it would raise `ValueError` rather than `OSError`.
    """
    encoded = os.fsencode(record.path)
    if len(encoded) > MAX_PATH_BYTES:
        raise RecordRefusedError(f"a {_WHAT} record's path over {MAX_PATH_BYTES} bytes")
    if _NUL in encoded:
        raise RecordRefusedError(f"a {_WHAT} record's path holds a NUL byte")
    if not _FINGERPRINT.fullmatch(record.fingerprint):
        raise RecordRefusedError(f"a {_WHAT} record's fingerprint is not a SHA-256")
    if not transcript_fits(record.transcript):
        raise RecordRefusedError(f"a transcript over {MAX_TRANSCRIPT_BYTES} bytes")
    return record


def write_record(folder: Path, record: HeardRecord) -> None:
    """Keep *record* in the store *folder*, replacing the track's earlier one.

    Checked first, so a relative path, one holding a NUL byte, a fingerprint that is
    not a SHA-256 or a transcript over `MAX_TRANSCRIPT_BYTES` raises `RecordRefusedError` with nothing
    written. Then written atomically, 0o600, read back. Raises one of `STORE_ERRORS`.
    """
    path = record_path(folder, record.path)
    document = {
        "path": os.fsdecode(_checked(record).path),
        "fingerprint": record.fingerprint,
        "transcript": record.transcript,
    }
    # ASCII JSON: a lone surrogate from an undecodable path name is escaped, not lost.
    write_state_file(path, json.dumps(document).encode("ascii"))


def _parsed(data: bytes, name: str) -> HeardRecord:
    """The record *data* holds, kept in a file called *name*; `ValueError` when not one."""
    document = load_record(data, _FIELDS, max_bytes=MAX_RECORD_BYTES, what=_WHAT)
    path, fingerprint, transcript = (
        document["path"],
        document["fingerprint"],
        document["transcript"],
    )
    if not all(isinstance(value, str) for value in (path, fingerprint, transcript)):
        raise RecordRefusedError(f"a {_WHAT} record's field is not text")
    record = _checked(HeardRecord(Path(str(path)), str(fingerprint), str(transcript)))
    if record_path(Path(), record.path).name != name:
        raise RecordRefusedError(f"a {_WHAT} record under another track's name")
    return record


def _read_file(path: Path) -> HeardRecord:
    """The record in the file *path*; raises one of `STORE_ERRORS` when it is not one."""
    data = lyrics_files.read_regular(path, MAX_RECORD_BYTES + 1, follow=False)
    return _parsed(data, path.name)


def read_record(folder: Path, track: Path) -> HeardRecord | None:
    """*track*'s record in the store *folder*; none when it has none.

    Raises one of `STORE_ERRORS` for a file that cannot be read, is not a regular file
    (a link under its name included), is over `MAX_RECORD_BYTES`, or is not a record of
    *track* in every field.
    """
    try:
        return _read_file(record_path(folder, track))
    except FileNotFoundError:
        return None


def list_records(folder: Path) -> Listing:
    """Every record in the store *folder*, in path order; none when there is no folder.

    Only a name of a record's form (sixty-four hex digits and ``.json``) is read, so a
    write's leftover temporary file is not a record. Such a file that cannot be read
    as a record is a problem naming it and the error's type, and the rest are still
    listed. A *folder* that is not a folder raises its `OSError`.
    """
    if not folder.exists():
        return Listing((), ())
    records: list[HeardRecord] = []
    problems: list[str] = []
    for path in sorted(folder.iterdir()):
        if not _RECORD_NAME.fullmatch(path.name):
            continue
        try:
            records.append(_read_file(path))
        except STORE_ERRORS as error:
            problems.append(f"{path}: {_WHAT} record ignored ({type(error).__name__})")
    return Listing(
        tuple(sorted(records, key=lambda record: record.path)), tuple(problems)
    )


def delete_record(folder: Path, track: Path) -> None:
    """Remove *track*'s record from the store *folder*; a missing one is fine.

    Raises `OSError` when it is there and cannot be removed.
    """
    path = record_path(folder, track)
    path.unlink(missing_ok=True)
    if folder.is_dir():
        lyrics_files.sync_folder(folder)


#: A file's identity: its device and its inode, the same for every spelling of its path.
_Identity = tuple[int, int]


#: Every record of a listing, by the identity of the file its path names now.
_ByIdentity = dict[_Identity, list[HeardRecord]]
_K = TypeVar("_K")


def _identity(path: Path) -> _Identity | None:
    """*path*'s file identity, links followed; none when it cannot be read as a file.

    A path holding a NUL byte, which the store refuses but a caller may still hand
    in, raises `ValueError` from the OS layer: it names nothing either.
    """
    try:
        status = path.stat()
    except (OSError, ValueError):
        return None
    return (status.st_dev, status.st_ino)


def _by_identity(listing: Listing) -> _ByIdentity:
    """*listing*'s records by file identity; a record naming no file is left out."""
    by_identity: _ByIdentity = {}
    for record in listing.records:
        identity = _identity(record.path)
        if identity is not None:
            by_identity.setdefault(identity, []).append(record)
    return by_identity


def _matched(
    by_identity: _ByIdentity, tracks: Iterable[Path]
) -> dict[Path, HeardRecord]:
    """Each of *tracks* that has a record in *by_identity*, mapped to it."""
    found: dict[Path, HeardRecord] = {}
    for track in tracks:
        identity = _identity(track)
        matches = by_identity.get(identity, []) if identity is not None else []
        if matches:
            own = [record for record in matches if record.path == track]
            found[track] = (own or matches)[0]
    return found


def records_for(listing: Listing, tracks: Iterable[Path]) -> dict[Path, HeardRecord]:
    """Each of *tracks* that has a record in *listing*, mapped to it, by file identity.

    A record matches a track when its path names the same file now, links followed, not
    when it names the same bytes, so a record written under another spelling of the
    track's path (a link in it, NFC rather than NFD) is still found. A record or a track
    whose path no longer names a file matches nothing. When several records name one
    file, the one whose path is the track's own wins, else the first in path order.
    """
    return _matched(_by_identity(listing), tracks)


def records_by_group(
    listing: Listing, groups: Mapping[_K, Iterable[Path]]
) -> dict[_K, dict[Path, HeardRecord]]:
    """`records_for` of each group of tracks in *groups*, by the group's key.

    Each record's identity is read once for all the groups, not once a group, so a
    caller matching a whole library's albums stats each record once.
    """
    by_identity = _by_identity(listing)
    return {key: _matched(by_identity, tracks) for key, tracks in groups.items()}
