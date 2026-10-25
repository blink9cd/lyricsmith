"""A test case that drives the entry point's `main` with faked wiring and captured streams.

`CliCase.main` runs `album.main` with the faked run folder, a recorded provider
factory and a frozen clock, and returns the exit code with what reached stdout and
stderr. Argparse's own refusals (exit 2) come back the same way. `RealWiringCliCase`
runs it with the real wiring instead, every stage real unless a test overrides it.

Not discovered as a suite (no ``test_`` prefix).
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import io
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE.parent)]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - the tests folder is on sys.path for every suite
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - the tests folder is on sys.path for every suite


#: What separates the cells of a summary row: two spaces or more, as a name has one.
_CELL_GAP = re.compile(r"\s{2,}")


def row_cells(stdout: str, name: str) -> list[str]:
    """The cells of the one summary row in *stdout* naming *name*, number first."""
    (row,) = [line for line in stdout.splitlines() if name in line]
    return _CELL_GAP.split(row.strip())


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


class CliCase(fixtures.IsolatedCase):
    """Runs `album.main` against an album under this test's directory."""

    def setUp(self) -> None:
        """An album folder, a fake run folder and a recording provider factory."""
        super().setUp()
        self.album = self.tmp / "album"
        self.folder = run_fakes.FakeRunFolder()
        self.providers: lm.ProviderTable = run_fakes.table()
        self.provider_calls: list[tuple[lr.Secret | None, float]] = []
        self.provider_settings: list[ltable.ProviderSettings] = []
        self.acquire_lock: lyrics_album.AcquireLock = run_fakes.acquire
        self.overrides: dict[str, object] = {}

    def build_providers(
        self,
        key: lr.Secret | None,
        deadline_s: float,
        settings: ltable.ProviderSettings = ltable.ProviderSettings(),
    ) -> lm.ProviderTable:
        """Record the call and its settings apart; answer this test's table."""
        self.provider_calls.append((key, deadline_s))
        self.provider_settings.append(settings)
        return self.providers

    def wiring(self) -> album.Wiring:
        """The faked wiring this test's overrides describe."""
        return album.Wiring(
            stages=run_fakes.make_stages(self.folder, **self.overrides),
            build_providers=self.build_providers,
            acquire_lock=self.acquire_lock,
            environ=dict(os.environ),
            now=self.fixed_now,
        )

    def fixed_now(self) -> dt.datetime:
        """The fixed clock: one bound method per test, so setups holding it compare equal."""
        return run_fakes.STARTED

    def youtube_setup(self) -> lyc.CoolingSetup:
        """The YouTube Music setup a run with no cool-down builds on this test's wiring."""
        return self.cooling_setup(lm.Source.YOUTUBE_MUSIC)

    def tekstowo_setup(self) -> lyc.CoolingSetup:
        """The Tekstowo setup a run with no cool-down builds on this test's wiring."""
        return self.cooling_setup(lm.Source.TEKSTOWO)

    def cooling_setup(self, source: lm.Source) -> lyc.CoolingSetup:
        """*source*'s setup when its cool-down file holds none, on this test's wiring."""
        state = lyc.cooldown_path(self.wiring().environ, source)
        return lyc.CoolingSetup(state, now=self.wiring().now)

    def main(
        self,
        *argv: str,
        stdout: io.StringIO | None = None,
        stderr: io.StringIO | None = None,
        prog: str | None = None,
        argv0: str = "album.py",
    ) -> Result:
        """Run `main` with *argv*, capturing both streams and argparse's exit.

        A test passes its own *stdout* or *stderr* to make one of them fail, *prog* to
        run as the lyricsmith dispatcher runs the tool, or *argv0* to name the entry
        file otherwise.
        """
        stdout = io.StringIO() if stdout is None else stdout
        stderr = io.StringIO() if stderr is None else stderr
        # The summary names the tool by argv[0], as an operator's shell would show it.
        named = mock.patch.object(sys, "argv", [argv0])
        with (
            named,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                code = album.main(list(argv), wiring=self.wiring(), prog=prog)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def run_album(self, *extra: str, prog: str | None = None) -> Result:
        """Run this test's album in run mode, with *extra* options, named *prog*."""
        return self.main("--album", str(self.album), *extra, prog=prog)


class RealWiringCliCase(CliCase):
    """`CliCase` on the real wiring: real stages, run folder and lock; providers faked.

    A stage in ``overrides`` replaces the real one. The clock is fixed, so the run id
    is `run_fakes.RUN_ID`, unless a suite sets *real_clock* to run on the UTC clock.
    """

    #: True to keep the real clock, as an end-to-end suite does.
    real_clock: ClassVar[bool] = False

    def wiring(self) -> album.Wiring:
        """The real wiring, with this test's providers, overrides and clock."""
        real = album.real_wiring()
        stages = dataclasses.replace(real.stages, **self.overrides)  # type: ignore[arg-type] # a test's override of one stage
        now = real.now if self.real_clock else self.fixed_now
        return dataclasses.replace(
            real, stages=stages, build_providers=self.build_providers, now=now
        )
