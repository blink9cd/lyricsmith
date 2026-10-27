"""The sidecar stage: normalize the ``.lrc`` / ``.txt`` beside an MP3, and write one.

A track keeps at most one lyrics sidecar, named after the MP3: ``.lrc`` for synced
lyrics, ``.txt`` for unsynced. `normalize_sidecars` brings what it finds to that shape
before any fetch, and reports what it changed so undo can reverse it; `write_sidecar`
writes the kind an accepted answer has, after the embed.

The normalization, by what is found beside the MP3:

- a ``.lrc`` with at least one time stamp and at least one worded line is used as
  synced, and a ``.txt`` beside it is removed (one sidecar per track);
- a timed ``.lrc`` whose every line is blank (stamps alone, as an instrumental marker
  is often written) never costs real lyrics: beside a ``.txt`` holding words it is
  removed, and the ``.txt`` is used as unsynced; otherwise it is kept, a blank
  ``.txt`` beside it goes, and it holds no lyrics;
- a ``.lrc`` with no time stamp was never synced, so it is renamed to ``.txt``,
  replacing any ``.txt`` already there, and used as unsynced;
- a ``.txt`` alone is used as unsynced;
- nothing, or a survivor holding only whitespace, is no lyrics, and the chain fetches.

Only the lowercase suffixes ``.lrc`` and ``.txt`` name a sidecar. On a
case-insensitive volume (the default on macOS) the lowercase name also finds a file
written ``.LRC`` or ``.TXT``; on a case-sensitive volume an uppercase sidecar is not
seen, and the track is treated as having none.

A sidecar name is read as `lyrics_slots` reads it, without following a link. A link
under a sidecar name is read through for its lyrics only when it resolves, every link
on the way followed, to a file inside the album: a link elsewhere in the album serves
a track as it always did. One that leads out of the album, by an absolute target or
by climbing with ``..``, holds no lyrics, as one that leads nowhere, round a loop or
to a folder does, so nothing outside the album is ever read or embedded and the chain
fetches; the state names it in *links_ignored*, so the reports can say why. What is
read is the file the check reached, never the link's name again, so a link re-pointed
in between changes nothing. Either way every change acts on the link itself, never
on the file it points at: a rename moves the link, a removal removes the link, and
`write_sidecar` replaces the link with the file it writes, so nothing is ever written
through a link to somewhere else. The backup recorded the link by its target, so undo
puts it back. A folder, a pipe or a device under a sidecar name is no sidecar here:
never read, moved or removed.

Sidecars are read as UTF-8 with an optional byte-order mark. One that does not decode
raises `SidecarDecodeError`, before anything on disk changes: a decode with
replacement characters would be embedded and then trusted. One holding more than
`MAX_LYRICS_CHARS` characters raises `SidecarTooLargeError`, the same way: its byte
count is checked first, reading no more than the most bytes text under the cap can
take, so a huge file is never read whole; `read_bounded` is that read, and the backup
copies a sidecar through it too. Every file read is decided before any rename or
removal, so a refused track is left exactly as it was found.

Each file the run leaves in place, renamed or written, is hashed from the bytes in hand
(read for the rename, written for the write), so undo can later tell the run's own
file from one a person has changed since. A write is confirmed by reading it back. No
function here takes the album lock; a caller that applies changes holds it.
"""

from __future__ import annotations

import codecs
import dataclasses
import hashlib
import io
import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import lyrics_files
from lyrics_model import (
    LyricsError,
    Lyrics,
    PlainLyrics,
    SidecarDecodeError,
    SidecarRename,
    SidecarState,
    SidecarWrite,
    Source,
    SyncedLine,
    SyncedLyrics,
    format_lrc,
    parse_lrc,
)
from lyrics_records import MAX_LYRICS_CHARS
from lyrics_slots import Slot, sidecar_entry, sidecar_paths

#: Reads accept, and drop, a leading UTF-8 byte-order mark.
_READ_ENCODING = "utf-8-sig"
#: Writes are plain UTF-8: no mark, which some players show as a stray character.
_WRITE_ENCODING = "utf-8"
#: The most bytes a sidecar under the cap can hold: UTF-8 takes at most four bytes a
#: character, and a byte-order mark three more. A larger file is refused unread.
_MAX_SIDECAR_BYTES = 4 * MAX_LYRICS_CHARS + len(codecs.BOM_UTF8)


class SidecarTooLargeError(LyricsError):
    """A sidecar holds more than the lyrics cap; it is refused, never read in part."""


def sidecar_digest(data: bytes) -> str:
    """The SHA-256 of a sidecar's bytes, as lowercase hex: how a run's sidecar is known."""
    return hashlib.sha256(data).hexdigest()


def _as_read(data: bytes) -> str:
    """*data* decoded as a sidecar is read, a leading byte-order mark dropped.

    As reading a file as text does, every line ending (CRLF, a lone CR) becomes a
    newline. Raises `UnicodeDecodeError` when the bytes are not UTF-8.
    """
    return io.TextIOWrapper(io.BytesIO(data), encoding=_READ_ENCODING).read()


def _decode(path: Path, data: bytes) -> str:
    """*data*, the bytes of the sidecar at *path*, as text, any byte-order mark dropped.

    Decoded as reading the file as text would, every line ending made a newline.
    Raises `SidecarDecodeError`, naming the file, when the bytes are not UTF-8.
    """
    try:
        text = _as_read(data)
    except UnicodeDecodeError as error:
        detail = f"{error.reason} at byte {error.start}"
        raise SidecarDecodeError(f"{path.name} is not UTF-8: {detail}") from error
    if len(text) > MAX_LYRICS_CHARS:
        raise SidecarTooLargeError(
            f"{path.name} is too large: over {MAX_LYRICS_CHARS} characters"
        )
    return text


def read_bounded(source: Path, name: str) -> bytes:
    """The bytes of the file *source*, the sidecar *name*; refused past the cap.

    Reads one byte more than text under the cap can take, so an oversized file is
    known by that byte alone and never read whole: `SidecarTooLargeError`, naming
    *name*. Read through `lyrics_files.read_regular` without following a link at the
    last step, so a file swapped since it was looked at, for a link or for a FIFO pipe,
    is an error at once, never read through or waited on. Binary mode, so the bytes
    are the file's own on every platform: the backup's copy and the rename's hash are
    made from them.
    """
    data = lyrics_files.read_regular(source, _MAX_SIDECAR_BYTES + 1, follow=False)
    if len(data) > _MAX_SIDECAR_BYTES:
        raise SidecarTooLargeError(
            f"{name} is too large: over {_MAX_SIDECAR_BYTES} bytes"
        )
    return data


def _present(path: Path) -> bool:
    """Whether the sidecar name *path* holds a file or a link, a dangling one included."""
    return sidecar_entry(path) is not Slot.ABSENT


def _target(path: Path) -> Path:
    """*path* with every link on the way followed, as far as they lead.

    `os.path.realpath`, never `Path.resolve`: on Python 3.12, the floor, `resolve`
    raises `RuntimeError` on a link loop, where this leaves the loop unresolved, a
    name that is no file.
    """
    return Path(os.path.realpath(path))


def _source(path: Path, root: Path) -> Path | None:
    """The file to read for the sidecar name *path*, or None when it holds no lyrics.

    A file is read as itself, and a link as the file it leads to when that lies under
    *root*, the album, already followed to its real path. The checked target is what
    is read, never the link's name again, so a link re-pointed after the check cannot
    send the read anywhere else. A link leading out of the album, nowhere, round a
    loop, or to a folder holds none.
    """
    entry = sidecar_entry(path)
    if entry is Slot.FILE:
        return path
    if entry is not Slot.LINK:
        return None
    target = _target(path)
    return target if target.is_relative_to(root) and target.is_file() else None


def _leads_out(path: Path, root: Path) -> bool:
    """Whether the sidecar name *path* holds a link leading out of *root*, the album."""
    return sidecar_entry(path) is Slot.LINK and not _target(path).is_relative_to(root)


def _read_text(path: Path, source: Path) -> str:
    """The text of *source*, the file the sidecar name *path* is read from.

    Raises `SidecarDecodeError`, naming the sidecar, when the bytes are not UTF-8, and
    `SidecarTooLargeError` when they hold more than the lyrics cap.
    """
    return _decode(path, read_bounded(source, path.name))


def read_sidecar_text(path: Path) -> str:
    """The text of the sidecar file *path*, read and decoded as any sidecar is.

    For a sidecar's copy in a run folder, which the check reads back: bounded by
    `read_bounded`, never through a link at its last step, and decoded as UTF-8 with
    an optional byte-order mark. Raises `SidecarDecodeError` or `SidecarTooLargeError`,
    naming *path*, as a sidecar beside an MP3 would.
    """
    return _read_text(path, path)


def _unless_blank(lyrics: Lyrics) -> Lyrics | None:
    """*lyrics*, or None when its text holds nothing but whitespace.

    Synced lyrics are blank when every line's text is.
    """
    if isinstance(lyrics, SyncedLyrics):
        has_words = any(line.text.strip() for line in lyrics.lines)
    else:
        has_words = bool(lyrics.text.strip())
    return lyrics if has_words else None


def _plan_from_blank_timed(
    lrc: Path, txt: Path, txt_source: Path | None
) -> SidecarState:
    """The outcome for a timed ``.lrc`` with no words in it.

    A ``.txt`` beside it holding words wins: the ``.lrc`` goes and the ``.txt`` is
    used as unsynced, so a stamps-only file never costs real lyrics. Otherwise the
    ``.lrc`` stays, a blank ``.txt`` goes, and there are no lyrics. *txt_source* is
    the file the ``.txt`` is read from, or None when it holds no lyrics.
    """
    if txt_source is None:
        return SidecarState(None)
    plain = _unless_blank(PlainLyrics(_read_text(txt, txt_source)))
    if plain is not None:
        return SidecarState(plain, removed=(lrc.name,))
    return SidecarState(None, removed=(txt.name,))


#: The header lines that label a ``.lrc`` as made by a machine, and the source each names.
#: Every place that treats a machine-made file apart reads this table, so a new label
#: needs only its row here.
MACHINE_LABELS: Mapping[str, Source] = MappingProxyType(
    {
        "[by:machine transcript]": Source.MACHINE_TRANSCRIPT,
        "[by:machine timed]": Source.MACHINE_TIMED,
    }
)


# fuzz-target
def machine_label(text: str) -> Source | None:
    """The source a ``.lrc``'s header labels it with, else None.

    The header is every line before the first one `parse_lrc` reads a stamp from. A
    header line labels the file when, stripped at both ends, it is a key of
    `MACHINE_LABELS`. The transcribe tool ends its header with its own stamp pattern,
    so on hand-edited input the two tools can disagree; on every file transcribe
    writes they agree, which a test pins against that tool's bundled sample. The sync
    tool has no reader of its own: it takes its by-line from this function, through
    `plan_sidecars`. A text with no stamped line has no header, so no label.
    """
    header: list[str] = []
    for line in text.splitlines():
        if isinstance(parse_lrc(line), SyncedLyrics):
            return next(
                (MACHINE_LABELS[h] for h in header if h in MACHINE_LABELS), None
            )
        header.append(line.strip())
    return None


def _plan_from_lrc(
    lrc: Path, lrc_source: Path, txt: Path, txt_source: Path | None
) -> SidecarState:
    """The outcome for a track with a ``.lrc``: keep it if timed, else rename it.

    Either way a ``.txt`` already there goes: removed beside a timed ``.lrc``,
    replaced by the renamed one beside an untimed ``.lrc``. The one exception is a
    timed ``.lrc`` with no words, which gives way to a worded ``.txt``. Each source
    is the file its name is read from, as `_source` chose it.
    """
    data = read_bounded(lrc_source, lrc.name)
    text = _decode(lrc, data)
    lyrics = parse_lrc(text)
    if isinstance(lyrics, SyncedLyrics) and _unless_blank(lyrics) is None:
        return _plan_from_blank_timed(lrc, txt, txt_source)
    removed = (txt.name,) if _present(txt) else ()
    if isinstance(lyrics, SyncedLyrics):
        return SidecarState(lyrics, removed=removed, label=machine_label(text))
    # The rename moves these very bytes, so their hash is the renamed file's.
    rename = SidecarRename(lrc.name, txt.name)
    return SidecarState(
        _unless_blank(lyrics),
        renamed=(rename,),
        removed=removed,
        hashes=((txt.name, sidecar_digest(data)),),
    )


def _plan_from_sources(lrc: Path, txt: Path, root: Path) -> SidecarState:
    """The outcome for the sidecar names *lrc* and *txt*, each read only inside *root*."""
    lrc_source, txt_source = _source(lrc, root), _source(txt, root)
    if lrc_source is not None:
        return _plan_from_lrc(lrc, lrc_source, txt, txt_source)
    if txt_source is not None:
        return SidecarState(_unless_blank(PlainLyrics(_read_text(txt, txt_source))))
    return SidecarState(None)


def plan_sidecars(mp3: Path, album: Path) -> SidecarState:
    """What normalizing *mp3*'s sidecars would do, and the lyrics the survivor holds.

    Reads, never writes, and never past *album*: a sidecar link leading out of it holds
    no lyrics, and the state names it in *links_ignored*. Raises `SidecarDecodeError`
    on a sidecar it must read that is not UTF-8, and `SidecarTooLargeError` on one past
    the lyrics cap. A ``.txt`` the outcome removes is never read, except beside a
    stamps-only ``.lrc``, where its words decide which file survives.
    """
    root = _target(album)
    lrc, txt = sidecar_paths(mp3)
    state = _plan_from_sources(lrc, txt, root)
    ignored = tuple(path.name for path in (lrc, txt) if _leads_out(path, root))
    return dataclasses.replace(state, links_ignored=ignored)


#: What a sidecar that cannot be read raises.
UNREADABLE = (SidecarDecodeError, SidecarTooLargeError)


def unreadable_txt(
    mp3: Path, album: Path
) -> SidecarDecodeError | SidecarTooLargeError | None:
    """The error planning *mp3*'s sidecars fails with at its ``.txt``; None if it does not.

    The plan reads the ``.lrc`` first, and the ``.txt`` only beside no ``.lrc`` or
    one of stamps alone. So once the ``.lrc`` is known to read, or there is none, a
    plan that still fails failed at the ``.txt``. None when the plan reads, and when
    the ``.lrc`` is what cannot be read. The error's message says why, naming the
    file, and its type whether the file is not UTF-8 or too large. Reads, never writes.
    """
    root = _target(album)
    lrc, _txt = sidecar_paths(mp3)
    lrc_source = _source(lrc, root)
    try:
        if lrc_source is not None:
            _read_text(lrc, lrc_source)
    except UNREADABLE:
        return None
    try:
        plan_sidecars(mp3, album)
    except UNREADABLE as error:
        return error
    return None


def _apply_plan(folder: Path, state: SidecarState) -> None:
    """Carry out *state*'s renames and removals among the files in *folder*.

    A rename replaces its target, so a removed name that a rename lands on needs no
    separate removal.
    """
    for rename in state.renamed:
        (folder / rename.old).replace(folder / rename.new)
    landed = {rename.new for rename in state.renamed}
    for name in state.removed:
        if name not in landed:
            (folder / name).unlink()


def normalize_sidecars(mp3: Path, *, album: Path, apply: bool) -> SidecarState:
    """Normalize the sidecars beside *mp3*; return what changed and the survivor's lyrics.

    With *apply* False nothing on disk changes, and the state returned is the one an
    applied run would return. A sidecar link leading out of *album* holds no lyrics.
    Raises `SidecarDecodeError` or `SidecarTooLargeError` before changing anything.
    """
    state = plan_sidecars(mp3, album)
    if apply:
        _apply_plan(mp3.parent, state)
    return state


def _lrc_lines(lyrics: SyncedLyrics) -> tuple[SyncedLine, ...]:
    """*lyrics*' lines as their ``.lrc`` holds them: each text stripped, in stored order.

    Stripped of whitespace and line breaks at either end, as reading a ``.lrc`` back
    trims them anyway: many taggers open each SYLT entry with a line break.
    """
    return tuple(SyncedLine(line.ms, line.text.strip()) for line in lyrics.lines)


def lrc_read_back(lyrics: SyncedLyrics) -> SyncedLyrics:
    """The lines a ``.lrc`` written from *lyrics* reads back as, when it keeps them whole.

    Each line's text stripped, as the ``.lrc`` holds it, and in time order, ties in
    stored order, as reading one sorts them. A line `sidecar_keeps` refuses reads
    back otherwise; no ``.lrc`` is written from such lyrics.
    """
    return SyncedLyrics(tuple(sorted(_lrc_lines(lyrics), key=lambda line: line.ms)))


def txt_read_back(text: str) -> str | None:
    """The text a ``.txt`` written from *text* reads back as; None when none can hold it.

    The write encodes *text* as UTF-8 verbatim and the read decodes it as every
    sidecar is read (`_as_read`), so this is that very round trip: line endings
    made newlines and a leading byte-order mark dropped. Text no UTF-8 can encode,
    holding a lone surrogate, is never written, so it reads back as nothing.
    """
    try:
        return _as_read(text.encode(_WRITE_ENCODING))
    except UnicodeEncodeError:
        return None


def _sidecar_content(mp3: Path, lyrics: Lyrics) -> tuple[Path, Path, str]:
    """The sidecar *lyrics* go to beside *mp3*, the other kind's path, and the text."""
    lrc, txt = sidecar_paths(mp3)
    if isinstance(lyrics, SyncedLyrics):
        return lrc, txt, format_lrc(_lrc_lines(lyrics))
    return txt, lrc, lyrics.text


def sidecar_keeps(lyrics: Lyrics) -> bool:
    """True when *lyrics*, written as their sidecar, read back with the same words.

    Untimed lyrics go to ``.txt`` verbatim, so always. Synced lyrics go to ``.lrc``
    one line each, each line's text stripped at both ends, and come back in time
    order, so only a line holding a line break inside it, or opening with what reads
    as a time stamp, does not survive. A provider's answer was read from LRC and
    always does; a SYLT frame's entries may not, so a run asks before writing a
    sidecar from them.
    """
    if isinstance(lyrics, PlainLyrics):
        return True
    return parse_lrc(format_lrc(_lrc_lines(lyrics))) == lrc_read_back(lyrics)


def _holds(target: Path, data: bytes) -> bool:
    """Whether the sidecar name *target* is a file, not a link, holding *data* exactly.

    Its size is compared first, so a file of another length is never read.
    """
    if sidecar_entry(target) is not Slot.FILE or target.stat().st_size != len(data):
        return False
    return lyrics_files.read_regular(target, follow=False) == data


def write_sidecar(mp3: Path, lyrics: Lyrics) -> SidecarWrite | None:
    """Write *lyrics* beside *mp3* as their kind's sidecar, and remove the other kind.

    Synced lyrics go to ``.lrc`` as millisecond-stamped lines, each line's text
    stripped, unsynced to ``.txt`` verbatim. The write replaces whatever the name
    held, a link itself included, and is read back; raise `ReadBackError` when it
    does not read back as written. It lands before the removal, so a failure
    part-way leaves two sidecars, which the next normalization resolves, rather than
    none. The hash returned is of the bytes written, encoded once.

    A file already holding exactly those bytes is not written again, so its
    modification time stays. With no other kind to remove, nothing changed, and the
    answer is None: the run records no sidecar written and no hash for it, and the
    backup's own copy of the file is what undo compares it with.
    """
    target, other, text = _sidecar_content(mp3, lyrics)
    data = text.encode(_WRITE_ENCODING)
    kept = _holds(target, data)
    if kept and not _present(other):
        return None
    if not kept:
        lyrics_files.write_confirmed(target, data)
    digest = sidecar_digest(data)
    if not _present(other):
        return SidecarWrite(target.name, digest)
    other.unlink()
    return SidecarWrite(target.name, digest, (other.name,))
