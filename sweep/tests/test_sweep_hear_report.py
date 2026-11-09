"""``sweep --mode hear-report`` end to end: a strata file, a hearing log and a library in, a report out.

`main` runs over an invented library whose tracks a fake draw put in three groups, and a
hearing log holding each verdict, a record of a track in no strata row, and a second
model's hearings. The report gives each group's counts and rate, the skips by reason,
the weighted estimate, the shift test and its genre tally, the comparison, and writes
nothing anywhere. `collect` is checked for the exact counts the printed report rounds.
Every name, word and genre here is invented.
"""

from __future__ import annotations

import contextlib
import io
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import draw_fixtures as df  # noqa: E402 - import follows the sys.path bootstrap above
import hear_fixtures as hf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_hear_format as hfm  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_hear_report as hr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_strata as ss  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_hearing_log import HeardKind, HeardVerdict  # noqa: E402 - bootstrap

_GROUP = ss.StrataGroup
_VERDICT = HeardVerdict
_TRANSCRIBE_SRC = _HERE.parents[1] / "transcribe" / "src"


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and printed."""

    code: int
    stdout: str
    stderr: str


class ReportCase(sf.IsolatedCase):
    """An invented library, its strata file and its hearing log."""

    def setUp(self) -> None:
        """Build the library, a strata file of three groups and a hearing log."""
        super().setUp()
        artist = self.library / "Invented Artist"
        self.kept = self._kept_album(artist / "Kept")
        self.wrong = self._wrong_album(artist / "Wrong")
        self.strata = self.tmp / "strata.tsv"
        self.log = self.tmp / "log"
        rows = [
            self._row("Kept", "01.mp3", _GROUP.NETEASE),
            self._row("Kept", "02.mp3", _GROUP.NETEASE),
            self._row("Kept", "03.mp3", _GROUP.NETEASE),
            self._row("Kept", "07.mp3", _GROUP.NETEASE),
            self._row("Kept", "05.mp3", _GROUP.LRCLIB),
            self._row("Wrong", "01.mp3", _GROUP.WRONG_TEXT_ALBUMS),
            self._row("Wrong", "02.mp3", _GROUP.WRONG_TEXT_ALBUMS),
        ]
        ss.write_strata(self.strata, 5, rows)
        hf.tag_genre(self.kept / "05.mp3", "Invented Noise")
        hf.log(
            self.log,
            hf.hearing(
                self.kept / "01.mp3",
                _VERDICT.NOT_SUNG,
                transcript=hf.song("lo"),
                share=0.1,
            ),
            hf.hearing(
                self.kept / "01.mp3", _VERDICT.NOT_SUNG, model=hf.LARGE, share=0.1
            ),
            hf.hearing(self.kept / "02.mp3", _VERDICT.SUNG),
            hf.hearing(
                self.kept / "05.mp3",
                _VERDICT.NOT_SUNG,
                transcript=hf.song("zu"),
                share=0.1,
            ),
            hf.hearing(self.kept / "05.mp3", _VERDICT.SUNG, model=hf.LARGE, share=0.4),
            hf.hearing(
                # The next track's song: a shift.
                self.wrong / "01.mp3",
                _VERDICT.NOT_SUNG,
                transcript=hf.song("su"),
            ),
            hf.hearing(self.wrong / "02.mp3", _VERDICT.LITTLE_HEARD),
            # A track the strata file does not name.
            hf.hearing(self.kept / "04.mp3", _VERDICT.SUNG),
        )

    def _row(self, album: str, name: str, group: ss.StrataGroup) -> ss.StrataRow:
        """The strata row of *album*'s track *name* in *group*."""
        folder = self.library / "Invented Artist" / album
        return ss.StrataRow(str(folder / name), group, f"Invented Artist/{album}")

    @staticmethod
    def _kept_album(album: Path) -> Path:
        """Four NetEase tracks, two LRCLIB ones and a frames-only one."""
        for name, prefix in (
            ("01.mp3", "ka"),
            ("02.mp3", "lo"),
            ("03.mp3", "mi"),
            ("04.mp3", "na"),
            ("05.mp3", "pe"),
            ("06.mp3", "ti"),
        ):
            sf.add_track(album, name, lrc=hf.lrc_of(hf.song(prefix)))
        df.frames_only(album, "07.mp3")
        sources = {"05.mp3": lm.Source.LRCLIB, "06.mp3": lm.Source.LRCLIB}
        entries = [
            df.entry(name, df.synced(sources.get(name, lm.Source.NETEASE)))
            for name in ("01.mp3", "02.mp3", "03.mp3", "04.mp3", "05.mp3", "06.mp3")
        ]
        df.write_manifest(album, df.OLD_RUN, entries)
        return album

    @staticmethod
    def _wrong_album(album: Path) -> Path:
        """Two provider-timed tracks on an album a fix found a wrong text on."""
        sf.add_track(album, "01.mp3", lrc=hf.lrc_of(hf.song("ro")))
        sf.add_track(album, "02.mp3", lrc=hf.lrc_of(hf.song("su")))
        df.untimed(album, "03.mp3")
        df.write_manifest(
            album, df.OLD_RUN, (df.entry("03.mp3", df.wrong_text_removed()),)
        )
        return album

    def main(self, *args: str) -> Result:
        """Run `main` with *args* and ``--library``; return the code and the output."""
        argv = [*args, "--library", str(self.library)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = sweep.main(argv)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def report(self, *args: str) -> Result:
        """Run the report over this test's strata file and log, with *args*."""
        return self.main(
            "--mode",
            "hear-report",
            "--hearing-log",
            str(self.log),
            "--strata",
            str(self.strata),
            *args,
        )


class ReportTest(ReportCase):
    """Each group, the estimate, the shift test, the strays, all read-only."""

    def test_each_group_is_tallied_with_its_rate_and_its_skips(self) -> None:
        result = self.report()
        self.assertEqual(result.code, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[:2], ["seed: 5", f"model: {hf.TURBO}"])
        self.assertIn(
            "netease: under 10 in the sample, under 10 heard; not sung under 10 "
            "(50.0% of those heard); little heard none; shifts under 10; "
            "suspect misses none",
            lines,
        )
        self.assertIn("netease, skipped, not heard: under 10", lines)
        self.assertIn("netease, skipped, frames-only: under 10", lines)
        self.assertIn(
            "wrong-text-albums: under 10 in the sample, under 10 heard; not sung under 10 "
            "(50.0% of those heard); little heard under 10; shifts under 10; "
            "suspect misses none",
            lines,
        )
        self.assertIn(
            "lrclib: under 10 in the sample, under 10 heard; not sung under 10 "
            "(100.0% of those heard); little heard none; shifts none; "
            "suspect misses under 10",
            lines,
        )

    def test_the_estimate_weights_each_drawn_rate_and_adds_the_wrong_text(
        self,
    ) -> None:
        lines = self.report().stdout.splitlines()
        # 4 NetEase tracks at 1/2, 2 LRCLIB at 1/1, and the wrong-text group's 1:
        # 5 of the library's 8 provider-timed tracks.
        self.assertIn(
            "library-wide estimate: under 10 not sung of under 10 provider-timed "
            "tracks (62.5%)",
            lines,
        )
        self.assertIn("  netease: under 10 in the library at 50.0%", lines)
        self.assertIn("  lrclib: under 10 in the library at 100.0%", lines)
        self.assertIn(
            "  wrong-text-albums: under 10 not sung as heard, of under 10", lines
        )
        self.assertIn("left out of every group, frames-only: under 10", lines)

    def test_collect_holds_the_exact_counts_the_report_rounds(self) -> None:
        found = hr.collect(
            ss.read_strata(self.strata),
            self.library,
            self.log,
            models=(hf.TURBO, None),
            tracks=lambda: hr.classified_library(self.library),
        )
        assert found is not None
        netease = found.tallies[_GROUP.NETEASE]
        self.assertEqual(
            (netease.rows, netease.heard, netease.not_sung, netease.shifts),
            (4, 2, 1, 1),
        )
        self.assertEqual(netease.skipped, {hr.NOT_HEARD: 1, "frames-only": 1})
        assert found.estimate is not None
        self.assertAlmostEqual(found.estimate.not_sung, 5)
        self.assertEqual(found.estimate.tracks, 8)
        self.assertEqual(found.genres, {"Invented Noise": 1})
        self.assertEqual((found.outside, len(found.problems)), (1, 0))

    def test_suspect_misses_are_tallied_by_genre_and_strays_counted(self) -> None:
        lines = self.report().stdout.splitlines()
        self.assertIn("suspect misses by genre tag, Invented Noise: under 10", lines)
        self.assertIn("log records in no strata row: under 10", lines)
        self.assertIn("log files unreadable: none", lines)

    def test_the_second_model_gives_each_tracks_share_and_the_crossings(
        self,
    ) -> None:
        result = self.report("--compare-model", hf.LARGE)
        self.assertEqual(result.code, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn(f"compared with {hf.LARGE}: under 10 heard by both", lines)
        self.assertIn(
            f"  {self.kept / '05.mp3'}: 0.10 not sung with {hf.TURBO}, "
            f"0.40 sung with {hf.LARGE}",
            lines,
        )
        self.assertIn(
            f"  {self.kept / '01.mp3'}: 0.10 not sung with {hf.TURBO}, "
            f"0.10 not sung with {hf.LARGE}",
            lines,
        )
        self.assertIn(f"rising to 0.25 or over with {hf.LARGE} alone: under 10", lines)

    def test_the_per_track_lines_are_marked_not_for_the_record(self) -> None:
        lines = self.report("--compare-model", hf.LARGE).stdout.splitlines()
        header = lines.index(hfm.PER_TRACK_HEADER)
        self.assertIn("not for the record", hfm.PER_TRACK_HEADER)
        # Every line naming a track follows the header, and only those lines do.
        named = [n for n, line in enumerate(lines) if str(self.library) in line]
        self.assertEqual(named, list(range(header + 1, header + 1 + len(named))))
        self.assertEqual(len(named), 2)

    def test_no_track_heard_by_both_prints_no_per_track_block(self) -> None:
        lines = self.report("--compare-model", "unheard.bin").stdout.splitlines()
        self.assertIn("compared with unheard.bin: none heard by both", lines)
        self.assertNotIn(hfm.PER_TRACK_HEADER, lines)

    def test_without_a_second_model_there_is_no_comparison(self) -> None:
        self.assertNotIn("compared with", self.report().stdout)

    def test_nothing_is_written_anywhere(self) -> None:
        library, log = df.tree_bytes(self.library), df.tree_bytes(self.log)
        strata = self.strata.read_bytes()
        self.assertEqual(self.report("--compare-model", hf.LARGE).code, 0)
        self.assertEqual(df.tree_bytes(self.library), library)
        self.assertEqual(df.tree_bytes(self.log), log)
        self.assertEqual(self.strata.read_bytes(), strata)

    def test_a_withheld_list_has_no_estimate_and_reads_no_library(self) -> None:
        row = self._row("Wrong", "03.mp3", _GROUP.WITHHELD)
        ss.write_strata(self.strata, 0, (row,))
        hf.log(
            self.log,
            hf.hearing(
                Path(row.path), _VERDICT.NOT_SUNG, kind=HeardKind.UNTIMED, share=0.1
            ),
        )
        with mock.patch.object(hr, "classified_library") as classified:
            result = self.report()
        classified.assert_not_called()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertNotIn("library-wide estimate", result.stdout)
        self.assertIn(
            "withheld: under 10 in the sample, under 10 heard;", result.stdout
        )

    def test_an_unreadable_log_file_is_counted_and_said(self) -> None:
        (self.log / f"{'a' * 64}.json").write_bytes(b"not a record")
        result = self.report()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("log files unreadable: under 10", result.stdout)
        self.assertIn("--hearing-log", result.stderr)

    def test_what_the_shift_test_cannot_read_is_counted(self) -> None:
        # A sibling sidecar that is not UTF-8, and a heard track whose album is gone.
        sf.add_track(self.kept, "08.mp3", txt=sf.NOT_UTF8)
        gone = self.library / "Invented Artist" / "Gone"
        row = ss.StrataRow(str(gone / "01.mp3"), _GROUP.LRCLIB, "Invented Artist/Gone")
        ss.write_strata(self.strata, 5, (*ss.read_strata(self.strata).rows, row))
        hf.log(self.log, hf.hearing(gone / "01.mp3", _VERDICT.NOT_SUNG))
        result = self.report()
        self.assertEqual(result.code, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn("shift test not run, album unread: under 10", lines)
        self.assertIn("sibling texts unread: under 10", lines)
        found = hr.collect(
            ss.read_strata(self.strata),
            self.library,
            self.log,
            models=(hf.TURBO, None),
            tracks=lambda: hr.classified_library(self.library),
        )
        assert found is not None
        # Not tested, so neither a shift nor a suspect miss: Kept/05's miss alone.
        lrclib = found.tallies[_GROUP.LRCLIB]
        self.assertEqual((lrclib.not_sung, lrclib.shifts, lrclib.suspects), (2, 0, 1))
        # Kept/08 is unread by Kept/01's test and by Kept/05's.
        self.assertEqual((found.untested, found.unread), (1, 2))


class ReportFailureTest(ReportCase):
    """What stops the report with exit 2, before anything prints."""

    def test_a_strata_file_that_cannot_be_read_is_exit_2(self) -> None:
        for data in (None, b"not a strata file\n"):
            with self.subTest(data=data):
                if data is None:
                    self.strata.unlink(missing_ok=True)
                else:
                    self.strata.write_bytes(data)
                result = self.report()
                self.assertEqual(result.code, 2)
                self.assertIn("--strata", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_a_log_that_is_not_a_folder_is_exit_2(self) -> None:
        for log in (self.tmp / "missing", self.strata):
            with self.subTest(log=log.name):
                self.log = log
                result = self.report()
                self.assertEqual(result.code, 2)
                self.assertIn("--hearing-log", result.stderr)

    def test_a_log_that_cannot_be_listed_is_exit_2(self) -> None:
        with mock.patch.object(hr, "list_hearings", side_effect=OSError("refused")):
            result = self.report()
        self.assertEqual(result.code, 2)
        self.assertIn("cannot be read", result.stderr)

    def test_a_strata_row_outside_the_library_is_exit_2(self) -> None:
        outside = self.tmp / "elsewhere" / "01.mp3"
        for row in (
            ss.StrataRow(str(outside), _GROUP.NETEASE, "Invented Artist/Kept"),
            ss.StrataRow(str(outside), _GROUP.NETEASE, "../elsewhere"),
            ss.StrataRow(str(self.library / "01.mp3"), _GROUP.NETEASE, "."),
        ):
            with self.subTest(album=row.album):
                ss.write_strata(self.strata, 5, (row,))
                result = self.report()
                self.assertEqual(result.code, 2)
                self.assertIn("outside --library", result.stderr)

    def test_a_strata_row_reached_through_a_link_is_exit_2(self) -> None:
        elsewhere = self.tmp / "elsewhere"
        self._kept_album(elsewhere / "Artist" / "Linked")
        artist = self.library / "Invented Artist"
        (artist / "Linked").symlink_to(elsewhere / "Artist" / "Linked")
        (self.library / "Linked Artist").symlink_to(elsewhere / "Artist")
        (artist / "Kept" / "09.mp3").symlink_to(
            elsewhere / "Artist" / "Linked" / "01.mp3"
        )
        for album, track in (
            ("Invented Artist/Kept", artist / "Kept" / "09.mp3"),
            ("Invented Artist/Linked", artist / "Linked" / "01.mp3"),
            ("Linked Artist/Linked", self.library / "Linked Artist/Linked/01.mp3"),
            ("Invented Artist", artist / "Kept" / "01.mp3"),
        ):
            with self.subTest(album=album):
                row = ss.StrataRow(str(track), _GROUP.NETEASE, album)
                ss.write_strata(self.strata, 5, (row,))
                result = self.report()
                self.assertEqual(result.code, 2)
                self.assertIn("outside --library", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_a_ledger_that_cannot_be_read_is_exit_2(self) -> None:
        with mock.patch.object(sl, "read_ledger", side_effect=OSError("refused")):
            result = self.report()
        self.assertEqual(result.code, 2)
        self.assertIn("the ledger cannot be read", result.stderr)
        self.assertEqual(result.stdout, "")


class ReportOptionsTest(ReportCase):
    """The report's options, refused elsewhere, and everything else refused by it."""

    def test_the_report_needs_a_log_and_a_strata_file(self) -> None:
        for missing, argv in (
            ("--hearing-log", ("--strata", str(self.strata))),
            ("--strata", ("--hearing-log", str(self.log))),
        ):
            with self.subTest(missing=missing):
                result = self.main("--mode", "hear-report", *argv)
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"{missing} is required with --mode hear-report", result.stderr
                )

    def test_every_other_option_is_refused_by_the_report(self) -> None:
        for argv in (
            ("--seed", "1"),
            ("--out", str(self.tmp)),
            ("--withheld",),
            ("--lang", "fra"),
            ("--offline",),
            ("--limit", "2"),
            ("--heard-dir", str(self.tmp)),
            ("--apply",),
            ("--resume", str(self.tmp / "r.tsv")),
            ("--pause-seconds", "1"),
        ):
            with self.subTest(option=argv[0]):
                result = self.report(*argv)
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"{argv[0]} cannot be used with --mode hear-report", result.stderr
                )

    def test_the_reports_options_are_refused_by_every_other_mode(self) -> None:
        for mode in ("survey", "run", "draw"):
            for argv in (
                ("--hearing-log", str(self.log)),
                ("--strata", str(self.strata)),
                ("--model", hf.LARGE),
                ("--compare-model", hf.LARGE),
            ):
                with self.subTest(mode=mode, option=argv[0]):
                    result = self.main("--mode", mode, *argv)
                    self.assertEqual(result.code, 2)
                    self.assertIn(
                        f"{argv[0]} cannot be used with --mode {mode}", result.stderr
                    )

    def test_a_comparison_with_the_same_model_is_refused(self) -> None:
        result = self.report("--compare-model", hf.TURBO)
        self.assertEqual(result.code, 2)
        self.assertIn("--compare-model names the --model", result.stderr)

    def test_a_chosen_model_is_the_one_reported(self) -> None:
        result = self.report("--model", hf.LARGE)
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn(f"model: {hf.LARGE}", result.stdout.splitlines())

    def test_the_default_model_is_the_pinned_turbo(self) -> None:
        # sweep never imports the transcribe tool, so its default names the file
        # itself; this holds it to the pin the hearing defaults to.
        loaded = set(sys.modules)
        sys.path.insert(0, str(_TRANSCRIBE_SRC))
        try:
            import at_models
        finally:
            sys.path.remove(str(_TRANSCRIBE_SRC))
            # The transcribe modules leave with their path, so no later suite finds them.
            for name in set(sys.modules) - loaded:
                del sys.modules[name]
        self.assertEqual(hr.DEFAULT_MODEL, at_models.WHISPER_MODEL_NAME)
        self.assertEqual(hr.DEFAULT_MODEL, hf.TURBO)

    def test_the_help_names_the_mode_and_its_options(self) -> None:
        result = self.main("--help")
        self.assertEqual(result.code, 0)
        for text in (
            "hear-report:",
            "--hearing-log",
            "--strata",
            "--model",
            "--compare-model",
            "Hear-report ",
        ):
            with self.subTest(text=text):
                self.assertIn(text, result.stdout)
