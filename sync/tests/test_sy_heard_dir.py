"""The --heard-dir store as a run keeps it: read, cleaned at the start, kept per track."""

from __future__ import annotations

import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import lyrics_heard_store as hs  # noqa: E402 - import follows the sys.path bootstrap
import sy_heard_dir as hd  # noqa: E402 - import follows the sys.path bootstrap above
import sy_lrc  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import text_fingerprint  # noqa: E402 - follows the bootstrap
from sy_types import Action, Label, Outcome, Planned, TrackResult  # noqa: E402

fixtures = sy_cases.fixtures
LINES = ("la la", "lo lo")
#: A timed .lrc of some other tool's: a timed sidecar whoever wrote it.
TIMED_LRC = sy_lrc.lrc_text(Label("0", "0", "0"), LINES, (1.0, 2.0))
FINGERPRINT = text_fingerprint(LINES)


class ReadStoreTest(sy_cases.IsolatedCase):
    def test_no_option_or_no_folder_yet_is_an_empty_store(self) -> None:
        for folder in (None, self.tmp / "missing"):
            with self.subTest(folder=folder):
                self.assertEqual(hd.read_store(folder), (hs.Listing((), ()), []))

    def test_a_file_at_the_name_is_a_setup_problem(self) -> None:
        folder = self.tmp / "heard"
        folder.write_text("x", encoding="utf-8", newline="\n")
        listing, problems = hd.read_store(folder)
        self.assertEqual(listing, hs.Listing((), ()))
        self.assertEqual(problems, [f"--heard-dir is not a folder: {folder}"])

    @unittest.skipIf(sy_cases.IS_ROOT, "root reads a mode-000 folder")
    def test_a_folder_that_cannot_be_read_is_a_setup_problem(self) -> None:
        folder = self.tmp / "heard"
        folder.mkdir(mode=0o000)
        self.addCleanup(folder.chmod, 0o700)
        _, problems = hd.read_store(folder)
        (problem,) = problems
        self.assertTrue(problem.startswith(f"--heard-dir {folder}: cannot be read"))

    def test_records_are_found_by_the_path_the_walk_names(self) -> None:
        folder = self.tmp / "heard"
        record = hs.HeardRecord(self.tmp / "a.mp3", FINGERPRINT, "heard")
        hs.write_record(folder, record)
        listing, _ = hd.read_store(folder)
        self.assertEqual(hd.by_path(listing), {str(record.path): record})

    def test_a_problem_file_is_a_line_to_print(self) -> None:
        folder = self.tmp / "heard"
        folder.mkdir()
        bad = hs.record_path(folder, self.tmp / "a.mp3")
        bad.write_text("{", encoding="utf-8", newline="\n")
        listing, problems = hd.read_store(folder)
        self.assertEqual(problems, [])
        self.assertEqual(
            hd.problem_lines(listing),
            [f"--heard-dir: {bad}: transcript record ignored (JSONDecodeError)"],
        )


class CleanTest(sy_cases.IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.folder = self.tmp / "heard"
        self.album = self.tmp / "Album"
        specs = (
            fixtures.TrackSpec("01 A.mp3", seconds=1.0, txt="la la"),
            fixtures.TrackSpec("02 B.mp3", seconds=1.0, lrc=TIMED_LRC),
        )
        self.untimed, self.timed = fixtures.build_album(self.album, specs)
        self.gone = self.album / "03 C.mp3"
        self.elsewhere = self.tmp / "Other" / "04 D.mp3"
        for track in (self.untimed, self.timed, self.gone, self.elsewhere):
            hs.write_record(self.folder, hs.HeardRecord(track, FINGERPRINT, "heard"))

    def test_records_of_tracks_gone_or_timed_under_the_path_are_removed(self) -> None:
        listing, _ = hd.read_store(self.folder)
        cleaned = hd.clean(self.folder, listing, self.album)
        kept = [record.path for record in cleaned.listing.records]
        self.assertEqual(kept, sorted([self.untimed, self.elsewhere]))
        self.assertEqual(
            cleaned.lines,
            (
                f"--heard-dir: removed 2 record(s) of tracks gone or timed from {self.folder}",
            ),
        )
        self.assertFalse(cleaned.failed)
        on_disk = hs.list_records(self.folder).records
        self.assertEqual(on_disk, cleaned.listing.records)

    def test_a_path_naming_one_track_cleans_only_its_record(self) -> None:
        listing, _ = hd.read_store(self.folder)
        cleaned = hd.clean(self.folder, listing, self.timed)
        self.assertEqual(len(cleaned.listing.records), 3)
        self.assertIsNone(hs.read_record(self.folder, self.timed))

    def test_nothing_to_remove_says_nothing(self) -> None:
        listing, _ = hd.read_store(self.folder)
        cleaned = hd.clean(self.folder, listing, self.untimed)
        self.assertEqual((cleaned.listing, cleaned.lines), (listing, ()))

    def test_a_record_that_cannot_be_removed_is_said_and_kept(self) -> None:
        listing, _ = hd.read_store(self.folder)
        with mock.patch.object(hd, "delete_record", side_effect=OSError("locked")):
            cleaned = hd.clean(self.folder, listing, self.gone)
        self.assertTrue(cleaned.failed)
        self.assertEqual(cleaned.listing, listing)
        self.assertEqual(
            cleaned.lines,
            (f"--heard-dir: could not remove the record of {self.gone}: locked",),
        )

    @unittest.skipIf(sy_cases.IS_ROOT, "root reads a mode-000 folder")
    def test_a_track_that_cannot_be_looked_at_is_not_gone(self) -> None:
        hidden = self.tmp / "Hidden"
        hidden.mkdir(mode=0o000)
        self.addCleanup(hidden.chmod, 0o700)
        track = hidden / "05 E.mp3"
        hs.write_record(self.folder, hs.HeardRecord(track, FINGERPRINT, "heard"))
        listing, _ = hd.read_store(self.folder)
        cleaned = hd.clean(self.folder, listing, hidden)
        self.assertIn(track, [record.path for record in cleaned.listing.records])

    def test_a_record_naming_a_path_with_a_nul_byte_names_nothing(self) -> None:
        # A listing built by hand, past the store's own refusal of such a file: the
        # cleaning must read the path as naming nothing, never raise ValueError.
        nul = self.album / "05 a\x00b.mp3"
        listing = hs.Listing((hs.HeardRecord(nul, FINGERPRINT, "heard"),), ())
        cleaned = hd.clean(self.folder, listing, self.album)
        self.assertEqual(cleaned.listing.records, ())
        self.assertFalse(cleaned.failed)

    def test_a_record_climbing_out_of_the_path_is_left_alone(self) -> None:
        # The store never normalizes a path, so ".." could make a record under the
        # --path name a track elsewhere, and the cleaning read its sidecars there.
        outside = self.tmp / "Other" / "06 F.mp3"
        fixtures.build_album(
            outside.parent, (fixtures.TrackSpec(outside.name, 1.0, lrc=TIMED_LRC),)
        )
        climbing = self.album / ".." / "Other" / outside.name
        hs.write_record(self.folder, hs.HeardRecord(climbing, FINGERPRINT, "heard"))
        listing, _ = hd.read_store(self.folder)
        with mock.patch.object(hd, "_is_timed", wraps=hd._is_timed) as timed:
            cleaned = hd.clean(self.folder, listing, self.album)
        self.assertIn(climbing, [record.path for record in cleaned.listing.records])
        self.assertNotIn(mock.call(climbing), timed.call_args_list)


class KeeperTest(sy_cases.IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.folder = self.tmp / "heard"
        self.audio = self.tmp / "Album" / "01 A.mp3"
        self.planned = Planned(self.audio, Action.TIME, lines=LINES)
        self.unsung = TrackResult(
            self.audio, Outcome.NOT_SUNG, ("0.04 of ...",), transcript="heard"
        )

    def test_a_text_not_sung_keeps_its_transcript_private(self) -> None:
        keeper = hd.Keeper(self.folder, ())
        self.assertEqual(keeper.keep(self.planned, self.unsung), self.unsung)
        self.assertEqual(
            hs.read_record(self.folder, self.audio),
            hs.HeardRecord(self.audio, FINGERPRINT, "heard"),
        )
        mode = stat.S_IMODE(hs.record_path(self.folder, self.audio).stat().st_mode)
        self.assertEqual((mode, keeper.added, keeper.failed), (0o600, 1, False))

    def test_a_timed_track_loses_its_record(self) -> None:
        keeper = hd.Keeper(self.folder, ())
        keeper.keep(self.planned, self.unsung)
        for outcome in (Outcome.TIMED, Outcome.RETIMED):
            with self.subTest(outcome=outcome):
                keeper.keep(self.planned, self.unsung)
                timed = TrackResult(self.audio, outcome)
                self.assertEqual(keeper.keep(self.planned, timed), timed)
                self.assertIsNone(hs.read_record(self.folder, self.audio))

    def test_a_held_record_from_before_the_run_goes_when_timed(self) -> None:
        hs.write_record(self.folder, hs.HeardRecord(self.audio, FINGERPRINT, "x"))
        keeper = hd.Keeper(self.folder, (str(self.audio),))
        keeper.keep(self.planned, TrackResult(self.audio, Outcome.TIMED))
        self.assertIsNone(hs.read_record(self.folder, self.audio))

    def test_a_transcript_too_long_to_keep_is_said_beneath_the_row(self) -> None:
        keeper = hd.Keeper(self.folder, ())
        unkept = TrackResult(self.audio, Outcome.NOT_SUNG, ("0.04 of ...",))
        result = keeper.keep(self.planned, unkept)
        self.assertEqual(
            result.detail,
            (
                "0.04 of ...",
                "--heard-dir: what was heard is over 64 KB and was not kept",
            ),
        )
        self.assertEqual((keeper.added, keeper.failed), (0, False))
        self.assertFalse(self.folder.exists())

    def test_a_held_record_goes_when_low_confidence_and_stays_when_too_long(
        self,
    ) -> None:
        for outcome, stays in (
            (Outcome.LOW_CONFIDENCE, False),
            (Outcome.TOO_LONG, True),
        ):
            with self.subTest(outcome=outcome):
                kept = hs.HeardRecord(self.audio, FINGERPRINT, "x")
                hs.write_record(self.folder, kept)
                keeper = hd.Keeper(self.folder, (str(self.audio),))
                keeper.keep(self.planned, TrackResult(self.audio, outcome))
                left = hs.read_record(self.folder, self.audio)
                self.assertEqual(left, kept if stays else None)

    def test_a_track_with_no_record_touches_nothing(self) -> None:
        keeper = hd.Keeper(self.folder, ())
        for outcome in (Outcome.TIMED, Outcome.LOW_CONFIDENCE, Outcome.NOT_SUNG):
            keeper.keep(self.planned, TrackResult(self.audio, outcome))
        self.assertFalse(self.folder.exists())

    def test_with_no_folder_nothing_is_kept(self) -> None:
        keeper = hd.Keeper(None, ())
        self.assertEqual(keeper.keep(self.planned, self.unsung), self.unsung)
        self.assertEqual(hd.kept_lines(keeper), [])

    def test_a_failed_write_is_said_beneath_the_row(self) -> None:
        keeper = hd.Keeper(self.folder, ())
        with mock.patch.object(hd, "write_record", side_effect=OSError("disk full")):
            result = keeper.keep(self.planned, self.unsung)
        self.assertEqual(
            result.detail[-1],
            f"--heard-dir: could not keep what was heard in {self.folder}: disk full",
        )
        self.assertEqual((keeper.added, keeper.failed), (0, True))

    def test_a_failed_removal_is_said_beneath_the_row(self) -> None:
        keeper = hd.Keeper(self.folder, (str(self.audio),))
        timed = TrackResult(self.audio, Outcome.TIMED)
        with mock.patch.object(hd, "delete_record", side_effect=OSError("locked")):
            result = keeper.keep(self.planned, timed)
        self.assertEqual(
            result.detail,
            (f"--heard-dir: could not remove the record of {self.audio}: locked",),
        )
        self.assertTrue(keeper.failed)

    def test_the_footer_names_the_folder_when_records_were_kept(self) -> None:
        keeper = hd.Keeper(self.folder, ())
        self.assertEqual(hd.kept_lines(keeper), [])
        keeper.keep(self.planned, self.unsung)
        self.assertEqual(
            hd.kept_lines(keeper),
            [f"kept what was heard of 1 track(s) not sung in {self.folder}"],
        )
