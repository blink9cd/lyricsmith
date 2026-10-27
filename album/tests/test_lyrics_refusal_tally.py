"""The album summary's ``refusals:`` line: tracks refused, by provider and reason.

Built from a manifest's tracks alone, so the sweep can count the same way. Each track
counts once per provider and reason, however many of that provider's steps refused it.
Every name is invented.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal_tally as lrt  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh import FreshCliCase  # noqa: E402 - follows the sys.path bootstrap

R = lrf.Refusal
S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB, GENIUS, TEKSTOWO = lm.Source.LRCLIB, lm.Source.GENIUS, lm.Source.TEKSTOWO
MUSIXMATCH = lm.Source.MUSIXMATCH
BREAKER_OPEN = lrt.Unsent.BREAKER_OPEN


def refusal(source: lm.Source, step: lm.LyricsKind, reason: R) -> lr.ProviderRefusal:
    """One refusal of *source*'s *step*, of *reason*."""
    return lr.ProviderRefusal(source, step, "invented", reason)


def track(name: str, *refusals: lr.ProviderRefusal) -> lr.ManifestTrack:
    """A not-found track that met *refusals*."""
    after = lr.TrackOutcome(lr.OutcomeStatus.NOT_FOUND, refusals=refusals)
    return lr.ManifestTrack(name, (2, 4), lr.TrackBefore((), ()), after)


class CountTest(fixtures.IsolatedCase):
    """Each track counts once per provider and reason."""

    def test_both_steps_of_one_provider_count_once(self) -> None:
        tracks = [
            track(
                "01.mp3",
                refusal(LRCLIB, S, R.OVERLOADED),
                refusal(LRCLIB, U, R.OVERLOADED),
            ),
            track("02.mp3", refusal(LRCLIB, S, R.OVERLOADED)),
        ]
        self.assertEqual(lrt.count_refusals(tracks), {LRCLIB: {R.OVERLOADED: 2}})

    def test_two_reasons_on_one_track_count_once_each(self) -> None:
        tracks = [
            track(
                "01.mp3",
                refusal(LRCLIB, S, R.OVERLOADED),
                refusal(LRCLIB, U, R.UNREACHABLE),
            )
        ]
        counts = lrt.count_refusals(tracks)
        self.assertEqual(counts, {LRCLIB: {R.OVERLOADED: 1, R.UNREACHABLE: 1}})

    def test_a_track_never_reached_or_unrefused_counts_nothing(self) -> None:
        unreached = lr.ManifestTrack("01.mp3", None, None)
        self.assertEqual(lrt.count_refusals([unreached, track("02.mp3")]), {})


class UnsentCountTest(fixtures.IsolatedCase):
    """A step an open breaker skipped counts as breaker open, never as its reason."""

    def test_a_block_and_the_lookups_it_left_unsent_count_apart(self) -> None:
        unsent = replace(refusal(GENIUS, U, R.BLOCKED), unsent=True)
        tracks = [track("01.mp3", refusal(GENIUS, U, R.BLOCKED))]
        tracks += [track(f"{n:02d}.mp3", unsent) for n in range(2, 22)]
        counts = lrt.count_refusals(tracks)
        self.assertEqual(counts, {GENIUS: {R.BLOCKED: 1, BREAKER_OPEN: 20}})
        self.assertEqual(
            lrt.format_refusals(counts), "refusals: Genius blocked 1, breaker open 20"
        )

    def test_one_track_counts_once_under_each_key_it_met(self) -> None:
        unsent = replace(refusal(LRCLIB, U, R.OVERLOADED), unsent=True)
        tracks = [track("01.mp3", refusal(LRCLIB, S, R.OVERLOADED), unsent, unsent)]
        self.assertEqual(
            lrt.count_refusals(tracks), {LRCLIB: {R.OVERLOADED: 1, BREAKER_OPEN: 1}}
        )

    def test_breaker_open_follows_every_class(self) -> None:
        counts: lrt.RefusalCounts = {
            LRCLIB: {BREAKER_OPEN: 2, R.UNRECOGNIZED: 1, R.RATE_LIMITED: 3}
        }
        self.assertEqual(
            lrt.format_refusals(counts),
            "refusals: LRCLIB rate limited 3, unrecognized 1, breaker open 2",
        )


class RefusalKeyTest(fixtures.IsolatedCase):
    """A key is read back from its words, as the sweep's ledger stores it."""

    def test_every_key_reads_back_from_its_words(self) -> None:
        for key in (*R, *lrt.Unsent):
            with self.subTest(key=key):
                self.assertIs(lrt.refusal_key(key.value), key)

    def test_words_naming_no_key_are_refused(self) -> None:
        for words in ("breaker", "Blocked", ""):
            with self.subTest(words=words), self.assertRaises(ValueError):
                lrt.refusal_key(words)


class LineTest(fixtures.IsolatedCase):
    """The line lists providers in the chain's order, each reason in the class order."""

    def test_the_line(self) -> None:
        counts = {
            GENIUS: {R.BLOCKED: 1},
            LRCLIB: {R.UNREACHABLE: 1, R.OVERLOADED: 12},
        }
        self.assertEqual(
            lrt.format_refusals(counts),
            "refusals: LRCLIB overloaded 12, unreachable 1; Genius blocked 1",
        )

    def test_no_refusals_is_no_line(self) -> None:
        self.assertIsNone(lrt.format_refusals({}))

    def test_another_label_opens_the_same_line(self) -> None:
        # The sweep's totals count retries by provider and reason the same way.
        counts = {LRCLIB: {R.OVERLOADED: 7}}
        self.assertEqual(
            lrt.format_refusals(counts, label="retried:"),
            "retried: LRCLIB overloaded 7",
        )

    def test_a_provider_no_step_asks_any_more_comes_last(self) -> None:
        counts = {MUSIXMATCH: {R.BLOCKED: 1}, TEKSTOWO: {R.UNRECOGNIZED: 2}}
        self.assertEqual(
            lrt.format_refusals(counts),
            "refusals: Tekstowo unrecognized 2; Musixmatch blocked 1",
        )


class SummaryTest(FreshCliCase):
    """The run summary prints the line under the providers line, and only with refusals."""

    def setUp(self) -> None:
        """A two-track album."""
        super().setUp()
        fixtures.build_album(
            self.album,
            (
                TrackSpec("01 One.mp3", title="One"),
                TrackSpec("02 Two.mp3", title="Two"),
            ),
        )

    def lines(self) -> list[str]:
        """The summary's lines from a run of the album."""
        result = self.run_album()
        self.assertIn(result.code, (album.EXIT_OK, album.EXIT_TRACK_ERRORS))
        return result.stdout.splitlines()

    def test_refusals_are_counted_per_track(self) -> None:
        refusing = fixtures.raising(lp.HttpStatusError(LRCLIB, 503))
        self.providers = {
            lm.ProviderKey(LRCLIB, S): refusing,
            lm.ProviderKey(LRCLIB, U): refusing,
        }
        lines = self.lines()
        at = next(i for i, line in enumerate(lines) if line.startswith("providers:"))
        self.assertEqual(lines[at + 1], "refusals: LRCLIB overloaded 2")

    def test_no_refusals_print_no_line(self) -> None:
        self.providers = run_fakes.table()
        self.assertFalse(any(line.startswith("refusals:") for line in self.lines()))
