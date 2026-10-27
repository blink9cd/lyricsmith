"""``--owe-earlier-runs`` end to end: the survey's preview, the run's revisits, the refusals.

An album whose newest ledger line predates the owed outcome is revisited, asking only
what it owes, as an owed album is; the survey lists it among the albums to visit, and
again with its debt; the option is refused beside ``--restart``, ``--offline`` and
``--mode accept``, and a resume command carries it. A revisit a cool-down misses again
stays owed, as any run a cool-down misses does. The album tool is played by
`sweep_fixtures.FakeAlbumTool`, and `main` runs with stdout and stderr captured, so
nothing here runs a process, reaches a provider, or touches a real library or the real
ledger.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import lyrics_options  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run as sr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_provider_states import CoolingState  # noqa: E402 - follows the bootstrap
from sweep_survey import AlbumSurvey  # noqa: E402 - follows the sys.path bootstrap

_ONLY = lyrics_options.ONLY_FLAG
_OPTION = "--owe-earlier-runs"
_RUN = "20261018T120000Z"
_VISIT_LINE = "albums a run would visit:"
_EARLIER_LINE = "owed by earlier runs, visited again:"


def _only_value(argv: list[str]) -> str | None:
    """The value *argv* gives ``--only-providers``, or None when it gives none."""
    return sf.option_value(argv, _ONLY) if _ONLY in argv else None


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


class EarlierCase(sf.IsolatedCase):
    """Albums A1 to A4: A1 refused by LRCLIB, A2 cooled Tekstowo, A3 owes nothing.

    Each of A1 to A3 holds an earlier run's manifest and a ``done`` line from before
    the owed outcome; A4 has no line, so every sweep visits it.
    """

    def setUp(self) -> None:
        """Build the albums, their earlier runs and their earlier lines."""
        super().setUp()
        self.albums = [
            sf.needy_album(self.library, "Artist", f"A{n}") for n in range(1, 5)
        ]
        self.ledger = sl.ledger_path(self.library, os.environ)
        self.fake = sf.FakeAlbumTool()
        self.notes: list[str] = []
        self._earlier("A1", ("LRCLIB",), CoolingState.ON)
        self._earlier("A2", (), CoolingState.COOLING_DOWN)
        self._earlier("A3", (), CoolingState.ON)

    def _earlier(
        self, name: str, providers: tuple[str, ...], tekstowo: CoolingState
    ) -> None:
        folder = self.library / "Artist" / name
        sf.write_run(folder, _RUN, (sf.not_found(),), tekstowo=tekstowo)
        entry = sl.LedgerEntry(
            f"Artist/{name}",
            _RUN,
            0,
            sl.Outcome.DONE,
            "t",
            refusing_providers=providers,
        )
        sf.append_line(self.ledger, sf.earlier_line(entry))

    def sweep_with(self, *, owe: bool, skip: tuple[str, ...] = ()) -> sr.SweepReport:
        """A sweep of A1 to A4, with or without the option, given *skip*."""
        seams = sr.SweepSeams(
            run_child=self.fake,
            sleep=sf.FakeSleep(),
            now=sf.fixed_now,
            progress=self.notes.append,
            entry=self.tmp / "album.py",
        )
        options = replace(sf.OPTIONS, owe_earlier_runs=owe, skip=skip, stop_after=9)
        surveys = [AlbumSurvey(folder, none=1) for folder in self.albums]
        return sr.sweep(self.library, surveys, self.ledger, options, seams)

    def only_of(self) -> dict[str, str | None]:
        """Each album the fake was run on, and the providers it was told to ask."""
        return {
            Path(sf.option_value(argv, "--album")).name: _only_value(argv)
            for argv in self.fake.calls
        }

    def main(self, *args: str) -> Result:
        """Run `main` with *args* and ``--library``; return the code and the output."""
        argv = [*args, "--library", str(self.library)]
        wiring = sweep.Wiring(self.fake, sf.FakeSleep(), sf.fixed_now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = sweep.main(argv, wiring=wiring)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    @staticmethod
    def listed(stdout: str, heading: str) -> list[str]:
        """The lines under the survey line starting *heading*, stripped, in order."""
        lines = stdout.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith(heading))
        names: list[str] = []
        for line in lines[start + 1 :]:
            if not line.startswith("  "):
                break
            names.append(line.strip())
        return names


class RunTest(EarlierCase):
    """A run with the option revisits each album an earlier run left owing."""

    def test_without_the_option_the_earlier_albums_stay_done(self) -> None:
        self.sweep_with(owe=False)
        self.assertEqual(self.only_of(), {"A4": None})

    def test_with_it_each_debt_is_asked_and_nothing_else(self) -> None:
        report = self.sweep_with(owe=True)
        self.assertEqual(self.only_of(), {"A1": "lrclib", "A2": "tekstowo", "A4": None})
        self.assertEqual(report.skipped_done, 1)

    def test_a_revisit_writes_an_ordinary_line_a_later_sweep_reads(self) -> None:
        self.sweep_with(owe=True)
        calls = len(self.fake.calls)
        self.sweep_with(owe=True)
        self.sweep_with(owe=False)
        self.assertEqual(self.fake.calls[calls:], [])

    def test_a_turned_off_provider_drops_its_debt_and_says_so(self) -> None:
        self.sweep_with(owe=True, skip=("--no-tekstowo",))
        self.assertIn("Artist/A2: no longer owed Tekstowo", "\n".join(self.notes))


class CommandLineTest(EarlierCase):
    """The option on the command line: the survey's preview, the refusals, the resume."""

    def test_the_survey_lists_each_added_album_with_its_debt(self) -> None:
        result = self.main("--mode", "survey", _OPTION)
        self.assertEqual(result.code, 0)
        visit = self.listed(result.stdout, _VISIT_LINE)
        self.assertEqual(visit, ["Artist/A1", "Artist/A2", "Artist/A4"])
        self.assertIn(f"{_EARLIER_LINE} 2\n", result.stdout)
        self.assertEqual(
            self.listed(result.stdout, _EARLIER_LINE),
            ["Artist/A1  owes: LRCLIB", "Artist/A2  owes: Tekstowo"],
        )

    def test_a_survey_without_it_says_nothing_of_earlier_runs(self) -> None:
        result = self.main("--mode", "survey")
        self.assertEqual(self.listed(result.stdout, _VISIT_LINE), ["Artist/A4"])
        self.assertNotIn(_EARLIER_LINE, result.stdout)

    def test_the_run_visits_what_the_survey_listed(self) -> None:
        survey = self.main("--mode", "survey", _OPTION)
        run = self.main("--mode", "run", _OPTION)
        visited = ["Artist/" + name for name in self.fake.albums()]
        self.assertEqual(visited, self.listed(survey.stdout, _VISIT_LINE))
        self.assertIn("Artist/A2  owes: Tekstowo", survey.stdout)
        self.assertIn("3 of 4 albums to visit", run.stderr)

    def test_an_unreadable_earlier_manifest_is_said_and_owes_no_cool_down(
        self,
    ) -> None:
        manifest = self.library / "Artist" / "A2" / ".lyricsmith" / _RUN
        (manifest / "manifest.json").write_bytes(b"not json")
        result = self.main("--mode", "survey", _OPTION)
        self.assertIn("Artist/A2", result.stderr)
        self.assertEqual(
            self.listed(result.stdout, _EARLIER_LINE), ["Artist/A1  owes: LRCLIB"]
        )

    def test_the_option_is_refused_beside_restart_offline_and_accept(self) -> None:
        for extra in (["--restart"], ["--offline"]):
            with self.subTest(extra=extra):
                result = self.main("--mode", "run", _OPTION, *extra)
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"{_OPTION} cannot be used with {extra[0]}", result.stderr
                )
        accept = self.main("--mode", "accept", _OPTION)
        self.assertEqual(accept.code, 2)
        self.assertIn(f"{_OPTION} cannot be used with --mode accept", accept.stderr)
        self.assertEqual(self.fake.calls, [])

    def test_the_resume_command_carries_the_option(self) -> None:
        first = self.main("--mode", "run", _OPTION, "--limit", "1")
        resume = next(
            line for line in first.stdout.splitlines() if line.startswith("resume:")
        )
        self.assertIn(f" {_OPTION} ", f"{resume} ")
        self.assertEqual(self.fake.albums(), ["A1"])
        self.assertEqual(self.main("--mode", "run", _OPTION).code, 0)
        self.assertEqual(self.fake.albums()[1:], ["A2", "A4"])
        self.assertEqual(self.only_of(), {"A1": "lrclib", "A2": "tekstowo", "A4": None})


class CoolingAgainTest(EarlierCase):
    """A cool-down that misses a run leaves its debt owed: a revisit's, a new run's."""

    def newest(self, name: str) -> sl.LedgerEntry:
        """The newest ledger entry of album *name*."""
        records = sl.read_ledger(self.ledger).records
        entries = [r for r in records if isinstance(r, sl.LedgerEntry)]
        return [e for e in entries if e.album == f"Artist/{name}"][-1]

    def test_a_revisit_tekstowo_misses_again_stays_owed_and_is_asked_later(
        self,
    ) -> None:
        self.fake.scripts["A2"] = sf.Script(tekstowo=CoolingState.COOLING_DOWN)
        self.sweep_with(owe=True)
        entry = self.newest("A2")
        self.assertIs(entry.outcome, sl.Outcome.OWED)
        self.assertEqual(entry.owed_providers, (lm.Source.TEKSTOWO,))
        self.assertEqual(entry.refusing_providers, ())
        self.fake.scripts.clear()
        calls = len(self.fake.calls)
        self.sweep_with(owe=False)
        self.assertEqual(self.fake.albums()[calls:], ["A2"])
        self.assertEqual(self.only_of()["A2"], "tekstowo")

    def test_a_new_run_a_cool_down_misses_ends_owed_by_what_it_could_answer(
        self,
    ) -> None:
        untimed = sf.status_outcome(
            lr.OutcomeStatus.WRITTEN,
            source=lm.Source.GENIUS,
            kind=lm.LyricsKind.UNSYNCED,
        )
        cases = (
            (sf.Script(tekstowo=CoolingState.COOLING_DOWN), (lm.Source.TEKSTOWO,)),
            (sf.Script((untimed,), tekstowo=CoolingState.COOLING_DOWN), ()),
            (
                sf.Script((untimed,), youtube_music=CoolingState.COOLING_DOWN),
                (lm.Source.YOUTUBE_MUSIC,),
            ),
        )
        for script, owed in cases:
            with self.subTest(owed=owed):
                self.fake.scripts["A4"] = script
                self.sweep_with(owe=False)
                entry = self.newest("A4")
                outcome = sl.Outcome.OWED if owed else sl.Outcome.DONE
                self.assertEqual((entry.outcome, entry.owed_providers), (outcome, owed))
                self.ledger.unlink()
