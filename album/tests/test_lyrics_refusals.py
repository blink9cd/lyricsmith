"""The refusals a track's fetch met: the record, and its trip through the manifest.

A refusal names the provider, the chain step it was asked at and a short detail,
never a reply body, so the detail is held to one line and capped, and its facts: its
class, status, retries, asked wait and server. A manifest written before the field
existed reads as no refusal, and an entry written before its facts reads its class from
its detail; a malformed entry is refused as every other malformed field is. Every value
here is invented.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above

_REFUSALS = "refusals"
R = lrf.Refusal
#: The facts a refusal gained after its first manifests: absent from an older entry.
_FACTS = ("reason", "status", "retries", "retry_after_s", "server")


def _after(document: dict[str, object]) -> dict[str, object]:
    """The first track's ``after`` block of manifest *document*, to edit in place."""
    return cast(dict[str, object], mb.first_track(document)["after"])


class ProviderRefusalTest(fixtures.IsolatedCase):
    """The record itself: its fields and the cap on its detail."""

    def test_an_outcome_holds_no_refusal_by_default(self) -> None:
        self.assertEqual(lr.TrackOutcome(lr.OutcomeStatus.NOT_FOUND).refusals, ())

    def test_a_short_detail_is_kept_as_given(self) -> None:
        refusal = lr.ProviderRefusal(
            lm.Source.NETEASE, lm.LyricsKind.SYNCED, "-460", R.UNRECOGNIZED
        )
        self.assertEqual(refusal.detail, "-460")

    def test_a_long_detail_is_cut_to_the_cap(self) -> None:
        detail = "HTTP 503 " + "x" * (2 * lr.MAX_REFUSAL_DETAIL_CHARS)
        refusal = lr.ProviderRefusal(
            lm.Source.LRCLIB, lm.LyricsKind.SYNCED, detail, R.OVERLOADED
        )
        self.assertEqual(len(refusal.detail), lr.MAX_REFUSAL_DETAIL_CHARS)
        self.assertTrue(refusal.detail.startswith("HTTP 503 x"))

    def test_a_detail_is_one_line_with_single_spaces(self) -> None:
        refusal = lr.ProviderRefusal(
            lm.Source.GENIUS,
            lm.LyricsKind.UNSYNCED,
            "  HTTP\n429 \t too many  ",
            R.RATE_LIMITED,
        )
        self.assertEqual(refusal.detail, "HTTP 429 too many")

    def test_the_cap_is_idempotent(self) -> None:
        detail = "timeout " * lr.MAX_REFUSAL_DETAIL_CHARS
        once = lr.ProviderRefusal(
            lm.Source.LRCLIB,
            lm.LyricsKind.UNSYNCED,
            detail,
            R.UNREACHABLE,
            server=" edge " * lr.MAX_REFUSAL_DETAIL_CHARS,
        )
        twice = lr.ProviderRefusal(
            once.provider, once.step, once.detail, once.reason, server=once.server
        )
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
        self.assertEqual(
            stored[0],
            {
                "provider": first.provider.value,
                "step": first.step.value,
                "detail": first.detail,
                "reason": first.reason.value,
                "status": first.status,
                "retries": first.retries,
                "retry_after_s": first.retry_after_s,
                "server": first.server,
                "unsent": first.unsent,
            },
        )
        self.assertEqual(
            (first.reason, first.status, first.retries, first.retry_after_s),
            (R.OVERLOADED, 503, 3, 120.0),
        )
        self.assertEqual(first.server, "edge-cache")

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


class RefusalFactsManifestTest(fixtures.IsolatedCase):
    """A refusal's facts round-trip; an entry older than them reads its class back."""

    def _first_entry(self, document: dict[str, object]) -> dict[str, object]:
        """The first refusal entry of *document*'s first track, to edit in place."""
        return cast(list[dict[str, object]], _after(document)[_REFUSALS])[0]

    def _first_read(self, document: dict[str, object]) -> lr.ProviderRefusal:
        """The first refusal *document* reads back as."""
        after = lmf.parse_manifest(mb.encode(document)).tracks[0].after
        assert after is not None
        return after.refusals[0]

    def _old(self, detail: str) -> dict[str, object]:
        """A manifest document whose first refusal is an old entry with *detail*."""
        document = mb.document()
        entry = self._first_entry(document)
        for key in _FACTS:
            del entry[key]
        entry["detail"] = detail
        return document

    def test_an_old_entry_reads_its_class_from_its_detail(self) -> None:
        cases = {
            "HTTP 503 after 3 retries": R.OVERLOADED,
            "HTTP 429": R.RATE_LIMITED,
            "HTTP 403": R.BLOCKED,
            "HTTP 400": R.UNRECOGNIZED,
            "HTTP 500": R.OVERLOADED,
            "timeout": R.UNREACHABLE,
            "ConnectionError after 1 retry": R.UNREACHABLE,
            "code -460": R.UNRECOGNIZED,
        }
        for detail, reason in cases.items():
            with self.subTest(detail=detail):
                read = self._first_read(self._old(detail))
                self.assertEqual(read.reason, reason)
                self.assertEqual(
                    (read.status, read.retries, read.retry_after_s, read.server),
                    (None, 0, None, None),
                )

    def test_absent_facts_are_written_as_null_and_read_back(self) -> None:
        document = mb.document()
        stored = cast(list[dict[str, object]], _after(document)[_REFUSALS])
        self.assertEqual(
            [stored[-1][key] for key in _FACTS], ["unreachable", None, 0, None, None]
        )
        lmf.write_manifest(self.tmp, mb.FULL)
        after = lmf.read_manifest(self.tmp).tracks[0].after
        assert after is not None
        self.assertEqual(after.refusals[-1], mb.FULL_AFTER.refusals[-1])

    def test_an_unknown_reason_is_refused(self) -> None:
        for value in ("throttled", "Overloaded", None, 3):
            with self.subTest(value=value):
                document = mb.document()
                self._first_entry(document)["reason"] = value
                with self.assertRaisesRegex(lm.ManifestSchemaError, r"\.reason"):
                    lmf.parse_manifest(mb.encode(document))

    def test_a_malformed_fact_is_refused(self) -> None:
        edits: tuple[tuple[str, object], ...] = (
            ("status", "503"),
            ("status", True),
            ("status", 50.3),
            ("status", -1),
            ("retry_after_s", -0.5),
            ("retry_after_s", -1),
            ("retries", -1),
            ("retries", True),
            ("retries", None),
            ("retries", "3"),
            ("retry_after_s", "120"),
            ("retry_after_s", False),
            ("server", 5),
            ("server", ["edge"]),
        )
        for key, value in edits:
            with self.subTest(key=key, value=value):
                document = mb.document()
                self._first_entry(document)[key] = value
                with self.assertRaisesRegex(lm.ManifestSchemaError, rf"\.{key}"):
                    lmf.parse_manifest(mb.encode(document))

    def test_a_stored_server_reads_as_one_capped_line(self) -> None:
        document = mb.document()
        self._first_entry(document)["server"] = "a\nb " + "c" * 200
        server = self._first_read(document).server
        assert server is not None
        self.assertEqual(len(server), lr.MAX_REFUSAL_DETAIL_CHARS)
        self.assertTrue(server.startswith("a b c"))


class UnsentManifestTest(fixtures.IsolatedCase):
    """A step an open breaker skipped is kept as unsent; an older entry reads as sent."""

    def _document(self) -> dict[str, object]:
        """A manifest document whose first refusal is marked unsent."""
        document = mb.document()
        cast(list[dict[str, object]], _after(document)[_REFUSALS])[0]["unsent"] = True
        return document

    def _first_read(self, document: dict[str, object]) -> lr.ProviderRefusal:
        """The first refusal *document* reads back as."""
        after = lmf.parse_manifest(mb.encode(document)).tracks[0].after
        assert after is not None
        return after.refusals[0]

    def test_an_unsent_refusal_round_trips(self) -> None:
        unsent = replace(mb.FULL_AFTER.refusals[0], unsent=True)
        after = replace(mb.FULL_AFTER, refusals=(unsent,))
        manifest = replace(mb.FULL, tracks=(replace(mb.FULL.tracks[0], after=after),))
        lmf.write_manifest(self.tmp, manifest)
        read = lmf.read_manifest(self.tmp).tracks[0].after
        assert read is not None
        self.assertEqual(read.refusals, (unsent,))

    def test_an_entry_written_before_the_field_reads_as_sent(self) -> None:
        document = self._document()
        del cast(list[dict[str, object]], _after(document)[_REFUSALS])[0]["unsent"]
        self.assertFalse(self._first_read(document).unsent)

    def test_a_stored_unsent_is_read(self) -> None:
        self.assertTrue(self._first_read(self._document()).unsent)

    def test_an_unsent_that_is_not_a_boolean_is_refused(self) -> None:
        values: tuple[object, ...] = (1, 0, "true", None, [])
        for value in values:
            with self.subTest(value=value):
                document = mb.document()
                entries = cast(list[dict[str, object]], _after(document)[_REFUSALS])
                entries[0]["unsent"] = value
                with self.assertRaisesRegex(lm.ManifestSchemaError, r"\.unsent"):
                    lmf.parse_manifest(mb.encode(document))
