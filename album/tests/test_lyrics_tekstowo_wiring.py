"""`--no-tekstowo` and Tekstowo's cool-down: the table a run builds, its line and its record.

Each test drives the entry point with faked wiring, or with the real wiring and a
provider table built by hand, so no test reaches the network; every cool-down file is
written under the test's own temporary XDG_STATE_HOME. Every title and lyric is invented.
"""

from __future__ import annotations

import json
import shlex
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_check  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tally  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tekstowo as lt  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_fakes as tf  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import FakeClock  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_provider_states import (  # noqa: E402 - import follows the sys.path bootstrap above
    CoolingState,
)

TEK = lm.Source.TEKSTOWO
KEY = lm.ProviderKey(TEK, lm.LyricsKind.UNSYNCED)
#: The line each choice prints after the YouTube Music line.
ON, SKIPPED, OFFLINE = (
    "tekstowo: on",
    "tekstowo: off (--no-tekstowo)",
    "tekstowo: off (offline)",
)
#: When a stop the fakes below make ends: a day after the fixed clock's start.
UNTIL = run_fakes.STARTED + lyc.COOL_DOWN
STOPPED = (
    f"tekstowo: stopped after HTTP 429; cooling down until {lyc.format_utc(UNTIL)}"
)
#: Two untimed-only tracks: the first one's answer agrees, the second one's does not.
UNTIMED = (
    TrackSpec("01 A.mp3", title="Beacon Light", txt=fixtures.LIGHTHOUSE_PLAIN),
    TrackSpec("02 B.mp3", title="Mill Wheel", txt=fixtures.LIGHTHOUSE_PLAIN),
)
#: What the plain Tekstowo fake answers, by title.
ANSWERS = {
    "Beacon Light": fixtures.LIGHTHOUSE_EDITED,
    "Mill Wheel": fixtures.ORCHARD_PLAIN,
}


def _after(stdout: str, prefix: str) -> str:
    """The line right after the one starting with *prefix*."""
    lines = stdout.splitlines()
    found = next(i for i, line in enumerate(lines) if line.startswith(prefix))
    return lines[found + 1]


def _after_youtube(stdout: str) -> str:
    """The line right after the ``youtube music:`` line."""
    return _after(stdout, "youtube music: ")


@dataclass
class StoppingStep:
    """A Tekstowo step that stops on its first call, as a refusal would."""

    write_error: str | None = None
    stop: lyc.Stop | None = None
    calls: list[lm.TrackQuery] = field(default_factory=list)

    def asks(self, query: lm.TrackQuery) -> bool:
        """True until the step stopped."""
        del query
        return self.stop is None

    def __call__(self, query: lm.TrackQuery) -> str | None:
        """Stop for the run, as a ``429`` would, and raise the stop."""
        self.calls.append(query)
        self.stop = lyc.Stop("HTTP 429", UNTIL, self.write_error)
        raise lt.TekstowoStopped("HTTP 429")

    def provider_stop(self) -> lyc.Stop | None:
        """What stopped the step, or None."""
        return self.stop


class _CoolDownCase(cli_fakes.CliCase):
    """A one-track album; Tekstowo's cool-down file under this test's XDG_STATE_HOME."""

    def setUp(self) -> None:
        """A one-track album."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))
        self.state = lyc.cooldown_path(self.wiring().environ, TEK)

    def refused(self, hours_ago: float) -> lyc.CoolDown:
        """Write a refusal *hours_ago* before the fixed clock's start."""
        moment = run_fakes.STARTED - timedelta(hours=hours_ago)
        return lyc.write_cooldown(self.state, moment, "HTTP 429")

    def malformed(self) -> None:
        """Write a cool-down file that does not hold a record."""
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_bytes(b"not a record")

    def settings(self) -> ltable.ProviderSettings:
        """The settings the one table built was given."""
        self.assertEqual(len(self.provider_settings), 1)
        return self.provider_settings[0]

    def setup(self) -> lyc.CoolingSetup | None:
        """The Tekstowo setup the one table built was given."""
        return self.settings().tekstowo


class RunChoiceTest(_CoolDownCase):
    """A run builds its table with Tekstowo on unless told otherwise, and says which."""

    def test_by_default_the_table_asks_it_with_the_state_file_and_the_clock(
        self,
    ) -> None:
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        setup = self.setup()
        assert setup is not None
        self.assertEqual(setup.state_path, self.state)
        self.assertTrue(setup.state_path.is_relative_to(self.xdg["XDG_STATE_HOME"]))
        self.assertEqual(setup.state_path.name, "tekstowo.json")
        self.assertEqual(setup.now(), run_fakes.STARTED)
        self.assertEqual(_after_youtube(result.stdout), ON)
        self.assertIs(self.folder.manifests[-1].tekstowo, CoolingState.ON)

    def test_no_tekstowo_builds_the_table_without_it_and_reads_no_file(self) -> None:
        self.malformed()
        result = self.run_album("--no-tekstowo")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertIsNone(self.setup())
        self.assertIsNotNone(self.settings().youtube_music)
        self.assertEqual(_after_youtube(result.stdout), SKIPPED)
        self.assertNotIn("tekstowo", result.stderr)
        self.assertIs(self.folder.manifests[-1].tekstowo, CoolingState.OFF)

    def test_offline_says_so_and_reads_no_file(self) -> None:
        self.malformed()
        result = self.run_album("--offline")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.provider_settings, [])
        self.assertEqual(_after_youtube(result.stdout), OFFLINE)
        self.assertNotIn("tekstowo", result.stderr)
        self.assertIs(self.folder.manifests[-1].tekstowo, CoolingState.OFF)

    def test_an_hour_old_refusal_leaves_it_out_and_says_until_when(self) -> None:
        cool_down = self.refused(1)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertIsNone(self.setup())
        until = lyc.format_utc(run_fakes.STARTED + timedelta(hours=23))
        self.assertEqual(lyc.format_utc(cool_down.until), until)
        self.assertEqual(
            _after_youtube(result.stdout), f"tekstowo: cooling down until {until}"
        )
        self.assertIs(self.folder.manifests[-1].tekstowo, CoolingState.COOLING_DOWN)

    def test_its_cool_down_leaves_youtube_music_on(self) -> None:
        self.refused(1)
        result = self.run_album()
        self.assertIsNotNone(self.settings().youtube_music)
        self.assertEqual(_after(result.stdout, "bandcamp: "), "youtube music: on")
        manifest = self.folder.manifests[-1]
        self.assertIs(manifest.youtube_music, CoolingState.ON)
        self.assertIs(manifest.tekstowo, CoolingState.COOLING_DOWN)

    def test_a_malformed_file_is_one_warning_naming_it_and_the_step_stays_on(
        self,
    ) -> None:
        self.malformed()
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        warnings = [line for line in result.stderr.splitlines() if "tekstowo" in line]
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("tekstowo: "))
        self.assertIn(str(self.state), warnings[0])
        self.assertIsNotNone(self.setup())
        self.assertEqual(_after_youtube(result.stdout), ON)

    def test_the_summary_closes_with_its_seven_lines(self) -> None:
        result = self.run_album()
        undo = f"album.py --album {shlex.quote(str(self.album))} --mode undo"
        self.assertEqual(
            result.stdout.splitlines()[-7:],
            [
                "synced 0 · unsynced 0 · unchanged 0 · not found 1 · "
                "no lyrics expected 0 · silent 0 · kept existing 0 · removed 0 · "
                "errors 0 · flagged 0 · genius: absent",
                "bandcamp: on",
                "youtube music: on",
                ON,
                "silence check: off (no ffmpeg)",
                "providers: LRCLIB 0/1 · NetEase - · YouTube Music - · Bandcamp - · "
                "Genius - · Tekstowo -",
                f"backup: .lyricsmith/{run_fakes.RUN_ID}/   undo: {undo}",
            ],
        )

    def test_the_modes_that_ask_no_provider_refuse_it(self) -> None:
        for mode in ("check", "undo", "strip"):
            with self.subTest(mode=mode):
                result = self.main(
                    "--album", str(self.album), "--mode", mode, "--no-tekstowo"
                )
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"--no-tekstowo cannot be used with --mode {mode}", result.stderr
                )
        self.assertEqual(self.provider_settings, [])


class StopTest(cli_fakes.RealWiringCliCase):
    """A stop mid-run: the manifest says stopped at the next write, the summary says why."""

    def setUp(self) -> None:
        """A two-track album, a stopping Tekstowo step, every write recorded."""
        super().setUp()
        fixtures.build_album(
            self.album, (TrackSpec("01 One.mp3"), TrackSpec("02 Two.mp3"))
        )
        self.step = StoppingStep()
        self.providers = {KEY: self.step}
        self.writes: list[lr.Manifest] = []
        self.overrides["write_manifest"] = self.write_manifest

    def write_manifest(self, run_dir: Path, manifest: lr.Manifest) -> None:
        """Record *manifest*, then write it with the real stage."""
        self.writes.append(manifest)
        lmf.write_manifest(run_dir, manifest)

    def stored(self) -> object:
        """The run's ``tekstowo`` field as stored on disk."""
        run_dir = lr.run_folder(self.album, run_fakes.RUN_ID)
        document = json.loads((run_dir / lr.MANIFEST_NAME).read_bytes())
        return document["tekstowo"]

    def test_the_write_after_the_stopping_track_already_says_stopped(self) -> None:
        result = self.run_album()
        self.assertEqual(len(self.step.calls), 1)
        self.assertIs(self.writes[0].tekstowo, CoolingState.STOPPED)
        self.assertIs(self.writes[0].youtube_music, CoolingState.ON)
        self.assertEqual(self.stored(), "stopped")
        self.assertEqual(_after_youtube(result.stdout), STOPPED)
        self.assertNotIn("could not write the cool-down file", result.stderr)

    def test_a_failed_cool_down_write_is_noticed(self) -> None:
        self.step.write_error = "invented write failure"
        result = self.run_album()
        self.assertIn(
            "tekstowo: could not write the cool-down file: invented write failure",
            result.stderr,
        )
        self.assertEqual(_after_youtube(result.stdout), STOPPED)


class CompareChoiceTest(_CoolDownCase):
    """Compare takes the option, asks Tekstowo last by default, and scores and tallies it."""

    def setUp(self) -> None:
        """The two untimed-only tracks; a table of Tekstowo's step alone."""
        super().setUp()
        self.album = self.tmp / "untimed"
        fixtures.build_album(self.album, UNTIMED)
        self.providers = {KEY: run_fakes.TitleProvider(ANSWERS)}

    def compare(self, *extra: str) -> cli_fakes.Result:
        """Run compare on this test's album, with *extra* options."""
        return self.main("--album", str(self.album), "--mode", "compare", *extra)

    def test_by_default_the_table_asks_it_and_the_report_says_on(self) -> None:
        result = self.compare()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        setup = self.setup()
        assert setup is not None
        self.assertEqual(setup.state_path, self.state)
        self.assertEqual(_after_youtube(result.stdout), ON)

    def test_no_tekstowo_is_accepted_and_builds_the_table_without_it(self) -> None:
        result = self.compare("--no-tekstowo")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertIsNone(self.setup())
        self.assertEqual(_after_youtube(result.stdout), SKIPPED)

    def test_a_cooling_down_compare_leaves_it_out(self) -> None:
        self.refused(1)
        result = self.compare()
        self.assertIsNone(self.setup())
        self.assertIn("tekstowo: cooling down until ", result.stdout)

    def test_a_tekstowo_answer_is_scored_and_tallied_last(self) -> None:
        result = self.compare()
        words = lyrics_check.normalize_words
        value = lyrics_check.shown(
            lyrics_check.word_similarity(
                words(fixtures.LIGHTHOUSE_PLAIN), words(fixtures.ORCHARD_PLAIN)
            )
        )
        self.assertIn(f"  Tekstowo {value}", result.stdout)
        absent = (
            lm.Source.LRCLIB,
            lm.Source.NETEASE,
            lm.Source.BANDCAMP,
            lm.Source.YOUTUBE_MUSIC,
            lm.Source.GENIUS,
        )
        tally = lyrics_tally.format_tally(
            (
                *(lyrics_tally.ProviderCount(source, 0, None) for source in absent),
                lyrics_tally.ProviderCount(TEK, 1, 2),
            )
        )
        self.assertEqual(result.stdout.splitlines()[-1], tally)


class CompareRefusalTest(_CoolDownCase):
    """A refusal during compare writes the cool-down file and nothing into the album."""

    def setUp(self) -> None:
        """The two untimed-only tracks; a server refusing every Tekstowo search."""
        super().setUp()
        self.album = self.tmp / "untimed"
        fixtures.build_album(self.album, UNTIMED)
        self.ticks = FakeClock()
        routes = {
            (
                lt.TEKSTOWO_HOST,
                tf.search_path(track.artist or "", track.title or ""),
            ): 429
            for track in UNTIMED
        }
        self.server = tf.TekstowoServer(routes, self.ticks)
        self.enterContext(http_fakes.transport(self.server))

    def build_providers(
        self,
        key: lr.Secret | None,
        deadline_s: float,
        settings: ltable.ProviderSettings = ltable.ProviderSettings(),
    ) -> lm.ProviderTable:
        """The real Tekstowo step, built from the setup the entry point gave."""
        super().build_providers(key, deadline_s, settings)
        setup = settings.tekstowo
        assert setup is not None
        client = lt.TekstowoClient(
            setup.state_path, clock=self.ticks, sleeper=self.ticks.sleep, now=setup.now
        )
        return {KEY: lt.TekstowoProvider(client)}

    def test_the_refusal_is_recorded_and_the_album_is_untouched(self) -> None:
        before = {p: p.read_bytes() for p in self.album.rglob("*") if p.is_file()}
        result = self.main("--album", str(self.album), "--mode", "compare")
        after = {p: p.read_bytes() for p in self.album.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())
        self.assertEqual(len(self.server.requests), 1)
        read = lyc.read_cooldown(self.state, run_fakes.STARTED)
        self.assertEqual(
            read.cool_down, lyc.CoolDown(run_fakes.STARTED, "HTTP 429", UNTIL)
        )
        self.assertEqual(_after_youtube(result.stdout), STOPPED)
