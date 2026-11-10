"""A run passes over an owed album whose whole debt waits on a cool-down or breaker.

Just before visiting an owed album, the sweep reads every provider's hold fresh
(`sweep_waiting.read_holds`). When each provider the album owes, less those the sweep
turns off, is held, the album is passed over: not run, no ledger line, no ``--limit``
place, no pause owed, no change to the refused streak, and reported apart, the sweep
then exiting at least 1. The album tool is played by `sweep_fixtures.FakeAlbumTool`,
and every state file is written under the test's own temporary ``XDG_STATE_HOME``.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_options  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run as sr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_waiting as sw  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_provider_states import CoolingState  # noqa: E402 - follows the bootstrap
from sweep_survey import AlbumSurvey  # noqa: E402 - follows the sys.path bootstrap
from waiting_fixtures import cool, open_breaker  # noqa: E402 - follows the bootstrap

_TEKSTOWO, _YOUTUBE = lm.Source.TEKSTOWO, lm.Source.YOUTUBE_MUSIC
_GENIUS, _LRCLIB = lm.Source.GENIUS, lm.Source.LRCLIB
_COOL, _BREAKER = sw.Hold.COOL_DOWN, sw.Hold.BREAKER
_ONLY = lyrics_options.ONLY_FLAG
_REFUSED = sf.Script(outcomes=sf.REFUSED, exit_code=album.EXIT_TRACK_ERRORS)
#: The heading of the report's and the survey's list of albums passed over.
_HEADING = "owed, waiting on a cool-down or breaker:"


def _read_now() -> sw.HoldsRead:
    """The holds at the sweep's fixed clock, in this test's state folder."""
    return sw.read_holds(os.environ, sf.fixed_now())


class WaitingCase(sf.SweepCase):
    """`SweepCase`, owed albums written to its ledger and the real holds read."""

    def setUp(self) -> None:
        """Read the holds for real, at the sweep's clock, unless a test swaps them."""
        super().setUp()
        self.holds: Callable[[], sw.HoldsRead] = _read_now

    def owe(self, name: str, *owed: lm.Source) -> None:
        """Record album Artist/*name* as owed *owed* by its last visit."""
        entry = sl.LedgerEntry(
            f"Artist/{name}", None, 0, sl.Outcome.OWED, "t", owed_providers=owed
        )
        sl.append_record(self.ledger, entry)

    def sweep_now(
        self,
        *,
        limit: int | None = None,
        offline: bool = False,
        skip: tuple[str, ...] = (),
        stop_after: int = 2,
    ) -> sr.SweepReport:
        """A sweep of A1 to A5 with the holds this test reads."""
        seams = sr.SweepSeams(
            run_child=self.run_child,
            sleep=self.sleep,
            now=sf.fixed_now,
            progress=self.notes.append,
            entry=self.tmp / "album.py",
            holds=self.holds,
        )
        options = replace(
            sf.OPTIONS, limit=limit, offline=offline, skip=skip, stop_after=stop_after
        )
        surveys = [AlbumSurvey(folder, none=1) for folder in self.albums]
        return sr.sweep(self.library, surveys, self.ledger, options, seams)

    def lines_of(self, name: str) -> list[sl.LedgerEntry]:
        """Every ledger entry of album Artist/*name*, in order."""
        return [entry for entry in self._ledger() if entry.album == f"Artist/{name}"]

    def only_of(self, name: str) -> str | None:
        """What the last visit of album *name* gave ``--only-providers``, if anything."""
        argv = [
            a for a in self.fake.calls if sf.option_value(a, "--album").endswith(name)
        ]
        last = argv[-1]
        return sf.option_value(last, _ONLY) if _ONLY in last else None


class PassedOverTest(WaitingCase):
    """An owed album whose whole debt is held is passed over, and reported."""

    def test_a_whole_debt_cooling_is_passed_over_and_reported(self) -> None:
        self.owe("A2", _TEKSTOWO)
        cool(_TEKSTOWO)
        report = self.sweep_now()
        self.assertEqual(self.fake.albums(), ["A1", "A3", "A4", "A5"])
        self.assertEqual(
            report.passed_over, (sw.PassedOver("Artist/A2", ((_TEKSTOWO, _COOL),)),)
        )
        self.assertEqual(len(self.lines_of("A2")), 1)
        self.assertIn("[2/5] Artist/A2", self.notes)
        self.assertIn(
            "Artist/A2: passed over, waits on: Tekstowo (cool-down)", self.notes
        )
        self.assertEqual(report.remaining, 0)
        self.assertEqual(sr.sweep_exit_code(report), sr.EXIT_ALBUM_ERRORS)

    def test_a_whole_debt_behind_an_open_breaker_is_passed_over(self) -> None:
        self.owe("A2", _GENIUS)
        open_breaker(_GENIUS)
        report = self.sweep_now()
        self.assertNotIn("A2", self.fake.albums())
        self.assertEqual(report.passed_over[0].waits, ((_GENIUS, _BREAKER),))
        self.assertEqual(sr.sweep_exit_code(report), sr.EXIT_ALBUM_ERRORS)

    def test_a_breaker_whose_window_ended_lets_the_album_be_visited(self) -> None:
        self.owe("A2", _GENIUS)
        open_breaker(_GENIUS, ends_in=-timedelta(minutes=1))
        report = self.sweep_now()
        self.assertIn("A2", self.fake.albums())
        self.assertEqual(report.passed_over, ())
        self.assertEqual(sr.sweep_exit_code(report), sr.EXIT_OK)

    def test_a_passed_over_album_takes_no_limit_place(self) -> None:
        self.owe("A2", _TEKSTOWO)
        cool(_TEKSTOWO)
        report = self.sweep_now(limit=2)
        self.assertEqual(self.fake.albums(), ["A1", "A3"])
        self.assertEqual(report.remaining, 2)
        self.assertIs(report.stop, sr.StopReason.LIMIT)

    def test_a_passed_over_album_owes_no_pause(self) -> None:
        for name in ("A2", "A3", "A4", "A5"):
            self.owe(name, _YOUTUBE)
        cool(_YOUTUBE)
        report = self.sweep_now()
        self.assertEqual(self.fake.albums(), ["A1"])
        self.assertEqual(self.sleep.pauses, [])
        self.assertEqual(len(report.passed_over), 4)

    def test_a_passed_over_album_between_two_visits_leaves_one_pause(self) -> None:
        self.owe("A2", _TEKSTOWO)
        cool(_TEKSTOWO)
        self.sweep_now(limit=2)
        self.assertEqual(self.fake.albums(), ["A1", "A3"])
        self.assertEqual(self.sleep.pauses, [sf.OPTIONS.pause_s])

    def test_a_passed_over_album_leaves_the_refused_streak_as_it_was(self) -> None:
        self.fake.scripts.update({"A1": _REFUSED, "A3": _REFUSED})
        self.owe("A2", _TEKSTOWO)
        cool(_TEKSTOWO)
        report = self.sweep_now(stop_after=2)
        self.assertIs(report.stop, sr.StopReason.REFUSALS)
        self.assertEqual(self.fake.albums(), ["A1", "A3"])


class VisitedAnywayTest(WaitingCase):
    """An owed album is visited unless its whole effective debt is held."""

    def test_a_partly_held_album_is_visited_naming_every_owed_provider(self) -> None:
        self.owe("A2", _LRCLIB, _TEKSTOWO)
        cool(_TEKSTOWO)
        report = self.sweep_now()
        self.assertEqual(self.only_of("A2"), "lrclib,tekstowo")
        self.assertEqual(report.passed_over, ())

    def test_a_held_provider_turned_off_leaves_the_rest_to_visit(self) -> None:
        self.owe("A2", _LRCLIB, _TEKSTOWO)
        cool(_TEKSTOWO)
        self.sweep_now(skip=("--no-tekstowo",))
        self.assertIn("A2", self.fake.albums())

    def test_an_unheld_provider_turned_off_leaves_a_held_debt(self) -> None:
        self.owe("A2", _GENIUS, _TEKSTOWO)
        open_breaker(_GENIUS)
        report = self.sweep_now(skip=("--no-tekstowo",))
        self.assertNotIn("A2", self.fake.albums())
        self.assertEqual(report.passed_over[0].waits, ((_GENIUS, _BREAKER),))

    def test_an_effective_debt_all_turned_off_is_visited(self) -> None:
        self.owe("A2", _TEKSTOWO)
        cool(_TEKSTOWO)
        self.sweep_now(skip=("--no-tekstowo",))
        self.assertIn("A2", self.fake.albums())

    def test_an_offline_sweep_never_passes_over(self) -> None:
        self.owe("A2", _TEKSTOWO)
        cool(_TEKSTOWO)
        self.holds = self._never_read
        report = self.sweep_now(offline=True)
        self.assertIn("A2", self.fake.albums())
        self.assertEqual(report.passed_over, ())

    def test_an_album_owing_nothing_never_reads_the_holds(self) -> None:
        self.holds = self._never_read
        report = self.sweep_now()
        self.assertEqual(len(self.fake.albums()), 5)
        self.assertEqual(report.passed_over, ())

    def _never_read(self) -> sw.HoldsRead:
        raise AssertionError("the holds were read")


class FreshReadTest(WaitingCase):
    """The holds are read again before each owed album, and a broken file holds none."""

    def test_a_hold_appearing_mid_sweep_passes_over_a_later_album(self) -> None:
        self.owe("A1", _TEKSTOWO)
        self.owe("A3", _TEKSTOWO)

        def run_then_cool(
            argv: Sequence[str],
            timeout_s: float,
            *,
            env: Mapping[str, str] | None = None,
        ) -> sweep_child.ChildResult:
            # The album tool's run on A1 meets a refusal and writes the cool-down.
            result = self.fake(argv, timeout_s, env=env)
            cool(_TEKSTOWO)
            return result

        self.run_child = run_then_cool
        report = self.sweep_now()
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A4", "A5"])
        self.assertEqual([p.name for p in report.passed_over], ["Artist/A3"])

    def test_a_broken_state_file_is_no_hold_and_one_warning(self) -> None:
        self.owe("A1", _TEKSTOWO)
        self.owe("A2", _TEKSTOWO)
        path = lyrics_cooldown.cooldown_path(os.environ, _TEKSTOWO)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{")
        report = self.sweep_now()
        self.assertEqual(len(self.fake.albums()), 5)
        self.assertEqual(report.passed_over, ())
        warnings = [note for note in self.notes if str(path) in note]
        self.assertEqual(len(warnings), 1)


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


class CliWaitingTest(sf.IsolatedCase):
    """The CLI reads the holds in the state folder ``XDG_STATE_HOME`` names."""

    def setUp(self) -> None:
        """Three needy albums, A1 to A3, the second owed Tekstowo, Tekstowo cooling.

        ``XDG_STATE_HOME`` names a folder apart from the home folder's default, so a
        hold found there was read where the variable says.
        """
        super().setUp()
        os.environ["XDG_STATE_HOME"] = str(self.tmp / "xdg-state")
        for name in ("A1", "A2", "A3"):
            sf.needy_album(self.library, "Artist", name)
        self.fake = sf.FakeAlbumTool()
        ledger = sl.ledger_path(self.library, os.environ)
        entry = sl.LedgerEntry(
            "Artist/A2", None, 0, sl.Outcome.OWED, "t", owed_providers=(_TEKSTOWO,)
        )
        sl.append_record(ledger, entry)
        cool(_TEKSTOWO)

    def main(self, *args: str) -> Result:
        """Run `main` with *args* and ``--library``; return the code and the output."""
        argv = [*args, "--library", str(self.library)]
        wiring = sweep.Wiring(self.fake, sf.FakeSleep(), sf.fixed_now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sweep.main(argv, wiring=wiring)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def test_a_run_passes_over_the_album_and_exits_one(self) -> None:
        result = self.main("--mode", "run")
        self.assertEqual(self.fake.albums(), ["A1", "A3"])
        self.assertEqual(result.code, sr.EXIT_ALBUM_ERRORS)
        line = "Artist/A2: passed over, waits on: Tekstowo (cool-down)"
        self.assertIn(line, result.stderr.splitlines())
        stdout = result.stdout.splitlines()
        self.assertIn(f"{_HEADING} 1", stdout)
        self.assertIn("  Artist/A2  waits on: Tekstowo (cool-down)", stdout)

    def test_a_cool_down_outside_that_folder_holds_nothing(self) -> None:
        lyrics_cooldown.cooldown_path(os.environ, _TEKSTOWO).unlink()
        home = {"HOME": os.environ["HOME"]}
        path = lyrics_cooldown.cooldown_path(home, _TEKSTOWO)
        lyrics_cooldown.write_cooldown(path, sf.fixed_now(), "HTTP 429")
        result = self.main("--mode", "run")
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A3"])
        self.assertNotIn(_HEADING, result.stdout)

    def test_the_survey_lists_the_album_apart_and_writes_nothing(self) -> None:
        state = Path(os.environ["XDG_STATE_HOME"])
        state_before = sorted(state.rglob("*"))
        result = self.main("--mode", "survey")
        stdout = result.stdout.splitlines()
        self.assertIn("albums a run would visit: 2", stdout)
        self.assertNotIn("  Artist/A2", stdout)
        self.assertIn(f"{_HEADING} 1", stdout)
        self.assertIn("  Artist/A2  waits on: Tekstowo (cool-down)", stdout)
        self.assertEqual(sorted(state.rglob("*")), state_before)
        self.assertEqual(result.code, sr.EXIT_OK)

    def test_the_survey_prints_no_heading_when_nothing_waits(self) -> None:
        lyrics_cooldown.cooldown_path(os.environ, _TEKSTOWO).unlink()
        result = self.main("--mode", "survey")
        self.assertIn("albums a run would visit: 3", result.stdout.splitlines())
        self.assertNotIn(_HEADING, result.stdout)

    def test_the_survey_says_a_broken_state_file_once(self) -> None:
        path = lyrics_cooldown.cooldown_path(os.environ, _YOUTUBE)
        path.write_bytes(b"{")
        result = self.main("--mode", "survey")
        self.assertEqual(result.stderr.count(str(path)), 1)

    def test_an_earlier_debt_that_waits_is_listed_only_as_waiting(self) -> None:
        run_id = "20261018T120000Z"
        folder = self.library / "Artist" / "A3"
        sf.write_run(
            folder, run_id, (sf.not_found(),), tekstowo=CoolingState.COOLING_DOWN
        )
        entry = sl.LedgerEntry("Artist/A3", run_id, 0, sl.Outcome.DONE, "t")
        sf.append_line(sl.ledger_path(self.library, os.environ), sf.earlier_line(entry))
        stdout = self.main("--mode", "survey", "--owe-earlier-runs").stdout.splitlines()
        self.assertIn("albums a run would visit: 1", stdout)
        self.assertIn(f"{_HEADING} 2", stdout)
        self.assertIn("  Artist/A3  waits on: Tekstowo (cool-down)", stdout)
        self.assertIn("owed by earlier runs, visited again: 0", stdout)
        self.assertNotIn("  Artist/A3  owes: Tekstowo", stdout)
