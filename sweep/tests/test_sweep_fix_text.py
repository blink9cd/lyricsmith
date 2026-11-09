"""--mode fix-text from the command line: which albums, the child's command, the report.

`main` runs with a fake album tool, a recording sleep and a fixed clock, as the run's
own command-line suite does, over an invented library and an invented transcript
store, so nothing here reaches a provider, a real library or the real ledger. Every
transcript and lyric here is invented for these tests.
"""

from __future__ import annotations

import contextlib
import io
import shlex
import sys
import unittest
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fix_text  # noqa: E402 - import follows the sys.path bootstrap above
import denied_fixtures as dn  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard_store import (  # noqa: E402 - follows the sys.path bootstrap
    HeardRecord,
    delete_record,
    list_records,
    record_path,
    write_record,
)
from lyrics_outcomes import WRONG_TEXT  # noqa: E402 - follows the sys.path bootstrap

#: An invented fingerprint: the store checks only its form.
_FINGERPRINT = "ab" * 32
#: An invented transcript.
_HEARD = "the kettle sings of harbor lights and nobody answers"
#: A fix that removed one wrong text and wrote another.
_FIXED = (
    lr.TrackOutcome(lr.OutcomeStatus.REMOVED, implausible=WRONG_TEXT),
    lr.TrackOutcome(lr.OutcomeStatus.WRITTEN, lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED),
)
#: A fix that kept a wrong text: a provider failed, so nothing was removed.
_KEPT = sf.Script(
    outcomes=(sf.fetch_error(sf.refusal(lm.Source.TEKSTOWO)), sf.written()),
    exit_code=album.EXIT_TRACK_ERRORS,
)


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


class FixTextCase(sf.IsolatedCase):
    """Three needy albums, a transcript store, and `main` run against them."""

    def setUp(self) -> None:
        """Albums A1 to A3 and an empty transcript store."""
        super().setUp()
        self.albums = [
            sf.needy_album(self.library, "Artist", f"A{n}") for n in range(1, 4)
        ]
        self.heard = self.tmp / "heard"
        self.heard.mkdir()
        self.fake = sf.FakeAlbumTool()

    def hear(self, album: Path, name: str = "02 Plain.mp3") -> Path:
        """Write a record for *album*'s track *name*; return the track."""
        track = album / name
        write_record(self.heard, HeardRecord(track, _FINGERPRINT, _HEARD))
        return track

    def main(self, *args: str, heard: bool = True) -> Result:
        """Run `main` with *args*, ``--library`` and, when *heard*, ``--heard-dir``."""
        argv = [*args, "--library", str(self.library)]
        argv += ["--heard-dir", str(self.heard)] if heard else []
        wiring = sweep.Wiring(self.fake, sf.FakeSleep(), sf.fixed_now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = sweep.main(argv, wiring=wiring)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def entries(self) -> list[sl.LedgerEntry]:
        """Every entry in this library's ledger."""
        path = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        records = sl.read_ledger(path).records
        return [r for r in records if isinstance(r, sl.LedgerEntry)]


class WhichAlbumsTest(FixTextCase):
    """Only the albums holding a record are visited, each with the fix's command."""

    def test_only_albums_holding_a_record_are_visited(self) -> None:
        self.hear(self.albums[0])
        self.hear(self.albums[2], "03 Bare.mp3")
        result = self.main("--mode", "fix-text")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.fake.albums(), ["A1", "A3"])
        for argv in self.fake.calls:
            self.assertEqual(sf.option_value(argv, "--mode"), "fix-text")
            self.assertEqual(sf.option_value(argv, "--heard-dir"), str(self.heard))

    def test_a_record_found_under_another_spelling_still_picks_its_album(self) -> None:
        # A link to the album's folder names the same file another way: the store
        # matches records by file identity, as the album tool does.
        link = self.tmp / "elsewhere"
        link.symlink_to(self.albums[1])
        self.hear(link)
        self.main("--mode", "fix-text")
        self.assertEqual(self.fake.albums(), ["A2"])

    def test_a_record_naming_no_track_of_the_library_visits_nothing(self) -> None:
        outside = sf.needy_album(self.tmp / "other", "Artist", "Elsewhere")
        self.hear(outside)
        result = self.main("--mode", "fix-text")
        self.assertEqual(result.code, 0)
        self.assertEqual(self.fake.calls, [])
        self.assertIn(
            "1 transcript record names no track of the library", result.stderr
        )

    def test_a_record_the_store_cannot_read_is_a_notice_and_the_rest_are_used(
        self,
    ) -> None:
        self.hear(self.albums[0])
        record_path(self.heard, self.albums[1] / "02 Plain.mp3").write_text(
            "not json", encoding="utf-8", newline=""
        )
        result = self.main("--mode", "fix-text")
        self.assertEqual(self.fake.albums(), ["A1"])
        self.assertIn("transcript record ignored", result.stderr)

    def test_an_all_synced_album_holding_a_record_is_visited(self) -> None:
        # A provider-timed text heard not sung is a fix's too.
        done = sf.synced_album(self.library, "Artist", "A0")
        self.hear(done, "01 Done.mp3")
        self.main("--mode", "fix-text")
        self.assertEqual(self.fake.albums(), ["A0"])

    def test_a_heard_dir_that_is_not_a_folder_is_a_usage_error(self) -> None:
        self.heard.rmdir()
        self.heard.write_bytes(b"")
        result = self.main("--mode", "fix-text")
        self.assertEqual(result.code, 2)
        self.assertIn("the transcript folder is not a folder", result.stderr)
        self.assertEqual(self.fake.calls, [])

    def test_a_missing_heard_dir_is_a_usage_error_too(self) -> None:
        # The store reads a missing folder as empty: a mistyped --heard-dir would
        # otherwise visit nothing and exit 0.
        self.heard.rmdir()
        result = self.main("--mode", "fix-text")
        self.assertEqual(result.code, 2)
        self.assertIn("the transcript folder is not a folder", result.stderr)

    @unittest.skipUnless(dn.MODE_000_LOCKS_A_FOLDER, dn.NO_FOLDER_LOCK)
    def test_a_heard_dir_that_cannot_be_listed_is_a_usage_error(self) -> None:
        self.hear(self.albums[0])
        self.heard.chmod(0)
        self.addCleanup(self.heard.chmod, 0o700)
        self.assert_an_unlistable_heard_dir_is_a_usage_error()

    def test_a_heard_dir_whose_listing_is_refused_is_a_usage_error(self) -> None:
        # The portable twin of the mode-000 case: the listing itself raises.
        self.hear(self.albums[0])
        with dn.listing_refused(self.heard):
            self.assert_an_unlistable_heard_dir_is_a_usage_error()

    def assert_an_unlistable_heard_dir_is_a_usage_error(self) -> None:
        """A fix-text run exits 2, naming the transcript folder, and visits nothing."""
        result = self.main("--mode", "fix-text")
        self.assertEqual(result.code, 2)
        self.assertIn("the transcript folder cannot be listed", result.stderr)
        self.assertEqual(self.fake.calls, [])

    def test_a_folder_holding_no_mp3_is_no_album_to_fix(self) -> None:
        empty = self.library / "Artist" / "A0"
        empty.mkdir()
        (empty / "notes.txt").write_bytes(b"an invented note\n")
        self.hear(self.albums[0])
        self.main("--mode", "fix-text")
        self.assertEqual(self.fake.albums(), ["A1"])


class OutcomesTest(FixTextCase):
    """How a fix's album is recorded, reported and retried."""

    def test_a_fix_with_kept_texts_is_not_done_and_is_tried_again(self) -> None:
        self.hear(self.albums[0])
        self.fake.scripts["A1"] = _KEPT
        first = self.main("--mode", "fix-text")
        self.assertEqual(first.code, 1)
        self.assertEqual(self.entries()[-1].outcome, sl.Outcome.TRACK_ERRORS)
        self.assertEqual(self.entries()[-1].pass_, sl.SweepPass.FIX_TEXT)
        self.assertIn("errors: 1 track", first.stdout)
        self.fake.scripts["A1"] = sf.Script(outcomes=_FIXED)
        second = self.main("--mode", "fix-text")
        self.assertEqual(second.code, 0)
        self.assertEqual(self.fake.albums(), ["A1", "A1"])
        # The retry asks every provider: a kept text owes no narrowed revisit.
        self.assertNotIn("--only-providers", self.fake.calls[-1])
        self.assertEqual(self.entries()[-1].outcome, sl.Outcome.DONE)

    def test_a_fix_with_nothing_to_fix_makes_no_run_and_is_done(self) -> None:
        self.hear(self.albums[0])
        self.fake.scripts["A1"] = sf.Script(manifest=False)
        result = self.main("--mode", "fix-text")
        self.assertEqual(result.code, 0, result.stdout)
        self.assertEqual(self.entries()[-1].outcome, sl.Outcome.DONE)
        self.main("--mode", "fix-text")
        self.assertEqual(self.fake.albums(), ["A1"])

    def test_the_totals_count_written_and_wrong_text_removed(self) -> None:
        self.hear(self.albums[0])
        self.fake.scripts["A1"] = sf.Script(outcomes=_FIXED)
        result = self.main("--mode", "fix-text")
        self.assertIn("removed: wrong text 1", result.stdout)
        self.assertIn("written: ", result.stdout)
        self.assertNotIn("errors:", result.stdout)
        self.assertIn("undo:", result.stdout)

    def test_the_passes_keep_apart_in_the_ledger(self) -> None:
        self.hear(self.albums[0])
        self.assertEqual(self.main("--mode", "run").code, 2)  # --heard-dir refused
        run = self.main("--mode", "run", heard=False)
        self.assertEqual(run.code, 0)
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A3"])
        self.main("--mode", "fix-text")
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A3", "A1"])
        # A done fix makes no run pass's album done, nor the other way.
        self.main("--mode", "run", heard=False)
        self.assertEqual(self.fake.albums()[4:], [])
        self.main("--mode", "fix-text")
        self.assertEqual(self.fake.albums()[4:], [])
        passes = [entry.pass_ for entry in self.entries()]
        self.assertEqual(passes.count(sl.SweepPass.FIX_TEXT), 1)

    def test_the_changed_list_gathers_every_album_a_fix_saved(self) -> None:
        self.hear(self.albums[0])
        self.hear(self.albums[1])
        listed = self.tmp / "changed.txt"
        for name in ("A1", "A2"):
            self.fake.scripts[name] = sf.Script(
                outcomes=_FIXED, changed=("02 Plain.mp3",)
            )
        result = self.main("--mode", "fix-text", "--changed-list", str(listed))
        self.assertEqual(result.code, 0, result.stderr)
        lines = listed.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            lines, [str(album / "02 Plain.mp3") for album in self.albums[:2]]
        )

    def test_a_stopped_fix_prints_a_resume_command_for_the_fix(self) -> None:
        self.hear(self.albums[0])
        self.hear(self.albums[1])
        result = self.main("--mode", "fix-text", "--limit", "1")
        resume = [
            line for line in result.stdout.splitlines() if line.startswith("resume: ")
        ]
        self.assertEqual(len(resume), 1)
        words = shlex.split(resume[0].removeprefix("resume: "))
        self.assertEqual(sf.option_value(words, "--mode"), "fix-text")
        self.assertEqual(sf.option_value(words, "--heard-dir"), str(self.heard))


@dataclass
class _Removing(sf.FakeAlbumTool):
    """The fake album tool, deleting its album's records as a fix's removals do."""

    heard: Path | None = None

    def __call__(
        self, argv: Sequence[str], timeout_s: float
    ) -> sweep_child.ChildResult:
        """Run the fake, then delete the album's records from the store."""
        result = super().__call__(argv, timeout_s)
        assert self.heard is not None
        album = Path(sf.option_value(argv, "--album"))
        for record in list_records(self.heard).records:
            if album in record.path.parents:
                delete_record(self.heard, record.path)
        return result


class RevisitTest(FixTextCase):
    """A done fix's album is passed over only while its records are as the visit left them."""

    def test_a_done_album_whose_records_are_unchanged_is_passed_over(self) -> None:
        self.hear(self.albums[0])
        self.fake.scripts["A1"] = sf.Script(outcomes=_FIXED)
        self.assertEqual(self.main("--mode", "fix-text").code, 0)
        self.assertEqual(self.main("--mode", "fix-text").code, 0)
        self.assertEqual(self.fake.albums(), ["A1"])

    def test_a_done_album_gaining_a_record_is_visited_again(self) -> None:
        self.hear(self.albums[0])
        self.main("--mode", "fix-text")
        self.hear(self.albums[0], "03 Bare.mp3")
        self.main("--mode", "fix-text")
        self.assertEqual(self.fake.albums(), ["A1", "A1"])

    def test_a_done_album_whose_record_changed_is_visited_again(self) -> None:
        track = self.hear(self.albums[0])
        self.main("--mode", "fix-text")
        write_record(self.heard, HeardRecord(track, "cd" * 32, _HEARD))
        self.main("--mode", "fix-text")
        self.assertEqual(self.fake.albums(), ["A1", "A1"])

    def test_a_done_album_regaining_the_record_its_fix_removed_is_visited_again(
        self,
    ) -> None:
        # The fix removed the text and deleted its record; an undo and sync's rerun
        # then keep the same record again: the album is owed a fix once more.
        removing = _Removing(heard=self.heard)
        self.fake = removing
        self.hear(self.albums[0])
        self.hear(self.albums[1])
        self.main("--mode", "fix-text")
        self.assertEqual(list_records(self.heard).records, ())
        self.hear(self.albums[0])
        self.main("--mode", "fix-text")
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A1"])

    def test_a_store_or_album_unread_after_the_fix_has_no_digest(self) -> None:
        # None agrees with no survey's digest, so the album is visited again.
        self.hear(self.albums[0])
        self.assertIsNotNone(sweep_fix_text.album_digest(self.albums[0], self.heard))
        not_a_folder = self.tmp / "file"
        not_a_folder.write_bytes(b"")
        no_album = self.tmp / "no-album"
        no_album.mkdir()
        for folder, heard in ((self.albums[0], not_a_folder), (no_album, self.heard)):
            with self.subTest(folder=folder, heard=heard):
                self.assertIsNone(sweep_fix_text.album_digest(folder, heard))

    def test_a_fix_entry_holds_its_records_digest_and_a_run_entry_none(self) -> None:
        self.hear(self.albums[0])
        self.main("--mode", "fix-text")
        self.main("--mode", "run", heard=False)
        digests = {entry.pass_: entry.records_digest for entry in self.entries()}
        fix = digests[sl.SweepPass.FIX_TEXT]
        self.assertRegex(fix or "", "^[0-9a-f]{64}$")
        self.assertIsNone(digests[sl.SweepPass.NORMAL])

    def test_the_run_pass_passes_a_done_album_over_whatever_its_records(self) -> None:
        self.main("--mode", "run", heard=False)
        self.hear(self.albums[0])
        self.main("--mode", "run", heard=False)
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A3"])

    def test_a_fix_restart_forgets_only_the_fix_pass_done_albums(self) -> None:
        self.hear(self.albums[0])
        self.main("--mode", "run", heard=False)
        self.main("--mode", "fix-text")
        self.assertEqual(self.main("--mode", "fix-text", "--restart").code, 0)
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A3", "A1", "A1"])
        self.main("--mode", "run", heard=False)
        self.assertEqual(self.fake.albums()[5:], [])

    def test_consecutive_refused_fixes_stop_the_sweep_with_exit_3(self) -> None:
        refused = sf.Script(outcomes=sf.REFUSED, exit_code=album.EXIT_TRACK_ERRORS)
        for folder in self.albums:
            self.hear(folder)
            self.fake.scripts[folder.name] = refused
        result = self.main("--mode", "fix-text", "--stop-after", "2")
        self.assertEqual(result.code, 3, result.stderr)
        self.assertEqual(self.fake.albums(), ["A1", "A2"])
        outcomes = [entry.outcome for entry in self.entries()]
        self.assertEqual(outcomes, [sl.Outcome.REFUSED, sl.Outcome.REFUSED])


class RefusalsTest(FixTextCase):
    """The options a fix refuses, and --heard-dir everywhere else."""

    def test_the_options_a_fix_cannot_use_are_refused(self) -> None:
        self.hear(self.albums[0])
        for option in (
            "--offline",
            "--refetch-untimed",
            "--refetch-implausible",
            "--owe-earlier-runs",
            "--apply",
        ):
            with self.subTest(option=option):
                result = self.main("--mode", "fix-text", option)
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"{option} cannot be used with --mode fix-text", result.stderr
                )
        resumed = self.main("--mode", "fix-text", "--resume", str(self.tmp / "list"))
        self.assertEqual(resumed.code, 2)
        self.assertEqual(self.fake.calls, [])

    def test_a_fix_needs_heard_dir_and_no_other_mode_takes_it(self) -> None:
        result = self.main("--mode", "fix-text", heard=False)
        self.assertEqual(result.code, 2)
        self.assertIn("--heard-dir is required with --mode fix-text", result.stderr)
        for mode in ("survey", "run", "accept", "silence"):
            with self.subTest(mode=mode):
                refused = self.main("--mode", mode)
                self.assertEqual(refused.code, 2)
                self.assertIn(
                    f"--heard-dir cannot be used with --mode {mode}", refused.stderr
                )

    def test_a_fix_takes_the_pace_and_the_providers_options(self) -> None:
        self.hear(self.albums[0])
        result = self.main(
            "--mode",
            "fix-text",
            "--lang",
            "deu",
            "--no-tekstowo",
            "--pause-seconds",
            "1",
            "--stop-after",
            "2",
        )
        self.assertEqual(result.code, 0, result.stderr)
        (argv,) = self.fake.calls
        self.assertEqual(sf.option_value(argv, "--lang"), "deu")
        self.assertIn("--no-tekstowo", argv)
