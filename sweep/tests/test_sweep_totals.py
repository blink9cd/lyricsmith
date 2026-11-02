"""The final report's removed column, owed albums and total lines, and the progress line.

The totals are summed over the albums the sweep visited, in the album summary's words
and order: written by category, removed by reason, refusals and retries by provider and
reason, each line printed only when it counts something. Every 25 albums visited, a
progress line says how many were visited, done and owed, and the refusals so far.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import lyrics_categories as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal_tally as lrt  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run as sr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_totals  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_survey import AlbumSurvey  # noqa: E402 - follows the sys.path bootstrap
from sweep_tally import Counts  # noqa: E402 - import follows the sys.path bootstrap above

_LRCLIB, _GENIUS = lm.Source.LRCLIB, lm.Source.GENIUS
_OVERLOADED = lrf.Refusal.OVERLOADED
_BLOCKED, _BREAKER_OPEN = lrf.Refusal.BLOCKED, lrt.Unsent.BREAKER_OPEN


def _entry(
    album: str,
    outcome: sl.Outcome = sl.Outcome.DONE,
    *,
    counts: Counts | None = None,
    owed: tuple[lm.Source, ...] = (),
    refusals: lrt.RefusalCounts | None = None,
    retried: dict[lm.Source, dict[lrf.Refusal, int]] | None = None,
) -> sl.LedgerEntry:
    """An entry for *album* that ended as *outcome*, with the fields given."""
    return sl.LedgerEntry(
        album,
        None,
        0,
        outcome,
        "t",
        counts=counts,
        owed_providers=owed,
        refusals=refusals or {},
        retried=retried or {},
    )


class ReportCase(sf.IsolatedCase):
    """The final report of visits made of *entries*."""

    def report(self, *entries: sl.LedgerEntry) -> list[str]:
        visits = tuple(sr.Visit(self.library, entry) for entry in entries)
        report = sr.SweepReport(visits, 0, 0, sr.StopReason.FINISHED)
        text = sweep_report.format_sweep(
            report, library=self.library, options=sf.OPTIONS, undo_prog=("al",)
        )
        return text.splitlines()


class RemovedColumnTest(ReportCase):
    """Removed tracks have a column of their own, after not found."""

    def test_removed_follows_not_found(self) -> None:
        counts = Counts(written=1, not_found=2, removed=6, no_lyrics_expected=3)
        header, row = self.report(_entry("A/B", counts=counts))[1:3]
        self.assertIn("  not found  removed  no lyrics expected  ", header)
        # written, unchanged, not found, removed, no lyrics expected, silent, kept,
        # errors, flagged, refusing: each count under its own column.
        self.assertEqual(
            row.split()[-10:], ["1", "0", "2", "6", "3", "0", "0", "0", "0", "0"]
        )

    def test_a_visit_with_no_manifest_shows_a_dash_for_removed_too(self) -> None:
        row = self.report(_entry("A/B", sl.Outcome.NO_RECORD))[2]
        self.assertEqual(row.split()[-10:], ["-"] * 10)


class OwedLinesTest(ReportCase):
    """Each owed album is named with what it owes, by the providers' labels."""

    def test_an_owed_album_names_its_providers(self) -> None:
        lines = self.report(
            _entry("A/B", sl.Outcome.OWED, owed=(_LRCLIB, _GENIUS)), _entry("A/C")
        )
        self.assertIn("owed", lines[2].split())
        self.assertIn("A/B  owes: LRCLIB, Genius", lines)
        self.assertFalse(any(line.startswith("A/C  owes") for line in lines))

    def test_a_refused_album_s_providers_are_not_said_to_be_owed(self) -> None:
        lines = self.report(_entry("A/B", sl.Outcome.REFUSED, owed=(_LRCLIB,)))
        self.assertFalse(any("owes:" in line for line in lines))


class TotalLinesTest(ReportCase):
    """The totals below the table, in the album summary's words and order."""

    def test_the_totals_sum_every_visit(self) -> None:
        first = Counts(
            written=3,
            removed=1,
            written_by={lc.WrittenCategory.UPGRADED: 2, lc.WrittenCategory.EMBEDDED: 1},
            removed_by={lc.RemovedReason.PLACEHOLDER: 1},
        )
        second = Counts(
            written=1,
            removed=2,
            written_by={lc.WrittenCategory.NEW_SYNCED: 1},
            removed_by={lc.RemovedReason.PAST_END: 2},
        )
        lines = self.report(
            _entry(
                "A/B",
                counts=first,
                refusals={_LRCLIB: {_OVERLOADED: 2}},
                retried={_LRCLIB: {_OVERLOADED: 7}},
            ),
            _entry(
                "A/C",
                counts=second,
                refusals={
                    _GENIUS: {lrf.Refusal.BLOCKED: 1},
                    _LRCLIB: {_OVERLOADED: 1, lrf.Refusal.UNREACHABLE: 1},
                },
                retried={_GENIUS: {lrf.Refusal.RATE_LIMITED: 2}},
            ),
        )
        self.assertIn("written: upgraded 2, new synced 1, embedded 1", lines)
        self.assertIn("removed: placeholder 1, past end 2", lines)
        self.assertIn(
            "refusals: LRCLIB overloaded 3, unreachable 1; Genius blocked 1", lines
        )
        self.assertIn("retried: LRCLIB overloaded 7; Genius rate limited 2", lines)

    def test_lookups_a_breaker_left_unsent_total_apart(self) -> None:
        lines = self.report(
            _entry("A/B", refusals={_GENIUS: {_BLOCKED: 1, _BREAKER_OPEN: 4}}),
            _entry("A/C", refusals={_GENIUS: {_BREAKER_OPEN: 16}}),
        )
        self.assertIn("refusals: Genius blocked 1, breaker open 20", lines)

    def test_removals_withheld_total_on_their_own_line(self) -> None:
        lines = self.report(
            _entry("A/B", counts=Counts(kept=2, withheld=2)),
            _entry("A/C", counts=Counts(kept=1, withheld=1)),
        )
        self.assertIn("removal withheld: spoken word 3", lines)

    def test_a_total_that_counts_nothing_prints_no_line(self) -> None:
        lines = self.report(_entry("A/B", counts=Counts(not_found=1)), _entry("A/C"))
        for label in (
            "written:",
            "removed:",
            "removal withheld:",
            "refusals:",
            "retried:",
        ):
            with self.subTest(label=label):
                self.assertFalse(any(line.startswith(label) for line in lines))

    def test_the_totals_come_right_below_the_table(self) -> None:
        counts = Counts(written=1, written_by={lc.WrittenCategory.UPGRADED: 1})
        lines = self.report(_entry("A/B", counts=counts))
        self.assertEqual(lines[3], "written: upgraded 1")


class ProgressLineTest(sf.IsolatedCase):
    """The progress line's words, from the entries so far."""

    def test_it_counts_the_albums_visited_done_and_owed_and_the_refusals(
        self,
    ) -> None:
        entries = [
            _entry("A/1"),
            _entry("A/2", sl.Outcome.OWED, refusals={_LRCLIB: {_OVERLOADED: 2}}),
            _entry("A/3", sl.Outcome.REFUSED, refusals={_GENIUS: {_OVERLOADED: 1}}),
        ]
        self.assertEqual(
            sweep_totals.progress_line(entries),
            "progress: 3 albums visited, 1 done, 1 owed; "
            "refusals: LRCLIB overloaded 2; Genius overloaded 1",
        )

    def test_lookups_a_breaker_left_unsent_count_apart(self) -> None:
        entries = [
            _entry("A/1", sl.Outcome.REFUSED, refusals={_GENIUS: {_BLOCKED: 1}}),
            _entry("A/2", sl.Outcome.OWED, refusals={_GENIUS: {_BREAKER_OPEN: 20}}),
        ]
        self.assertEqual(
            sweep_totals.progress_line(entries),
            "progress: 2 albums visited, 0 done, 1 owed; "
            "refusals: Genius blocked 1, breaker open 20",
        )

    def test_with_no_refusal_it_names_none(self) -> None:
        self.assertEqual(
            sweep_totals.progress_line([_entry("A/1")]),
            "progress: 1 album visited, 1 done, 0 owed",
        )

    def test_the_album_count_takes_its_plural(self) -> None:
        self.assertEqual(
            [sweep_totals.album_count(n) for n in (0, 1, 2)],
            ["0 albums", "1 album", "2 albums"],
        )

    def test_the_interval_is_twenty_five_albums(self) -> None:
        self.assertEqual(sweep_totals.PROGRESS_EVERY_ALBUMS, 25)


class ProgressDuringASweepTest(sf.SweepCase):
    """A sweep says the progress line after its 25th album, and not before."""

    def setUp(self) -> None:
        """Twenty-five albums a run visits, A1 to A25, A2 owed LRCLIB."""
        super().setUp()
        self.albums = [
            sf.needy_album(self.library, "Artist", f"A{n:02d}") for n in range(1, 26)
        ]
        self.fake.scripts["A02"] = sf.Script(
            outcomes=(
                sf.not_found(sf.refusal(_LRCLIB, "HTTP 503", _OVERLOADED)),
                sf.written(lm.Source.NETEASE),
            )
        )

    def _progress(self, limit: int) -> list[str]:
        seams = sr.SweepSeams(
            run_child=self.fake,
            sleep=self.sleep,
            now=sf.fixed_now,
            progress=self.notes.append,
            entry=self.tmp / "album.py",
        )
        # A stop-after no run of refused albums here reaches.
        options = replace(sf.OPTIONS, limit=limit, stop_after=99)
        surveys: Sequence[AlbumSurvey] = [AlbumSurvey(a, none=1) for a in self.albums]
        sr.sweep(self.library, surveys, self.ledger, options, seams)
        return [note for note in self.notes if note.startswith("progress:")]

    def test_no_progress_line_after_24_albums(self) -> None:
        self.assertEqual(self._progress(24), [])

    def test_one_progress_line_after_the_25th_album(self) -> None:
        self.assertEqual(
            self._progress(25),
            [
                "progress: 25 albums visited, 24 done, 1 owed; "
                "refusals: LRCLIB overloaded 1"
            ],
        )
