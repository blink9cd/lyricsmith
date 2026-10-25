"""A silent track's record: its manifest fields, its row, and the tests' isolation from ffmpeg.

The two ``after`` fields and the header's ``silence_check`` round-trip through the
manifest codec, and a manifest written before them reads them as absent and the check
as off. `describe_status` names a silent track's peak and a failed check's reason.
Every fixture MP3 is silent, so no test may reach a real ffmpeg: `fixtures.IsolatedCase`
patches the lookup to find none.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_silence  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_outcomes import describe_status  # noqa: E402 - follows the bootstrap
from lyrics_provider_states import SilenceCheck  # noqa: E402 - follows the bootstrap

SILENT = lr.TrackOutcome(lr.OutcomeStatus.SILENT, peak_dbfs=-91.0)
FAILED = lr.TrackOutcome(lr.OutcomeStatus.NOT_FOUND, silence_failed="ffmpeg exit 1")


def _with_afters(*afters: lr.TrackOutcome) -> lr.Manifest:
    """The full manifest, its tracks replaced by one per outcome in *afters*."""
    tracks = tuple(
        lr.ManifestTrack(f"{index:02}.mp3", (2, 4), lr.TrackBefore((), ()), after)
        for index, after in enumerate(afters, start=1)
    )
    return dataclasses.replace(mb.FULL, tracks=tracks)


def _document(manifest: lr.Manifest) -> dict[str, object]:
    """*manifest* as the JSON object the codec writes."""
    document: dict[str, object] = json.loads(lmf.manifest_bytes(manifest))
    return document


class SilentFieldsTest(fixtures.IsolatedCase):
    """The two ``after`` fields and the header field, through the manifest codec."""

    def test_a_round_trip_keeps_the_peak_the_failure_and_the_check(self) -> None:
        manifest = dataclasses.replace(
            _with_afters(SILENT, FAILED), silence_check=SilenceCheck.ON
        )
        self.assertEqual(_document(manifest)["silence_check"], "on")
        self.assertEqual(lmf.parse_manifest(lmf.manifest_bytes(manifest)), manifest)

    def test_an_old_manifest_reads_no_peak_no_failure_and_the_check_off(self) -> None:
        document = _document(_with_afters(SILENT))
        del document["silence_check"]
        after = mb.first_track(document)["after"]
        assert isinstance(after, dict)
        del after["peak_dbfs"], after["silence_failed"]
        manifest = lmf.parse_manifest(mb.encode(document))
        (track,) = manifest.tracks
        assert track.after is not None
        self.assertEqual(
            (track.after.peak_dbfs, track.after.silence_failed), (None, None)
        )
        self.assertIs(manifest.silence_check, SilenceCheck.OFF)

    def test_a_null_check_reads_off(self) -> None:
        document = _document(mb.FULL)
        document["silence_check"] = None
        self.assertIs(
            lmf.parse_manifest(mb.encode(document)).silence_check, SilenceCheck.OFF
        )

    def test_a_whole_number_peak_reads_as_a_float(self) -> None:
        document = _document(_with_afters(SILENT))
        after = mb.first_track(document)["after"]
        assert isinstance(after, dict)
        after["peak_dbfs"] = -91
        (track,) = lmf.parse_manifest(mb.encode(document)).tracks
        assert track.after is not None
        self.assertEqual(track.after.peak_dbfs, -91.0)
        self.assertIs(type(track.after.peak_dbfs), float)

    def test_a_peak_that_is_not_a_finite_number_is_refused(self) -> None:
        for value in ("-91", True, "NaN", -(10**400)):
            with self.subTest(value=value):
                document = _document(_with_afters(SILENT))
                after = mb.first_track(document)["after"]
                assert isinstance(after, dict)
                after["peak_dbfs"] = value
                data = mb.encode(document)
                if value == "NaN":
                    # JSON's own NaN token, which json.loads reads as a float.
                    data = data.replace(b'"NaN"', b"NaN")
                with self.assertRaisesRegex(lm.ManifestSchemaError, "peak_dbfs"):
                    lmf.parse_manifest(data)


class SilentRowTest(fixtures.IsolatedCase):
    """What a row says of a silent track and of a failed check."""

    def test_a_silent_track_names_its_peak(self) -> None:
        self.assertEqual(describe_status(SILENT), "silent (peak -91.0 dBFS)")

    def test_a_failed_check_names_its_reason(self) -> None:
        self.assertEqual(
            describe_status(FAILED), "not found (silence check failed: ffmpeg exit 1)"
        )


class FfmpegIsolationTest(fixtures.IsolatedCase):
    """No test finds an ffmpeg, through the silence module or the real wiring."""

    def test_the_lookup_finds_none(self) -> None:
        self.assertIsNone(lyrics_silence.find_ffmpeg())

    def test_the_real_wiring_finds_none(self) -> None:
        self.assertIsNone(album.real_wiring().find_ffmpeg())

    def test_the_real_wiring_asks_the_silence_modules_lookup(self) -> None:
        with mock.patch.object(
            lyrics_silence, "find_ffmpeg", return_value="/probe/ffmpeg"
        ):
            self.assertEqual(album.real_wiring().find_ffmpeg(), "/probe/ffmpeg")
