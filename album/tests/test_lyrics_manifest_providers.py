"""The manifest's optional provider states: read when present, ``off`` when absent or null.

A manifest written before the field existed must still parse, check and undo, under the
same schema. The real-folder case runs a strip with the real wiring, then takes the
field out of the manifest it wrote. Every track here is invented.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_provider_states import (  # noqa: E402 - import follows the sys.path bootstrap above
    BandcampState,
    CoolingState,
)


def _document(manifest: lr.Manifest) -> dict[str, object]:
    """*manifest* as the JSON object the codec writes."""
    document: dict[str, object] = json.loads(lmf.manifest_bytes(manifest))
    return document


class BandcampFieldTest(fixtures.IsolatedCase):
    """The field's codec, on the full manifest the other manifest suites use."""

    def test_a_manifest_without_the_field_reads_off_under_schema_one(self) -> None:
        document = _document(mb.FULL)
        del document["bandcamp"]
        manifest = lmf.parse_manifest(mb.encode(document))
        self.assertEqual((manifest.bandcamp, manifest.schema), (BandcampState.OFF, 1))

    def test_null_reads_off(self) -> None:
        document = _document(mb.FULL)
        document["bandcamp"] = None
        manifest = lmf.parse_manifest(mb.encode(document))
        self.assertIs(manifest.bandcamp, BandcampState.OFF)

    def test_an_unknown_state_is_refused(self) -> None:
        document = _document(mb.FULL)
        document["bandcamp"] = "maybe"
        with self.assertRaisesRegex(lm.ManifestSchemaError, r"manifest\.bandcamp"):
            lmf.parse_manifest(mb.encode(document))

    def test_a_round_trip_keeps_on(self) -> None:
        manifest = dataclasses.replace(mb.FULL, bandcamp=BandcampState.ON)
        self.assertEqual(_document(manifest)["bandcamp"], "on")
        self.assertEqual(lmf.parse_manifest(lmf.manifest_bytes(manifest)), manifest)


class YouTubeMusicFieldTest(fixtures.IsolatedCase):
    """The ``youtube_music`` field's codec, on the same full manifest."""

    def test_a_manifest_without_the_field_reads_off(self) -> None:
        document = _document(mb.FULL)
        del document["youtube_music"]
        manifest = lmf.parse_manifest(mb.encode(document))
        self.assertEqual(
            (manifest.youtube_music, manifest.schema), (CoolingState.OFF, 1)
        )

    def test_null_reads_off(self) -> None:
        document = _document(mb.FULL)
        document["youtube_music"] = None
        manifest = lmf.parse_manifest(mb.encode(document))
        self.assertIs(manifest.youtube_music, CoolingState.OFF)

    def test_each_state_round_trips(self) -> None:
        for state in CoolingState:
            with self.subTest(state=state):
                manifest = dataclasses.replace(mb.FULL, youtube_music=state)
                self.assertEqual(_document(manifest)["youtube_music"], state.value)
                parsed = lmf.parse_manifest(lmf.manifest_bytes(manifest))
                self.assertEqual(parsed, manifest)
        values = [state.value for state in CoolingState]
        self.assertEqual(values, ["on", "off", "cooling down", "stopped"])

    def test_an_unknown_state_is_refused(self) -> None:
        document = _document(mb.FULL)
        document["youtube_music"] = "blocked"
        with self.assertRaisesRegex(lm.ManifestSchemaError, r"manifest\.youtube_music"):
            lmf.parse_manifest(mb.encode(document))


class TekstowoFieldTest(fixtures.IsolatedCase):
    """The ``tekstowo`` field's codec, on the same full manifest."""

    def test_a_manifest_without_the_field_reads_off(self) -> None:
        document = _document(mb.FULL)
        del document["tekstowo"]
        manifest = lmf.parse_manifest(mb.encode(document))
        self.assertEqual((manifest.tekstowo, manifest.schema), (CoolingState.OFF, 1))

    def test_null_reads_off(self) -> None:
        document = _document(mb.FULL)
        document["tekstowo"] = None
        manifest = lmf.parse_manifest(mb.encode(document))
        self.assertIs(manifest.tekstowo, CoolingState.OFF)

    def test_each_state_round_trips(self) -> None:
        for state in CoolingState:
            with self.subTest(state=state):
                manifest = dataclasses.replace(mb.FULL, tekstowo=state)
                self.assertEqual(_document(manifest)["tekstowo"], state.value)
                parsed = lmf.parse_manifest(lmf.manifest_bytes(manifest))
                self.assertEqual(parsed, manifest)

    def test_an_unknown_state_is_refused(self) -> None:
        document = _document(mb.FULL)
        document["tekstowo"] = "blocked"
        with self.assertRaisesRegex(lm.ManifestSchemaError, r"manifest\.tekstowo"):
            lmf.parse_manifest(mb.encode(document))


class OlderManifestTest(cli_fakes.RealWiringCliCase):
    """A real run folder whose manifest lacks the field still checks and undoes."""

    def test_check_and_undo_read_it(self) -> None:
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))
        result = self.main("--album", str(self.album), "--mode", "strip")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        path = lr.run_folder(self.album, run_fakes.RUN_ID) / lr.MANIFEST_NAME
        document = json.loads(path.read_bytes())
        del document["bandcamp"]
        del document["youtube_music"]
        del document["tekstowo"]
        path.write_bytes(mb.encode(document))
        for mode in ("check", "undo"):
            with self.subTest(mode=mode):
                result = self.main("--album", str(self.album), "--mode", mode)
                self.assertEqual(result.code, album.EXIT_OK, result.stderr)
