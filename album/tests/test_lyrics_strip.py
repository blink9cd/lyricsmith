"""The strip stage: a track's lyrics frames and both its sidecars removed, or nothing.

`strip_track` checks both sidecar names before it changes anything, so a folder, a pipe
or a device under either name refuses the track whole, frames included; those refusals
are tested with the other slot rules, in `test_lyrics_slots`. A link under a sidecar
name is removed itself, never the file it points at. A track with nothing to remove is
never written. Every case is checked against the files on disk afterward. Every lyric
here is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages as ls  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_strip  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

# Bound to its protocol, so the type checker holds it to the orchestrator's call.
_STRIP: ls.StripTrack = lyrics_strip.strip_track

_LRC = "[00:01.00]A harbor bell rings twice\n[00:04.00]and the gulls go quiet\n"
_TXT = "A harbor bell rings twice\nand the gulls go quiet\n"
_FRAMES = (
    tag_builders.uslt("A verse kept in the tag\n"),
    tag_builders.sylt((1000, "A timed line"), (4000, "and another")),
)


class StripCase(fixtures.IsolatedCase):
    """An album folder, and one track built in it on demand."""

    def setUp(self) -> None:
        """An empty album folder for this test."""
        super().setUp()
        self.album = self.tmp / "album"

    def track(
        self,
        frames: tuple[lm.FrameRecord, ...] = (),
        *,
        lrc: str | None = None,
        txt: str | None = None,
    ) -> Path:
        """``01.mp3`` holding *frames*, with the given sidecars beside it."""
        (mp3,) = fixtures.build_album(
            self.album, (TrackSpec("01.mp3", lrc=lrc, txt=txt),)
        )
        if frames:
            tag_builders.plant_frames(mp3, frames)
        return mp3


class StripTrackTest(StripCase):
    """Frames and sidecars go; a track with nothing to remove is left unwritten."""

    def test_frames_and_both_sidecars_are_removed(self) -> None:
        mp3 = self.track(_FRAMES, lrc=_LRC, txt=_TXT)
        result = lyrics_strip.strip_track(mp3)
        self.assertEqual(result, lr.StripResult(True, ("01.lrc", "01.txt")))
        tags = lyrics_tags.read_tags(mp3)
        self.assertEqual(tags.frames, ())
        self.assertEqual((tags.id3_version, tags.title), ((2, 4), "Invented Title"))
        self.assertEqual(rb.sidecars(mp3), {})
        self.assertFalse(mp3.with_suffix(".lrc").exists())
        self.assertFalse(mp3.with_suffix(".txt").exists())

    def test_nothing_to_remove_writes_nothing(self) -> None:
        mp3 = self.track()
        before, mtime = mp3.read_bytes(), mp3.stat().st_mtime_ns
        result = lyrics_strip.strip_track(mp3)
        self.assertEqual(result, lr.StripResult(False, ()))
        self.assertEqual(mp3.read_bytes(), before)
        self.assertEqual(mp3.stat().st_mtime_ns, mtime)
        self.assertEqual(sorted(p.name for p in self.album.iterdir()), ["01.mp3"])

    def test_frames_alone_are_removed(self) -> None:
        mp3 = self.track(_FRAMES)
        self.assertEqual(lyrics_strip.strip_track(mp3), lr.StripResult(True, ()))
        self.assertEqual(lyrics_tags.read_tags(mp3).frames, ())

    def test_a_sidecar_alone_is_removed_and_the_mp3_is_not_written(self) -> None:
        mp3 = self.track(txt=_TXT)
        before, mtime = mp3.read_bytes(), mp3.stat().st_mtime_ns
        result = lyrics_strip.strip_track(mp3)
        self.assertEqual(result, lr.StripResult(False, ("01.txt",)))
        self.assertEqual(rb.sidecars(mp3), {})
        self.assertEqual(mp3.read_bytes(), before)
        self.assertEqual(mp3.stat().st_mtime_ns, mtime)

    def test_a_link_is_removed_and_its_target_survives(self) -> None:
        mp3 = self.track(_FRAMES, txt=_TXT)
        elsewhere = self.tmp / "elsewhere.lrc"
        elsewhere.write_text(_LRC, encoding="utf-8", newline="")
        mp3.with_suffix(".lrc").symlink_to(elsewhere)
        result = lyrics_strip.strip_track(mp3)
        self.assertEqual(result, lr.StripResult(True, ("01.lrc", "01.txt")))
        self.assertFalse(mp3.with_suffix(".lrc").is_symlink())
        self.assertFalse(mp3.with_suffix(".lrc").exists())
        self.assertEqual(elsewhere.read_bytes(), _LRC.encode())

    def test_a_dangling_link_is_removed(self) -> None:
        mp3 = self.track()
        mp3.with_suffix(".txt").symlink_to(self.tmp / "gone.txt")
        result = lyrics_strip.strip_track(mp3)
        self.assertEqual(result, lr.StripResult(False, ("01.txt",)))
        self.assertFalse(mp3.with_suffix(".txt").is_symlink())

    def test_a_v22_tag_holding_lyrics_raises_and_nothing_changes(self) -> None:
        self.album.mkdir()
        mp3 = tag_builders.build_v22_mp3(self.album / "01.mp3", lyrics="A v2.2 verse")
        mp3.with_suffix(".txt").write_text(_TXT, encoding="utf-8", newline="")
        before = mp3.read_bytes()
        with self.assertRaises(lm.TagVersionError):
            lyrics_strip.strip_track(mp3)
        self.assertEqual(mp3.read_bytes(), before)
        self.assertEqual(rb.sidecars(mp3), {"01.txt": _TXT.encode()})


if __name__ == "__main__":
    unittest.main()
