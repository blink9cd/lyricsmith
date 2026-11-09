"""lyrics_hearing_log: one hearing record per track and model, private and read strictly.

Every track path, model name and transcript here is invented; each log folder is under
the test's own folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_hearing_log as hl  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import text_fingerprint  # noqa: E402 - follows the sys.path bootstrap

#: An invented transcript, and the fingerprint of an invented text it was heard against.
HEARD = "seven lanterns in the rain\nred kites over the harbor wall\n"
PRINT = text_fingerprint("paper boats along the river\n")
#: Two invented model file names.
MODEL = "ggml-invented-turbo.bin"
OTHER_MODEL = "ggml-invented-large.bin"


class LogCase(fixtures.IsolatedCase):
    """A log folder under the test's own folder, and an invented track in a library."""

    def setUp(self) -> None:
        super().setUp()
        self.log = self.tmp / "hearings"
        self.track = str(self.tmp / "library" / "Some Artist" / "01 A Song.mp3")

    # Any, not object: the refusal tests hand fields of the wrong type on purpose, which
    # the record's own field types would reject before the write could.
    def record(self, **changes: Any) -> hl.HearingRecord:
        """The test track's record, sung under `MODEL`, with *changes* made."""
        base = hl.HearingRecord(
            path=self.track,
            fingerprint=PRINT,
            kind=hl.HeardKind.PROVIDER_TIMED,
            model=MODEL,
            pairs=42,
            share=0.75,
            verdict=hl.HeardVerdict.SUNG,
            transcript=HEARD,
        )
        return replace(base, **changes)

    def document(self, **changes: object) -> dict[str, object]:
        """The test track's record as its JSON fields, with *changes* made."""
        fields: dict[str, object] = {
            "path": self.track,
            "fingerprint": PRINT,
            "kind": "provider-timed",
            "model": MODEL,
            "pairs": 42,
            "share": 0.75,
            "verdict": "sung",
            "transcript": HEARD,
        }
        return {**fields, **changes}

    def put(self, raw: str | bytes, track: str | None = None) -> Path:
        """Write *raw* where *track*'s record under `MODEL` goes; return its path."""
        path = hl.hearing_path(self.log, track or self.track, MODEL)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw.encode("utf-8") if isinstance(raw, str) else raw)
        return path


class HearingPathTest(LogCase):
    def test_a_record_is_named_by_the_sha256_of_path_nul_and_model(self) -> None:
        digest = hashlib.sha256(
            os.fsencode(self.track) + b"\x00" + MODEL.encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            hl.hearing_path(self.log, self.track, MODEL), self.log / f"{digest}.json"
        )

    def test_a_relative_track_path_is_refused(self) -> None:
        with self.assertRaises(hl.RecordRefusedError):
            hl.hearing_path(self.log, "library/a.mp3", MODEL)

    def test_a_model_that_is_not_a_plain_file_name_is_refused(self) -> None:
        for model in ("", ".", "..", "dir/model.bin", "dir\\model.bin", "a\x00b"):
            with self.subTest(model=model):
                with self.assertRaises(hl.RecordRefusedError):
                    hl.hearing_path(self.log, self.track, model)

    def test_a_model_name_over_the_cap_is_refused(self) -> None:
        with self.assertRaises(hl.RecordRefusedError):
            hl.hearing_path(self.log, self.track, "m" * (hl.MAX_MODEL_BYTES + 1))


class RoundTripTest(LogCase):
    def test_each_verdict_and_kind_reads_back_as_written(self) -> None:
        for kind in hl.HeardKind:
            for verdict in hl.HeardVerdict:
                with self.subTest(kind=kind, verdict=verdict):
                    record = self.record(kind=kind, verdict=verdict)
                    hl.write_hearing(self.log, record)
                    read = hl.read_hearing(self.log, self.track, MODEL)
                    self.assertEqual(read, record)
                    assert read is not None
                    self.assertIs(type(read.kind), hl.HeardKind)
                    self.assertIs(type(read.verdict), hl.HeardVerdict)

    def test_the_enums_spell_the_logs_words(self) -> None:
        self.assertEqual(
            [str(kind) for kind in hl.HeardKind],
            ["provider-timed", "machine-timed", "untimed"],
        )
        self.assertEqual(
            [str(verdict) for verdict in hl.HeardVerdict],
            ["sung", "not sung", "little heard"],
        )

    def test_the_file_holds_the_eight_fields_as_json(self) -> None:
        hl.write_hearing(self.log, self.record())
        data = hl.hearing_path(self.log, self.track, MODEL).read_bytes()
        self.assertEqual(json.loads(data), self.document())

    def test_a_whole_share_written_as_an_integer_reads_as_a_float(self) -> None:
        self.put(json.dumps(self.document(share=1)))
        read = hl.read_hearing(self.log, self.track, MODEL)
        assert read is not None
        self.assertEqual((read.share, type(read.share)), (1.0, float))

    @unittest.skipIf(os.name == "nt", fixtures.NO_POSIX_MODES)
    def test_the_file_and_the_folder_are_private(self) -> None:
        hl.write_hearing(self.log, self.record())
        path = hl.hearing_path(self.log, self.track, MODEL)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.log.stat().st_mode), 0o700)

    def test_a_second_write_replaces_the_first(self) -> None:
        hl.write_hearing(self.log, self.record())
        changed = self.record(fingerprint="cd" * 32, verdict=hl.HeardVerdict.NOT_SUNG)
        hl.write_hearing(self.log, changed)
        self.assertEqual(hl.read_hearing(self.log, self.track, MODEL), changed)

    def test_two_models_on_one_track_are_kept_apart(self) -> None:
        turbo = self.record()
        large = self.record(model=OTHER_MODEL, verdict=hl.HeardVerdict.NOT_SUNG)
        hl.write_hearing(self.log, turbo)
        hl.write_hearing(self.log, large)
        self.assertEqual(hl.read_hearing(self.log, self.track, MODEL), turbo)
        self.assertEqual(hl.read_hearing(self.log, self.track, OTHER_MODEL), large)
        self.assertEqual(hl.list_hearings(self.log).records, (large, turbo))

    def test_the_worst_escaped_record_still_reads_back(self) -> None:
        track = str(self.tmp / ("\x01" * 200) / "a.mp3")
        record = self.record(path=track, transcript="\x00" * hl.MAX_TRANSCRIPT_BYTES)
        hl.write_hearing(self.log, record)
        self.assertEqual(hl.read_hearing(self.log, track, MODEL), record)


class WriteRefusedTest(LogCase):
    def assert_refused(self, record: hl.HearingRecord) -> None:
        """*record* is refused before anything is written."""
        with self.assertRaises(hl.RecordRefusedError):
            hl.write_hearing(self.log, record)
        self.assertFalse(self.log.exists())

    def test_a_transcript_at_the_cap_is_kept(self) -> None:
        longest = "é" * (hl.MAX_TRANSCRIPT_BYTES // 2)
        hl.write_hearing(self.log, self.record(transcript=longest))
        read = hl.read_hearing(self.log, self.track, MODEL)
        self.assertEqual(read, self.record(transcript=longest))

    def test_a_transcript_over_the_64_kb_cap_is_refused_unquoted(self) -> None:
        over = "é" * (hl.MAX_TRANSCRIPT_BYTES // 2) + "é"
        with self.assertRaisesRegex(hl.RecordRefusedError, "transcript") as caught:
            hl.write_hearing(self.log, self.record(transcript=over))
        self.assertNotIn("é", str(caught.exception))
        self.assertFalse(self.log.exists())

    def test_a_fingerprint_not_sixty_four_hex_digits_is_refused(self) -> None:
        for fingerprint in ("", "AB" * 32, "ab" * 31, "zz" * 32):
            with self.subTest(fingerprint=fingerprint):
                self.assert_refused(self.record(fingerprint=fingerprint))

    def test_a_path_over_the_cap_or_holding_a_nul_is_refused(self) -> None:
        for path in (
            str(self.tmp / ("a" * hl.MAX_PATH_BYTES)),
            str(self.tmp / "a\x00b.mp3"),
            "a.mp3",
        ):
            with self.subTest(path=path[:20]):
                self.assert_refused(self.record(path=path))

    def test_a_count_or_share_out_of_its_form_is_refused(self) -> None:
        for changes in (
            {"pairs": -1},
            {"pairs": True},
            {"pairs": 2.0},
            {"pairs": hl.MAX_TRANSCRIPT_BYTES + 1},
            {"pairs": 10**600},
            {"share": -0.01},
            {"share": 1.01},
            {"share": float("nan")},
            {"share": "half"},
        ):
            with self.subTest(changes=changes):
                self.assert_refused(self.record(**changes))

    def test_the_most_pairs_a_transcript_can_hold_is_kept(self) -> None:
        # A pair is a word heard, so no count exceeds the transcript cap; the largest
        # count kept reads back, and every one written fits the record's envelope.
        record = self.record(pairs=hl.MAX_TRANSCRIPT_BYTES)
        hl.write_hearing(self.log, record)
        self.assertEqual(hl.read_hearing(self.log, self.track, MODEL), record)

    def test_a_kind_or_verdict_not_of_its_enum_is_refused(self) -> None:
        for changes in ({"kind": "provider-timed"}, {"verdict": "sung"}):
            with self.subTest(changes=changes):
                self.assert_refused(self.record(**changes))

    def test_a_failed_write_raises_a_store_error(self) -> None:
        self.log.write_text("not a folder", encoding="utf-8", newline="\n")
        with self.assertRaises(hl.STORE_ERRORS):
            hl.write_hearing(self.log, self.record())


class ReadTest(LogCase):
    def test_a_track_with_no_record_has_none(self) -> None:
        self.assertIsNone(hl.read_hearing(self.log, self.track, MODEL))

    def test_another_models_record_is_not_this_ones(self) -> None:
        hl.write_hearing(self.log, self.record(model=OTHER_MODEL))
        self.assertIsNone(hl.read_hearing(self.log, self.track, MODEL))

    def test_a_file_over_the_record_cap_is_refused_unread(self) -> None:
        self.put(b" " * (hl.MAX_RECORD_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "too large"):
            hl.read_hearing(self.log, self.track, MODEL)

    def test_a_malformed_file_is_refused_never_quoted(self) -> None:
        malformed: dict[str, object] = {
            "not json": "{secret words",
            "truncated": json.dumps(self.document())[:40],
            "not an object": ["secret words"],
            "a missing field": {"path": self.track, "transcript": "secret words"},
            "an extra field": self.document(extra="secret words"),
            "a path not text": self.document(path=7),
            "a relative path": self.document(path="library/a.mp3"),
            "a fingerprint not hex": self.document(fingerprint="secret words"),
            "a kind unknown": self.document(kind="secret words"),
            "a verdict unknown": self.document(verdict="secret words"),
            "a model not text": self.document(model=["secret words"]),
            "a model with a folder": self.document(model="secret/words"),
            "pairs not a count": self.document(pairs="secret words"),
            "pairs a boolean": self.document(pairs=True),
            "pairs negative": self.document(pairs=-1),
            "pairs past any transcript": self.document(
                pairs=hl.MAX_TRANSCRIPT_BYTES + 1
            ),
            "share a boolean": self.document(share=False),
            "share over one": self.document(share=1.5),
            "a transcript not text": self.document(transcript=["secret words"]),
            "another track's path": self.document(path=str(self.tmp / "b.mp3")),
            "another model's name": self.document(model=OTHER_MODEL),
            "a transcript over the cap": self.document(
                transcript="x" * (hl.MAX_TRANSCRIPT_BYTES + 1)
            ),
        }
        for name, document in malformed.items():
            with self.subTest(name):
                raw = document if isinstance(document, str) else json.dumps(document)
                self.put(raw)
                with self.assertRaises(ValueError) as caught:
                    hl.read_hearing(self.log, self.track, MODEL)
                self.assertNotIn("secret", str(caught.exception))

    def test_undecodable_bytes_are_refused(self) -> None:
        self.put(b'{"path": "\xff"}')
        with self.assertRaises(ValueError):
            hl.read_hearing(self.log, self.track, MODEL)

    def test_a_link_under_the_records_name_is_never_followed(self) -> None:
        elsewhere = self.tmp / "elsewhere.json"
        elsewhere.write_text(
            json.dumps(self.document()), encoding="utf-8", newline="\n"
        )
        path = hl.hearing_path(self.log, self.track, MODEL)
        path.parent.mkdir(parents=True)
        path.symlink_to(elsewhere)
        with self.assertRaises(OSError):
            hl.read_hearing(self.log, self.track, MODEL)


class ListTest(LogCase):
    def test_a_missing_log_lists_nothing(self) -> None:
        self.assertEqual(hl.list_hearings(self.log), hl.HearingListing((), ()))

    def test_every_record_is_listed_in_path_then_model_order(self) -> None:
        other = str(self.tmp / "library" / "Another" / "02 B.mp3")
        records = (
            self.record(path=other),
            self.record(model=OTHER_MODEL),
            self.record(),
        )
        for record in reversed(records):
            hl.write_hearing(self.log, record)
        listing = hl.list_hearings(self.log)
        self.assertEqual((listing.records, listing.problems), (records, ()))

    def test_truncated_oversized_and_schema_failing_files_are_problems(self) -> None:
        hl.write_hearing(self.log, self.record())
        tracks = [str(self.tmp / f"{name}.mp3") for name in ("t", "o", "s")]
        truncated = self.put(json.dumps(self.document(path=tracks[0]))[:30], tracks[0])
        oversized = self.put(b" " * (hl.MAX_RECORD_BYTES + 1), tracks[1])
        schema = self.put(
            json.dumps(self.document(path=tracks[2], verdict="maybe")), tracks[2]
        )
        listing = hl.list_hearings(self.log)
        self.assertEqual(listing.records, (self.record(),))
        self.assertEqual(len(listing.problems), 3)
        for broken in (truncated, oversized, schema):
            with self.subTest(broken=broken.name):
                (problem,) = [p for p in listing.problems if broken.name in p]
                self.assertIn("hearing record ignored", problem)
        self.assertTrue(any("RecordRefusedError" in p for p in listing.problems))

    def test_a_file_not_named_as_a_record_is_not_one(self) -> None:
        hl.write_hearing(self.log, self.record())
        (self.log / ".leftover.tmp").write_text("half", encoding="utf-8", newline="\n")
        (self.log / "notes.json").write_text("{}", encoding="utf-8", newline="\n")
        (self.log / ("ab" * 32 + ".json")).mkdir()
        listing = hl.list_hearings(self.log)
        self.assertEqual(listing.records, (self.record(),))
        self.assertEqual(len(listing.problems), 1)

    def test_a_log_that_is_not_a_folder_is_an_error(self) -> None:
        self.log.write_text("not a folder", encoding="utf-8", newline="\n")
        with self.assertRaises(OSError):
            hl.list_hearings(self.log)
