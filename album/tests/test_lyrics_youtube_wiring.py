"""`--no-youtube-music` and the cool-down: the table a run builds, its lines and its record.

Each test drives the entry point with faked wiring, or with the real wiring and a
provider table built by hand, so no test reaches the network; every cool-down file is
written under the test's own temporary XDG_STATE_HOME. Every title and lyric is invented.
"""

from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from unittest import mock

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
import lyrics_recorder  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tally  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_music as lym  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_provider as lyp  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import youtube_fakes as yf  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import FakeClock  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_answer import LrcAnswer  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_provider_states import (  # noqa: E402 - import follows the sys.path bootstrap above
    CoolingState,
)

YTM = lm.Source.YOUTUBE_MUSIC
SYNCED_KEY = lm.ProviderKey(YTM, lm.LyricsKind.SYNCED)
PLAIN_KEY = lm.ProviderKey(YTM, lm.LyricsKind.UNSYNCED)
#: The line each choice prints after the Bandcamp line.
ON, SKIPPED, OFFLINE = (
    "youtube music: on",
    "youtube music: off (--no-youtube-music)",
    "youtube music: off (offline)",
)
#: When a stop the fakes below make ends: a day after the fixed clock's start.
UNTIL = run_fakes.STARTED + lyc.COOL_DOWN
STOPPED = (
    f"youtube music: stopped after HTTP 429; cooling down until {lyc.format_utc(UNTIL)}"
)
#: Two untimed-only tracks: the first one's answer agrees, the second one's does not.
UNTIMED = (
    TrackSpec("01 A.mp3", title="Beacon Light", txt=fixtures.LIGHTHOUSE_PLAIN),
    TrackSpec("02 B.mp3", title="Mill Wheel", txt=fixtures.LIGHTHOUSE_PLAIN),
)
#: What the plain YouTube Music fake answers, by title.
ANSWERS = {
    "Beacon Light": fixtures.LIGHTHOUSE_EDITED,
    "Mill Wheel": fixtures.ORCHARD_PLAIN,
}


def _after_bandcamp(stdout: str) -> str:
    """The line right after the ``bandcamp:`` line."""
    lines = stdout.splitlines()
    bandcamp = next(i for i, line in enumerate(lines) if line.startswith("bandcamp: "))
    return lines[bandcamp + 1]


@dataclass
class StoppingStep:
    """A YouTube Music step that stops on its first call, as a refusal would."""

    write_error: str | None = None
    stop: lyc.Stop | None = None
    calls: list[lm.TrackQuery] = field(default_factory=list)

    def asks(self, query: lm.TrackQuery) -> bool:
        """True until the step stopped."""
        del query
        return self.stop is None

    def answer_all_kinds(self, query: lm.TrackQuery) -> LrcAnswer:
        """Stop for the run, as a ``429`` would, and raise the stop."""
        self.calls.append(query)
        self.stop = lyc.Stop("HTTP 429", UNTIL, self.write_error)
        raise lym.YouTubeMusicStopped("HTTP 429")

    def __call__(self, query: lm.TrackQuery) -> str | None:  # pragma: no cover
        # Never called: the chain asks a provider that has answer_all_kinds
        # through that method.
        """The plain step's text (`answer_all_kinds`)."""
        return self.answer_all_kinds(query).text(lm.LyricsKind.UNSYNCED)

    def provider_stop(self) -> lyc.Stop | None:
        """What stopped the step, or None."""
        return self.stop


class _CoolDownCase(cli_fakes.CliCase):
    """A one-track album; the cool-down file under this test's XDG_STATE_HOME."""

    def setUp(self) -> None:
        """A one-track album."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))
        self.state = lyc.cooldown_path(self.wiring().environ, lm.Source.YOUTUBE_MUSIC)

    def refused(self, hours_ago: float) -> lyc.CoolDown:
        """Write a refusal *hours_ago* before the fixed clock's start."""
        moment = run_fakes.STARTED - timedelta(hours=hours_ago)
        return lyc.write_cooldown(self.state, moment, "HTTP 429")

    def malformed(self) -> None:
        """Write a cool-down file that does not hold a record."""
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_bytes(b"not a record")

    def setup(self) -> lyc.CoolingSetup | None:
        """The YouTube Music setup the one table built was given."""
        self.assertEqual(len(self.provider_settings), 1)
        return self.provider_settings[0].youtube_music


class RunChoiceTest(_CoolDownCase):
    """A run builds its table with YouTube Music on unless told otherwise, and says which."""

    def test_by_default_the_table_asks_it_with_the_state_file_and_the_clock(
        self,
    ) -> None:
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        setup = self.setup()
        assert setup is not None
        self.assertEqual(setup.state_path, self.state)
        self.assertTrue(setup.state_path.is_relative_to(self.home))
        self.assertEqual(setup.now(), run_fakes.STARTED)
        self.assertEqual(_after_bandcamp(result.stdout), ON)
        self.assertIs(self.folder.manifests[-1].youtube_music, CoolingState.ON)

    def test_no_youtube_music_builds_the_table_without_it_and_reads_no_file(
        self,
    ) -> None:
        self.malformed()
        result = self.run_album("--no-youtube-music")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertIsNone(self.setup())
        self.assertEqual(_after_bandcamp(result.stdout), SKIPPED)
        self.assertNotIn("youtube music", result.stderr)
        self.assertIs(self.folder.manifests[-1].youtube_music, CoolingState.OFF)

    def test_offline_says_so_and_reads_no_file(self) -> None:
        self.malformed()
        result = self.run_album("--offline", "--no-youtube-music")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.provider_settings, [])
        self.assertEqual(_after_bandcamp(result.stdout), OFFLINE)
        self.assertNotIn("youtube music", result.stderr)

    def test_an_hour_old_refusal_leaves_it_out_and_says_until_when(self) -> None:
        cool_down = self.refused(1)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertIsNone(self.setup())
        until = lyc.format_utc(run_fakes.STARTED + timedelta(hours=23))
        self.assertEqual(lyc.format_utc(cool_down.until), until)
        self.assertEqual(
            _after_bandcamp(result.stdout), f"youtube music: cooling down until {until}"
        )
        manifest = self.folder.manifests[-1]
        self.assertIs(manifest.youtube_music, CoolingState.COOLING_DOWN)

    def test_a_refusal_older_than_a_day_is_over(self) -> None:
        self.refused(25)
        result = self.run_album()
        self.assertIsNotNone(self.setup())
        self.assertEqual(_after_bandcamp(result.stdout), ON)

    def test_a_malformed_file_is_one_warning_naming_it_and_the_step_stays_on(
        self,
    ) -> None:
        self.malformed()
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        warnings = [
            line for line in result.stderr.splitlines() if "youtube music:" in line
        ]
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("youtube music: "))
        self.assertIn(str(self.state), warnings[0])
        self.assertIsNotNone(self.setup())
        self.assertEqual(_after_bandcamp(result.stdout), ON)

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
                ON,
                "tekstowo: on",
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
                    "--album", str(self.album), "--mode", mode, "--no-youtube-music"
                )
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"--no-youtube-music cannot be used with --mode {mode}",
                    result.stderr,
                )
        self.assertEqual(self.provider_settings, [])


class RecorderTest(fixtures.IsolatedCase):
    """The recorder writes the state its reader gives, else the manifest's own."""

    def record(self, reader: Callable[[], CoolingState] | None) -> lr.Manifest:
        """Complete a run whose manifest says cooling down; the manifest written."""
        folder = run_fakes.FakeRunFolder()
        manifest = lr.Manifest(
            "r",
            run_fakes.LANG,
            lr.RunState.BACKED_UP,
            lr.GeniusState.ABSENT,
            (),
            youtube_music=CoolingState.COOLING_DOWN,
        )
        stages = run_fakes.make_stages(folder)
        recorder = lyrics_recorder.RunRecorder(
            self.tmp, manifest, stages, lambda _line: None, youtube_music=reader
        )
        recorder.complete()
        [written] = folder.manifests
        return written

    def test_with_no_reader_the_manifests_own_state_is_kept(self) -> None:
        written = self.record(None)
        self.assertIs(written.youtube_music, CoolingState.COOLING_DOWN)

    def test_a_readers_state_replaces_it(self) -> None:
        written = self.record(lambda: CoolingState.STOPPED)
        self.assertIs(written.youtube_music, CoolingState.STOPPED)


class StopTest(cli_fakes.RealWiringCliCase):
    """A stop mid-run: the manifest says stopped at the next write, the summary says why."""

    def setUp(self) -> None:
        """A two-track album, a stopping step under both keys, every write recorded."""
        super().setUp()
        fixtures.build_album(
            self.album, (TrackSpec("01 One.mp3"), TrackSpec("02 Two.mp3"))
        )
        self.step = StoppingStep()
        self.providers = {SYNCED_KEY: self.step, PLAIN_KEY: self.step}
        self.writes: list[lr.Manifest] = []
        self.overrides["write_manifest"] = self.write_manifest

    def write_manifest(self, run_dir: Path, manifest: lr.Manifest) -> None:
        """Record *manifest*, then write it with the real stage."""
        self.writes.append(manifest)
        lmf.write_manifest(run_dir, manifest)

    def stored(self) -> object:
        """The run's manifest field as stored on disk."""
        run_dir = lr.run_folder(self.album, run_fakes.RUN_ID)
        document = json.loads((run_dir / lr.MANIFEST_NAME).read_bytes())
        return document["youtube_music"]

    def test_the_write_after_the_stopping_track_already_says_stopped(self) -> None:
        result = self.run_album()
        self.assertEqual(len(self.step.calls), 1)
        self.assertIs(self.writes[0].youtube_music, CoolingState.STOPPED)
        self.assertEqual(self.stored(), "stopped")
        self.assertEqual(_after_bandcamp(result.stdout), STOPPED)
        self.assertNotIn("could not write the cool-down file", result.stderr)

    def test_a_failed_cool_down_write_is_noticed(self) -> None:
        self.step.write_error = "invented write failure"
        result = self.run_album()
        self.assertIn(
            "youtube music: could not write the cool-down file: invented write failure",
            result.stderr,
        )
        self.assertEqual(_after_bandcamp(result.stdout), STOPPED)

    def test_the_notice_survives_an_album_that_then_raises(self) -> None:
        self.step.write_error = "invented write failure"

        def failing_write(run_dir: Path, manifest: lr.Manifest) -> None:
            if self.step.stop is not None:  # the failure comes after the stop
                raise RuntimeError("invented album failure")
            lmf.write_manifest(run_dir, manifest)

        self.overrides["write_manifest"] = failing_write
        notices: list[str] = []
        # main's own stream capture would swallow stderr when the run raises, so the
        # notice is read where it is sent.
        with (
            mock.patch.object(album, "_notice", notices.append),
            self.assertRaises(RuntimeError),
        ):
            self.run_album()
        self.assertIn(
            "youtube music: could not write the cool-down file: invented write failure",
            notices,
        )

    def test_a_made_run_whose_cool_down_notice_is_lost_is_exit_3(self) -> None:
        self.step.write_error = "invented write failure"
        with mock.patch.object(album, "_notice", _closed_for_cool_down_notice):
            result = self.run_album()
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)

    def test_an_album_failure_outranks_a_lost_cool_down_notice(self) -> None:
        self.step.write_error = "invented write failure"

        def failing_write(run_dir: Path, manifest: lr.Manifest) -> None:
            if self.step.stop is not None:  # the failure comes after the stop
                raise RuntimeError("invented album failure")
            lmf.write_manifest(run_dir, manifest)

        self.overrides["write_manifest"] = failing_write
        with (
            mock.patch.object(album, "_notice", _closed_for_cool_down_notice),
            self.assertRaises(RuntimeError),
        ):
            self.run_album()


class CompareStopTest(cli_fakes.RealWiringCliCase):
    """A compare whose stop could not write its cool-down file, said or lost."""

    def setUp(self) -> None:
        """The two untimed-only tracks; a stopping step whose cool-down write fails."""
        super().setUp()
        fixtures.build_album(self.album, UNTIMED)
        self.step = StoppingStep(write_error="invented write failure")
        self.providers = {SYNCED_KEY: self.step, PLAIN_KEY: self.step}

    def compare(self) -> cli_fakes.Result:
        """Run compare on this test's album."""
        return self.main("--album", str(self.album), "--mode", "compare")

    def test_the_failed_write_is_noticed(self) -> None:
        result = self.compare()
        self.assertIn(
            "youtube music: could not write the cool-down file", result.stderr
        )

    def test_a_compare_whose_cool_down_notice_is_lost_is_exit_3(self) -> None:
        with mock.patch.object(album, "_notice", _closed_for_cool_down_notice):
            result = self.compare()
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)


def _closed_for_cool_down_notice(text: str) -> None:
    """A stderr that takes every line but the cool-down notice, which it refuses."""
    if text.startswith("youtube music: could not write the cool-down file"):
        raise OSError("invented closed stderr")


class CompareChoiceTest(_CoolDownCase):
    """Compare takes the option, asks YouTube Music by default, and scores and tallies it."""

    def setUp(self) -> None:
        """The two untimed-only tracks; a table of YouTube Music's plain step alone."""
        super().setUp()
        self.album = self.tmp / "untimed"
        fixtures.build_album(self.album, UNTIMED)
        self.providers = {PLAIN_KEY: run_fakes.TitleProvider(ANSWERS)}

    def compare(self, *extra: str) -> cli_fakes.Result:
        """Run compare on this test's album, with *extra* options."""
        return self.main("--album", str(self.album), "--mode", "compare", *extra)

    def test_by_default_the_table_asks_it_and_the_report_says_on(self) -> None:
        result = self.compare()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        setup = self.setup()
        assert setup is not None
        self.assertEqual(setup.state_path, self.state)
        self.assertEqual(_after_bandcamp(result.stdout), ON)

    def test_no_youtube_music_is_accepted_and_builds_the_table_without_it(
        self,
    ) -> None:
        result = self.compare("--no-youtube-music")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertIsNone(self.setup())
        self.assertEqual(_after_bandcamp(result.stdout), SKIPPED)

    def test_a_cooling_down_compare_leaves_it_out(self) -> None:
        self.refused(1)
        result = self.compare()
        self.assertIsNone(self.setup())
        self.assertIn("youtube music: cooling down until ", result.stdout)

    def test_a_youtube_music_answer_is_scored_and_tallied(self) -> None:
        result = self.compare()
        words = lyrics_check.normalize_words
        value = lyrics_check.shown(
            lyrics_check.word_similarity(
                words(fixtures.LIGHTHOUSE_PLAIN), words(fixtures.ORCHARD_PLAIN)
            )
        )
        self.assertIn(f"  YouTube Music {value}", result.stdout)
        absent = (lm.Source.LRCLIB, lm.Source.NETEASE, lm.Source.BANDCAMP)
        tally = lyrics_tally.format_tally(
            (
                *(lyrics_tally.ProviderCount(source, 0, None) for source in absent),
                lyrics_tally.ProviderCount(YTM, 1, 2),
                lyrics_tally.ProviderCount(lm.Source.GENIUS, 0, None),
                lyrics_tally.ProviderCount(lm.Source.TEKSTOWO, 0, None),
            )
        )
        self.assertEqual(result.stdout.splitlines()[-1], tally)


class CompareRefusalTest(_CoolDownCase):
    """A refusal during compare writes the cool-down file and nothing into the album."""

    def setUp(self) -> None:
        """The two untimed-only tracks; a server refusing YouTube Music's first request."""
        super().setUp()
        self.album = self.tmp / "untimed"
        fixtures.build_album(self.album, UNTIMED)
        self.ticks = FakeClock()
        self.server = yf.ScriptedServer([(429, b"", {})], self.ticks)
        self.enterContext(http_fakes.transport(self.server))

    def build_providers(
        self,
        key: lr.Secret | None,
        deadline_s: float,
        settings: ltable.ProviderSettings = ltable.ProviderSettings(),
    ) -> lm.ProviderTable:
        """The real YouTube Music step, built from the setup the entry point gave."""
        super().build_providers(key, deadline_s, settings)
        setup = settings.youtube_music
        assert setup is not None
        client = lym.YouTubeMusicClient(
            setup.state_path, clock=self.ticks, sleeper=self.ticks.sleep, now=setup.now
        )
        step = lyp.YouTubeMusicProvider(client)
        return {SYNCED_KEY: step, PLAIN_KEY: step}

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
        self.assertEqual(_after_bandcamp(result.stdout), STOPPED)
