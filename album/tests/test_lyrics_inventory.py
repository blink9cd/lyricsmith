"""The track classifier: synced, untimed or no lyrics, from the frames and the sidecars.

One rule for "synced" runs through every test here: a millisecond SYLT with a worded
entry, or a timed ``.lrc`` with a worded line. A SYLT timed in MPEG frames is untimed,
and lyrics holding only whitespace are none. Classifying writes nothing, so every file
test also checks the album is byte-for-byte as it was. Every lyric here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory as li  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above

_SYNCED = li.TrackLyricsKind.SYNCED
_UNTIMED = li.TrackLyricsKind.UNTIMED
_NONE = li.TrackLyricsKind.NONE

#: An invented worded line, and a second one.
_LINE = "Chalk birds fly off the pavement"
_LINE_TWO = "when the street sweeper sings"
#: An invented untimed lyric, as a USLT or a ``.txt`` holds it.
_PLAIN = "Our kettle hums in a minor key\nand the cat keeps time\n"
#: An invented timed ``.lrc``.
_LRC = f"[00:01.00]{_LINE}\n[00:04.50]{_LINE_TWO}\n"
#: A ``.lrc`` with no stamp: never synced, so the sidecar stage reads it as untimed.
_LRC_UNTIMED = f"{_LINE}\n{_LINE_TWO}\n"
#: A timed ``.lrc`` whose every line is blank.
_STAMPS_ONLY = "[00:01.00]\n[00:05.00]  \n"
#: Bytes that are not UTF-8: a Latin-1 e-acute.
_NOT_UTF8 = b"caf\xe9 by the station\n"


def _ms_sylt(*texts: str, lang: str = "eng") -> lm.FrameRecord:
    """A millisecond SYLT holding *texts*, one second apart."""
    entries = tuple((1000 * (i + 1), text) for i, text in enumerate(texts))
    return tb.sylt(*entries, lang=lang)


def _mpeg_sylt(*texts: str) -> lm.FrameRecord:
    """A SYLT timed in MPEG frames holding *texts*."""
    entries = tuple((40 * (i + 1), text) for i, text in enumerate(texts))
    return tb.sylt(*entries, time_format=tb.MPEG_FRAMES)


class LyricsKindTest(fixtures.IsolatedCase):
    """The shared rule, on lyrics values alone."""

    def test_worded_synced_lyrics_are_synced(self) -> None:
        lines = (lm.SyncedLine(0, " "), lm.SyncedLine(900, _LINE))
        self.assertIs(li.lyrics_kind(lm.SyncedLyrics(lines)), _SYNCED)

    def test_synced_lyrics_of_stamps_alone_are_none(self) -> None:
        lines = (lm.SyncedLine(0, ""), lm.SyncedLine(900, "  \t"))
        self.assertIs(li.lyrics_kind(lm.SyncedLyrics(lines)), _NONE)

    def test_worded_plain_lyrics_are_untimed(self) -> None:
        self.assertIs(li.lyrics_kind(lm.PlainLyrics(_PLAIN)), _UNTIMED)

    def test_blank_plain_lyrics_and_no_lyrics_are_none(self) -> None:
        self.assertIs(li.lyrics_kind(lm.PlainLyrics(" \n\t")), _NONE)
        self.assertIs(li.lyrics_kind(None), _NONE)

    def test_several_values_take_the_strongest_kind(self) -> None:
        synced = lm.SyncedLyrics((lm.SyncedLine(0, _LINE),))
        plain = lm.PlainLyrics(_PLAIN)
        self.assertIs(li.lyrics_kind(plain, synced, None), _SYNCED)
        self.assertIs(li.lyrics_kind(None, plain), _UNTIMED)
        self.assertIs(li.lyrics_kind(), _NONE)


class EmbeddedLyricsTest(fixtures.IsolatedCase):
    """The embedded side: the synced and the untimed lyrics a track's frames hold."""

    def test_a_worded_millisecond_sylt_is_synced(self) -> None:
        held = li.embedded_lyrics((_ms_sylt(_LINE, _LINE_TWO),), "eng")
        expected = lm.SyncedLyrics(
            (lm.SyncedLine(1000, _LINE), lm.SyncedLine(2000, _LINE_TWO))
        )
        self.assertEqual(held.synced, expected)
        self.assertIsNone(held.untimed)
        self.assertEqual(held.best, expected)
        self.assertIs(held.kind, _SYNCED)

    def test_a_worded_mpeg_frame_sylt_is_untimed(self) -> None:
        held = li.embedded_lyrics((_mpeg_sylt(_LINE, _LINE_TWO),), "eng")
        self.assertIsNone(held.synced)
        self.assertEqual(held.untimed, lm.PlainLyrics(f"{_LINE}\n{_LINE_TWO}"))
        self.assertIs(held.kind, _UNTIMED)

    def test_a_worded_uslt_is_untimed(self) -> None:
        held = li.embedded_lyrics((tb.uslt(_PLAIN),), "eng")
        self.assertEqual(held.untimed, lm.PlainLyrics(_PLAIN))
        self.assertEqual(held.best, lm.PlainLyrics(_PLAIN))
        self.assertIs(held.kind, _UNTIMED)

    def test_blank_frames_hold_none(self) -> None:
        frames = (_ms_sylt("", "  "), _mpeg_sylt(" "), tb.uslt("\n \t"))
        held = li.embedded_lyrics(frames, "eng")
        self.assertEqual(held, li.EmbeddedLyrics(None, None))
        self.assertIsNone(held.best)
        self.assertIs(held.kind, _NONE)

    def test_no_frame_holds_none(self) -> None:
        self.assertIs(li.embedded_lyrics((), "eng").kind, _NONE)

    def test_a_blank_millisecond_sylt_leaves_a_worded_uslt_untimed(self) -> None:
        # Read whole, the blank SYLT would win as synced lyrics with no words.
        held = li.embedded_lyrics((_ms_sylt(" "), tb.uslt(_PLAIN)), "eng")
        self.assertIsNone(held.synced)
        self.assertEqual(held.untimed, lm.PlainLyrics(_PLAIN))

    def test_a_worded_uslt_wins_the_untimed_slot_over_an_mpeg_frame_sylt(self) -> None:
        # Both are untimed; the USLT's text is the words as written, so it ranks first,
        # whichever frame is stored first.
        for frames in (
            (_mpeg_sylt(_LINE), tb.uslt(_PLAIN)),
            (tb.uslt(_PLAIN), _mpeg_sylt(_LINE)),
        ):
            with self.subTest(first=frames[0].kind):
                held = li.embedded_lyrics(frames, "eng")
                self.assertEqual(
                    (held.synced, held.untimed), (None, lm.PlainLyrics(_PLAIN))
                )

    def test_a_worded_sylt_in_another_language_beats_a_blank_one_in_lang(self) -> None:
        frames = (_ms_sylt(" "), _ms_sylt(_LINE, lang="deu"))
        held = li.embedded_lyrics(frames, "eng")
        self.assertEqual(held.synced, lm.SyncedLyrics((lm.SyncedLine(1000, _LINE),)))

    def test_synced_and_untimed_frames_both_come_back(self) -> None:
        frames = (tb.uslt(_PLAIN), _ms_sylt(_LINE))
        held = li.embedded_lyrics(frames, "eng")
        self.assertEqual(held.synced, lm.SyncedLyrics((lm.SyncedLine(1000, _LINE),)))
        self.assertEqual(held.untimed, lm.PlainLyrics(_PLAIN))
        self.assertEqual(held.best, held.synced)


class ClassifyTrackTest(fixtures.IsolatedCase):
    """`classify_track` on real files: frames, sidecars, both, and neither."""

    def _track(
        self,
        frames: tuple[lm.FrameRecord, ...] = (),
        *,
        lrc: str | bytes | None = None,
        txt: str | bytes | None = None,
        id3_version: tuple[int, int] | None = (2, 4),
    ) -> Path:
        """A silent MP3 in the test's album with *frames* and the given sidecars."""
        mp3 = fixtures.build_silent_mp3(
            self.tmp / "album" / "01 Track.mp3", 2.0, id3_version=id3_version
        )
        if frames:
            tb.plant_frames(mp3, frames)
        for suffix, content in ((".lrc", lrc), (".txt", txt)):
            if content is not None:
                data = content if isinstance(content, bytes) else content.encode()
                mp3.with_suffix(suffix).write_bytes(data)
        return mp3

    def _classify(self, mp3: Path) -> li.TrackLyricsKind:
        """Classify *mp3*, and fail unless its album is exactly as it was."""
        album = mp3.parent
        before = {p.name: p.read_bytes() for p in album.iterdir()}
        kind = li.classify_track(mp3, album=album, lang="eng")
        self.assertEqual({p.name: p.read_bytes() for p in album.iterdir()}, before)
        return kind

    def test_a_millisecond_sylt_is_synced(self) -> None:
        self.assertIs(self._classify(self._track((_ms_sylt(_LINE),))), _SYNCED)

    def test_an_mpeg_frame_sylt_is_untimed(self) -> None:
        self.assertIs(self._classify(self._track((_mpeg_sylt(_LINE),))), _UNTIMED)

    def test_a_uslt_is_untimed(self) -> None:
        self.assertIs(self._classify(self._track((tb.uslt(_PLAIN),))), _UNTIMED)

    def test_a_timed_lrc_is_synced(self) -> None:
        self.assertIs(self._classify(self._track(lrc=_LRC)), _SYNCED)

    def test_an_lrc_with_no_stamp_is_untimed_and_stays_put(self) -> None:
        self.assertIs(self._classify(self._track(lrc=_LRC_UNTIMED)), _UNTIMED)

    def test_a_txt_is_untimed(self) -> None:
        self.assertIs(self._classify(self._track(txt=_PLAIN)), _UNTIMED)

    def test_blank_lyrics_everywhere_are_none(self) -> None:
        mp3 = self._track((tb.uslt("  "), _ms_sylt("")), lrc=_STAMPS_ONLY, txt=" \n")
        self.assertIs(self._classify(mp3), _NONE)

    def test_nothing_is_none(self) -> None:
        self.assertIs(self._classify(self._track()), _NONE)

    def test_a_file_with_no_id3_header_is_none(self) -> None:
        self.assertIs(self._classify(self._track(id3_version=None)), _NONE)

    def test_a_synced_sidecar_beside_untimed_frames_is_synced(self) -> None:
        mp3 = self._track((tb.uslt(_PLAIN), _mpeg_sylt(_LINE)), lrc=_LRC)
        self.assertIs(self._classify(mp3), _SYNCED)

    def test_synced_frames_beside_a_txt_are_synced(self) -> None:
        self.assertIs(
            self._classify(self._track((_ms_sylt(_LINE),), txt=_PLAIN)), _SYNCED
        )

    def test_a_bad_sidecar_raises_as_the_sidecar_stage_does(self) -> None:
        mp3 = self._track((_ms_sylt(_LINE),), txt=_NOT_UTF8)
        with self.assertRaisesRegex(lm.SidecarDecodeError, "01 Track.txt"):
            li.classify_track(mp3, album=mp3.parent, lang="eng")
