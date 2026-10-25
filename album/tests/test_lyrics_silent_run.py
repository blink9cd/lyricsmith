"""Silent tracks through a run: a miss whose audio is silent ends ``silent``.

Each test drives the entry point through `cli_fakes.CliCase`, its wiring given an
ffmpeg and a fake measuring stage, so no real ffmpeg runs. Only a miss is measured: a
found, kept or errored track never is, and a dry run measures nothing. Without an
ffmpeg nothing is measured and the summary says so. Every title and lyric here is
invented.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_silence as ls  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_provider_states import SilenceCheck  # noqa: E402 - follows the bootstrap

STANDUP = ("Stand-up Comedy",)
#: The run id a second run in one test takes, as the fake run folder keeps one per id.
_SECOND_RUN_ID = "20261014T153013Z"


class SilentRunCase(cli_fakes.CliCase):
    """A run whose wiring finds an ffmpeg, measuring through a fake stage."""

    levels: dict[str, ls.Level]

    def setUp(self) -> None:
        """No level set yet, so every track measures as sounding; nothing measured."""
        super().setUp()
        self.levels = {}
        self.measured: list[str] = []
        self.overrides["measure_level"] = self._measure

    def _measure(self, track: Path, ffmpeg: str) -> ls.Level:
        """Record *track*; its level from this test's table, else a sounding peak."""
        self.measured.append(track.name)
        return self.levels.get(track.name, ls.Peak(-12.0))

    def wiring(self) -> album.Wiring:
        """The faked wiring, finding an ffmpeg."""
        return dataclasses.replace(super().wiring(), find_ffmpeg=lambda: "/x/ffmpeg")

    def _after(self, rel: str) -> lr.TrackOutcome:
        """*rel*'s outcome in the newest manifest."""
        after = next(t.after for t in self.folder.manifests[-1].tracks if t.path == rel)
        assert after is not None
        return after


class SilentRunTest(SilentRunCase):
    """Which tracks are measured, and what each end of a measurement records."""

    def test_a_silent_miss_is_silent_a_sounding_one_not_found(self) -> None:
        fixtures.build_album(
            self.album, (TrackSpec("01 Gap.mp3"), TrackSpec("02 Song.mp3"))
        )
        self.levels["01 Gap.mp3"] = ls.Peak(-91.0)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertIs(self._after("01 Gap.mp3").status, lr.OutcomeStatus.SILENT)
        self.assertEqual(self._after("01 Gap.mp3").peak_dbfs, -91.0)
        self.assertIs(self._after("02 Song.mp3").status, lr.OutcomeStatus.NOT_FOUND)
        self.assertIsNone(self._after("02 Song.mp3").peak_dbfs)
        self.assertIn("not found 1 · no lyrics expected 0 · silent 1", result.stdout)
        self.assertIn("silent (peak -91.0 dBFS)", result.stdout)
        self.assertIn("silence check: on", result.stdout)
        self.assertIs(self.folder.manifests[-1].silence_check, SilenceCheck.ON)

    def test_silent_comes_before_the_genre(self) -> None:
        fixtures.build_album(self.album, (TrackSpec("01 Bit.mp3", genres=STANDUP),))
        self.levels["01 Bit.mp3"] = ls.Peak(-91.0)
        result = self.run_album()
        self.assertIs(self._after("01 Bit.mp3").status, lr.OutcomeStatus.SILENT)
        self.assertIn("no lyrics expected 0 · silent 1", result.stdout)

    def test_a_found_track_is_never_measured(self) -> None:
        self.providers = run_fakes.table({"The Song": fixtures.LIGHTHOUSE_LRC})
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3", title="The Song"),))
        self.levels["01 Song.mp3"] = ls.Peak(-91.0)
        self.run_album()
        self.assertIs(self._after("01 Song.mp3").status, lr.OutcomeStatus.WRITTEN)
        self.assertEqual(self.measured, [])

    def test_kept_and_error_are_never_measured(self) -> None:
        # The first run embeds the sidecar; with it removed, a fresh miss keeps the frames.
        mp3 = fixtures.build_album(
            self.album, (TrackSpec("01 Gap.mp3", lrc=fixtures.LIGHTHOUSE_LRC),)
        )[0]
        self.levels["01 Gap.mp3"] = ls.Peak(-91.0)
        self.run_album("--offline")
        mp3.with_suffix(".lrc").unlink()
        self.folder.run_id = _SECOND_RUN_ID
        self.run_album("--fresh")
        self.assertIs(self._after("01 Gap.mp3").status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertEqual(self.measured, [])
        self.providers = run_fakes.failing_table()
        fixtures.build_album(self.tmp / "other", (TrackSpec("01 Gap.mp3"),))
        self.album = self.tmp / "other"
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIs(self._after("01 Gap.mp3").status, lr.OutcomeStatus.ERROR)
        self.assertEqual(self.measured, [])

    def test_a_failed_measurement_names_its_reason(self) -> None:
        fixtures.build_album(self.album, (TrackSpec("01 Gap.mp3"),))
        self.levels["01 Gap.mp3"] = ls.MeasureFailed("timed out")
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK)
        after = self._after("01 Gap.mp3")
        self.assertIs(after.status, lr.OutcomeStatus.NOT_FOUND)
        self.assertEqual((after.silence_failed, after.peak_dbfs), ("timed out", None))
        self.assertIn("not found (silence check failed: timed out)", result.stdout)

    def test_offline_still_measures(self) -> None:
        fixtures.build_album(
            self.album, (TrackSpec("01 Gap.mp3"), TrackSpec("02 Gap.mp3"))
        )
        self.levels = {name: ls.Peak(-91.0) for name in ("01 Gap.mp3", "02 Gap.mp3")}
        result = self.run_album("--offline")
        self.assertEqual(self.measured, ["01 Gap.mp3", "02 Gap.mp3"])
        self.assertIs(self._after("02 Gap.mp3").status, lr.OutcomeStatus.SILENT)
        self.assertIn("silent 2", result.stdout)

    def test_dry_run_measures_nothing(self) -> None:
        fixtures.build_album(self.album, (TrackSpec("01 Gap.mp3"),))
        self.levels["01 Gap.mp3"] = ls.Peak(-91.0)
        planned = self.run_album("--dry-run").stdout
        self.assertEqual(self.measured, [])
        self.assertNotIn("silence check", planned)
        self.assertNotIn("silent", planned)


class NoFfmpegTest(cli_fakes.CliCase):
    """The default wiring finds no ffmpeg, so a run measures nothing."""

    def test_without_ffmpeg_nothing_is_measured_and_the_summary_says_so(self) -> None:
        fixtures.build_album(self.album, (TrackSpec("01 Gap.mp3"),))
        result = self.run_album()
        (track,) = self.folder.manifests[-1].tracks
        assert track.after is not None
        self.assertIs(track.after.status, lr.OutcomeStatus.NOT_FOUND)
        self.assertIsNone(track.after.silence_failed)
        self.assertIn("silence check: off (no ffmpeg)", result.stdout)
        self.assertIs(self.folder.manifests[-1].silence_check, SilenceCheck.OFF)
