"""A write that would change nothing is skipped: the frames' save and the sidecar's.

`write_lyrics` compares the lyrics frames its replace would leave with those the track
holds, each by everything but the encoding byte and in no order, and does not save the
MP3 when they are the same; it says whether it saved. `write_sidecar` leaves a sidecar
file whose bytes already match, and says it wrote nothing. A synced answer's ``.lrc``
holds each line's text stripped, so an entry opening with a line break, as many taggers
store one, keeps. Every track is a silent MP3 built at test time; every lyric is
invented.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_frames  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags as lt  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above

#: Invented synced lyrics, and the untimed ones of the same words.
_SYNCED = lm.SyncedLyrics(
    (lm.SyncedLine(1000, "A lamp swings"), lm.SyncedLine(4000, "and holds"))
)
_PLAIN = lm.PlainLyrics("A lamp swings\nand holds\n")
#: An mtime long before any test runs, in nanoseconds, so a save would show.
_OLD_NS = 1_000_000_000 * 1_000_000_000


def _age(path: Path) -> int:
    """Set *path*'s modification time back to `_OLD_NS`; return it."""
    os.utime(path, ns=(_OLD_NS, _OLD_NS))
    return path.stat().st_mtime_ns


class SkipCase(fixtures.IsolatedCase):
    """A tagged track holding the frames a write of an answer would leave."""

    def track(self, minor: int, frames: tuple[lm.FrameRecord, ...]) -> Path:
        """A track at ID3v2.*minor* holding *frames*, its mtime set back."""
        path = tb.build_rich_mp3(self.tmp / f"v2{minor}.mp3", minor)
        tb.plant_frames(path, frames)
        _age(path)
        return path

    def assert_skipped(self, path: Path, lyrics: lm.Lyrics) -> None:
        """Write *lyrics* to *path*: no save, its bytes and mtime as they were."""
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        with mock.patch.object(lt, "_save", wraps=lt._save) as seam:
            saved = lt.write_lyrics(path, lyrics, "eng")
        self.assertFalse(saved)
        self.assertEqual(seam.call_count, 0)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def assert_saved(self, path: Path, lyrics: lm.Lyrics) -> None:
        """Write *lyrics* to *path*: saved, and it now holds exactly their frames."""
        self.assertTrue(lt.write_lyrics(path, lyrics, "eng"))
        self.assertNotEqual(path.stat().st_mtime_ns, _OLD_NS)
        found = lt.read_tags(path).frames
        expected = lyrics_frames.new_frames(lyrics, "eng")
        self.assertTrue(lyrics_frames.same_frames(found, expected))


class IdenticalFramesTest(SkipCase):
    """Frames the write would leave as they are are not saved again."""

    def test_identical_frames_are_not_saved_at_either_version(self) -> None:
        for minor in (3, 4):
            for lyrics in (_SYNCED, _PLAIN):
                with self.subTest(minor=minor, kind=lyrics.kind):
                    frames = lyrics_frames.new_frames(lyrics, "eng")
                    self.assert_skipped(self.track(minor, frames), lyrics)

    def test_the_order_they_are_stored_in_does_not_count(self) -> None:
        # A save orders frames by size, so a stored order is never the write's to keep.
        sylt, uslt = lyrics_frames.new_frames(_SYNCED, "eng")
        self.assert_skipped(self.track(4, (uslt, sylt)), _SYNCED)

    def test_the_encoding_byte_does_not_count(self) -> None:
        # A v2.3 save re-encodes UTF-8 as UTF-16 anyway; the words are what is written.
        sylt, uslt = lyrics_frames.new_frames(_SYNCED, "eng")
        frames = (
            tb.sylt((1000, "A lamp swings"), (4000, "and holds"), encoding=tb.UTF16),
            tb.uslt(_SYNCED.plain_text(), encoding=tb.LATIN1),
        )
        self.assertEqual({f.encoding for f in (sylt, uslt)}, {tb.UTF8})
        self.assert_skipped(self.track(4, frames), _SYNCED)

    def test_a_track_written_once_is_not_saved_by_the_same_write(self) -> None:
        path = tb.build_rich_mp3(self.tmp / "track.mp3", 3)
        self.assertTrue(lt.write_lyrics(path, _SYNCED, "eng"))
        _age(path)
        self.assert_skipped(path, _SYNCED)


class ChangedFramesTest(SkipCase):
    """Any difference the write would make still saves, and reads back."""

    def test_each_field_that_differs_saves(self) -> None:
        sylt, uslt = lyrics_frames.new_frames(_SYNCED, "eng")
        variants = {
            "language": (sylt, tb.uslt(_SYNCED.plain_text(), lang="fra")),
            "description": (sylt, tb.uslt(_SYNCED.plain_text(), desc="alt")),
            "text": (sylt, tb.uslt("Other invented words\n")),
            "entries": (tb.sylt((1000, "A lamp swings"), (4100, "and holds")), uslt),
            "time format": (
                tb.sylt((1000, "A lamp swings"), (4000, "and holds"), time_format=1),
                uslt,
            ),
            "a frame missing": (sylt,),
            "a frame beside them": (sylt, uslt, tb.uslt("Extra words\n", desc="x")),
        }
        for name, frames in variants.items():
            with self.subTest(differs=name):
                self.assert_saved(self.track(4, frames), _SYNCED)

    def test_utf8_frames_in_a_v2_3_tag_are_saved_as_utf16(self) -> None:
        # UTF-8 is not a v2.3 encoding: the same words stored so by another tool are
        # saved, which re-encodes them as UTF-16, the encoding v2.3 allows.
        path = tb.build_v23_utf8_mp3(self.tmp / "track.mp3", _PLAIN.text)
        _age(path)
        self.assertEqual([f.encoding for f in lt.read_tags(path).frames], [tb.UTF8])
        self.assert_saved(path, _PLAIN)
        self.assertEqual([f.encoding for f in lt.read_tags(path).frames], [tb.UTF16])

    def test_a_stale_sylt_beside_an_untimed_answer_saves(self) -> None:
        frames = lyrics_frames.new_frames(_SYNCED, "eng")
        self.assert_saved(self.track(4, frames), _PLAIN)

    def test_a_track_with_no_lyrics_frames_saves(self) -> None:
        self.assert_saved(self.track(3, ()), _PLAIN)

    def test_a_file_with_no_id3_header_saves(self) -> None:
        path = fixtures.build_silent_mp3(
            self.tmp / "bare.mp3", tb.SECONDS, id3_version=None
        )
        self.assertTrue(lt.write_lyrics(path, _PLAIN, "eng"))
        self.assertEqual(lt.read_tags(path).id3_version, (2, 4))

    def test_an_id3v2_2_tag_is_still_refused(self) -> None:
        path = tb.build_v22_mp3(self.tmp / "v22.mp3", lyrics=_PLAIN.text)
        with self.assertRaises(lm.TagVersionError):
            lt.write_lyrics(path, _PLAIN, "eng")


class SidecarSkipTest(fixtures.IsolatedCase):
    """A sidecar file whose bytes already match is not written again."""

    def mp3(self) -> Path:
        """A one-track album's MP3, with no sidecar yet."""
        track = fixtures.TrackSpec("01 Track.mp3", seconds=1.0)
        return fixtures.build_album(self.tmp / "album", (track,))[0]

    def test_a_matching_sidecar_is_left_and_nothing_is_reported(self) -> None:
        mp3 = self.mp3()
        for lyrics, suffix in ((_SYNCED, ".lrc"), (_PLAIN, ".txt")):
            with self.subTest(suffix=suffix):
                first = lyrics_sidecar.write_sidecar(mp3, lyrics)
                self.assertIsNotNone(first)
                sidecar = mp3.with_suffix(suffix)
                before = (sidecar.read_bytes(), _age(sidecar))
                self.assertIsNone(lyrics_sidecar.write_sidecar(mp3, lyrics))
                after = (sidecar.read_bytes(), sidecar.stat().st_mtime_ns)
                self.assertEqual(after, before)

    def test_a_matching_sidecar_beside_the_other_kind_removes_only_that(
        self,
    ) -> None:
        mp3 = self.mp3()
        lrc = mp3.with_suffix(".lrc")
        lrc.write_bytes(lm.format_lrc(_SYNCED.lines).encode())
        _age(lrc)
        mp3.with_suffix(".txt").write_text(
            "Older words\n", encoding="utf-8", newline=""
        )
        written = lyrics_sidecar.write_sidecar(mp3, _SYNCED)
        assert written is not None
        self.assertEqual(written.removed, ("01 Track.txt",))
        self.assertEqual(lrc.stat().st_mtime_ns, _OLD_NS)
        self.assertFalse(mp3.with_suffix(".txt").exists())

    def test_different_bytes_are_written(self) -> None:
        mp3 = self.mp3()
        mp3.with_suffix(".txt").write_text(
            "Older words\n", encoding="utf-8", newline=""
        )
        written = lyrics_sidecar.write_sidecar(mp3, _PLAIN)
        assert written is not None
        self.assertEqual(written.written, "01 Track.txt")
        self.assertEqual(mp3.with_suffix(".txt").read_text("utf-8"), _PLAIN.text)

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symbolic links")
    def test_a_link_to_matching_bytes_is_still_replaced_by_a_file(self) -> None:
        mp3 = self.mp3()
        target = mp3.parent / "elsewhere.txt"
        target.write_text(_PLAIN.text, encoding="utf-8", newline="")
        mp3.with_suffix(".txt").symlink_to(target.name)
        self.assertIsNotNone(lyrics_sidecar.write_sidecar(mp3, _PLAIN))
        self.assertFalse(mp3.with_suffix(".txt").is_symlink())


class StrippedLrcTest(fixtures.IsolatedCase):
    """A synced answer's ``.lrc`` holds each line's text stripped of its padding."""

    @staticmethod
    def synced(*lines: tuple[int, str]) -> lm.SyncedLyrics:
        """Synced lyrics of *lines*, given as ``(ms, text)`` in stored order."""
        return lm.SyncedLyrics(tuple(lm.SyncedLine(ms, text) for ms, text in lines))

    def test_an_entry_opening_with_a_line_break_keeps(self) -> None:
        lyrics = self.synced((1000, "\nA lamp swings"), (4000, "\r\nand holds\n"))
        self.assertTrue(lyrics_sidecar.sidecar_keeps(lyrics))

    def test_an_interior_line_break_still_does_not_keep(self) -> None:
        for text in ("\nA lamp\nswings", "A lamp\r\nswings "):
            with self.subTest(text=text):
                lyrics = self.synced((1000, text))
                self.assertFalse(lyrics_sidecar.sidecar_keeps(lyrics))

    def test_the_lrc_holds_the_stripped_text_and_reads_back_as_it(self) -> None:
        mp3 = fixtures.build_album(
            self.tmp / "album", (fixtures.TrackSpec("01 Track.mp3", seconds=1.0),)
        )[0]
        lyrics = self.synced((1000, "\nA lamp swings"), (4000, "\nand holds "))
        lyrics_sidecar.write_sidecar(mp3, lyrics)
        text = mp3.with_suffix(".lrc").read_text("utf-8")
        self.assertEqual(text, "[00:01.000]A lamp swings\n[00:04.000]and holds\n")
        self.assertEqual(lm.parse_lrc(text), _SYNCED)


if __name__ == "__main__":
    unittest.main()
