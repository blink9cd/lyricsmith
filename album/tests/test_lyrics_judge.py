"""Which of a track's lyrics frames the check judges: the one that served it.

`judged_lyrics` takes the frame equal to the served lyrics, and falls back to the
``--lang`` frame when none is; `frames_served` names the frame a run serves a track
from, so a re-check of a finished run judges the same one. Every lyric is invented.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_judge as lj  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above

_FRA = tb.sylt((1000, "Une lampe au palier"), (4500, "et tout se tait"), lang="fra")
_DEU = tb.sylt((4500, "und die Treppe schweigt"), (1000, "Ein Licht"), lang="deu")
_FRA_WORDS = tb.uslt("Une lampe au palier\net tout se tait", lang="fra")
_DEU_WORDS = tb.uslt("Ein Licht\nund die Treppe schweigt", lang="deu")
_FRAMES = (_FRA, _DEU)


def _one(frame: lm.FrameRecord) -> lm.Lyrics:
    """*frame*'s lyrics, converted alone."""
    lyrics = lm.lyrics_from_frames([frame], frame.lang)
    assert lyrics is not None
    return lyrics


class JudgedLyricsTest(fixtures.IsolatedCase):
    """The lyrics judged among a track's frames."""

    def test_the_frame_that_served_is_judged_over_the_lang_frame(self) -> None:
        self.assertEqual(lj.judged_lyrics(_FRAMES, "deu", _one(_FRA)), _one(_FRA))

    def test_with_no_frame_equal_to_the_served_lyrics_the_lang_frame_is(self) -> None:
        gone = lm.PlainLyrics("A line no frame holds")
        expected = lm.lyrics_from_frames(_FRAMES, "deu")
        self.assertEqual(lj.judged_lyrics(_FRAMES, "deu", gone), expected)
        self.assertEqual(lj.judged_lyrics(_FRAMES, "deu", gone), _one(_DEU))

    def test_with_nothing_served_the_lang_frame_is_judged(self) -> None:
        self.assertEqual(lj.judged_lyrics(_FRAMES, "fra", None), _one(_FRA))

    def test_a_worded_frame_that_served_is_judged_over_lang_stamps(self) -> None:
        stamps = tb.sylt((1000, ""), (4500, " "), lang="deu")
        frames = (stamps, _FRA)
        self.assertEqual(lj.judged_lyrics(frames, "deu", _one(_FRA)), _one(_FRA))

    def test_with_no_frames_nothing_is_judged(self) -> None:
        self.assertIsNone(lj.judged_lyrics((), "fra", _one(_FRA)))


class FramesServedTest(fixtures.IsolatedCase):
    """The frame a run serves a track from, given its sidecar."""

    def test_a_sidecar_of_one_frame_names_that_frame(self) -> None:
        lines = (
            lm.SyncedLine(1000, "Une lampe au palier"),
            lm.SyncedLine(4500, "et tout se tait"),
        )
        lrc = lm.SyncedLyrics(lines)
        self.assertEqual(lj.frames_served(_FRAMES, "deu", lrc), _one(_FRA))

    def test_with_no_sidecar_the_lang_frame_serves(self) -> None:
        self.assertEqual(lj.frames_served(_FRAMES, "deu", None), _one(_DEU))

    def test_a_sidecar_of_no_frame_leaves_the_lang_frame(self) -> None:
        other = lm.PlainLyrics("A line no frame holds")
        self.assertEqual(lj.frames_served(_FRAMES, "fra", other), _one(_FRA))

    def test_synced_frames_serve_before_a_txt_of_untimed_ones(self) -> None:
        frames = (_DEU, _FRA_WORDS, _DEU_WORDS)
        txt = lm.PlainLyrics("Une lampe au palier\net tout se tait")
        self.assertEqual(lj.frames_served(frames, "fra", txt), _one(_DEU))

    def test_a_txt_of_an_untimed_frame_names_it_when_none_is_synced(self) -> None:
        frames = (_DEU_WORDS, _FRA_WORDS)
        txt = lm.PlainLyrics("Une lampe au palier\net tout se tait")
        self.assertEqual(lj.frames_served(frames, "deu", txt), _one(_FRA_WORDS))

    def test_with_no_worded_frame_nothing_serves(self) -> None:
        self.assertIsNone(lj.frames_served((), "fra", None))


_WORDS = lm.PlainLyrics("A gull above the harbor\nturns once and is gone")


class BackupSidecarTest(fixtures.IsolatedCase):
    """The backed-up sidecar, compared with when the backed-up frames hold no word."""

    def check(
        self,
        held: tuple[lm.FrameRecord, ...],
        backed_up: tuple[lm.FrameRecord, ...],
        sidecar: lm.Lyrics | Exception,
    ) -> lr.CheckResult:
        """`check_written` of a track holding *held*, its backup *backed_up* and *sidecar*."""

        def load_before_sidecar(run_dir: Path, before: lr.TrackBefore) -> lm.Lyrics:
            if isinstance(sidecar, Exception):
                raise sidecar
            return sidecar

        stages = dataclasses.replace(
            album.real_wiring().stages,
            read_tags=lambda path: lm.TrackTags("A", "T", 180.0, (2, 4), held),
            load_before=lambda run_dir, before: backed_up,
            load_before_sidecar=load_before_sidecar,
        )
        before = lr.TrackBefore((), ())
        return lj.check_written(stages, self.tmp / "t.mp3", self.tmp, before, "eng")

    def test_worded_frames_are_compared_and_the_sidecar_is_not_read(self) -> None:
        held = (tb.uslt(_WORDS.text),)
        result = self.check(held, held, OSError("never read"))
        self.assertEqual(result.verdict, lr.CheckVerdict.PASS)

    def test_stamps_alone_fall_back_to_the_sidecar(self) -> None:
        held = (tb.uslt(_WORDS.text),)
        stamps = (tb.sylt((1000, ""), (4500, " ")),)
        other = lm.PlainLyrics("Under the orchard wall\nwe named the clouds")
        result = self.check(held, stamps, other)
        self.assertEqual([f.rule for f in result.flags], [lr.CheckRule.UNLIKE_BACKUP])

    def test_an_unreadable_sidecar_is_a_skip(self) -> None:
        error = lm.SidecarDecodeError("t.txt is not UTF-8")
        result = self.check((tb.uslt(_WORDS.text),), (), error)
        self.assertEqual(
            result.summary(), "SKIP: unlike backup (backed-up t.txt is not UTF-8)"
        )

    def test_no_lyrics_held_is_the_one_flag_beside_an_unreadable_sidecar(self) -> None:
        result = self.check((), (), lm.SidecarDecodeError("t.txt is not UTF-8"))
        self.assertEqual([f.rule for f in result.flags], [lr.CheckRule.NO_LYRICS])
        self.assertEqual(result.skips, ())
