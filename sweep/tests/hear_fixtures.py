"""Builders for the hearing report's suites: invented songs, timed sidecars, genres, records.

A song is thirty distinct invented words in five lines, so a transcript of it holds
more distinct word pairs than the hearing's fewest (`MIN_HEARD_PAIRS`), and a transcript
of one song shares no pair with another's. A hearing record is written as sync's
hearing mode writes one, through the hearing log's own writer. A genre, or an untimed
text, is written into an empty MP3 as an ID3 TCON or USLT frame through mutagen, as the
album tool reads it back.
Every name, word and genre here is invented.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``, after importing the entry point.
"""

from __future__ import annotations

from itertools import product
from pathlib import Path

from mutagen.id3 import ID3, TCON, USLT

from lyrics_heard import text_fingerprint
from lyrics_hearing_log import HeardKind, HeardVerdict, HearingRecord, write_hearing

#: The default model's file name, as the report's default names it.
TURBO = "ggml-large-v3-turbo.bin"
#: A second model's file name.
LARGE = "ggml-large-v3.bin"
_SYLLABLES = "bdfgkm"
_WORDS_A_LINE = 6
_WORDS = 30


def song(prefix: str) -> str:
    """An invented song of thirty distinct words, each opening with *prefix*'s letters."""
    words = [prefix + a + b for a, b in product(_SYLLABLES, repeat=2)][:_WORDS]
    lines = (
        " ".join(words[start : start + _WORDS_A_LINE])
        for start in range(0, _WORDS, _WORDS_A_LINE)
    )
    return "".join(line + "\n" for line in lines)


def lrc_of(text: str) -> str:
    """*text* as a timed ``.lrc``, a line a second."""
    return "".join(
        f"[00:{second:02d}.00]{line}\n"
        for second, line in enumerate(text.splitlines(), start=1)
    )


def tag_genre(mp3: Path, *genres: str) -> Path:
    """Write *genres* into *mp3*'s TCON frame; return it."""
    tags = ID3()
    tags.add(TCON(encoding=3, text=list(genres)))
    tags.save(mp3)
    return mp3


def frame_text(mp3: Path, text: str) -> Path:
    """Write *text* into *mp3*'s untimed USLT frame, in English; return it."""
    tags = ID3()
    tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
    tags.save(mp3)
    return mp3


def hearing(
    path: Path,
    verdict: HeardVerdict,
    *,
    transcript: str = "",
    model: str = TURBO,
    share: float = 0.5,
    kind: HeardKind = HeardKind.PROVIDER_TIMED,
) -> HearingRecord:
    """An invented hearing of the track *path* by *model*."""
    return HearingRecord(
        str(path),
        text_fingerprint(transcript),
        kind,
        model,
        30,
        share,
        verdict,
        transcript,
    )


def log(folder: Path, *records: HearingRecord) -> Path:
    """Write *records* into the hearing log *folder*, made first; return it."""
    folder.mkdir(parents=True, exist_ok=True)
    for record in records:
        write_hearing(folder, record)
    return folder
