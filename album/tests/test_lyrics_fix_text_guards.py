"""`--mode fix-text`'s guards: when a wrong text is kept, and what a removal leaves.

A fix removes a wrong text only when every provider the run meant to ask was asked and
none failed: one failing beside another's unsung answer, one cooling down from an
earlier refusal, or one stopped part-way through the run keeps the text, as the track's
error. A provider left out on purpose (``--no-tekstowo``, ``--only-providers``) is not
one the run meant to ask, so it blocks nothing. A removal keeps the track's transcript
record: the track then holds no untimed text, so a later fix leaves it alone, and once
undo brings the wrong text back a fix takes it again. Each test drives the entry point
with its real wiring; every title and lyric is invented.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    FOUNDRY,
    HARBOR,
    MEADOW,
    QUARRY,
    TITLES,
    FixCase,
    song_track,
    sung_neighbor,
    words,
)
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_tekstowo_wiring import StoppingStep  # noqa: E402 - import follows the sys.path bootstrap above

#: NetEase's untimed step, which a test makes fail beside LRCLIB's answer.
NETEASE_KEY = lm.ProviderKey(lm.Source.NETEASE, lm.LyricsKind.UNSYNCED)
TEKSTOWO_KEY = lm.ProviderKey(lm.Source.TEKSTOWO, lm.LyricsKind.UNSYNCED)


class _CoolingCase(FixCase):
    """`FixCase`, with a way to start Tekstowo cooling down."""

    def cool_down_tekstowo(self) -> None:
        """Write a Tekstowo refusal an hour before the clock, so it cools down."""
        state = lyc.cooldown_path(self.wiring().environ, lm.Source.TEKSTOWO)
        lyc.write_cooldown(state, self.started - timedelta(hours=1), "HTTP 429")


class KeptTest(_CoolingCase):
    """No sung text, yet a provider the run meant to ask did not answer: kept."""

    def assert_kept(self, paths: list[Path], name: str, *cells: str) -> None:
        """The fix ran and kept *name*'s wrong text, its row naming each of *cells*."""
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        row = cli_fakes.row_cells(result.stdout, name)[-1]
        for cell in (*cells, "nothing removed"):
            self.assertIn(cell, row)
        self.assertEqual(raw(paths), before)
        self.assertIs(self.outcome(name).status, lr.OutcomeStatus.ERROR)
        self.assertIsNotNone(self.record_of(paths[0]))

    def test_a_provider_failing_beside_an_unsung_answer_removes_nothing(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY))
        self.hear(paths[0], HARBOR)
        self.answer({TITLES[HARBOR]: FOUNDRY})
        self.providers = {
            **self.titles,
            NETEASE_KEY: fixtures.raising(OSError("refused")),
        }
        self.assert_kept(paths, paths[0].name, "NetEase failed")

    def test_a_provider_cooling_down_removes_nothing(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY))
        self.hear(paths[0], HARBOR)
        self.answer({TITLES[HARBOR]: FOUNDRY})
        self.cool_down_tekstowo()
        self.assert_kept(paths, paths[0].name, "Tekstowo not asked")

    def test_a_provider_stopped_part_way_removes_nothing_after(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY), song_track(2, MEADOW, txt=QUARRY)
        )
        self.hear(paths[0], HARBOR)
        self.hear(paths[1], MEADOW)
        self.answer({TITLES[HARBOR]: FOUNDRY, TITLES[MEADOW]: FOUNDRY})
        # Tekstowo refuses the first track and is not asked for the second.
        self.providers = {**self.titles, TEKSTOWO_KEY: StoppingStep()}
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        first = cli_fakes.row_cells(result.stdout, paths[0].name)[-1]
        self.assertIn("Tekstowo failed", first)
        second = cli_fakes.row_cells(result.stdout, paths[1].name)[-1]
        self.assertIn("Tekstowo not asked", second)
        self.assertEqual(raw(paths), before)
        for path in paths:
            self.assertIs(self.outcome(path.name).status, lr.OutcomeStatus.ERROR)
            self.assertIsNotNone(self.record_of(path))


class LeftOutTest(_CoolingCase):
    """A provider left out on purpose blocks no removal, even one cooling down."""

    def assert_removed(self, *extra: str) -> None:
        """With Tekstowo cooling down, the fix given *extra* removes the wrong text."""
        paths = self.build(song_track(1, HARBOR, txt=QUARRY), sung_neighbor())
        self.hear(paths[0], HARBOR)
        self.answer({TITLES[HARBOR]: FOUNDRY})
        self.cool_down_tekstowo()
        result = self.fix(*extra)
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.REMOVED)
        self.assertFalse(paths[0].with_suffix(".txt").exists())

    def test_no_tekstowo_blocks_nothing(self) -> None:
        self.assert_removed("--no-tekstowo")

    def test_only_providers_blocks_nothing(self) -> None:
        self.assert_removed("--only-providers", "lrclib")


class RemovedRecordTest(FixCase):
    """A removal keeps the record; undo brings the text back for a fix to take."""

    def test_a_removed_track_is_taken_again_once_undone(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY), sung_neighbor())
        self.hear(paths[0], HARBOR)
        record = self.record_of(paths[0])
        removed = self.fix()
        self.assertEqual(removed.code, album.EXIT_OK, removed.stdout + removed.stderr)
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.REMOVED)
        # The track holds nothing untimed now, so a fix leaves it alone.
        self.later(5)
        again = self.fix()
        self.assertEqual(again.code, album.EXIT_OK, again.stdout + again.stderr)
        skipped = f"skipped {paths[0].name}: its lyrics are not untimed only"
        self.assertIn(skipped, again.stderr)
        undone = self.run_album("--mode", "undo")
        self.assertEqual(undone.code, album.EXIT_OK, undone.stdout + undone.stderr)
        self.assertEqual(self.text_of(paths[0]), words(QUARRY))
        # The wrong text and the record match again: the fix takes the track.
        self.later(10)
        self.answer({TITLES[HARBOR]: HARBOR})
        fixed = self.fix()
        self.assertEqual(fixed.code, album.EXIT_OK, fixed.stdout + fixed.stderr)
        self.assertEqual(self.text_of(paths[0]), words(HARBOR))
        self.assertEqual(self.record_of(paths[0]), record)


class TimedFrameTest(FixCase):
    """A worded SYLT frame beside an untimed ``.txt`` makes the track timed: skipped."""

    def test_a_worded_sylt_beside_a_txt_is_skipped(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY))
        timed = tag_builders.sylt((1_000, "Up at the quarry on a chalky shelf"))
        tag_builders.plant_frames(paths[0], (timed,))
        self.hear(paths[0], HARBOR)
        self.answer({TITLES[HARBOR]: HARBOR})
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        skipped = f"skipped {paths[0].name}: its lyrics are not untimed only"
        self.assertIn(skipped, result.stderr)
        self.assertIn("nothing to fix", result.stdout)
        self.assertEqual(raw(paths), before)
        self.assertEqual(self.asked(), [])
        self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())
