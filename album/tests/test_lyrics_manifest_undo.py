"""The manifest's undo-facing records: the run's sidecar hashes and undo's displaced copies.

A run records the SHA-256 of each sidecar it left, and undo records each copy it
displaced, by its path in the album. Both are read back from a file anything may have
edited, so a hash that is not 64 lowercase hex characters, a sidecar name with a folder
in it, or a copy path climbing out of the album is refused like any other malformed
field.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above


def _hashes(document: dict[str, object]) -> list[dict[str, object]]:
    """The first track's recorded sidecar hashes, to edit in place."""
    after = cast(dict[str, list[dict[str, object]]], mb.first_track(document)["after"])
    return after["sidecar_hashes"]


class UndoRecordRefusalTest(fixtures.IsolatedCase):
    """A malformed hash or displaced path is a ManifestSchemaError."""

    def assert_refused(self, document: object) -> None:
        """Assert *document* is refused as malformed."""
        with self.assertRaises(lm.ManifestSchemaError) as caught:
            lmf.parse_manifest(mb.encode(document))
        self.assertIn("malformed", str(caught.exception))

    def test_a_sidecar_hash_is_64_lowercase_hex_characters(self) -> None:
        for value in ("ab" * 31, "AB" * 32, "zz" * 32, 7, None):
            with self.subTest(value=value):
                document = mb.document()
                _hashes(document)[0]["sha256"] = value
                self.assert_refused(document)

    def test_a_hashed_sidecar_is_a_plain_file_name(self) -> None:
        document = mb.document()
        _hashes(document)[0]["name"] = "CD2/01.txt"
        self.assert_refused(document)

    def test_a_displaced_copy_stays_inside_the_album(self) -> None:
        for path in ("../01.lrc", "/abs/01.lrc", 3):
            with self.subTest(path=path):
                document = mb.document()
                mb.first_track(document)["displaced"] = [path]
                self.assert_refused(document)

    def test_both_records_are_required(self) -> None:
        document = mb.document()
        del mb.first_track(document)["displaced"]
        self.assert_refused(document)
        document = mb.document()
        del cast(dict[str, object], mb.first_track(document)["after"])["sidecar_hashes"]
        self.assert_refused(document)
