"""Tag builders for the tag, backup and undo suites: planted frames, tags mutagen cannot write.

`plant_frames` writes lyrics frames through mutagen directly, so the suite that tests
reading frames does not depend on the module's own writer. `build_rich_mp3` and
`append_v1_tail` build the tags a write must leave alone: frames beyond the query's,
and an ID3v1 tail whose values differ from the v2 tag. `build_v22_mp3` and
`build_v1_only_mp3` make the two tag shapes a save must not produce: an ID3v2.2 tag,
which mutagen reads but cannot write, and a file with only an ID3v1 tag.
`make_read_only` makes a track no write can open, and `MODES_BIND` says whether a mode
stops this process at all. `uslt` and `sylt` build frame records, and `other_frames`
lists the frames a lyrics write must leave alone. Every lyric here is invented.

Not discovered as a suite (no ``test_`` prefix).
"""

from __future__ import annotations

import os
import unittest
from collections.abc import Iterable
from pathlib import Path
from tempfile import TemporaryDirectory

from mutagen.id3 import (
    ID3,
    SYLT,
    TALB,
    TCOM,
    TDAT,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TSOA,
    TSOP,
    TSOT,
    TXXX,
    TYER,
    USLT,
    Frame,
    MakeID3v1,
    delete,
)

import fixtures
import lyrics_model as lm

#: How long every built track plays, in seconds.
SECONDS = 12.0
#: Bits per byte of a synchsafe integer: the high bit of each byte stays clear.
_SYNCHSAFE_BITS = 7
_SYNCHSAFE_BYTES = 4
#: An ID3v2.3 frame header's size field, a plain big-endian integer, and its flags.
_V23_SIZE_BYTES = 4
_V23_NO_FLAGS = b"\x00\x00"
#: A v2.2 frame header's size field width, in bytes.
_V22_SIZE_BYTES = 3
#: The ID3 text encodings a planted frame may name.
LATIN1, UTF16, UTF8 = 0, 1, 3
#: The SYLT time formats: MPEG frames, and milliseconds.
MPEG_FRAMES, MILLISECONDS = 1, 2
#: The SYLT content type for lyrics.
LYRICS_TYPE = 1
#: How many bytes an ID3v1 tag takes at the end of a file.
V1_TAG_BYTES = 128
#: The ID3v2.3 minor version, which dates a track with TYER and TDAT.
_V23_MINOR = 3
#: The year an ID3v1-only track carries.
V1_YEAR = "1987"
#: The modes of a track no one may write, and of one its creator may write again.
_READ_ONLY_MODE, _WRITABLE_MODE = 0o444, 0o644


def uslt(
    text: str, *, lang: str = "eng", desc: str = "", encoding: int = UTF8
) -> lm.FrameRecord:
    """A USLT frame record holding *text*."""
    return lm.FrameRecord(lm.FrameKind.USLT, lang, desc, encoding, None, None, text)


def sylt(
    *entries: tuple[int, str],
    lang: str = "eng",
    desc: str = "",
    encoding: int = UTF8,
    time_format: int = MILLISECONDS,
) -> lm.FrameRecord:
    """A SYLT frame record holding *entries*, given as ``(time, text)`` in stored order."""
    payload = tuple(lm.SyltEntry(text, time) for time, text in entries)
    return lm.FrameRecord(
        lm.FrameKind.SYLT, lang, desc, encoding, time_format, LYRICS_TYPE, payload
    )


def other_frames(path: Path) -> list[str]:
    """Every frame of *path*'s ID3v2 tag but SYLT and USLT, as stored, sorted.

    Loaded untranslated and without the ID3v1 tail, so a frame a save converted,
    dropped or merged in shows as a difference.
    """
    tags = ID3(path, translate=False, load_v1=False)
    lyrics_ids = {kind.value for kind in lm.FrameKind}
    return sorted(repr(f) for f in tags.values() if f.FrameID not in lyrics_ids)


def to_mutagen(frame: lm.FrameRecord) -> Frame:
    """The mutagen frame *frame* describes, field for field."""
    if isinstance(frame.payload, str):
        return USLT(
            encoding=frame.encoding,
            lang=frame.lang,
            desc=frame.desc,
            text=frame.payload,
        )
    return SYLT(
        encoding=frame.encoding,
        lang=frame.lang,
        format=frame.format,
        type=frame.type,
        desc=frame.desc,
        text=[(entry.text, entry.time) for entry in frame.payload],
    )


def plant_frames(path: Path, frames: Iterable[lm.FrameRecord]) -> None:
    """Add *frames* to *path*'s existing ID3v2 tag, keeping its version and other frames.

    Loaded untranslated, so a v2.3 tag is saved back without a conversion that would
    drop its v2.4-only frames.
    """
    tags = ID3(path, translate=False, load_v1=False)
    for frame in frames:
        tags.add(to_mutagen(frame))
    tags.save(path, v1=0, v2_version=tags.version[1], v23_sep=None)


def build_rich_mp3(path: Path, minor: int) -> Path:
    """A silent MP3 under an ID3v2.*minor* tag holding frames beyond the query's.

    Both versions carry sort-order frames, which are v2.4-only, and a user-defined
    text frame; v2.3 adds its own date pair (``TYER`` and ``TDAT``), v2.4 its
    ``TDRC``. v2.3 also carries a two-valued text frame and a value holding a slash,
    which a v2.3 save joining values with a slash would merge or split. The frames are
    built untranslated and saved as they are, values separated by a null.
    """
    fixtures.build_silent_mp3(path, SECONDS, id3_version=None)
    tags = ID3()
    frames: list[Frame] = [
        TPE1(encoding=LATIN1, text=["Invented Artist"]),
        TIT2(encoding=LATIN1, text=["Invented Title"]),
        TSOP(encoding=LATIN1, text=["Artist, Invented"]),
        TSOA(encoding=LATIN1, text=["Album, Invented"]),
        TSOT(encoding=LATIN1, text=["Title, Invented"]),
        TXXX(encoding=LATIN1, desc="Invented Key", text=["invented value"]),
    ]
    if minor == _V23_MINOR:
        frames += [
            TYER(encoding=LATIN1, text=["2001"]),
            TDAT(encoding=LATIN1, text=["1503"]),
            TCOM(encoding=LATIN1, text=["Alpha Composer", "Beta Composer"]),
            TPE2(encoding=LATIN1, text=["Gamma/Delta"]),
        ]
    else:
        frames.append(TDRC(encoding=LATIN1, text=["2001-03-15"]))
    for frame in frames:
        tags.add(frame)
    tags.save(path, v1=0, v2_version=minor, v23_sep=None)
    return path


def append_v1_tail(path: Path) -> bytes:
    """Append an ID3v1 tag to *path* whose values differ from any v2 tag built here.

    It names another artist and title, and an album and year a built v2 tag lacks,
    so a save that merged it into v2 or rebuilt it from v2 would show. Return the
    appended 128 bytes.
    """
    source = ID3()
    source.add(TPE1(encoding=LATIN1, text=["Tail Artist"]))
    source.add(TIT2(encoding=LATIN1, text=["Tail Title"]))
    source.add(TALB(encoding=LATIN1, text=["Tail Album"]))
    source.add(TDRC(encoding=LATIN1, text=["1999"]))
    tail = MakeID3v1(source)
    with path.open("ab") as stream:
        stream.write(tail)
    return tail


def _synchsafe(value: int) -> bytes:
    """*value* as a four-byte ID3 synchsafe integer."""
    mask = (1 << _SYNCHSAFE_BITS) - 1
    groups = [
        (value >> (_SYNCHSAFE_BITS * shift)) & mask
        for shift in reversed(range(_SYNCHSAFE_BYTES))
    ]
    return bytes(groups)


def _v22_frame(frame_id: bytes, body: bytes) -> bytes:
    """One ID3v2.2 frame: a three-letter id, a three-byte size, then *body*."""
    return frame_id + len(body).to_bytes(_V22_SIZE_BYTES, "big") + body


def build_v22_mp3(
    path: Path, title: str = "Invented Title", lyrics: str | None = None
) -> Path:
    """A silent MP3 under a hand-built ID3v2.2 tag: a ``TT2`` title, and ``ULT`` lyrics.

    The ``ULT`` frame (v2.2's ``USLT``) is Latin-1, English, with an empty
    description, and is left out when *lyrics* is None.
    """
    fixtures.build_silent_mp3(path, SECONDS, id3_version=None)
    latin1 = bytes([LATIN1])
    frames = _v22_frame(b"TT2", latin1 + title.encode("latin-1"))
    if lyrics is not None:
        body = latin1 + b"eng" + b"\x00" + lyrics.encode("latin-1")
        frames += _v22_frame(b"ULT", body)
    header = b"ID3\x02\x00\x00" + _synchsafe(len(frames))
    path.write_bytes(header + frames + path.read_bytes())
    return path


def build_v23_utf8_mp3(path: Path, lyrics: str) -> Path:
    """A silent MP3 under a hand-built ID3v2.3 tag whose one USLT is stored as UTF-8.

    UTF-8 is not a v2.3 encoding, so mutagen's own v2.3 save never stores it; another
    tool may have, so the frame is built by hand: English, with an empty description.
    """
    fixtures.build_silent_mp3(path, SECONDS, id3_version=None)
    body = bytes([UTF8]) + b"eng" + b"\x00" + lyrics.encode("utf-8")
    size = len(body).to_bytes(_V23_SIZE_BYTES, "big")
    frame = b"USLT" + size + _V23_NO_FLAGS + body
    header = b"ID3\x03\x00\x00" + _synchsafe(len(frame))
    path.write_bytes(header + frame + path.read_bytes())
    return path


def build_v1_only_mp3(path: Path, artist: str) -> Path:
    """A silent MP3 carrying an ID3v1 tag with *artist* and a year, and no ID3v2 header."""
    fixtures.build_silent_mp3(path, SECONDS, id3_version=None)
    tags = ID3()
    tags.add(TPE1(encoding=LATIN1, text=[artist]))
    tags.add(TDRC(encoding=LATIN1, text=[V1_YEAR]))
    tags.save(path, v1=2)
    delete(path, delete_v1=False, delete_v2=True)
    return path


def _probe_modes_bind() -> bool:
    """True when a read-only mode stops this process writing a file.

    It does not for root, nor on a file system that ignores modes, so the suites that
    rely on it skip there. The probe has no branch, so the answer costs no coverage.
    """
    with TemporaryDirectory() as folder:
        probe = Path(folder) / "probe"
        probe.touch()
        probe.chmod(_READ_ONLY_MODE)
        binds = not os.access(probe, os.W_OK)
        # Windows refuses to remove a read-only file, so the folder's cleanup needs it.
        probe.chmod(_WRITABLE_MODE)
    return binds


#: Whether `make_read_only` keeps this process from writing; probed once, at import.
MODES_BIND = _probe_modes_bind()
#: The decorator every read-only test wears, naming why it would skip.
needs_binding_modes = unittest.skipUnless(
    MODES_BIND, "a read-only mode does not stop this process writing (root?)"
)


def make_read_only(case: unittest.TestCase, path: Path) -> Path:
    """Make *path* read-only for the rest of *case*, writable again at its cleanup."""
    path.chmod(_READ_ONLY_MODE)
    case.addCleanup(path.chmod, _WRITABLE_MODE)
    return path
