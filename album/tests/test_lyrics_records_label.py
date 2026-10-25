"""The machine-transcript source in the manifest, compare's provenance and the summary.

The new source reads and writes through the manifest with its schema unchanged, and is
never one of the providers. The summary's source column is pinned by the
served-transcript run test, which reads the real CLI's output.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import cast

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_provenance as lpv  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above

MT = lm.Source.MACHINE_TRANSCRIPT


class TranscriptRecordTest(fixtures.IsolatedCase):
    """The new value reads, writes and stays out of the providers."""

    def test_a_manifest_naming_it_reads(self) -> None:
        document = mb.document()
        cast(dict[str, object], mb.first_track(document)["after"])["source"] = (
            "machine transcript"
        )
        after = lmf.parse_manifest(mb.encode(document)).tracks[0].after
        assert after is not None
        self.assertIs(after.source, MT)

    def test_it_round_trips_through_the_run_folder(self) -> None:
        track = mb.FULL.tracks[0]
        assert track.after is not None
        after = dataclasses.replace(track.after, source=MT)
        manifest = dataclasses.replace(
            mb.FULL,
            tracks=(dataclasses.replace(track, after=after), *mb.FULL.tracks[1:]),
        )
        lmf.write_manifest(self.tmp, manifest)
        self.assertEqual(lmf.read_manifest(self.tmp), manifest)

    def test_it_is_never_a_provider(self) -> None:
        self.assertNotIn(MT, lpv.PROVIDER_SOURCES)
        self.assertEqual(lr.MANIFEST_SCHEMA, 1)
