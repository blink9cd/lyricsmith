"""The refusals a track's fetch met: the record, and its trip through the manifest.

A refusal names the provider, the chain step it was asked at and a short detail,
never a reply body, so the detail is held to one line and capped. A manifest written
before the field existed reads as no refusal; a malformed entry is refused as every
other malformed field is. Every value here is invented.
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
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above

_REFUSALS = "refusals"


def _after(document: dict[str, object]) -> dict[str, object]:
    """The first track's ``after`` block of manifest *document*, to edit in place."""
    return cast(dict[str, object], mb.first_track(document)["after"])


class ProviderRefusalTest(fixtures.IsolatedCase):
    """The record itself: its fields and the cap on its detail."""

    def test_an_outcome_holds_no_refusal_by_default(self) -> None:
        self.assertEqual(lr.TrackOutcome(lr.OutcomeStatus.NOT_FOUND).refusals, ())

    def test_a_short_detail_is_kept_as_given(self) -> None:
        refusal = lr.ProviderRefusal(lm.Source.NETEASE, lm.LyricsKind.SYNCED, "-460")
        self.assertEqual(refusal.detail, "-460")

    def test_a_long_detail_is_cut_to_the_cap(self) -> None:
        detail = "HTTP 503 " + "x" * (2 * lr.MAX_REFUSAL_DETAIL_CHARS)
        refusal = lr.ProviderRefusal(lm.Source.LRCLIB, lm.LyricsKind.SYNCED, detail)
        self.assertEqual(len(refusal.detail), lr.MAX_REFUSAL_DETAIL_CHARS)
        self.assertTrue(refusal.detail.startswith("HTTP 503 x"))

    def test_a_detail_is_one_line_with_single_spaces(self) -> None:
        refusal = lr.ProviderRefusal(
            lm.Source.GENIUS, lm.LyricsKind.UNSYNCED, "  HTTP\n429 \t too many  "
        )
        self.assertEqual(refusal.detail, "HTTP 429 too many")

    def test_the_cap_is_idempotent(self) -> None:
        detail = "timeout " * lr.MAX_REFUSAL_DETAIL_CHARS
        once = lr.ProviderRefusal(lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED, detail)
        twice = lr.ProviderRefusal(once.provider, once.step, once.detail)
        self.assertEqual(twice, once)


class RefusalManifestTest(fixtures.IsolatedCase):
    """Refusals round-trip through the manifest, and older manifests still read."""

    def assert_refused(self, document: dict[str, object]) -> None:
        """Assert manifest *document* is refused as malformed."""
        with self.assertRaisesRegex(lm.ManifestSchemaError, "malformed"):
            lmf.parse_manifest(mb.encode(document))

    def test_refusals_round_trip_in_order(self) -> None:
        self.assertTrue(mb.FULL_AFTER.refusals)
        lmf.write_manifest(self.tmp, mb.FULL)
        after = lmf.read_manifest(self.tmp).tracks[0].after
        assert after is not None
        self.assertEqual(after.refusals, mb.FULL_AFTER.refusals)

    def test_a_refusal_is_stored_by_value(self) -> None:
        stored = cast(list[dict[str, object]], _after(mb.document())[_REFUSALS])
        first = mb.FULL_AFTER.refusals[0]
        expected = {"provider": first.provider.value, "step": first.step.value}
        self.assertEqual(stored[0], {**expected, "detail": first.detail})

    def test_a_manifest_written_before_the_field_reads_as_none(self) -> None:
        document = mb.document()
        del _after(document)[_REFUSALS]
        after = lmf.parse_manifest(mb.encode(document)).tracks[0].after
        assert after is not None
        self.assertEqual(after.refusals, ())

    def test_a_refusals_field_that_is_not_a_list_is_refused(self) -> None:
        for value in (None, "HTTP 503", {"provider": "Lrclib"}):
            with self.subTest(value=value):
                document = mb.document()
                _after(document)[_REFUSALS] = value
                self.assert_refused(document)

    def test_a_malformed_refusal_entry_is_refused(self) -> None:
        edits: dict[str, object] = {
            "provider": "Nobody",
            "step": "genius",
            "detail": 503,
        }
        for key, value in edits.items():
            with self.subTest(key=key):
                document = mb.document()
                entry = cast(list[dict[str, object]], _after(document)[_REFUSALS])[0]
                entry[key] = value
                self.assert_refused(document)
                del entry[key]
                self.assert_refused(document)

    def test_an_overlong_stored_detail_reads_capped(self) -> None:
        document = mb.document()
        entry = cast(list[dict[str, object]], _after(document)[_REFUSALS])[0]
        entry["detail"] = "y" * (lr.MAX_REFUSAL_DETAIL_CHARS + 5)
        after = lmf.parse_manifest(mb.encode(document)).tracks[0].after
        assert after is not None
        self.assertEqual(len(after.refusals[0].detail), lr.MAX_REFUSAL_DETAIL_CHARS)
