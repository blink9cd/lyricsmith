"""Read a track's tags and lyrics frames through mutagen; write, strip and restore them.

`read_tags` reads the search query's artist and title, the audio duration, the ID3
version and every `SYLT` / `USLT` frame verbatim, `read_track_number` the track number
as its tag writes it (for the sweep's silence list), and `check_writable` says, changing
nothing, whether `write_lyrics` could save a track: its tag version and whether the file
opens for writing. `write_lyrics` saves the frames `lyrics_frames` builds for an answer,
unless the track already holds exactly those, and asks its pre-flight first, so an
answer it cannot encode leaves the file as it was. The three writers change only those
two frame kinds, and each follows the same order: load the tag, change the lyrics
frames, save in place, then read the file back and compare.

Four properties hold for every write:

- **The ID3 version stays.** mutagen saves as v2.4 unless told otherwise, and a tagger
  set to write v2.3 would see its whole tag rewritten. So a save passes the loaded tag's
  own version. mutagen cannot write v2.2, so a v2.2 tag raises `TagVersionError` before
  anything changes. A file with no ID3v2 header has no version to keep and is written as
  v2.4, or at the recorded version for a restore.
- **No other frame changes.** mutagen's default load translates a v2.3 tag to v2.4 in
  memory, and converting it back for a v2.3 save deletes every v2.4-only frame (the sort
  orders, mood, release time and more) and rewrites the dates. So a writer loads the tag
  untranslated and saves it at its own version with no conversion, keeping a v2.3
  frame's several values null-separated rather than joined with mutagen's default
  slash: only the `SYLT` and `USLT` frames differ afterwards. The one exception no load
  setting avoids: a frame mutagen cannot parse (a lyrics frame whose language is not
  ASCII, say) is skipped at load, so any save drops it. To avoid converting, a
  restore whose recorded version differs from the file's current ID3v2 version raises
  `TagVersionError`, untouched, rather than converting the tag: the recorded version
  was the file's when the backup was taken, so a different one now means another tool
  rewrote the tag since, and undoing that is not this tool's to do. Only a file with no
  ID3v2 header takes the recorded version, since it has no frames to convert but the
  ID3v1 fields below.
- **The ID3v1 tail stays byte-identical.** mutagen merges a v1 tag's fields into the v2
  tag at load and rebuilds the v1 tag from v2 at save. So a file with an ID3v2 header is
  loaded without its v1 tag, and every save cuts the original v1 bytes off and puts them
  back exactly. A file with only a v1 tag gains a v2.4 tag holding its v1 fields, and its
  v1 tail stays as it was. APE tags sit before the v1 tail and are not touched.
- **Act, then confirm.** The file is re-read after the save and its lyrics frames are
  compared with what was written, by payload only: kind, language, description, the
  SYLT format and type, and the text and times. Never the encoding byte, because a v2.3
  save re-encodes UTF-8 text as UTF-16, which v2.3 requires. The file must also end in
  the ID3v1 bytes put back. A mismatch raises `ReadBackError`.

mutagen keys a lyrics frame by its kind, description and language, and collapses frames
sharing a key at read, keeping the last one stored. A file holding such duplicates
therefore reads, and survives any save here, as only that last one; an embed whose
frames are already the ones read skips its save, and leaves the duplicates as they were.

The save is mutagen's in-place one, not a temporary copy renamed over the file: a music
library that tracks files by identity would lose a file given a new one. Every read of
a track goes through `lyrics_files.open_regular`, and mutagen is handed the open file:
a FIFO pipe or a device swapped in under a track's name since it was found is
`NotRegularFileError` at once, never waited on or read without end. A save opens the
track that way too, for update. The caller holds the album lock. Nothing here prints.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol, cast

from mutagen import MutagenError
from mutagen.id3 import (
    ID3,
    TCON,
    USLT,
    Encoding,
    Frame,
    ID3NoHeaderError,
    ID3v1SaveOptions,
)

# mutagen's private ID3v1 finder, kept on purpose: it is the one that agrees with how
# mutagen itself reads the tail. Private means no deprecation contract, so any mutagen
# version bump must re-run the ID3v1 tail tests; a tail it misreads fails closed, since
# the read-back checks the file still ends in the bytes put back.
from mutagen.id3._id3v1 import find_id3v1
from mutagen.mp3 import MP3

from lyrics_files import NotRegularFileError, open_regular
from lyrics_frames import (
    WRITABLE_MINORS,
    new_frames,
    preflight,
    same_frames,
    to_mutagen,
)
from lyrics_model import (
    FrameKind,
    FrameRecord,
    Lyrics,
    ReadBackError,
    SyltEntry,
    TagVersionError,
    TrackTags,
)
from lyrics_stages import (
    CheckWritable,
    ReadTags,
    RestoreFrames,
    StripLyrics,
    WriteLyrics,
)

#: The ID3 major version every writable tag carries, and the minor versions mutagen
#: can write. v2.2 reads but cannot be written back.
_ID3V2 = 2
_WRITABLE_MINORS = WRITABLE_MINORS
#: The v2.3 minor: a header-less file restored at it has its v1 fields converted.
_V23_MINOR = 3
#: The UTF-8 encoding byte, which v2.3 does not allow: its save re-encodes it.
_UTF8 = Encoding.UTF8
#: The version a file with no ID3v2 header is written at: mutagen's default.
_DEFAULT_VERSION = (_ID3V2, 4)
#: The text frames the search query is built from: artist, title and album.
_QUERY_FRAMES = ("TPE1", "TIT2", "TALB")
_LYRICS_FRAME_IDS = frozenset(kind.value for kind in FrameKind)


# --- Reading ---------------------------------------------------------------------


def _load(path: Path, *, load_v1: bool) -> ID3 | None:
    """*path*'s ID3 tag loaded untranslated, or None when mutagen finds no tag to load.

    Untranslated, so a v2.3 tag keeps its own frames and saving it at its own version
    converts nothing. A v2.2 tag, which is never written, needs no translation to be
    read: mutagen names its three-letter frames by their v2.3 and v2.4 names as it
    parses them. With *load_v1*, ID3v1 fields the v2 tag lacks are merged in, and a
    file with only a v1 tag loads with version 1.1, its fields as v2.4 frames.
    """
    try:
        with open_regular(path, "rb") as stream:
            return ID3(stream, translate=False, load_v1=load_v1)
    except ID3NoHeaderError:
        return None


def _load_id3(path: Path) -> ID3 | None:
    """*path*'s ID3 tag as a reader sees it: v1 fields fill gaps the query can use."""
    return _load(path, load_v1=True)


def _load_for_write(path: Path) -> ID3 | None:
    """*path*'s ID3 tag as a writer changes it, or None when the file carries none.

    A file with an ID3v2 header is loaded without its ID3v1 tag, which a save would
    otherwise copy into v2. A file with only a v1 tag loads it, so its fields carry
    into the v2.4 tag the save creates.
    """
    tags = _load(path, load_v1=False)
    return tags if tags is not None else _load(path, load_v1=True)


def _v2_version(tags: ID3 | None) -> tuple[int, int] | None:
    """The ``(2, minor)`` ID3v2 version of *tags*, or None when it is not ID3v2."""
    if tags is None or tags.version[0] != _ID3V2:
        return None
    return (_ID3V2, tags.version[1])


def _duration(path: Path) -> float | None:
    """The audio length in seconds, or None when no MPEG frame can be read."""
    try:
        with open_regular(path, "rb") as stream:
            info = MP3(stream).info
    except MutagenError:
        return None
    return float(info.length) if info is not None else None


def _first_text(tags: ID3 | None, frame_id: str) -> str | None:
    """The first value of text frame *frame_id*, or None when absent or blank.

    A multi-valued frame gives its first value: the query needs one artist.
    """
    frame = tags.get(frame_id) if tags is not None else None
    if frame is None or not frame.text:
        return None
    value = str(frame.text[0])
    return value if value.strip() else None


def _genres(tags: ID3 | None) -> tuple[str, ...]:
    """The TCON frame's genres through mutagen, blank ones dropped; () without a frame.

    ``TCON.genres`` splits a v2.4 frame's values and names ID3v1 numeric references
    such as ``(57)``, which the untranslated load leaves raw in the frame's text.
    """
    frame = tags.get("TCON") if tags is not None else None
    if not isinstance(frame, TCON):
        return ()
    return tuple(genre for genre in frame.genres if genre.strip())


class _LyricsFields(Protocol):
    """The fields a mutagen `USLT` frame carries, which mutagen sets dynamically."""

    lang: str
    desc: str
    encoding: int


class _UsltFields(_LyricsFields, Protocol):
    """A `USLT`'s fields: the text is one string."""

    text: str


class _SyltFields(_LyricsFields, Protocol):
    """A `SYLT`'s fields: the time format, the content type, ``(text, time)`` pairs."""

    format: int
    type: int
    text: list[tuple[str, int]]


def _record(frame: Frame) -> FrameRecord:
    """The verbatim record of one mutagen `SYLT` or `USLT` frame.

    mutagen declares a frame's fields at runtime from its field list, which the type
    checker cannot see, so the fields are read through the protocols above and each
    value is converted to the type the record holds.
    """
    if isinstance(frame, USLT):
        uslt = cast(_UsltFields, frame)
        return FrameRecord(
            FrameKind.USLT,
            str(uslt.lang),
            str(uslt.desc),
            int(uslt.encoding),
            None,
            None,
            str(uslt.text),
        )
    sylt = cast(_SyltFields, frame)
    entries = tuple(SyltEntry(str(text), int(time)) for text, time in sylt.text)
    return FrameRecord(
        FrameKind.SYLT,
        str(sylt.lang),
        str(sylt.desc),
        int(sylt.encoding),
        int(sylt.format),
        int(sylt.type),
        entries,
    )


def _lyrics_frames(tags: ID3 | None) -> tuple[FrameRecord, ...]:
    """Every `SYLT` and `USLT` frame in *tags*, in stored order."""
    if tags is None:
        return ()
    return tuple(
        _record(frame) for frame in tags.values() if frame.FrameID in _LYRICS_FRAME_IDS
    )


def read_tags(path: Path) -> TrackTags:
    """Read *path*'s query tags, genres, duration, ID3v2 version and every lyrics frame."""
    tags = _load_id3(path)
    artist, title, album = (_first_text(tags, frame_id) for frame_id in _QUERY_FRAMES)
    return TrackTags(
        artist=artist,
        title=title,
        album=album,
        genres=_genres(tags),
        duration_s=_duration(path),
        id3_version=_v2_version(tags),
        frames=_lyrics_frames(tags),
    )


def read_track_number(path: Path) -> str | None:
    """*path*'s TRCK frame as written (``3/12``), or None when absent or blank."""
    return _first_text(_load_id3(path), "TRCK")


# --- Saving and confirming -------------------------------------------------------


def _writable_minor(version: tuple[int, int]) -> int:
    """The minor version a save passes for *version*; raise when mutagen cannot write it."""
    major, minor = version
    if major != _ID3V2 or minor not in _WRITABLE_MINORS:
        raise TagVersionError(f"cannot write an ID3v{major}.{minor} tag")
    return minor


def _v1_tail(path: Path) -> bytes:
    """*path*'s ID3v1 tag as stored, or no bytes when it has none.

    Found by mutagen's own locator, so the bytes are exactly the ones its save cuts off.
    It sits in a private module: mutagen exposes no public way to keep a v1 tag as it
    is, and the exact pin holds the locator's behavior, which the suite proves.
    """
    with open_regular(path, "rb") as stream:
        _tag, offset = find_id3v1(stream)
        # A zero offset (no tag) reads from the end: no bytes.
        stream.seek(offset, os.SEEK_END)
        return stream.read()


def _last_bytes(path: Path, count: int) -> bytes:
    """The last *count* bytes of *path*, or the whole file when it is shorter."""
    with open_regular(path, "rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        stream.seek(max(size - count, 0))
        return stream.read()


def _save(tags: ID3, path: Path, minor: int) -> bytes:
    """Save *tags* into *path* in place, as ID3v2.*minor*, its ID3v1 tail as it was.

    The one save every writer goes through, which is the seam a test injects a fault
    at. No frame is converted: the tag was loaded untranslated at this version. mutagen
    either removes the v1 tail or rebuilds it from the v2 tag, so the save removes it
    and the original bytes are appended back. A v2.3 frame holding several values
    keeps them null-separated, as stored: mutagen's default joins them with a slash,
    which merges them into one value, and splits nothing back apart on reading. The
    track is opened by `open_regular`, the tail appended through it, so mutagen opens
    no name; a refused open is raised as mutagen raised one, `MutagenError` from the
    `OSError`. Return the tail appended, which the read-back checks.
    """
    tail = _v1_tail(path)
    try:
        stream = open_regular(path, "r+b")
    except OSError as error:
        if isinstance(error, NotRegularFileError):
            raise
        raise MutagenError(error) from error
    with stream:
        tags.save(stream, v1=ID3v1SaveOptions.REMOVE, v2_version=minor, v23_sep=None)
        if tail:
            stream.seek(0, os.SEEK_END)
            stream.write(tail)
    return tail


def _confirm(path: Path, expected: Iterable[FrameRecord], tail: bytes) -> None:
    """Re-read *path*; raise `ReadBackError` unless it holds *expected* and ends in *tail*.

    Compared as `same_frames` compares, so in no order. *tail* is the ID3v1 tag the
    save appended back, or no bytes when there was none.
    """
    if _last_bytes(path, len(tail)) != tail:
        raise ReadBackError(f"the ID3v1 tag of {path.name} did not go back unchanged")
    if not same_frames(read_tags(path).frames, expected):
        raise ReadBackError(f"the lyrics frames read back from {path.name} differ")


def _save_and_confirm(
    tags: ID3, path: Path, minor: int, expected: Sequence[FrameRecord]
) -> None:
    """Save *tags* as ID3v2.*minor*, then confirm *path* holds *expected* and its tail."""
    tail = _save(tags, path, minor)
    _confirm(path, expected, tail)


# --- Writing ---------------------------------------------------------------------


def _replace_lyrics_frames(tags: ID3, frames: Iterable[FrameRecord]) -> None:
    """Remove every `SYLT` and `USLT` frame from *tags*, then add *frames*."""
    for kind in FrameKind:
        tags.delall(kind.value)
    for frame in frames:
        tags.add(to_mutagen(frame))


def _write_target(path: Path) -> tuple[ID3 | None, int]:
    """*path*'s tag as `write_lyrics` loads it, and the minor version it saves at.

    Raise `TagVersionError` on a tag mutagen cannot write, before anything changes.
    """
    loaded = _load_for_write(path)
    return loaded, _writable_minor(_v2_version(loaded) or _DEFAULT_VERSION)


def check_writable(path: Path) -> None:
    """Raise if `write_lyrics` could not save *path*, foreseeably; change nothing.

    Two refusals are foreseen: a tag version the write cannot save, raised as the
    `TagVersionError` the write raises, since the tag is loaded as the write loads it,
    and a file the write cannot open for writing, raised as the open's `OSError`. A
    fresh run asks this before it strips, so it never strips a track either would
    stop. A name that is no regular file (a FIFO pipe swapped in) raises
    `lyrics_files.NotRegularFileError`, at once. Nothing else is foreseen: a full disk
    during the write still leaves a fresh run's track stripped.
    """
    _write_target(path)
    # Opened as the save opens it, then closed unwritten: the open itself is the
    # authority on modes, ACLs and flags, where os.access answers for modes alone.
    # Closing a file nothing was written to leaves its bytes and mtime alone.
    with open_regular(path, "r+b"):
        pass


def _already_written(
    held: Sequence[FrameRecord], frames: Sequence[FrameRecord], minor: int
) -> bool:
    """True when *held*, in a tag saved at v2.*minor*, already are the write's *frames*.

    Compared by `same_frames`, so the encoding byte does not count, except UTF-8 in a
    v2.3 tag: v2.3 does not allow it, so such a frame, stored by another tool, is
    saved, which re-encodes it as UTF-16.
    """
    invalid = minor == _V23_MINOR and any(f.encoding == _UTF8 for f in held)
    return not invalid and same_frames(held, frames)


def write_lyrics(path: Path, lyrics: Lyrics, lang: str) -> bool:
    """Embed *lyrics* in *path*, keeping its ID3 version, then confirm by reading back.

    Synced lyrics leave a millisecond `SYLT` and a `USLT` of the same lines without
    their times; unsynced lyrics leave a `USLT` alone, so no stale `SYLT` can disagree
    with it. Either way every earlier frame of both kinds goes. Return whether the
    file was saved: when its lyrics frames already are the ones the write would leave
    (`_already_written`: `same_frames`, and none stored as UTF-8 in a v2.3 tag), it
    is not, so its bytes and modification time stay. Nothing else
    a save would do is the write's to do: the version stays, the other frames and
    the ID3v1 tail come back as they were, and a file with no ID3v2 header holds no
    lyrics frame, so it is always saved. Raise what `lyrics_frames.check_encodable`
    raises, before the file is opened, `TagVersionError` on an ID3v2.2 tag, even one
    holding these very frames, and `ReadBackError` on a mismatch.
    """
    frames = new_frames(lyrics, lang)
    preflight(frames)
    loaded, minor = _write_target(path)
    if _already_written(_lyrics_frames(loaded), frames, minor):
        return False
    tags = loaded if loaded is not None else ID3()
    _replace_lyrics_frames(tags, frames)
    _save_and_confirm(tags, path, minor, frames)
    return True


def strip_lyrics(path: Path) -> bool:
    """Remove every `SYLT` and `USLT` frame from *path*, keeping its ID3 version.

    Return True when a lyrics frame was removed. A file with no lyrics frame, a file
    with no ID3v2 header among them, is left unwritten, and the answer is False, read
    from the one load the strip itself makes. Raise `TagVersionError` on an ID3v2.2 tag
    that holds lyrics frames, and `ReadBackError` when a frame survives the save.
    """
    tags = _load_for_write(path)
    if tags is None or not _lyrics_frames(tags):
        return False
    minor = _writable_minor((tags.version[0], tags.version[1]))
    _replace_lyrics_frames(tags, ())
    _save_and_confirm(tags, path, minor, ())
    return True


def restore_frames(
    path: Path, frames: Sequence[FrameRecord], id3_version: tuple[int, int]
) -> None:
    """Replace *path*'s lyrics frames with *frames*, verbatim, saved as *id3_version*.

    Every field is written as recorded, the encoding byte included; only a v2.3 save's
    re-encoding of UTF-8 can change that byte, which the read-back allows. Raise
    `TagVersionError`, before anything changes, when *id3_version* cannot be written or
    differs from the file's current ID3v2 version (converting the tag would change
    frames this tool does not own), and `ReadBackError` when the file does not hold
    *frames* afterwards.
    """
    minor = _writable_minor(id3_version)
    loaded = _load_for_write(path)
    tags = _restorable(loaded, minor)
    _replace_lyrics_frames(tags, frames)
    _save_and_confirm(tags, path, minor, frames)


def _restorable(loaded: ID3 | None, minor: int) -> ID3:
    """The tag a restore at ID3v2.*minor* writes into, given the file's *loaded* tag.

    A tag at another ID3v2 version is refused. A file with no ID3v2 header gets a new
    tag, holding any ID3v1 fields as v2.4 frames, which a v2.3 save needs converted.
    """
    current = _v2_version(loaded)
    if current is not None and current[1] != minor:
        raise TagVersionError(
            f"the file is now ID3v{current[0]}.{current[1]}, recorded as v2.{minor}"
        )
    tags = loaded if loaded is not None else ID3()
    if current is None and minor == _V23_MINOR:
        tags.update_to_v23()
    return tags


#: Each stage function bound to its protocol, so the type checker holds the signatures.
STAGES: tuple[ReadTags, WriteLyrics, CheckWritable, StripLyrics, RestoreFrames] = (
    read_tags,
    write_lyrics,
    check_writable,
    strip_lyrics,
    restore_frames,
)
