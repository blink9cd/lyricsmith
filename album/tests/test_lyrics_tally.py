"""The per-provider tally: how many tracks each provider was asked for, and won.

Pure records in, records out, so each case builds its attempts by hand. A provider is
counted once per track however many of its steps ran, synced and plain alike. Every
name here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tally as lt  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB, NETEASE, GENIUS = lm.Source.LRCLIB, lm.Source.NETEASE, lm.Source.GENIUS
#: Every provider the real table holds with a Genius key.
EVERY = frozenset({LRCLIB, NETEASE, GENIUS})
#: A provider these tables leave out, as the default table does.
YTM = lm.Source.YOUTUBE_MUSIC


def attempt(
    source: lm.Source, kind: lm.LyricsKind, outcome: lm.AttemptOutcome
) -> lm.ProviderAttempt:
    """One attempt of *source*'s *kind* step that ended as *outcome*."""
    return lm.ProviderAttempt(lm.ProviderKey(source, kind), False, outcome, "")


EMPTY = lm.AttemptOutcome.EMPTY
ANSWERED = lm.AttemptOutcome.ANSWERED
ERROR = lm.AttemptOutcome.ERROR
TIMEOUT = lm.AttemptOutcome.TIMEOUT


def counts(tally: tuple[lt.ProviderCount, ...]) -> dict[lm.Source, tuple[int, int]]:
    """Each provider's (accepted, asked), a provider out of the run's table left out."""
    return {c.source: (c.accepted, c.asked) for c in tally if c.asked is not None}


class TallyTest(fixtures.IsolatedCase):
    """Counting each provider once per track, and its answer only when it was kept."""

    def test_a_provider_is_counted_once_per_track_over_both_its_steps(self) -> None:
        track = (
            attempt(LRCLIB, S, EMPTY),
            attempt(NETEASE, S, EMPTY),
            attempt(LRCLIB, U, EMPTY),
            attempt(NETEASE, U, ANSWERED),
        )
        tally = lt.tally_providers(EVERY, [lt.TrackFetch(track, NETEASE)])
        self.assertEqual(
            counts(tally), {LRCLIB: (0, 1), NETEASE: (1, 1), GENIUS: (0, 0)}
        )

    def test_the_chain_order_is_kept_whatever_order_the_table_holds(self) -> None:
        tally = lt.tally_providers(frozenset({GENIUS, NETEASE, LRCLIB}), [])
        self.assertEqual(
            [c.source for c in tally],
            [LRCLIB, NETEASE, YTM, lm.Source.BANDCAMP, GENIUS, lm.Source.TEKSTOWO],
        )

    def test_a_declined_or_missing_answer_is_asked_but_not_accepted(self) -> None:
        answered = (attempt(NETEASE, S, ANSWERED),)
        tracks = [lt.TrackFetch(answered, None), lt.TrackFetch(answered, NETEASE)]
        self.assertEqual(counts(lt.tally_providers(EVERY, tracks))[NETEASE], (1, 2))

    def test_a_provider_outside_the_table_has_no_count(self) -> None:
        tally = lt.tally_providers(frozenset({LRCLIB}), [])
        self.assertEqual(
            [(c.source, c.asked) for c in tally],
            [
                (LRCLIB, 0),
                (NETEASE, None),
                (YTM, None),
                (lm.Source.BANDCAMP, None),
                (GENIUS, None),
                (lm.Source.TEKSTOWO, None),
            ],
        )


class RefusedTallyTest(fixtures.IsolatedCase):
    """Counting the tracks each provider refused or failed for, once per track."""

    def test_a_refusing_provider_counts_once_per_track_over_both_its_steps(
        self,
    ) -> None:
        refused = (
            attempt(LRCLIB, S, TIMEOUT),
            attempt(NETEASE, S, ERROR),
            attempt(LRCLIB, U, EMPTY),
            attempt(NETEASE, U, ERROR),
        )
        answered = (attempt(LRCLIB, S, EMPTY), attempt(NETEASE, S, ANSWERED))
        tracks = [lt.TrackFetch(refused, None), lt.TrackFetch(answered, NETEASE)]
        tally = lt.tally_providers(EVERY, tracks)
        self.assertEqual(
            {c.source: c.refused for c in tally},
            {
                LRCLIB: 1,
                NETEASE: 1,
                YTM: 0,
                lm.Source.BANDCAMP: 0,
                GENIUS: 0,
                lm.Source.TEKSTOWO: 0,
            },
        )
        self.assertEqual(counts(tally)[NETEASE], (1, 2))

    def test_an_empty_answer_is_not_a_refusal(self) -> None:
        track = (attempt(LRCLIB, S, EMPTY), attempt(GENIUS, U, EMPTY))
        tally = lt.tally_providers(EVERY, [lt.TrackFetch(track, None)])
        self.assertEqual([c.refused for c in tally], [0, 0, 0, 0, 0, 0])


def blocked(kind: lm.LyricsKind, *, unsent: bool) -> lm.ProviderAttempt:
    """A Genius *kind* step refused as blocked, *unsent* when an open breaker skipped it."""
    facts = lrf.RefusalFacts(lrf.Refusal.BLOCKED, unsent=unsent)
    return lm.ProviderAttempt(lm.ProviderKey(GENIUS, kind), False, ERROR, "", facts)


class BreakerOpenTallyTest(fixtures.IsolatedCase):
    """A track whose only refusals went unsent counts as breaker open, not refused."""

    def genius(self, *tracks: tuple[lm.ProviderAttempt, ...]) -> lt.ProviderCount:
        """Genius's tally over one fetch per track of *tracks*."""
        fetches = [lt.TrackFetch(track, None) for track in tracks]
        tally = lt.tally_providers(EVERY, fetches)
        return next(count for count in tally if count.source is GENIUS)

    def test_one_block_and_the_lookups_it_left_unsent(self) -> None:
        tracks = [(blocked(U, unsent=False),)] + [(blocked(U, unsent=True),)] * 20
        count = self.genius(*tracks)
        self.assertEqual((count.refused, count.breaker_open), (1, 20))
        self.assertEqual(count.describe(), "Genius 0/21 (1 refused, 20 breaker open)")

    def test_a_track_with_a_sent_refusal_beside_an_unsent_one_is_refused(
        self,
    ) -> None:
        count = self.genius((blocked(S, unsent=False), blocked(U, unsent=True)))
        self.assertEqual((count.refused, count.breaker_open), (1, 0))

    def test_an_attempt_kept_without_facts_counts_as_refused(self) -> None:
        count = self.genius((attempt(GENIUS, U, TIMEOUT),))
        self.assertEqual((count.refused, count.breaker_open), (1, 0))

    def test_breaker_open_sits_between_refused_and_retried(self) -> None:
        count = lt.ProviderCount(GENIUS, 0, 9, refused=1, retried=2, breaker_open=8)
        self.assertEqual(
            count.describe(), "Genius 0/9 (1 refused, 8 breaker open, 2 retried)"
        )


class TallyLineTest(fixtures.IsolatedCase):
    """The summary's providers line."""

    def test_the_line_shows_accepted_over_asked_and_a_dash_for_an_absent_one(
        self,
    ) -> None:
        tally = (
            lt.ProviderCount(LRCLIB, 5, 12),
            lt.ProviderCount(NETEASE, 7, 7),
            lt.ProviderCount(GENIUS, 0, None),
        )
        self.assertEqual(
            lt.format_tally(tally),
            "providers: LRCLIB 5/12 · NetEase 7/7 · Genius -",
        )

    def test_a_provider_that_refused_shows_how_many_tracks_it_refused(self) -> None:
        tally = (
            lt.ProviderCount(LRCLIB, 0, 12, refused=12),
            lt.ProviderCount(NETEASE, 5, 12, refused=3),
            lt.ProviderCount(GENIUS, 2, 4),
        )
        self.assertEqual(
            lt.format_tally(tally),
            "providers: LRCLIB 0/12 (12 refused) · NetEase 5/12 (3 refused)"
            " · Genius 2/4",
        )


class TrackCountTest(fixtures.IsolatedCase):
    """The plural the summary's header and the run's progress line share."""

    def test_one_track_is_not_plural(self) -> None:
        self.assertEqual(lt.track_count(1), "1 track")

    def test_more_or_none_is_plural(self) -> None:
        self.assertEqual(
            (lt.track_count(0), lt.track_count(3)), ("0 tracks", "3 tracks")
        )


class RunTallyLineTest(cli_fakes.CliCase):
    """A run's summary carries the line, after the counts and before the backup line."""

    def test_a_provider_that_answers_nothing_useful_shows_none_accepted(self) -> None:
        fixtures.build_album(
            self.album,
            (TrackSpec("01 A.mp3", title="One"), TrackSpec("02 B.mp3", title="Two")),
        )
        canned = run_fakes.TitleProvider({})
        found = run_fakes.TitleProvider(
            dict.fromkeys(("One", "Two"), fixtures.LIGHTHOUSE_LRC)
        )
        self.providers = {
            lm.ProviderKey(LRCLIB, S): canned,
            lm.ProviderKey(LRCLIB, U): canned,
            lm.ProviderKey(NETEASE, S): found,
        }
        result = self.run_album()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines()[-2],
            "providers: LRCLIB 0/2 · NetEase 2/2 (2 synced) · YouTube Music - · Bandcamp - · Genius - · Tekstowo -",
        )
        self.assertEqual(len(canned.calls), 2)

    def test_a_track_asked_by_its_file_name_is_not_counted_for_the_lrc_providers(
        self,
    ) -> None:
        # No artist tag on one, no title tag on the other: both are asked by file
        # name, with no artist, which the LRC adapters send no request for.
        fixtures.build_album(
            self.album,
            (TrackSpec("01 A.mp3", artist=None), TrackSpec("02 B.mp3", title=None)),
        )
        sent: list[str] = []

        def answer(request: requests.PreparedRequest) -> requests.Response:
            sent.append(request.url or "")
            return http_fakes.response(request, 404)

        self.providers = ltable.build_provider_table(None)
        with http_fakes.transport(answer):
            result = self.run_album()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines()[-2],
            "providers: LRCLIB 0/0 · NetEase 0/0 · YouTube Music - · Bandcamp - · Genius - · Tekstowo -",
        )
        self.assertEqual(sent, [])
