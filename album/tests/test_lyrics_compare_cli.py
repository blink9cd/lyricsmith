"""`--mode compare` end to end: the report, its strip lines, its exit, its refusals.

Each test drives the entry point with faked wiring and a provider table built by hand,
so no test reaches the network, and every title and lyric is invented. The album
folder's name holds a space, so each strip line is shown to survive a shell. The last
class calls `format_compare` on hand-built reports for the rows an album cannot easily
produce through the CLI.
"""

from __future__ import annotations

import itertools
import os
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_check  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_compare as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_summary  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tally  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

LRCLIB, NETEASE, GENIUS = lm.Source.LRCLIB, lm.Source.NETEASE, lm.Source.GENIUS
#: Each compared step's key, by source.
KEYS = {
    source: lm.ProviderKey(source, lm.LyricsKind.UNSYNCED) for source in lc.COMPARED
}
#: The program name the CLI cases run under (`cli_fakes.CliCase.main`).
PROG = "album.py"
#: One that agrees (no title word, its hint hidden), one flagged, one synced.
TRACKS = (
    TrackSpec("01 A.mp3", title="Beacon Light", txt=fixtures.LIGHTHOUSE_PLAIN),
    TrackSpec("02 B.mp3", title="Mill Wheel", txt=fixtures.LIGHTHOUSE_PLAIN),
    TrackSpec("03 C.mp3", title="Ford Stones", lrc=fixtures.LIGHTHOUSE_LRC),
)
#: What every provider answers, by title: the flagged track gets another song.
ANSWERS = {
    "Beacon Light": fixtures.LIGHTHOUSE_EDITED,
    "Mill Wheel": fixtures.ORCHARD_PLAIN,
}


def every(answers: Mapping[str, str | Exception]) -> lm.ProviderTable:
    """A table whose compared steps each answer *answers*."""
    return {KEYS[source]: run_fakes.TitleProvider(answers) for source in lc.COMPARED}


def similarity(judged: str, answer: str) -> str:
    """The similarity of *answer* to *judged*, as the report shows it."""
    words = lyrics_check.normalize_words
    return lyrics_check.shown(
        lyrics_check.word_similarity(words(judged), words(answer))
    )


class CompareCliCase(cli_fakes.CliCase):
    """The three-track album, under a folder whose name holds a space."""

    def setUp(self) -> None:
        """Build the album; record any lock taken."""
        super().setUp()
        self.album = self.tmp / "an album"
        self.paths = fixtures.build_album(self.album, TRACKS)
        self.providers = every(ANSWERS)
        self.locks: list[Path] = []
        real = self.acquire_lock

        def recording(album: Path) -> lyrics_album.HeldLock | None:
            self.locks.append(album)
            return real(album)

        self.acquire_lock = recording

    def compare(self, *extra: str, prog: str | None = None) -> cli_fakes.Result:
        """Run compare on this test's album, with *extra* options, named *prog*."""
        return self.main(
            "--album", str(self.album), "--mode", "compare", *extra, prog=prog
        )

    def rows(self, result: cli_fakes.Result) -> list[str]:
        """The table's rows, its header excluded: each opens with its row number."""
        lines = result.stdout.splitlines()[3:]
        return list(itertools.takewhile(lambda line: line[:2].strip().isdigit(), lines))


class PassedOverTest(CompareCliCase):
    """A run whose manifest cannot be read is named on stderr, never silently skipped."""

    def test_the_passed_over_run_is_a_notice(self) -> None:
        run_id = run_fakes.RUN_ID
        run_dir = self.album / lr.RUN_FOLDER_NAME / run_id
        run_dir.mkdir(parents=True)
        (run_dir / lr.MANIFEST_NAME).write_bytes(b"\xff{")
        self.overrides["read_manifest"] = lyrics_manifest.read_manifest
        result = self.compare()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn(
            f"passed over the run {lr.RUN_FOLDER_NAME}/{run_id}: its manifest "
            "cannot be read (ManifestSchemaError)\n",
            result.stderr,
        )


class ReportTest(CompareCliCase):
    """A row per track, a strip line per flag after the table, the counts and the tally."""

    def test_the_three_verdicts_and_the_counts(self) -> None:
        result = self.compare()
        self.assertEqual(result.code, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "album compare: 3 tracks under an album")
        self.assertEqual(lines[2].split(), ["#", "track", "verdict", "scores"])
        rows = self.rows(result)
        best = similarity(fixtures.LIGHTHOUSE_PLAIN, fixtures.ORCHARD_PLAIN)
        self.assertIn("  agrees  ", rows[0])
        self.assertIn(f"  FLAG: no source agrees (best {best})  ", rows[1])
        self.assertEqual(rows[2].split(), ["3", "03", "C.mp3", "-", "-"])
        self.assertEqual(
            lines[-5],
            "agrees 1 · flagged 1 · no second source 0 · errors 0 · not judged 1 "
            "· source unrecorded 2 · genius: absent",
        )
        self.assertEqual(
            lines[-4:-1], ["bandcamp: on", "youtube music: on", "tekstowo: on"]
        )
        self.assertEqual(
            lines[-1],
            "providers: LRCLIB 1/2 · NetEase 1/2 · Bandcamp 1/2 · YouTube Music 1/2 "
            "· Genius 1/2 · Tekstowo 1/2",
        )

    def test_the_flagged_rows_scores_each_truncated(self) -> None:
        rows = self.rows(self.compare())
        value = similarity(fixtures.LIGHTHOUSE_PLAIN, fixtures.ORCHARD_PLAIN)
        cell = (
            f"LRCLIB {value} · NetEase {value} · Bandcamp {value} · "
            f"YouTube Music {value} · Genius {value} · Tekstowo {value}"
        )
        self.assertIn(f"  {cell}  ", rows[1])

    def test_no_answer_a_failure_and_an_absent_provider_in_the_scores(self) -> None:
        self.providers = {
            KEYS[LRCLIB]: run_fakes.TitleProvider({"Mill Wheel": OSError("refused")}),
            KEYS[NETEASE]: run_fakes.TitleProvider({}),
        }
        result = self.compare("--track", str(self.paths[1]))
        self.assertEqual(result.code, 0, result.stderr)
        [row] = self.rows(result)
        self.assertIn("  no second source  LRCLIB ERROR · NetEase -  ", row)
        self.assertNotIn("Genius", row)
        self.assertTrue(row.endswith("no title word"))

    def test_a_strip_line_after_the_table_for_each_flag_round_trips_a_shell(
        self,
    ) -> None:
        result = self.compare()
        lines = result.stdout.splitlines()
        strips = [line for line in lines if line.startswith("strip #")]
        self.assertEqual(len(strips), 1)
        label, command = strips[0].split(": ", 1)
        self.assertEqual(label, "strip #2")
        self.assertEqual(
            shlex.split(command),
            [PROG, "--album", str(self.album), "--mode", "strip", "--track",
             str(self.album / "02 B.mp3")],
        )  # fmt: skip
        # Straight below the table.
        self.assertEqual(lines.index(strips[0]), 3 + len(self.rows(result)))

    def test_through_the_dispatcher_the_strip_line_names_its_two_words(self) -> None:
        result = self.compare(prog="lyricsmith album")
        [strip] = [
            line for line in result.stdout.splitlines() if line.startswith("strip #")
        ]
        self.assertEqual(
            shlex.split(strip.split(": ", 1)[1]),
            ["lyricsmith", "album", "--album", str(self.album), "--mode", "strip",
             "--track", str(self.album / "02 B.mp3")],
        )  # fmt: skip

    def test_hints_show_on_the_flagged_row_alone_never_on_agreeing(self) -> None:
        rows = self.rows(self.compare())
        self.assertNotIn(lc.NO_TITLE_WORD, rows[0])
        self.assertTrue(rows[1].endswith(f"; {lc.NO_TITLE_WORD}"))
        self.assertNotIn(lc.NO_TITLE_WORD, rows[2])

    def test_track_limits_compare_to_one_row(self) -> None:
        result = self.compare("--track", str(self.paths[0]))
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(len(self.rows(result)), 1)
        self.assertTrue(result.stdout.startswith("album compare: 1 track "))


class ErrorTest(CompareCliCase):
    """A track every provider failed for is an error row, and exit 1."""

    def test_every_provider_failing_is_an_error_row(self) -> None:
        self.providers = every({"Mill Wheel": OSError("refused")})
        result = self.compare("--track", str(self.paths[1]))
        self.assertEqual(result.code, 1)
        [row] = self.rows(result)
        self.assertIn("  ERROR fetch: every provider failed (6 errors)  ", row)
        self.assertIn("errors 1", result.stdout)


class RefusalTest(CompareCliCase):
    """Compare refuses what it has no use for, and takes the options it reads."""

    def test_each_refused_option_exits_two(self) -> None:
        for extra in (
            ("--run-id", "x"),
            ("--dry-run",),
            ("--offline",),
            ("--fresh",),
            ("--refetch-untimed",),
            ("--changed-list", str(self.tmp / "changed.txt")),
        ):
            with self.subTest(option=extra[0]):
                result = self.compare(*extra)
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"{extra[0]} cannot be used with --mode compare", result.stderr
                )
        self.assertFalse((self.tmp / "changed.txt").exists())

    def test_lang_and_no_genius_are_accepted(self) -> None:
        result = self.compare("--lang", "deu", "--no-genius")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertTrue(result.stdout.splitlines()[-5].endswith("genius: skipped"))

    def test_no_genius_leaves_genius_out_of_the_provider_table(self) -> None:
        # The fake factory receives the key the real one builds Genius from: none, so
        # no Genius step, though the environment holds one.
        deadline = cli_fakes.album.DEADLINE_S
        with mock.patch.dict(os.environ, {"GENIUS_ACCESS_TOKEN": "not-a-real-key"}):
            self.compare()
            self.compare("--no-genius")
        (with_key, _), skipped = self.provider_calls
        self.assertEqual(with_key and with_key.reveal(), "not-a-real-key")
        self.assertEqual(skipped, (None, deadline))


class WritesNothingTest(CompareCliCase):
    """Compare leaves the album byte for byte, makes no run folder and takes no lock."""

    def test_nothing_written_and_no_lock(self) -> None:
        before = {p: p.read_bytes() for p in self.album.rglob("*") if p.is_file()}
        self.assertEqual(self.compare().code, 0)
        after = {p: p.read_bytes() for p in self.album.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())
        self.assertEqual(self.locks, [])


class FormatCompareTest(fixtures.IsolatedCase):
    """`format_compare` on rows the CLI cases above do not reach."""

    def test_a_reason_a_timeout_and_a_plain_no_second_source(self) -> None:
        timeout = lm.AttemptOutcome.TIMEOUT
        report = lc.CompareReport(
            (
                lc.ComparedTrack(
                    "01 A.mp3",
                    lc.CompareVerdict.NO_SECOND_SOURCE,
                    reason="no provider asked",
                ),
                lc.ComparedTrack(
                    "02 B.mp3",
                    lc.CompareVerdict.NO_SECOND_SOURCE,
                    (lc.ProviderScore(GENIUS, timeout, None),),
                    hints=("few words (x)", lc.NO_TITLE_WORD),
                ),
            ),
            (lyrics_tally.ProviderCount(GENIUS, 0, 1, 1),),
        )
        text = lyrics_summary.format_compare(
            report,
            album=Path("an album"),
            prog=PROG,
            command=PROG,
            genius="configured",
        )
        lines = text.splitlines()
        verdict = "no second source (no provider asked)"
        self.assertEqual(lines[3], f" 1  01 A.mp3  {verdict}  -")
        self.assertEqual(
            lines[4],
            f" 2  02 B.mp3  {'no second source':<{len(verdict)}}  Genius ERROR  "
            "few words (x); no title word",
        )
        self.assertNotIn("strip #", text)
        self.assertEqual(
            lines[-2],
            "agrees 0 · flagged 0 · no second source 2 · errors 0 · not judged 0 "
            "· source unrecorded 0 · genius: configured",
        )

    def test_a_prog_holding_a_space_round_trips_a_shell(self) -> None:
        answered = lm.AttemptOutcome.ANSWERED
        flagged = lc.ComparedTrack(
            "01 A.mp3",
            lc.CompareVerdict.NO_SOURCE_AGREES,
            (lc.ProviderScore(LRCLIB, answered, 0.25),),
        )
        album, prog = Path("an album"), "my tools/album.py"
        # Run alone, the program name is one word whatever it holds.
        text = lyrics_summary.format_compare(
            lc.CompareReport((flagged,), ()),
            album=album,
            prog=prog,
            command=lyrics_summary.command_prefix(prog, None),
            genius="absent",
        )
        [strip] = [line for line in text.splitlines() if line.startswith("strip #")]
        self.assertEqual(
            shlex.split(strip.split(": ", 1)[1]),
            [prog, "--album", str(album), "--mode", "strip", "--track",
             str(album / "01 A.mp3")],
        )  # fmt: skip
