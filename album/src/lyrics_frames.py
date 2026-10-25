"""The lyrics frames an answer is embedded as, and the pre-flight that they can be saved.

`new_frames` builds the frames `lyrics_tags.write_lyrics` leaves for an answer, and
`to_mutagen` turns a recorded frame into the mutagen frame every writer adds. Both live
here, apart from the file I/O, so `check_encodable` can build exactly those frames and
render them with no file at all: an answer the writer could not encode is then refused
before anything is changed, not part-way through a run's strip and embed.

`same_frames` is the one rule for "these frames hold the same lyrics": every field but
the encoding byte, in no order. The read-back after every save compares by it, and a
write whose frames are the same as the track's skips its save by it.

Three answers pass every earlier stage and still cannot be saved. A `SYLT` stamp holds
four bytes, so one at 2**32 ms or later fails to pack; a lone surrogate encodes in no
Unicode encoding; both fail in mutagen's own render, which the pre-flight runs into
memory. A NUL renders without complaint, but ends an ID3 text field, so the frame
would read back cut short there: it is refused by name, before the render. Pure: no
file is opened here, and nothing prints.
"""

from __future__ import annotations

import io
from collections import Counter
from collections.abc import Iterable, Iterator

from mutagen.id3 import ID3, SYLT, USLT, Frame, ID3v1SaveOptions

from lyrics_model import (
    SYLT_MILLISECONDS,
    LyricsError,
    FrameKind,
    FrameRecord,
    Lyrics,
    SyltEntry,
    SyncedLyrics,
)
from lyrics_stages import CheckEncodable

#: The ID3v2 minor versions mutagen can write, which every write saves at one of.
WRITABLE_MINORS = frozenset({3, 4})
#: The encoding byte this tool writes: UTF-8 (a v2.3 save turns it into UTF-16).
_UTF8 = 3
#: The SYLT content type for lyrics.
_SYLT_LYRICS = 1
#: Ends an ID3 text field, so text holding one would be saved cut short there.
_NUL = "\x00"


class UnencodableLyricsError(LyricsError):
    """An answer's frames cannot be saved as they read: a NUL would cut a field short."""


def _uslt_record(text: str, lang: str) -> FrameRecord:
    """The `USLT` this tool writes: UTF-8, *lang*, an empty description."""
    return FrameRecord(FrameKind.USLT, lang, "", _UTF8, None, None, text)


def new_frames(lyrics: Lyrics, lang: str) -> tuple[FrameRecord, ...]:
    """The frames embedding *lyrics* leaves: a timed `SYLT` and its `USLT`, or a `USLT`."""
    uslt = _uslt_record(lyrics.plain_text(), lang)
    if not isinstance(lyrics, SyncedLyrics):
        return (uslt,)
    entries = tuple(SyltEntry(line.text, line.ms) for line in lyrics.lines)
    sylt = FrameRecord(
        FrameKind.SYLT, lang, "", _UTF8, SYLT_MILLISECONDS, _SYLT_LYRICS, entries
    )
    return (sylt, uslt)


#: A frame's identity for comparing lyrics frames: everything but the encoding byte.
type PayloadKey = tuple[object, ...]


def payload_key(frame: FrameRecord) -> PayloadKey:
    """What a comparison of lyrics frames weighs of *frame*: all of it but the encoding.

    The kind, language, description, SYLT time format and content type, and the text
    or the entries with their times. Never the encoding byte: a v2.3 save re-encodes
    UTF-8 text as UTF-16, which v2.3 requires, so the same words read back under
    another encoding.
    """
    return (frame.kind, frame.lang, frame.desc, frame.format, frame.type, frame.payload)


def same_frames(found: Iterable[FrameRecord], expected: Iterable[FrameRecord]) -> bool:
    """True when *found* holds exactly *expected*, by `payload_key`, in any order.

    Order does not count: mutagen orders the frames it saves by their size, so no
    save keeps a stored order. Each frame counts as often as it appears.
    """
    return Counter(map(payload_key, found)) == Counter(map(payload_key, expected))


def to_mutagen(frame: FrameRecord) -> Frame:
    """The mutagen frame *frame* records, every field as recorded."""
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


def _frame_texts(frame: FrameRecord) -> Iterator[str]:
    """Every text field of *frame*: its language, description, and each line's text."""
    yield from (frame.lang, frame.desc)
    if isinstance(frame.payload, str):
        yield frame.payload
    else:
        yield from (entry.text for entry in frame.payload)


def preflight(frames: Iterable[FrameRecord]) -> None:
    """Raise unless *frames* save at every writable version; no file is touched.

    Rendered by mutagen's own save into memory, through `to_mutagen` as the write
    converts them, so an encoding failure raises here as the save would raise it, its
    type and message the same. A NUL is refused first, since it renders.
    """
    tags = ID3()
    for frame in frames:
        if any(_NUL in text for text in _frame_texts(frame)):
            raise UnencodableLyricsError(
                "the lyrics hold a NUL character, which would cut the frame short"
            )
        tags.add(to_mutagen(frame))
    for minor in sorted(WRITABLE_MINORS):
        memory = io.BytesIO()
        tags.save(memory, v1=ID3v1SaveOptions.REMOVE, v2_version=minor, v23_sep=None)


def check_encodable(lyrics: Lyrics, lang: str) -> None:
    """Raise if `write_lyrics` could not encode *lyrics* in *lang*; touch no file.

    The frames are the ones the write builds, so the two cannot disagree. Every run
    asks this before its embed, a fresh one before it strips, and `write_lyrics` asks
    it again before it opens the file.
    """
    preflight(new_frames(lyrics, lang))


#: The stage function bound to its protocol, so the type checker holds the signature.
STAGE: CheckEncodable = check_encodable
