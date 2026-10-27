"""The `--no-*` options: passed on to each album child a run starts, refused by a survey.

Each leaves one source out of every album the sweep visits: `--no-netease`,
`--no-bandcamp`, `--no-youtube-music`, `--no-genius` and `--no-tekstowo`. `main` runs
with a fake album tool, a recording sleep and a fixed clock, with stdout and stderr
captured, so nothing here waits, reaches a provider, or touches a real library or the
real ledger.
"""

from __future__ import annotations

import contextlib
import io
import shlex
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run  # noqa: E402 - import follows the sys.path bootstrap above

#: The options this suite is about, in the order a command line holds them.
OPTIONS = (
    "--no-netease",
    "--no-bandcamp",
    "--no-youtube-music",
    "--no-genius",
    "--no-tekstowo",
)


def _options(*skip: str) -> sweep_run.SweepOptions:
    """A sweep's options that leave out the sources *skip* names."""
    return sweep_run.SweepOptions(
        lang=None,
        offline=False,
        pause_s=1.0,
        stop_after=3,
        limit=None,
        restart=False,
        skip=skip,
    )


class ArgvTest(sf.IsolatedCase):
    """The child's command line ends with the options asked for, in chain order."""

    def test_the_options_are_the_ones_named_in_chain_order(self) -> None:
        self.assertEqual(tuple(flag for _, flag in sweep_child.SKIP_OPTIONS), OPTIONS)

    def test_asked_they_end_the_command_line_once_each(self) -> None:
        argv = sweep_child.album_argv(
            self.tmp / "e.py",
            self.library,
            lang="deu",
            offline=False,
            refetch_untimed=True,
            changed_list=self.tmp / "changed.txt",
            skip=OPTIONS,
        )
        self.assertEqual(tuple(argv[-len(OPTIONS) :]), OPTIONS)

    def test_by_default_none_is_there(self) -> None:
        argv = sweep_child.album_argv(
            self.tmp / "e.py", self.library, lang=None, offline=False
        )
        self.assertFalse(set(OPTIONS) & set(argv))


class _CliCase(sf.IsolatedCase):
    """Two albums, a fake album tool, and `main` run against them."""

    def setUp(self) -> None:
        """Two albums to visit and a fake album tool."""
        super().setUp()
        for name in ("A1", "A2"):
            sf.needy_album(self.library, "Artist", name)
        self.fake = sf.FakeAlbumTool()

    def main(self, *args: str) -> tuple[int, str, str]:
        """Run `main` with *args* and ``--library``; the code, stdout and stderr."""
        argv = [*args, "--library", str(self.library)]
        wiring = sweep.Wiring(self.fake, sf.FakeSleep(), sf.fixed_now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = sweep.main(argv, wiring=wiring)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return code, stdout.getvalue(), stderr.getvalue()


class CliTest(_CliCase):
    """A run gives each child each option; a survey refuses it; a resume carries it."""

    def test_a_run_gives_every_child_each_option(self) -> None:
        for option in OPTIONS:
            with self.subTest(option=option):
                self.fake.calls.clear()
                code, _, stderr = self.main("--mode", "run", "--restart", option)
                self.assertEqual(code, 0, stderr)
                self.assertEqual(len(self.fake.calls), 2)
                self.assertTrue(all(argv[-1] == option for argv in self.fake.calls))

    def test_a_run_given_them_all_passes_them_in_chain_order(self) -> None:
        code, _, stderr = self.main("--mode", "run", *reversed(OPTIONS))
        self.assertEqual(code, 0, stderr)
        self.assertTrue(
            all(tuple(argv[-len(OPTIONS) :]) == OPTIONS for argv in self.fake.calls)
        )

    def test_a_run_without_them_gives_no_child_any(self) -> None:
        self.main("--mode", "run")
        self.assertEqual(len(self.fake.calls), 2)
        self.assertTrue(all(not set(OPTIONS) & set(argv) for argv in self.fake.calls))

    def test_the_survey_refuses_each(self) -> None:
        for option in OPTIONS:
            with self.subTest(option=option):
                code, _, stderr = self.main("--mode", "survey", option)
                self.assertEqual(code, 2)
                self.assertIn(f"{option} cannot be used with --mode survey", stderr)
        self.assertEqual(self.fake.calls, [])

    def test_the_resume_command_carries_each(self) -> None:
        library = shlex.quote(str(self.library))
        for option in OPTIONS:
            with self.subTest(option=option):
                command = sweep_report.resume_command(
                    ("sweep.py",), self.library, _options(option)
                )
                self.assertEqual(
                    command,
                    f"sweep.py --mode run --library {library} "
                    f"{option} --pause-seconds 1.0 --stop-after 3",
                )


class StopRuleTest(_CliCase):
    """Without NetEase, LRCLIB's synced refusal alone is every synced step refusing."""

    def setUp(self) -> None:
        """Both albums' one track served its own untimed words after LRCLIB refused."""
        super().setUp()
        served = sf.status_outcome(
            lr.OutcomeStatus.UNCHANGED,
            source=lm.Source.SIDECAR,
            kind=lm.LyricsKind.UNSYNCED,
            refusals=(sf.refusal(lm.Source.LRCLIB),),
        )
        for name in ("A1", "A2"):
            self.fake.scripts[name] = sf.Script(outcomes=(served,))

    def test_under_no_netease_the_album_counts_refused_and_the_sweep_stops(
        self,
    ) -> None:
        code, _, stderr = self.main("--mode", "run", "--stop-after", "1", OPTIONS[0])
        self.assertEqual(code, sweep_run.EXIT_REFUSED, stderr)
        self.assertEqual(len(self.fake.calls), 1)

    def test_with_netease_asked_its_silence_is_an_answer_and_the_sweep_goes_on(
        self,
    ) -> None:
        code, _, stderr = self.main("--mode", "run", "--stop-after", "1")
        # Answered, but each album still owes LRCLIB, which refused its track: owed,
        # not done, so the sweep goes on and exits 1.
        self.assertEqual(code, sweep_run.EXIT_ALBUM_ERRORS, stderr)
        self.assertEqual(len(self.fake.calls), 2)


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
