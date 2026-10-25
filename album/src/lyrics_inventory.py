"""What lyrics a track already holds: synced, untimed only, or none.

`classify_track` reads a track's lyrics frames and its sidecars and names the strongest
kind they hold, writing nothing and contacting nothing. The one rule for "synced" lives
here, in `lyrics_kind`, so every caller deciding whether a track is already synced asks
the same question:

- **synced**: a SYLT timed in milliseconds with at least one worded entry, or a timed
  ``.lrc`` with at least one worded line;
- **untimed**: worded lyrics with no usable timing: a USLT, a ``.txt``, a ``.lrc`` with
  no stamp, or a SYLT timed in MPEG frames, which cannot become an ``.lrc`` without the
  audio's frame rate;
- **none**: nothing worded anywhere. Lyrics holding only whitespace are none.

`sidecar_matches` is the one rule for "this sidecar holds the frames' own lyrics": the
lyrics of its kind as the sidecar writer and reader carry them, so a sidecar a run
wrote from the frames always matches them. Every worded frame of the sidecar's kind is
compared, whatever its language or description, so a sidecar written under one
``--lang`` still matches under another. `EmbeddedLyrics.serving` sets such a sidecar
aside and `EmbeddedLyrics.favoring` ranks the frame it holds first, so the run's fetch
and the dry run's plan serve the track from that frame, which keeps its place.

The frames are read by `lyrics_tags.read_tags` and converted by
`lyrics_model.lyrics_from_frames`, the sidecars by `lyrics_sidecar.plan_sidecars`, so
nothing here parses a file itself. A sidecar that cannot be read raises as the sidecar
stage raises it; the caller decides what that means. Nothing here prints.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from lyrics_model import (
    SYLT_MILLISECONDS,
    FrameKind,
    FrameRecord,
    Lyrics,
    PlainLyrics,
    SyncedLyrics,
    frame_rank,
    lyrics_from_frames,
)
from lyrics_placeholder import is_placeholder_lyrics
from lyrics_sidecar import lrc_read_back, plan_sidecars, txt_read_back
from lyrics_tags import read_tags


class TrackLyricsKind(StrEnum):
    """The strongest lyrics a track holds, in its frames or its sidecars."""

    SYNCED = "synced"
    UNTIMED = "untimed"
    NONE = "none"


#: Each kind's strength, so several held lyrics reduce to the strongest.
_STRENGTH = {
    TrackLyricsKind.NONE: 0,
    TrackLyricsKind.UNTIMED: 1,
    TrackLyricsKind.SYNCED: 2,
}


def _kind_of_one(lyrics: Lyrics | None) -> TrackLyricsKind:
    """The kind one lyrics value is: synced or untimed when worded, else none."""
    if isinstance(lyrics, SyncedLyrics):
        worded = any(line.text.strip() for line in lyrics.lines)
        return TrackLyricsKind.SYNCED if worded else TrackLyricsKind.NONE
    if lyrics is not None and lyrics.text.strip():
        return TrackLyricsKind.UNTIMED
    return TrackLyricsKind.NONE


def lyrics_kind(*held: Lyrics | None) -> TrackLyricsKind:
    """The strongest kind among *held*: the shared rule for synced, untimed and none.

    Synced lyrics count only with a worded line, plain lyrics only with non-blank text.
    The values are taken as converted: `lyrics_from_frames` already reads a SYLT timed
    in MPEG frames as plain, and `plan_sidecars` reads a ``.lrc`` with no stamp as
    plain, so neither is synced here. No value at all is none.
    """
    kinds = (_kind_of_one(lyrics) for lyrics in held)
    return max(kinds, key=_STRENGTH.__getitem__, default=TrackLyricsKind.NONE)


@dataclass(frozen=True)
class EmbeddedLyrics:
    """The worded lyrics a track's frames hold, by kind.

    *synced* comes from the best millisecond SYLT with a worded entry; *untimed* from
    the best worded USLT, else the best worded SYLT timed in MPEG frames. Either is
    None when no frame holds it. *candidates* is every worded frame's lyrics, as
    `lyrics_from_frames` converts each alone, synced before untimed and best-ranked
    first within each: the lyrics a sidecar may hold, whatever the run's ``--lang``.
    """

    synced: SyncedLyrics | None
    untimed: PlainLyrics | None
    candidates: tuple[Lyrics, ...] = ()

    @property
    def best(self) -> Lyrics | None:
        """The synced lyrics when present, else the untimed, else None."""
        return self.synced if self.synced is not None else self.untimed

    @property
    def kind(self) -> TrackLyricsKind:
        """The strongest kind the frames hold."""
        return lyrics_kind(self.synced, self.untimed)

    def held_by(self, sidecar: Lyrics | None) -> Lyrics | None:
        """The frames' lyrics *sidecar* holds (`sidecar_matches`), else None.

        Every frame of the sidecar's kind is compared, in any language and with any
        description, not only the one ``--lang`` ranks first: a sidecar a run wrote
        under one ``--lang`` still holds those frames' lyrics under another.
        """
        pool = (self.synced, self.untimed, *self.candidates)
        return next((held for held in pool if sidecar_matches(sidecar, held)), None)

    def serving(self, sidecar: Lyrics | None) -> Lyrics | None:
        """*sidecar*'s lyrics as they serve beside these frames: None when the frames'.

        A sidecar holding the lyrics of any frame of its kind (`held_by`) is set
        aside, so the frames serve the track and keep their place; any other sidecar
        serves as it is.
        """
        return None if self.held_by(sidecar) is not None else sidecar

    def synced_besides(self, timed: SyncedLyrics) -> bool:
        """True when synced frames hold other lines than the labeled .lrc *timed*.

        Frames holding *timed* itself (`held_by`) are a run's own embed of it. Frames
        holding its lines at other times are an earlier embed of the same file, whose
        stamps were since corrected by hand or re-timed. Neither takes the track from it;
        only frames with another line count, or any line's text different, do.
        """
        return (
            self.synced is not None
            and self.held_by(timed) is None
            and not same_lines(self.synced, timed)
        )

    def favoring(self, sidecar: Lyrics | None) -> EmbeddedLyrics:
        """These frames, with the ones *sidecar* holds ranked first of their kind.

        So a track whose sidecar holds a frame ``--lang`` does not rank first is
        served from that frame, which the sidecar already holds; with no such frame,
        the ranking stays.
        """
        held = self.held_by(sidecar)
        if isinstance(held, SyncedLyrics):
            return replace(self, synced=held)
        if isinstance(held, PlainLyrics):
            return replace(self, untimed=held)
        return self

    def without_placeholders(self) -> EmbeddedLyrics:
        """These frames with every placeholder set aside (`is_placeholder_lyrics`).

        A placeholder frame holds no lyrics, so it neither serves nor counts as one a
        sidecar holds: the best worded frame of its kind left serves instead, or none.
        """
        kept = tuple(c for c in self.candidates if not is_placeholder_lyrics(c))
        synced, untimed = self.synced, self.untimed
        if synced is not None and is_placeholder_lyrics(synced):
            synced = next((c for c in kept if isinstance(c, SyncedLyrics)), None)
        if untimed is not None and is_placeholder_lyrics(untimed):
            untimed = next((c for c in kept if isinstance(c, PlainLyrics)), None)
        return EmbeddedLyrics(synced, untimed, kept)


def same_lines(frames: SyncedLyrics, sidecar: SyncedLyrics) -> bool:
    """True when *frames* and *sidecar* hold the same lines in order, whatever their times.

    Each is read back as a ``.lrc`` holds it (`lrc_read_back`): every line's text
    stripped, in time order.
    """
    return _line_texts(frames) == _line_texts(sidecar)


def _line_texts(lyrics: SyncedLyrics) -> tuple[str, ...]:
    """*lyrics*' line texts as a ``.lrc`` reads them back."""
    return tuple(line.text for line in lrc_read_back(lyrics).lines)


def sidecar_matches(sidecar: Lyrics | None, frames: Lyrics | None) -> bool:
    """True when *sidecar* holds *frames*' lyrics as their own sidecar would hold them.

    Only lyrics of one kind match. A timed ``.lrc`` matches synced frames when its
    lines are theirs as a ``.lrc`` reads them back: each line's text stripped, in
    time order (`lrc_read_back`). A ``.txt`` matches untimed frames when its text is
    theirs after the ``.txt`` round trip (`txt_read_back`). So a sidecar written
    from the frames always matches them, and one edited to other lyrics never does.
    """
    if isinstance(sidecar, SyncedLyrics) and isinstance(frames, SyncedLyrics):
        return sidecar == lrc_read_back(frames)
    if isinstance(sidecar, PlainLyrics) and isinstance(frames, PlainLyrics):
        return sidecar.text == txt_read_back(frames.text)
    return False


#: Frames holding no worded lyrics: what a fresh run passes, since it serves no track
#: from what the track already holds.
NO_EMBEDDED_LYRICS = EmbeddedLyrics(None, None)


def _is_worded(frame: FrameRecord) -> bool:
    """True when *frame* holds a word: a USLT's text, or any SYLT entry's."""
    if isinstance(frame.payload, str):
        return bool(frame.payload.strip())
    return any(entry.text.strip() for entry in frame.payload)


def _is_millisecond_sylt(frame: FrameRecord) -> bool:
    """True when *frame* is a SYLT timed in milliseconds."""
    return isinstance(frame.payload, tuple) and frame.format == SYLT_MILLISECONDS


def _ranked(frames: Sequence[FrameRecord], lang: str) -> list[Lyrics]:
    """Each of *frames* as `lyrics_from_frames` converts it alone, best-ranked first.

    A USLT ranks before a SYLT, as `lyrics_from_frames` prefers a USLT's untimed words
    to an MPEG-frame SYLT's; then `frame_rank`, the first stored winning a tie.
    """
    ordered = sorted(
        frames, key=lambda f: (f.kind is FrameKind.SYLT, *frame_rank(f, lang))
    )
    converted = (lyrics_from_frames([frame], lang) for frame in ordered)
    return [lyrics for lyrics in converted if lyrics is not None]


def embedded_lyrics(frames: Sequence[FrameRecord], lang: str) -> EmbeddedLyrics:
    """The worded synced and untimed lyrics in *frames*, ranked by `lyrics_from_frames`.

    Frames with no word are set aside first, so a blank frame never wins over a worded
    one, in *lang* or not; what remains is ranked as `lyrics_from_frames` ranks it, the
    millisecond SYLTs as synced lyrics and the rest as untimed, and the best of each
    kind serves. Every one is kept as a candidate.
    """
    worded = [frame for frame in frames if _is_worded(frame)]
    timed = _ranked([f for f in worded if _is_millisecond_sylt(f)], lang)
    rest = _ranked([f for f in worded if not _is_millisecond_sylt(f)], lang)
    # The split decides each conversion's kind: millisecond SYLTs give synced lyrics,
    # and the rest plain, so the isinstance checks only narrow the type.
    synced = [lyrics for lyrics in timed if isinstance(lyrics, SyncedLyrics)]
    untimed = [lyrics for lyrics in rest if isinstance(lyrics, PlainLyrics)]
    best_synced = synced[0] if synced else None
    best_untimed = untimed[0] if untimed else None
    return EmbeddedLyrics(best_synced, best_untimed, (*synced, *untimed))


def classify_track(mp3: Path, *, album: Path, lang: str) -> TrackLyricsKind:
    """The strongest lyrics *mp3* holds, in its frames or its sidecars; writes nothing.

    *album* bounds the sidecar read as the sidecar stage bounds it; *lang* ranks the
    frames. Raises what `plan_sidecars` raises on a sidecar it cannot read, and what
    `read_tags` raises on a file it cannot read.
    """
    sidecar = plan_sidecars(mp3, album).lyrics
    embedded = embedded_lyrics(read_tags(mp3).frames, lang)
    return lyrics_kind(sidecar, embedded.synced, embedded.untimed)
