"""A run judges each provider answer against the track's duration, with no option given.

The duration the run hands the fetch is the track's own, read before anything
changed: an answer whose lines run past a short track's end is declined there, so the
track ends not found and nothing is written, while the same answer on a long enough
track is taken. Each test drives the entry point with its real wiring; only the clock
and the provider table are faked, and every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh import FreshCliCase  # noqa: E402 - import follows the sys.path bootstrap above

#: Shorter than the lighthouse's latest line (25.75 s) by more than the tolerance.
SHORT_S = 20.0


class AnswerJudgedRunTest(FreshCliCase):
    """The run passes the track's duration to the fetch, which judges the answer."""

    def run_keeper(self, seconds: float) -> tuple[lr.TrackOutcome, list[Path]]:
        """Run an album of one track of *seconds*, answered the lighthouse's lines."""
        self.answer({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        track = TrackSpec("01 Keeper.mp3", seconds=seconds, title="The Keeper")
        paths = fixtures.build_album(self.album, (track,))
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (keeper,) = self.afters()
        assert keeper is not None
        return keeper, paths

    def test_an_answer_past_a_short_track_end_is_declined(self) -> None:
        keeper, paths = self.run_keeper(SHORT_S)
        self.assertEqual(keeper.status, lr.OutcomeStatus.NOT_FOUND)
        self.assertFalse(paths[0].with_suffix(".lrc").exists())

    def test_the_same_answer_on_a_long_enough_track_is_taken(self) -> None:
        keeper, paths = self.run_keeper(30.0)
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertTrue(paths[0].with_suffix(".lrc").exists())
