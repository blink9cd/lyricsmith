"""`--mode fix-text`'s guards on provider-timed tracks, as entries 84 and 89 set them.

No timed text is removed while a provider the run meant to ask failed, cooled down or
stopped part-way: the track keeps its ``.lrc`` and frames, as its error. On an album that
looks spoken a removal is withheld. A track too little heard to judge is skipped. Each
test drives the entry point with its real wiring; every title and lyric is invented.
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
from fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    FOUNDRY,
    HARBOR,
    LITTLE,
    MEADOW,
    QUARRY,
    TITLES,
    sung_neighbor,
)
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_tekstowo_wiring import StoppingStep  # noqa: E402 - import follows the sys.path bootstrap above
from timed_fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    TimedFixCase,
    lrc_of,
    timed_track,
)

NETEASE_KEY = lm.ProviderKey(lm.Source.NETEASE, lm.LyricsKind.UNSYNCED)
TEKSTOWO_KEY = lm.ProviderKey(lm.Source.TEKSTOWO, lm.LyricsKind.UNSYNCED)
#: How a withheld track's row opens.
WITHHELD_ROW = "kept existing: removal withheld, spoken word"


class _Case(TimedFixCase):
    """A timed wrong text no provider sings, beside a sung neighbor."""

    def setUp(self) -> None:
        """The harbor track holding the quarry song timed; every answer unsung."""
        super().setUp()
        self.paths = self.build_timed(timed_track(1, HARBOR, QUARRY), sung_neighbor())
        self.hear(self.paths[0], HARBOR, held=QUARRY)
        self.answer_both({TITLES[HARBOR]: lrc_of(FOUNDRY)}, {TITLES[HARBOR]: FOUNDRY})

    def assert_kept(self, *cells: str) -> None:
        """The fix kept the timed text as the track's error, its row naming *cells*."""
        name = self.paths[0].name
        before = raw(self.paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        row = cli_fakes.row_cells(result.stdout, name)[-1]
        for cell in (*cells, "nothing removed"):
            self.assertIn(cell, row)
        self.assertEqual(raw(self.paths), before)
        self.assertIs(self.outcome(name).status, lr.OutcomeStatus.ERROR)
        self.assertIsNotNone(self.record_of(self.paths[0]))


class KeptTest(_Case):
    """A provider the run meant to ask did not answer: the timed text stays."""

    def test_a_provider_failing_keeps_the_timed_text(self) -> None:
        self.providers = {**self.titles, NETEASE_KEY: fixtures.raising(OSError("x"))}
        self.assert_kept("NetEase failed")

    def test_a_provider_cooling_down_keeps_the_timed_text(self) -> None:
        state = lyc.cooldown_path(self.wiring().environ, lm.Source.TEKSTOWO)
        lyc.write_cooldown(state, self.started - timedelta(hours=1), "HTTP 429")
        self.assert_kept("Tekstowo not asked")

    def test_a_provider_stopping_keeps_the_timed_text(self) -> None:
        self.providers = {**self.titles, TEKSTOWO_KEY: StoppingStep()}
        self.assert_kept("Tekstowo failed")


class SpokenTest(TimedFixCase):
    """On an album that looks spoken, a timed text's removal is withheld."""

    def test_a_spoken_genre_withholds_the_removal(self) -> None:
        lrc = lrc_of(QUARRY)
        spoken = TrackSpec(
            "01 Harbor.mp3", title=TITLES[HARBOR], lrc=lrc, genres=("Comedy",)
        )
        neighbor = TrackSpec("02 Meadow.mp3", title=TITLES[MEADOW], txt=MEADOW)
        paths = self.build_timed(spoken, neighbor)
        self.hear(paths[0], HARBOR, held=QUARRY)
        self.answer_both({}, {TITLES[HARBOR]: FOUNDRY})
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        row = cli_fakes.row_cells(result.stdout, paths[0].name)[-1]
        self.assertTrue(row.startswith(WITHHELD_ROW), row)
        self.assertEqual(raw(paths), before)
        outcome = self.outcome(paths[0].name)
        self.assertIs(outcome.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertIsNotNone(outcome.withheld)


class LittleHeardTest(TimedFixCase):
    """A timed track whose transcript heard too little to judge is skipped."""

    def test_a_little_heard_timed_track_is_skipped(self) -> None:
        paths = self.build_timed(timed_track(1, HARBOR, QUARRY))
        self.hear(paths[0], LITTLE, held=QUARRY)
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIn(f"skipped {paths[0].name}: too little heard", result.stderr)
        self.assertEqual(raw(paths), before)
        self.assertEqual(self.order, [])
