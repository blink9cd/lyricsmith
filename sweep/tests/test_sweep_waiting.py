"""What an owed album waits on: the providers' holds, read fresh, and the lines naming them.

`sweep_waiting.read_holds` reads each provider's cool-down and breaker file through the
album tool's own readers, in the state folder the environment names; `waits_on` says
whether an album's whole effective debt is held, and what it waits on. Every state file
here is written under the test's own temporary ``XDG_STATE_HOME``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import lyrics_breaker  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run as sr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_waiting as sw  # noqa: E402 - import follows the sys.path bootstrap above
import waiting_fixtures as wf  # noqa: E402 - import follows the sys.path bootstrap above
from waiting_fixtures import cool, open_breaker  # noqa: E402 - follows the bootstrap

_TEKSTOWO, _YOUTUBE = lm.Source.TEKSTOWO, lm.Source.YOUTUBE_MUSIC
_GENIUS, _LRCLIB = lm.Source.GENIUS, lm.Source.LRCLIB
_COOL, _BREAKER = sw.Hold.COOL_DOWN, sw.Hold.BREAKER
_MARGIN = wf.MARGIN


def read_now() -> sw.HoldsRead:
    """The holds at the sweep's fixed clock, in this test's state folder."""
    return sw.read_holds(os.environ, sf.fixed_now())


class ReadHoldsTest(sf.IsolatedCase):
    """Each provider's hold, read fresh through the album tool's own readers."""

    def test_no_state_files_hold_nothing(self) -> None:
        self.assertEqual(read_now(), sw.HoldsRead())

    def test_a_cooling_provider_is_held_by_its_cool_down(self) -> None:
        cool(_TEKSTOWO)
        self.assertEqual(read_now().held, {_TEKSTOWO: _COOL})

    def test_a_cool_down_that_ended_holds_nothing(self) -> None:
        cool(_YOUTUBE, ago=lyrics_cooldown.COOL_DOWN + _MARGIN)
        self.assertEqual(read_now().held, {})

    def test_an_open_breaker_holds_its_provider(self) -> None:
        open_breaker(_GENIUS)
        self.assertEqual(read_now().held, {_GENIUS: _BREAKER})

    def test_a_breaker_whose_window_ended_holds_nothing(self) -> None:
        # Half-open: the next track may ask again.
        open_breaker(_LRCLIB, ends_in=-_MARGIN)
        self.assertEqual(read_now().held, {})

    def test_a_broken_file_is_no_hold_and_a_warning(self) -> None:
        path = lyrics_cooldown.cooldown_path(os.environ, _TEKSTOWO)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"{")
        read = read_now()
        self.assertEqual(read.held, {})
        self.assertEqual(len(read.warnings), 1)
        self.assertIn(str(path), read.warnings[0])

    def test_the_state_folder_is_the_one_the_environment_names(self) -> None:
        cool(_TEKSTOWO)
        elsewhere = {"XDG_STATE_HOME": str(self.tmp / "elsewhere")}
        self.assertEqual(sw.read_holds(elsewhere, sf.fixed_now()), sw.HoldsRead())

    def test_a_cool_down_wins_over_a_breaker(self) -> None:
        files = {_TEKSTOWO: "tekstowo_breaker.json"}
        with mock.patch.object(lyrics_breaker, "BREAKER_FILES", files):
            cool(_TEKSTOWO)
            open_breaker(_TEKSTOWO)
            self.assertEqual(read_now().held, {_TEKSTOWO: _COOL})


class WaitsOnTest(sf.IsolatedCase):
    """An album waits only when every provider of its effective debt is held."""

    def test_a_partly_held_debt_waits_on_nothing(self) -> None:
        waits = sw.waits_on((_LRCLIB, _TEKSTOWO), {_TEKSTOWO: _COOL}, ())
        self.assertEqual(waits, ())

    def test_a_wholly_held_debt_waits_on_each_in_the_owed_order(self) -> None:
        holds = {_TEKSTOWO: _COOL, _GENIUS: _BREAKER}
        waits = sw.waits_on((_GENIUS, _TEKSTOWO), holds, ())
        self.assertEqual(waits, ((_GENIUS, _BREAKER), (_TEKSTOWO, _COOL)))

    def test_a_provider_turned_off_leaves_the_debt(self) -> None:
        holds = {_GENIUS: _BREAKER}
        waits = sw.waits_on((_GENIUS, _TEKSTOWO), holds, ("--no-tekstowo",))
        self.assertEqual(waits, ((_GENIUS, _BREAKER),))

    def test_a_held_provider_turned_off_leaves_an_unheld_debt(self) -> None:
        waits = sw.waits_on(
            (_LRCLIB, _TEKSTOWO), {_TEKSTOWO: _COOL}, ("--no-tekstowo",)
        )
        self.assertEqual(waits, ())

    def test_an_empty_effective_debt_waits_on_nothing(self) -> None:
        for owed in ((), (_TEKSTOWO,)):
            with self.subTest(owed=owed):
                waits = sw.waits_on(owed, {_TEKSTOWO: _COOL}, ("--no-tekstowo",))
                self.assertEqual(waits, ())


class WaitingWordsTest(sf.IsolatedCase):
    """How the progress line, the report and the survey name an album passed over."""

    _WAITS = ((_GENIUS, _BREAKER), (_TEKSTOWO, _COOL))

    def test_the_progress_line_names_each_hold(self) -> None:
        self.assertEqual(
            sw.passed_over_line("Artist/Album", self._WAITS),
            "Artist/Album: passed over, waits on: Genius (breaker), Tekstowo (cool-down)",
        )

    def test_the_report_lists_each_album_under_a_count(self) -> None:
        passed = (
            sw.PassedOver("Artist/Album", self._WAITS),
            sw.PassedOver("Artist/Other", ((_YOUTUBE, _COOL),)),
        )
        self.assertEqual(
            sw.waiting_lines(passed),
            [
                "owed, waiting on a cool-down or breaker: 2",
                "  Artist/Album  waits on: Genius (breaker), Tekstowo (cool-down)",
                "  Artist/Other  waits on: YouTube Music (cool-down)",
            ],
        )

    def test_none_passed_over_prints_nothing(self) -> None:
        self.assertEqual(sw.waiting_lines(()), [])

    def test_a_report_with_every_album_passed_over_lists_them(self) -> None:
        passed = (sw.PassedOver("Artist/Album", self._WAITS),)
        report = sr.SweepReport((), 3, 0, sr.StopReason.FINISHED, passed_over=passed)
        text = sweep_report.format_sweep(
            report, library=self.library, options=sf.OPTIONS, undo_prog=("album.py",)
        )
        self.assertEqual(
            text.splitlines()[:3],
            [
                "already done: 3",
                "owed, waiting on a cool-down or breaker: 1",
                "  Artist/Album  waits on: Genius (breaker), Tekstowo (cool-down)",
            ],
        )
