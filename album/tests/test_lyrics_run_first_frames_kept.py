"""A track served from its own embedded lyrics keeps its MP3 exactly as it was.

The frames that serve a track are not written back: the run writes only the sidecar the
track lacked, from them. So an ID3v2.2 tag, which the embed cannot save, is no error;
an MPEG-frame SYLT keeps its timing; and the frames' language and any other lyrics
frames beside the serving one stay. Undo removes the sidecar and leaves the MP3 alone.
Each test drives the entry point with its real wiring; every lyric is invented.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_embedded import (  # noqa: E402 - import follows the sys.path bootstrap above
    KEEPER,
    MILL,
    EmbeddedCase,
)

#: Invented timed lines, and the millisecond SYLT holding them.
_TIMED = ((1000, "A kettle ticks on the stove"), (4200, "and the window fogs"))
_SYLT = tag_builders.sylt(*_TIMED)
#: Invented words an ID3v2.2 track holds in its ULT frame.
_V22_TEXT = "A kettle ticks on the stove\nand the window fogs\n"


class FirstRunFramesKeptCase(EmbeddedCase):
    """A first run its frames serve: the keeper's MP3 bytes, compared after it."""

    def run_and_keep(self, path: Path, *extra: str) -> lr.TrackOutcome:
        """Run with *extra*; assert *path*'s bytes did not change and its frames unwritten."""
        before = path.read_bytes()
        keeper = self.run_keeper(*extra)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(
            (keeper.source, keeper.frames_written), (lm.Source.FRAMES, False)
        )
        return keeper

    def no_answer(self) -> None:
        """No step finds anything for any track."""
        self.answer_steps(synced={}, plain={})


class V22FramesTest(FirstRunFramesKeptCase):
    """An ID3v2.2 track holding lyrics is served from them, its tag never rewritten."""

    def v22_keeper(self) -> Path:
        """An ID3v2.2 keeper holding untimed lyrics, then the mill with nothing."""
        fixtures.build_album(self.album, (MILL,))
        return tag_builders.build_v22_mp3(self.album / KEEPER, lyrics=_V22_TEXT)

    def assert_served(self, *extra: str) -> None:
        """Run with *extra*: exit 0, the tag untouched, a .txt written from the frames."""
        path = self.v22_keeper()
        keeper = self.run_and_keep(path, *extra)
        self.assertIsNone(keeper.error)
        self.assertEqual(keeper.sidecar_written, "01 Keeper.txt")
        self.assertEqual(rb.sidecars(path), {"01 Keeper.txt": _V22_TEXT.encode()})

    def test_an_offline_run_serves_it_and_writes_only_the_sidecar(self) -> None:
        self.assert_served("--offline")

    def test_a_run_finding_nothing_serves_it_from_its_frames(self) -> None:
        self.no_answer()
        self.assert_served()


class FramesUntouchedTest(FirstRunFramesKeptCase):
    """The serving frame, its language and the frames beside it stay as they were."""

    def test_an_mpeg_frame_sylt_stays_a_sylt_and_a_txt_is_written(self) -> None:
        self.no_answer()
        mpeg = tag_builders.sylt(*_TIMED, time_format=1)
        paths = self.keeper(mpeg)
        keeper = self.run_and_keep(paths[0])
        self.assertEqual(keeper.kind, lm.LyricsKind.UNSYNCED)
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter([mpeg]))
        served = lm.lyrics_from_frames([mpeg], run_fakes.LANG)
        assert isinstance(served, lm.PlainLyrics)
        self.assertEqual(rb.sidecars(paths[0]), {"01 Keeper.txt": served.text.encode()})

    def test_the_language_and_the_other_lyrics_frames_stay(self) -> None:
        frames = (
            tag_builders.sylt(*_TIMED, lang="fra"),
            tag_builders.uslt("Other invented words\n", lang="deu"),
            tag_builders.uslt("Still other words\n", desc="alt"),
        )
        paths = self.keeper(*frames)
        self.run_and_keep(paths[0])
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter(frames))

    def test_an_offline_run_writes_only_the_missing_sidecar(self) -> None:
        paths = self.keeper(_SYLT)
        keeper = self.run_and_keep(paths[0], "--offline")
        self.assertEqual(keeper.sidecar_written, "01 Keeper.lrc")
        self.assertEqual(list(rb.sidecars(paths[0])), ["01 Keeper.lrc"])

    def test_the_run_still_checks_the_frames_it_kept(self) -> None:
        paths = self.keeper(_SYLT)
        keeper = self.run_and_keep(paths[0])
        assert keeper.check is not None
        self.assertEqual(keeper.check.flags, ())
        tags = lyrics_tags.read_tags(paths[0])
        self.assertIsNotNone(lm.lyrics_from_frames(tags.frames, run_fakes.LANG))


class FramesKeptUndoTest(FirstRunFramesKeptCase):
    """Undo of a frames-served track removes its new sidecar; the MP3 never changes."""

    def test_undo_removes_the_sidecar_and_leaves_the_mp3(self) -> None:
        paths = self.keeper(_SYLT)
        before = paths[0].read_bytes()
        self.run_and_keep(paths[0])
        self.assertIn("01 Keeper.lrc", rb.sidecars(paths[0]))
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertEqual(rb.sidecars(paths[0]), {})
        self.assertEqual(paths[0].read_bytes(), before)
