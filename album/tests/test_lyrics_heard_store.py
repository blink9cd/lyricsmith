"""lyrics_heard_store: one transcript record per track, kept private and read back strictly.

Every track path and transcript here is invented; each store folder is under the test's
own folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard_store as hs  # noqa: E402 - import follows the sys.path bootstrap above

#: An invented fingerprint: sixty-four lower-case hex digits.
PRINT = "ab" * 32
#: An invented transcript.
HEARD = "seven lanterns in the rain\nred kites over the harbor wall\n"


class StoreCase(fixtures.IsolatedCase):
    """A store folder under the test's own folder, and an invented track in a library."""

    def setUp(self) -> None:
        super().setUp()
        self.store = self.tmp / "heard"
        self.track = self.tmp / "library" / "Some Artist" / "01 A Song.mp3"

    def record(
        self, transcript: str = HEARD, track: Path | None = None
    ) -> hs.HeardRecord:
        """A record for *track*, the test's own by default."""
        return hs.HeardRecord(track or self.track, PRINT, transcript)

    def put(self, document: object, track: Path | None = None) -> Path:
        """Write *document* as JSON where *track*'s record goes; return its path."""
        path = hs.record_path(self.store, track or self.track)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8", newline="\n")
        return path

    def document(self, **changes: object) -> dict[str, object]:
        """The test track's record as JSON fields, with *changes* made."""
        fields = {"path": str(self.track), "fingerprint": PRINT, "transcript": HEARD}
        return {**fields, **changes}


class RecordPathTest(StoreCase):
    def test_a_record_is_named_by_the_sha256_of_its_tracks_path(self) -> None:
        digest = hashlib.sha256(os.fsencode(str(self.track))).hexdigest()
        self.assertEqual(
            hs.record_path(self.store, self.track), self.store / f"{digest}.json"
        )

    def test_a_relative_track_path_is_refused(self) -> None:
        with self.assertRaises(hs.RecordRefusedError):
            hs.record_path(self.store, Path("library") / "a.mp3")


class WriteTest(StoreCase):
    def test_a_record_written_reads_back_as_written(self) -> None:
        hs.write_record(self.store, self.record())
        self.assertEqual(hs.read_record(self.store, self.track), self.record())

    def test_the_file_holds_the_three_fields_as_json(self) -> None:
        hs.write_record(self.store, self.record())
        data = hs.record_path(self.store, self.track).read_bytes()
        self.assertEqual(json.loads(data), self.document())

    @unittest.skipIf(os.name == "nt", fixtures.NO_POSIX_MODES)
    def test_the_file_and_the_folder_are_private(self) -> None:
        hs.write_record(self.store, self.record())
        path = hs.record_path(self.store, self.track)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)

    def test_a_second_write_replaces_the_first(self) -> None:
        hs.write_record(self.store, self.record())
        hs.write_record(self.store, self.record("la la"))
        self.assertEqual(hs.read_record(self.store, self.track), self.record("la la"))

    def test_a_transcript_at_the_cap_is_kept(self) -> None:
        longest = "é" * (hs.MAX_TRANSCRIPT_BYTES // 2)
        hs.write_record(self.store, self.record(longest))
        self.assertEqual(hs.read_record(self.store, self.track), self.record(longest))

    def test_a_transcript_fits_up_to_the_cap_measured_in_utf8(self) -> None:
        cap = hs.MAX_TRANSCRIPT_BYTES
        self.assertTrue(hs.transcript_fits("a" * cap))
        self.assertFalse(hs.transcript_fits("a" * (cap + 1)))
        self.assertTrue(hs.transcript_fits("é" * (cap // 2)))
        self.assertFalse(hs.transcript_fits("é" * (cap // 2) + "a"))

    def test_a_lone_surrogate_is_measured_as_the_three_bytes_it_takes(self) -> None:
        cap = hs.MAX_TRANSCRIPT_BYTES
        self.assertTrue(hs.transcript_fits("a" * (cap - 3) + "\udc80"))
        self.assertFalse(hs.transcript_fits("a" * (cap - 2) + "\udc80"))

    def test_a_transcript_that_does_not_fit_is_refused(self) -> None:
        over = "a" * (hs.MAX_TRANSCRIPT_BYTES + 1)
        self.assertFalse(hs.transcript_fits(over))
        with self.assertRaises(hs.RecordRefusedError):
            hs.write_record(self.store, self.record(over))

    def test_a_transcript_over_the_cap_is_refused_and_nothing_written(self) -> None:
        # Counted in UTF-8 bytes: one more two-byte letter is over.
        over = "é" * (hs.MAX_TRANSCRIPT_BYTES // 2) + "é"
        with self.assertRaisesRegex(hs.RecordRefusedError, "transcript") as caught:
            hs.write_record(self.store, self.record(over))
        self.assertNotIn("é", str(caught.exception))
        self.assertFalse(self.store.exists())

    def test_the_worst_escaped_record_still_reads_back(self) -> None:
        # Each control character is six bytes of JSON; the file cap allows for it.
        track = self.tmp / ("\x01" * 200) / "a.mp3"
        record = self.record("\x00" * hs.MAX_TRANSCRIPT_BYTES, track)
        hs.write_record(self.store, record)
        self.assertEqual(hs.read_record(self.store, track), record)

    def test_a_fingerprint_not_sixty_four_hex_digits_is_refused(self) -> None:
        for fingerprint in ("", "AB" * 32, "ab" * 31, "zz" * 32):
            with self.subTest(fingerprint=fingerprint):
                record = hs.HeardRecord(self.track, fingerprint, HEARD)
                with self.assertRaises(hs.RecordRefusedError):
                    hs.write_record(self.store, record)

    def test_a_track_path_over_the_cap_is_refused(self) -> None:
        track = self.tmp / ("a" * hs.MAX_PATH_BYTES)
        with self.assertRaisesRegex(hs.RecordRefusedError, "path"):
            hs.write_record(self.store, self.record(track=track))

    def test_a_track_path_holding_a_nul_byte_is_refused_and_nothing_written(
        self,
    ) -> None:
        # No file system path holds a NUL: such a record could name no track, and
        # every stat of it raises ValueError rather than OSError.
        with self.assertRaises(hs.RecordRefusedError):
            hs.write_record(self.store, self.record(track=self.tmp / "a\x00b.mp3"))
        self.assertFalse(self.store.exists())

    def test_a_relative_track_is_refused(self) -> None:
        with self.assertRaises(hs.RecordRefusedError):
            hs.write_record(self.store, self.record(track=Path("a.mp3")))

    def test_a_failed_write_raises_a_store_error(self) -> None:
        self.store.write_text("not a folder", encoding="utf-8", newline="\n")
        with self.assertRaises(hs.STORE_ERRORS):
            hs.write_record(self.store, self.record())


class ReadTest(StoreCase):
    def test_a_track_with_no_record_has_none(self) -> None:
        self.assertIsNone(hs.read_record(self.store, self.track))

    def test_a_file_over_the_record_cap_is_refused_unread(self) -> None:
        path = hs.record_path(self.store, self.track)
        path.parent.mkdir(parents=True)
        path.write_bytes(b" " * (hs.MAX_RECORD_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "too large"):
            hs.read_record(self.store, self.track)

    def test_a_malformed_file_is_refused_never_quoted(self) -> None:
        malformed: dict[str, object] = {
            "not json": "{secret words",
            "not an object": ["secret words"],
            "a missing field": {"path": str(self.track), "transcript": "secret words"},
            "an extra field": self.document(extra="secret words"),
            "a path not text": self.document(path=7),
            "a relative path": self.document(path="library/a.mp3"),
            "a fingerprint not hex": self.document(fingerprint="secret words"),
            "a transcript not text": self.document(transcript=["secret words"]),
            "another track's path": self.document(path=str(self.tmp / "b.mp3")),
            "a transcript over the cap": self.document(
                transcript="x" * (hs.MAX_TRANSCRIPT_BYTES + 1)
            ),
        }
        for name, document in malformed.items():
            with self.subTest(name):
                path = hs.record_path(self.store, self.track)
                path.parent.mkdir(parents=True, exist_ok=True)
                raw = document if isinstance(document, str) else json.dumps(document)
                path.write_text(raw, encoding="utf-8", newline="\n")
                with self.assertRaises(ValueError) as caught:
                    hs.read_record(self.store, self.track)
                self.assertNotIn("secret", str(caught.exception))

    def test_undecodable_bytes_are_refused(self) -> None:
        path = hs.record_path(self.store, self.track)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{"path": "\xff"}')
        with self.assertRaises(ValueError):
            hs.read_record(self.store, self.track)

    def test_a_link_under_the_records_name_is_never_followed(self) -> None:
        elsewhere = self.tmp / "elsewhere.json"
        elsewhere.write_text(
            json.dumps(self.document()), encoding="utf-8", newline="\n"
        )
        path = hs.record_path(self.store, self.track)
        path.parent.mkdir(parents=True)
        path.symlink_to(elsewhere)
        with self.assertRaises(OSError):
            hs.read_record(self.store, self.track)

    def test_a_folder_under_the_records_name_is_an_error(self) -> None:
        hs.record_path(self.store, self.track).mkdir(parents=True)
        with self.assertRaises(OSError):
            hs.read_record(self.store, self.track)


class ListTest(StoreCase):
    def test_a_missing_store_lists_nothing(self) -> None:
        self.assertEqual(hs.list_records(self.store), hs.Listing((), ()))

    def test_every_record_is_listed_in_path_order(self) -> None:
        other = self.tmp / "library" / "Another" / "02 B.mp3"
        hs.write_record(self.store, self.record("b", other))
        hs.write_record(self.store, self.record("a"))
        listing = hs.list_records(self.store)
        self.assertEqual(listing.records, (self.record("b", other), self.record("a")))
        self.assertEqual(listing.problems, ())

    def test_a_broken_record_is_a_problem_and_the_rest_still_listed(self) -> None:
        hs.write_record(self.store, self.record())
        broken = self.put(self.document(transcript=7), self.tmp / "c.mp3")
        listing = hs.list_records(self.store)
        self.assertEqual(listing.records, (self.record(),))
        self.assertEqual(len(listing.problems), 1)
        self.assertIn(broken.name, listing.problems[0])
        self.assertIn("RecordRefusedError", listing.problems[0])

    def test_a_file_misplaced_under_another_records_name_is_a_problem(self) -> None:
        # The file says it is the test track's, under another track's name.
        self.put(self.document(), self.tmp / "c.mp3")
        listing = hs.list_records(self.store)
        self.assertEqual((listing.records, len(listing.problems)), ((), 1))

    def test_a_file_whose_path_holds_a_nul_byte_is_a_problem(self) -> None:
        # A crafted file: the store never writes one, and no reader may trust it.
        nul = self.tmp / "a\x00b.mp3"
        hs.write_record(self.store, self.record())
        broken = self.put(self.document(path=str(nul)), nul)
        listing = hs.list_records(self.store)
        self.assertEqual(listing.records, (self.record(),))
        (problem,) = listing.problems
        self.assertIn(broken.name, problem)
        self.assertIn("RecordRefusedError", problem)

    def test_a_file_not_named_as_a_record_is_not_one(self) -> None:
        hs.write_record(self.store, self.record())
        (self.store / ".leftover.tmp").write_text(
            "half", encoding="utf-8", newline="\n"
        )
        (self.store / "notes.json").write_text("{}", encoding="utf-8", newline="\n")
        (self.store / ("ab" * 32 + ".json")).mkdir()
        listing = hs.list_records(self.store)
        self.assertEqual(listing.records, (self.record(),))
        self.assertEqual(len(listing.problems), 1)

    def test_a_store_that_is_not_a_folder_is_an_error(self) -> None:
        self.store.write_text("not a folder", encoding="utf-8", newline="\n")
        with self.assertRaises(OSError):
            hs.list_records(self.store)


class DeleteTest(StoreCase):
    def test_a_deleted_record_is_gone(self) -> None:
        hs.write_record(self.store, self.record())
        hs.delete_record(self.store, self.track)
        self.assertIsNone(hs.read_record(self.store, self.track))
        self.assertEqual(list(self.store.iterdir()), [])

    def test_deleting_a_missing_record_is_fine(self) -> None:
        hs.delete_record(self.store, self.track)
        self.assertFalse(self.store.exists())

    def test_only_that_tracks_record_is_deleted(self) -> None:
        other = self.tmp / "b.mp3"
        hs.write_record(self.store, self.record())
        hs.write_record(self.store, self.record(track=other))
        hs.delete_record(self.store, self.track)
        self.assertEqual(
            hs.list_records(self.store).records, (self.record(track=other),)
        )


class RecordsForTest(StoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.track.parent.mkdir(parents=True)
        self.track.write_bytes(b"invented audio")

    def test_a_track_finds_its_record_by_the_path_it_was_written_under(self) -> None:
        hs.write_record(self.store, self.record())
        found = hs.records_for(hs.list_records(self.store), [self.track])
        self.assertEqual(found, {self.track: self.record()})

    def test_a_record_written_through_a_linked_folder_is_still_found(self) -> None:
        linked = self.tmp / "linked"
        linked.symlink_to(self.tmp / "library", target_is_directory=True)
        spelled = linked / "Some Artist" / self.track.name
        hs.write_record(self.store, self.record(track=spelled))
        self.assertIsNone(hs.read_record(self.store, self.track))
        found = hs.records_for(hs.list_records(self.store), [self.track])
        self.assertEqual(found, {self.track: self.record(track=spelled)})

    def test_a_record_whose_track_is_gone_matches_nothing(self) -> None:
        hs.write_record(self.store, self.record())
        listing = hs.list_records(self.store)
        self.track.unlink()
        self.assertEqual(hs.records_for(listing, [self.track]), {})

    def test_a_track_with_no_record_and_another_file_match_nothing(self) -> None:
        other = self.track.with_name("02 Another.mp3")
        other.write_bytes(b"other audio")
        hs.write_record(self.store, self.record(track=other))
        found = hs.records_for(hs.list_records(self.store), [self.track])
        self.assertEqual(found, {})

    def test_of_two_spellings_stored_the_tracks_own_wins(self) -> None:
        linked = self.tmp / "linked"
        linked.symlink_to(self.tmp / "library", target_is_directory=True)
        spelled = linked / "Some Artist" / self.track.name
        hs.write_record(self.store, self.record("linked", spelled))
        hs.write_record(self.store, self.record("own"))
        for track, transcript in ((self.track, "own"), (spelled, "linked")):
            with self.subTest(track=track):
                found = hs.records_for(hs.list_records(self.store), [track])
                self.assertEqual(found[track].transcript, transcript)

    def test_a_path_holding_a_nul_byte_names_nothing(self) -> None:
        # A listing built by hand, past the store's own refusal: the match must
        # still pass such a path over rather than raise its ValueError.
        nul = self.tmp / "a\x00b.mp3"
        listing = hs.Listing((self.record(track=nul), self.record()), ())
        found = hs.records_for(listing, [nul, self.track])
        self.assertEqual(found, {self.track: self.record()})


class RecordsByGroupTest(StoreCase):
    def test_each_group_is_matched_as_records_for_matches_it(self) -> None:
        other = self.tmp / "library" / "Another" / "02 B.mp3"
        lone = self.tmp / "library" / "Another" / "03 C.mp3"
        for track in (self.track, other, lone):
            track.parent.mkdir(parents=True, exist_ok=True)
            track.write_bytes(b"invented audio")
        hs.write_record(self.store, self.record("a"))
        hs.write_record(self.store, self.record("b", other))
        listing = hs.list_records(self.store)
        groups = {"first": [self.track], "second": [other, lone], "none": [lone]}
        found = hs.records_by_group(listing, groups)
        self.assertEqual(
            found,
            {key: hs.records_for(listing, tracks) for key, tracks in groups.items()},
        )
        self.assertEqual(found["second"], {other: self.record("b", other)})
        self.assertEqual(found["none"], {})
