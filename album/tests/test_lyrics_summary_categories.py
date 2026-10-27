"""The run summary's ``written:`` and ``removed:`` lines, and the provider line's kinds.

The two lines follow the counts line, each only when it counts something; the provider
line splits each provider's accepted answers by kind before its refused and retried
counts. Every name and lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refetch as lrefetch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_summary  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tally as lt  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh import FreshCliCase  # noqa: E402 - follows the sys.path bootstrap

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB, NETEASE = lm.Source.LRCLIB, lm.Source.NETEASE
WRITTEN, REMOVED = lr.OutcomeStatus.WRITTEN, lr.OutcomeStatus.REMOVED
_UNTIMED = lr.TrackBefore(
    (lr.FrameExport(lm.FrameKind.USLT, "eng", "", 3, None, None, "frames/01.txt"),), ()
)
_NOTHING = lr.TrackBefore((), ())


def entry(
    name: str, before: lr.TrackBefore, after: lr.TrackOutcome
) -> lr.ManifestTrack:
    """A reached track named *name*."""
    return lr.ManifestTrack(name, (2, 4), before, after)


def summary(*tracks: lr.ManifestTrack) -> list[str]:
    """The run summary's lines for *tracks*."""
    report = lyrics_run.RunReport(run_fakes.RUN_ID, tracks)
    text = lyrics_summary.format_run(
        report, album=Path("album"), prog="album", command="album", genius="absent"
    )
    return text.splitlines()


def counts_at(lines: list[str]) -> int:
    """The index of the counts line."""
    return next(i for i, line in enumerate(lines) if line.startswith("synced "))


class SummaryLinesTest(fixtures.IsolatedCase):
    """The lines sit right after the counts line, non-zero entries only."""

    def test_both_lines_follow_the_counts_line(self) -> None:
        lines = summary(
            entry("01.mp3", _UNTIMED, lr.TrackOutcome(WRITTEN, LRCLIB, S)),
            entry("02.mp3", _NOTHING, lr.TrackOutcome(WRITTEN, LRCLIB, S)),
            entry("03.mp3", _NOTHING, lr.TrackOutcome(WRITTEN, lm.Source.SIDECAR, S)),
            entry(
                "04.mp3",
                _NOTHING,
                lr.TrackOutcome(REMOVED, implausible=lrefetch.PLACEHOLDER),
            ),
        )
        at = counts_at(lines)
        self.assertEqual(
            lines[at + 1 : at + 3],
            ["written: upgraded 1, new synced 1, embedded 1", "removed: placeholder 1"],
        )

    def test_no_written_and_no_removed_is_no_line(self) -> None:
        lines = summary(
            entry("01.mp3", _NOTHING, lr.TrackOutcome(lr.OutcomeStatus.NOT_FOUND))
        )
        self.assertFalse(
            any(line.startswith(("written:", "removed:")) for line in lines)
        )

    def test_removed_alone_prints_its_line_alone(self) -> None:
        lines = summary(entry("01.mp3", _NOTHING, lr.TrackOutcome(REMOVED)))
        at = counts_at(lines)
        self.assertEqual(lines[at + 1], "removed: other 1")
        self.assertFalse(any(line.startswith("written:") for line in lines))


class KindSplitTest(fixtures.IsolatedCase):
    """The provider line splits accepted answers by kind, then refused and retried."""

    def test_the_parts(self) -> None:
        cases = [
            (
                lt.ProviderCount(LRCLIB, 30, 50, 3, retried=7, synced=24, untimed=6),
                "LRCLIB 30/50 (24 synced, 6 untimed; 3 refused, 7 retried)",
            ),
            (lt.ProviderCount(LRCLIB, 4, 5, synced=4), "LRCLIB 4/5 (4 synced)"),
            (
                lt.ProviderCount(LRCLIB, 1, 5, 2, untimed=1),
                "LRCLIB 1/5 (1 untimed; 2 refused)",
            ),
            (lt.ProviderCount(LRCLIB, 0, 5, 2), "LRCLIB 0/5 (2 refused)"),
            (lt.ProviderCount(LRCLIB, 0, 5), "LRCLIB 0/5"),
            (lt.ProviderCount(LRCLIB, 0, None), "LRCLIB -"),
        ]
        for count, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(count.describe(), expected)

    def test_the_tally_counts_each_accepted_answer_by_its_kind(self) -> None:
        asked = (
            lm.ProviderAttempt(
                lm.ProviderKey(LRCLIB, S), False, lm.AttemptOutcome.ANSWERED, ""
            ),
        )
        fetches = [
            lt.TrackFetch(asked, LRCLIB, S),
            lt.TrackFetch(asked, LRCLIB, S),
            lt.TrackFetch(asked, LRCLIB, U),
            lt.TrackFetch(asked, None, None),
            lt.TrackFetch(asked, LRCLIB),
        ]
        tally = lt.tally_providers(frozenset({LRCLIB, NETEASE}), fetches)
        lrclib = next(count for count in tally if count.source is LRCLIB)
        self.assertEqual(
            (lrclib.accepted, lrclib.asked, lrclib.synced, lrclib.untimed), (4, 5, 2, 1)
        )


class CliTest(FreshCliCase):
    """A real run prints the categories and the kinds."""

    def test_a_run_s_lines(self) -> None:
        fixtures.build_album(
            self.album,
            (
                TrackSpec(
                    "01 Keeper.mp3", title="The Keeper", lrc=fixtures.LIGHTHOUSE_LRC
                ),
                TrackSpec("02 Mill.mp3", title="The Mill"),
                TrackSpec("03 Reed.mp3", title="The Reed"),
            ),
        )
        self.providers = run_fakes.table(
            synced={"The Mill": fixtures.LIGHTHOUSE_LRC},
            plain={"The Reed": fixtures.ORCHARD_PLAIN},
        )
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK)
        lines = result.stdout.splitlines()
        at = counts_at(lines)
        self.assertEqual(
            lines[at + 1], "written: new synced 1, embedded 1, new untimed 1"
        )
        providers = next(line for line in lines if line.startswith("providers:"))
        self.assertIn("LRCLIB 2/2 (1 synced, 1 untimed)", providers)
