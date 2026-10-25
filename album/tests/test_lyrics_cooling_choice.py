"""A cooling source's run choice, state, label and stop report, for any source.

YouTube Music's own run choice is exercised end to end in test_lyrics_youtube_wiring;
these cases pin what is per source: each source reads its own cool-down file, and its
notices open with its own name. Every cool-down file is written under the test's own
temporary HOME, so no test reaches the real state folder.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_choices as lch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_provider_states import (  # noqa: E402 - follows the bootstrap
    CoolingState,
)

YTM, TEKSTOWO = lm.Source.YOUTUBE_MUSIC, lm.Source.TEKSTOWO
#: The moment the tests read as now, and a refusal an hour before it.
NOW = datetime(2026, 10, 21, 9, 30, tzinfo=UTC)
REFUSED = NOW - timedelta(hours=1)
#: A stop's record, as a refusal during the run leaves it.
STOP = lyc.Stop("HTTP 403", NOW + lyc.COOL_DOWN)


def _clock() -> datetime:
    """The setup's clock: the tests' fixed now."""
    return NOW


class _ChoiceCase(fixtures.IsolatedCase):
    """Collects the notices a choice gives; asks with Tekstowo's own flag by default."""

    def setUp(self) -> None:
        """Collect the notices."""
        super().setUp()
        self.notices: list[str] = []

    def choose(
        self,
        source: lm.Source = TEKSTOWO,
        *,
        off: bool = False,
        offline: bool = False,
    ) -> lch.CoolingChoice:
        """*source*'s choice at `NOW`, under the isolated environment."""
        return lch.cooling_choice(
            source,
            flag="--no-tekstowo",
            off=off,
            offline=offline,
            environ=os.environ,
            now=NOW,
            notice=self.notices.append,
            clock=_clock,
        )

    def refuse(self, source: lm.Source) -> lyc.CoolDown:
        """Write a refusal an hour ago to *source*'s cool-down file."""
        path = lyc.cooldown_path(os.environ, source)
        return lyc.write_cooldown(path, REFUSED, "HTTP 429")


class CoolingChoiceTest(_ChoiceCase):
    """Off offline or with the flag, off while cooling, else on with its own file."""

    def test_offline_is_off_and_reads_nothing(self) -> None:
        self.refuse(TEKSTOWO)
        choice = self.choose(offline=True, off=True)
        self.assertEqual(
            choice, lch.CoolingChoice(None, CoolingState.OFF, "off (offline)")
        )
        self.assertEqual(self.notices, [])

    def test_the_flag_turns_it_off_and_is_named(self) -> None:
        choice = self.choose(off=True)
        expected = lch.CoolingChoice(None, CoolingState.OFF, "off (--no-tekstowo)")
        self.assertEqual(choice, expected)

    def test_a_cool_down_still_holding_is_cooling_down(self) -> None:
        until = self.refuse(TEKSTOWO).until
        label = f"cooling down until {lyc.format_utc(until)}"
        expected = lch.CoolingChoice(None, CoolingState.COOLING_DOWN, label)
        self.assertEqual(self.choose(), expected)

    def test_no_cool_down_is_on_with_its_own_file_and_clock(self) -> None:
        choice = self.choose()
        path = lyc.cooldown_path(os.environ, TEKSTOWO)
        self.assertEqual(
            choice,
            lch.CoolingChoice(
                lyc.CoolingSetup(path, now=_clock), CoolingState.ON, "on"
            ),
        )
        self.assertEqual(self.notices, [])

    def test_a_broken_file_is_noticed_under_the_source_name_and_ignored(self) -> None:
        path = lyc.cooldown_path(os.environ, TEKSTOWO)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{")
        self.assertIs(self.choose().state, CoolingState.ON)
        self.assertEqual(
            self.notices,
            [f"tekstowo: {path}: cool-down file ignored (JSONDecodeError)"],
        )


class OwnFileTest(_ChoiceCase):
    """One source's cool-down leaves the other on."""

    def test_a_tekstowo_cool_down_leaves_youtube_music_on(self) -> None:
        self.refuse(TEKSTOWO)
        self.assertIs(self.choose(YTM).state, CoolingState.ON)
        self.assertIs(self.choose(TEKSTOWO).state, CoolingState.COOLING_DOWN)

    def test_a_youtube_music_cool_down_leaves_tekstowo_on(self) -> None:
        self.refuse(YTM)
        self.assertIs(self.choose(TEKSTOWO).state, CoolingState.ON)
        self.assertIs(self.choose(YTM).state, CoolingState.COOLING_DOWN)


class StopReportTest(_ChoiceCase):
    """A stop outranks the choice; a failed write is noticed under the source's name."""

    def test_a_stop_is_stopped_and_says_until_when(self) -> None:
        choice = self.choose()
        self.assertIs(lch.cooling_state(choice, STOP), CoolingState.STOPPED)
        self.assertIs(lch.cooling_state(choice, None), CoolingState.ON)
        self.assertEqual(
            lch.cooling_label(choice, STOP),
            f"stopped after HTTP 403; cooling down until {lyc.format_utc(STOP.until)}",
        )
        self.assertEqual(lch.cooling_label(choice, None), "on")

    def test_a_failed_write_is_noticed_under_each_source_name(self) -> None:
        choice = self.choose()
        failed = lyc.Stop("HTTP 403", STOP.until, write_error="disk full")
        for source, prefix in ((TEKSTOWO, "tekstowo"), (YTM, "youtube music")):
            with self.subTest(source=source):
                self.notices.clear()
                label = lch.cooling_report(source, choice, failed, self.notices.append)
                self.assertTrue(label.startswith("stopped after HTTP 403"))
                notice = f"{prefix}: could not write the cool-down file: disk full"
                self.assertEqual(self.notices, [notice])

    def test_a_written_stop_or_none_is_not_noticed(self) -> None:
        choice = self.choose()
        for stop in (STOP, None):
            lch.cooling_report(TEKSTOWO, choice, stop, self.notices.append)
        self.assertEqual(self.notices, [])
