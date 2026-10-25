"""lyrics_from_frames: which lyrics a track's SYLT / USLT frames hold, and in what order.

The check judges lyrics read back from a file and lyrics restored from a run's backup,
and both arrive as frames. These tests fix the one conversion both sides go through:
SYLT before USLT, the run's language and an empty description before any other frame
of a kind, an MPEG-frame SYLT never read as milliseconds, and SYLT entries kept in the
order they were stored, because the check's "out of order" row looks for exactly the
disorder a time sort would hide. Every lyric here is invented (see fixtures.py).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above

#: The ID3 SYLT time-stamp formats.
_MPEG_FRAMES = 1
_MILLISECONDS = 2
#: The ID3 SYLT content type for lyrics.
_LYRICS_TYPE = 1
#: UTF-8, the encoding byte this tool's frames carry.
_UTF8 = 3


def _sylt(
    *entries: tuple[int, str],
    lang: str = "eng",
    desc: str = "",
    time_format: int = _MILLISECONDS,
) -> lm.FrameRecord:
    """A SYLT frame holding *entries*, given as ``(time, text)`` in stored order."""
    payload = tuple(lm.SyltEntry(text, time) for time, text in entries)
    return lm.FrameRecord(
        lm.FrameKind.SYLT, lang, desc, _UTF8, time_format, _LYRICS_TYPE, payload
    )


def _uslt(text: str, *, lang: str = "eng", desc: str = "") -> lm.FrameRecord:
    """A USLT frame holding *text*."""
    return lm.FrameRecord(lm.FrameKind.USLT, lang, desc, _UTF8, None, None, text)


class StoredOrderTest(fixtures.IsolatedCase):
    """SYLT entries come back in stored order, never sorted by time."""

    def test_out_of_order_entries_stay_out_of_order(self) -> None:
        frame = _sylt((5000, "the gulls come in"), (1000, "the tide goes out"))
        self.assertEqual(
            lm.lyrics_from_frames((frame,), "eng"),
            fixtures.synced_lines(
                (5000, "the gulls come in"), (1000, "the tide goes out")
            ),
        )

    def test_entry_text_is_kept_verbatim(self) -> None:
        frame = _sylt((0, "\nfirst light"), (900, " second bell "))
        self.assertEqual(
            lm.lyrics_from_frames((frame,), "eng"),
            fixtures.synced_lines((0, "\nfirst light"), (900, " second bell ")),
        )


class FrameChoiceTest(fixtures.IsolatedCase):
    """Which frame wins when a track holds more than one."""

    def test_no_frames_mean_no_lyrics(self) -> None:
        self.assertIsNone(lm.lyrics_from_frames((), "eng"))

    def test_a_millisecond_sylt_wins_over_a_uslt_in_either_order(self) -> None:
        sylt = _sylt((1000, "rope and lantern"))
        uslt = _uslt("rope and lantern")
        for frames in ((sylt, uslt), (uslt, sylt)):
            with self.subTest(first=frames[0].kind):
                self.assertEqual(
                    lm.lyrics_from_frames(frames, "eng"),
                    fixtures.synced_lines((1000, "rope and lantern")),
                )

    def test_a_uslt_alone_is_plain_and_verbatim(self) -> None:
        self.assertEqual(
            lm.lyrics_from_frames((_uslt("one\r\ntwo\n"),), "eng"),
            lm.PlainLyrics("one\r\ntwo\n"),
        )

    def test_the_run_language_wins_then_an_empty_description(self) -> None:
        frames = (
            _uslt("other tongue", lang="deu"),
            _uslt("described", desc="annotated"),
            _uslt("plain match"),
        )
        self.assertEqual(
            lm.lyrics_from_frames(frames, "eng"), lm.PlainLyrics("plain match")
        )

    def test_language_outranks_description(self) -> None:
        frames = (_uslt("empty desc", lang="fra"), _uslt("lang match", desc="d"))
        self.assertEqual(
            lm.lyrics_from_frames(frames, "eng"), lm.PlainLyrics("lang match")
        )

    def test_equal_rank_keeps_the_first_stored_frame(self) -> None:
        frames = (
            _sylt((1, "first"), lang="deu"),
            _sylt((2, "second"), lang="fra"),
        )
        self.assertEqual(
            lm.lyrics_from_frames(frames, "eng"), fixtures.synced_lines((1, "first"))
        )

    def test_the_same_ranking_picks_among_sylt_frames(self) -> None:
        frames = (_sylt((1, "foreign"), lang="deu"), _sylt((2, "home"), desc=""))
        self.assertEqual(
            lm.lyrics_from_frames(frames, "eng"), fixtures.synced_lines((2, "home"))
        )


class UnusableFrameTest(fixtures.IsolatedCase):
    """Frames that hold no lyrics, or no millisecond timing, step aside."""

    def test_an_empty_sylt_is_passed_over(self) -> None:
        frames = (_sylt(), _uslt("fallback words"))
        self.assertEqual(
            lm.lyrics_from_frames(frames, "eng"), lm.PlainLyrics("fallback words")
        )

    def test_a_blank_uslt_holds_no_lyrics(self) -> None:
        self.assertIsNone(lm.lyrics_from_frames((_uslt(" \r\n\t"),), "eng"))
        frames = (_uslt(""), _uslt("words", lang="deu"))
        self.assertEqual(lm.lyrics_from_frames(frames, "eng"), lm.PlainLyrics("words"))

    def test_an_mpeg_frame_sylt_is_never_read_as_milliseconds(self) -> None:
        mpeg = _sylt((40, "counted"), (10, "in frames"), time_format=_MPEG_FRAMES)
        self.assertEqual(
            lm.lyrics_from_frames((mpeg,), "eng"),
            lm.PlainLyrics("counted\nin frames"),
        )

    def test_a_uslt_wins_over_an_mpeg_frame_sylt(self) -> None:
        mpeg = _sylt((40, "counted"), time_format=_MPEG_FRAMES)
        self.assertEqual(
            lm.lyrics_from_frames((mpeg, _uslt("the words")), "eng"),
            lm.PlainLyrics("the words"),
        )

    def test_a_millisecond_sylt_wins_over_an_mpeg_frame_one(self) -> None:
        mpeg = _sylt((40, "counted"), time_format=_MPEG_FRAMES)
        timed = _sylt((1500, "timed"), lang="deu")
        self.assertEqual(
            lm.lyrics_from_frames((mpeg, timed), "eng"),
            fixtures.synced_lines((1500, "timed")),
        )


if __name__ == "__main__":
    unittest.main()
