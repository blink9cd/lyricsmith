"""A run's breakers in the fetch layer: a step whose breaker is open is not asked.

The providers are fakes carrying a breaker as the real LRC, Genius and Bandcamp steps
do, and the clock is a list the test moves. Every state file is written under the
isolation's temporary state folder, so no test reaches the real one. Every name and
lyric is invented.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_answer as la  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breaker as lbk  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breakers as lbs  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above

R = lrf.Refusal
S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB, GENIUS = lm.Source.LRCLIB, lm.Source.GENIUS
NOW = datetime(2026, 10, 25, 9, 30, tzinfo=UTC)
QUERY = lm.TrackQuery("Invented Artist", "Lamp Room Hymn", album="Lamp Room")
NO_SIDECAR = lm.SidecarState(None)
PLAIN = "The keeper climbs the stair\nand counts the lamps there"
DEADLINE_S = 5.0


@dataclass
class Clock:
    """A clock a test moves by hand."""

    now: datetime = NOW

    def __call__(self) -> datetime:
        """The current moment."""
        return self.now


@dataclass
class GuardedFake:
    """A one-kind provider carrying a breaker; it raises its errors in turn, then answers."""

    breaker: lbs.BreakerGuard | None
    errors: list[Exception] = field(default_factory=list)
    answer: str | None = None
    calls: int = 0

    def __call__(self, query: lm.TrackQuery) -> str | None:
        """The next error raised, else the answer."""
        del query
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.answer


@dataclass
class EveryKindFake(GuardedFake):
    """A provider whose one call settles both kinds, as LRCLIB's does."""

    def answer_all_kinds(self, query: lm.TrackQuery) -> la.LrcAnswer:
        """Both kinds from one call."""
        return la.LrcAnswer.same(self(query))


def overloaded() -> lp.HttpStatusError:
    """A 503, as a busy server answers."""
    return lp.HttpStatusError(LRCLIB, 503)


class GuardCase(fixtures.IsolatedCase):
    """One guard on a moving clock, writing its state under the temporary state folder."""

    def setUp(self) -> None:
        """A closed breaker for Genius, and its file's path."""
        super().setUp()
        self.clock = Clock()
        self.environ = dict(os.environ)
        self.path = lbk.breaker_path(self.environ, GENIUS)
        self.guard = self.guard_from(lbk.CLOSED)

    def guard_from(self, state: lbk.BreakerState) -> lbs.BreakerGuard:
        """A guard starting at *state*, on this test's clock and file."""
        return lbs.BreakerGuard(lbs.BreakerSetup(self.path, state, now=self.clock))

    def fetch(
        self, provider: GuardedFake, key: lm.ProviderKey | None = None
    ) -> lm.FetchResult:
        """One track's fetch over *provider* alone, at *key* (Genius's step by default)."""
        table: dict[lm.ProviderKey, lm.Provider] = {
            key or lm.ProviderKey(GENIUS, U): provider
        }
        return lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)

    def fetch_each(self, provider: GuardedFake, tracks: int) -> list[lm.FetchResult]:
        """*tracks* fetches in a row over *provider*."""
        return [self.fetch(provider) for _ in range(tracks)]


class SkipTest(GuardCase):
    """Three refusing tracks open it; the fourth is not asked, and records why."""

    def test_an_open_breaker_skips_the_step_with_a_refusal(self) -> None:
        provider = GuardedFake(self.guard, [overloaded() for _ in range(3)], PLAIN)
        self.fetch_each(provider, 3)
        skipped = self.fetch(provider)
        self.assertEqual(provider.calls, 3)
        (tried,) = skipped.attempts
        self.assertEqual(tried.outcome, lm.AttemptOutcome.ERROR)
        self.assertEqual(tried.detail, "breaker open until 09:35:00 UTC")
        # Recorded as unsent, a fact, so a count tells it from a refusal heard.
        self.assertEqual(tried.facts, lrf.RefusalFacts(R.OVERLOADED, unsent=True))
        self.assertTrue(skipped.every_provider_failed)

    def test_a_refusal_heard_before_it_opens_is_sent(self) -> None:
        provider = GuardedFake(self.guard, [overloaded() for _ in range(3)], PLAIN)
        heard = [result.attempts[0].facts for result in self.fetch_each(provider, 3)]
        self.assertEqual(
            [facts.unsent for facts in heard if facts is not None], [False] * 3
        )

    def test_past_its_window_one_track_asks_and_an_answer_closes_it(self) -> None:
        provider = GuardedFake(self.guard, [overloaded() for _ in range(3)], PLAIN)
        self.fetch_each(provider, 3)
        self.clock.now = NOW + lbk.FIRST_WINDOW
        self.assertIsNotNone(self.fetch(provider).lyrics)
        self.assertEqual(self.guard.state, lbk.CLOSED)

    def test_a_refused_probe_reopens_it_at_double(self) -> None:
        provider = GuardedFake(self.guard, [overloaded() for _ in range(4)], PLAIN)
        self.fetch_each(provider, 3)
        self.clock.now = NOW + lbk.FIRST_WINDOW
        self.fetch_each(provider, 2)
        self.assertEqual(provider.calls, 4)
        self.assertEqual(self.guard.state.window, 2 * lbk.FIRST_WINDOW)

    def test_a_timeout_counts_as_unreachable(self) -> None:
        errors: list[Exception] = [lp.ProviderTimeout("slow") for _ in range(3)]
        provider = GuardedFake(self.guard, errors)
        self.fetch_each(provider, 3)
        self.assertEqual(self.guard.state.reason, R.UNREACHABLE)
        self.assertTrue(self.guard.state.is_open(NOW))

    def test_one_block_opens_it_at_the_cap(self) -> None:
        provider = GuardedFake(self.guard, [lp.HttpStatusError(GENIUS, 403)], PLAIN)
        self.fetch(provider)
        self.assertEqual(self.guard.state.open_until, NOW + lbk.MAX_WINDOW)
        self.assertEqual(
            self.fetch(provider).attempts[0].facts,
            lrf.RefusalFacts(R.BLOCKED, unsent=True),
        )

    def test_an_unrecognized_reply_is_neutral(self) -> None:
        odd = lp.ProviderError("odd", detail="unexpected reply")
        provider = GuardedFake(
            self.guard, [overloaded(), overloaded(), odd, overloaded()]
        )
        self.fetch_each(provider, 4)
        self.assertTrue(self.guard.state.is_open(NOW))

    def test_a_single_400_leaves_it_closed_and_the_next_track_is_asked(self) -> None:
        # A real run once lost LRCLIB for an hour to one Cloudflare 400.
        provider = GuardedFake(self.guard, [lp.HttpStatusError(GENIUS, 400)], PLAIN)
        refused = self.fetch(provider)
        facts = refused.attempts[0].facts
        self.assertEqual(facts, lrf.RefusalFacts(R.UNRECOGNIZED, 400))
        self.assertEqual(self.guard.state, lbk.CLOSED)
        self.assertIsNotNone(self.fetch(provider).lyrics)
        self.assertEqual(provider.calls, 2)

    def test_a_client_error_of_one_request_neither_counts_nor_resets(self) -> None:
        for status in (400, 404, 405, 410, 414, 422):
            with self.subTest(status=status):
                self.guard = self.guard_from(lbk.CLOSED)
                own = lp.HttpStatusError(GENIUS, status)
                provider = GuardedFake(self.guard, [overloaded(), own, overloaded()])
                self.fetch_each(provider, 3)
                self.assertEqual(self.guard.state.run, 2)
                self.assertFalse(self.guard.state.is_open(NOW))

    def test_a_client_error_barring_the_client_opens_it_at_the_cap(self) -> None:
        for status in (401, 403, 407, 451):
            with self.subTest(status=status):
                self.guard = self.guard_from(lbk.CLOSED)
                error = lp.HttpStatusError(GENIUS, status)
                self.fetch(GuardedFake(self.guard, [error]))
                self.assertEqual(self.guard.state.reason, R.BLOCKED)
                self.assertEqual(self.guard.state.open_until, NOW + lbk.MAX_WINDOW)

    def test_three_server_errors_in_a_row_open_it_as_overloaded(self) -> None:
        errors: list[Exception] = [
            lp.HttpStatusError(GENIUS, status) for status in (500, 501, 505)
        ]
        provider = GuardedFake(self.guard, errors)
        self.fetch_each(provider, 2)
        self.assertFalse(self.guard.state.is_open(NOW))
        self.fetch(provider)
        self.assertEqual(self.guard.state.reason, R.OVERLOADED)
        self.assertEqual(self.guard.state.open_until, NOW + lbk.FIRST_WINDOW)

    def test_a_provider_with_no_breaker_is_always_asked(self) -> None:
        provider = GuardedFake(None, [overloaded() for _ in range(5)])
        self.fetch_each(provider, 5)
        self.assertEqual(provider.calls, 5)


class OneCallCountsOnceTest(GuardCase):
    """A failure kept and raised again counts once, however many steps meet it."""

    def test_both_kinds_of_one_every_kind_call_count_once(self) -> None:
        provider = EveryKindFake(self.guard, [overloaded()])
        table: dict[lm.ProviderKey, lm.Provider] = {
            lm.ProviderKey(LRCLIB, S): provider,
            lm.ProviderKey(LRCLIB, U): provider,
        }
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(self.guard.state.run, 1)

    def test_a_step_reading_what_its_call_heard_is_never_skipped(self) -> None:
        # Two refusals in a row, then a track whose synced call opens the breaker: its
        # plain step still re-reads that call's failure rather than the breaker's.
        self.guard = self.guard_from(lbk.BreakerState(run=2, reason=R.OVERLOADED))
        provider = EveryKindFake(self.guard, [overloaded()])
        table: dict[lm.ProviderKey, lm.Provider] = {
            lm.ProviderKey(LRCLIB, S): provider,
            lm.ProviderKey(LRCLIB, U): provider,
        }
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertEqual([a.detail for a in result.attempts], ["HTTP 503", "HTTP 503"])
        self.assertTrue(self.guard.state.is_open(NOW))

    def test_an_album_failure_kept_for_each_track_counts_once(self) -> None:
        # Bandcamp keeps an album's failure and raises it for each of its tracks.
        provider = _Kept(self.guard)
        for _ in range(4):
            self.fetch(provider)
        self.assertEqual((provider.calls, self.guard.state.run), (4, 1))


@dataclass
class _Kept(GuardedFake):
    """A guarded provider raising one kept failure on every call."""

    failure: Exception = field(default_factory=overloaded)

    def __call__(self, query: lm.TrackQuery) -> str | None:
        """Raise the kept failure, the same object every time."""
        del query
        self.calls += 1
        raise self.failure


@dataclass
class KeepingFake(GuardedFake):
    """A guarded provider that says it keeps what it heard, as Bandcamp does an album."""

    keeps: bool = True

    def kept(self, query: lm.TrackQuery) -> bool:
        """Whether a call for *query* reads what was kept: this fake's *keeps*."""
        del query
        return self.keeps


class KeptTest(GuardCase):
    """A kept answer or failure is no evidence of the provider's health."""

    def test_a_kept_answer_leaves_the_count_unchanged(self) -> None:
        counting = lbk.BreakerState(run=2, reason=R.OVERLOADED)
        self.guard = self.guard_from(counting)
        self.fetch(KeepingFake(self.guard, answer=PLAIN))
        self.assertEqual(self.guard.state, counting)

    def test_a_kept_answer_does_not_close_a_half_open_breaker(self) -> None:
        opened = lbk.after_refusal(
            lbk.BreakerState(run=2, reason=R.OVERLOADED), R.OVERLOADED, NOW
        )
        self.guard = self.guard_from(opened)
        self.clock.now = NOW + lbk.FIRST_WINDOW
        self.fetch(KeepingFake(self.guard, answer=PLAIN))
        self.assertEqual(self.guard.state, opened)

    def test_a_kept_failure_does_not_count(self) -> None:
        self.fetch(KeepingFake(self.guard, [overloaded()]))
        self.assertEqual(self.guard.state, lbk.CLOSED)

    def test_a_kept_slot_is_read_while_the_breaker_is_open(self) -> None:
        opened = lbk.after_refusal(lbk.CLOSED, R.BLOCKED, NOW)
        self.guard = self.guard_from(opened)
        provider = KeepingFake(self.guard, answer=PLAIN)
        self.assertIsNotNone(self.fetch(provider).lyrics)
        self.assertEqual(provider.calls, 1)

    def test_a_slot_not_kept_is_guarded(self) -> None:
        provider = KeepingFake(self.guard, [overloaded()], keeps=False)
        self.fetch(provider)
        self.assertEqual(self.guard.state.run, 1)


class StateFileTest(GuardCase):
    """The guard writes its state on each change, so the next process reads it."""

    def test_the_state_carries_to_a_guard_read_from_the_file(self) -> None:
        provider = GuardedFake(self.guard, [overloaded() for _ in range(3)])
        self.fetch_each(provider, 3)
        read = lbk.read_breaker(self.path, NOW)
        self.assertEqual(read, lbk.BreakerRead(self.guard.state))
        resumed = GuardedFake(self.guard_from(read.state), answer=PLAIN)
        self.assertEqual(self.fetch(resumed).attempts[0].detail[:12], "breaker open")
        self.assertEqual(resumed.calls, 0)

    def test_nothing_is_written_while_nothing_changes(self) -> None:
        self.fetch_each(GuardedFake(self.guard, answer=PLAIN), 2)
        self.assertFalse(self.path.exists())

    def test_a_write_that_fails_is_kept_and_the_run_goes_on(self) -> None:
        self.path.mkdir(parents=True)
        provider = GuardedFake(self.guard, [overloaded()], PLAIN)
        self.fetch_each(provider, 2)
        self.assertIsNotNone(self.guard.write_error)
        self.assertEqual(self.guard.state, lbk.CLOSED)
        self.assertEqual(provider.calls, 2)


class TableGuardsTest(GuardCase):
    """The guards a table's providers carry, by source."""

    def test_the_guards_are_read_from_the_providers(self) -> None:
        other = self.guard_from(lbk.CLOSED)
        table: dict[lm.ProviderKey, lm.Provider] = {
            lm.ProviderKey(LRCLIB, S): GuardedFake(other),
            lm.ProviderKey(LRCLIB, U): GuardedFake(other),
            lm.ProviderKey(GENIUS, U): GuardedFake(self.guard),
            lm.ProviderKey(lm.Source.BANDCAMP, U): GuardedFake(None),
            lm.ProviderKey(lm.Source.TEKSTOWO, U): fixtures.empty(),
        }
        self.assertEqual(lbs.table_guards(table), {LRCLIB: other, GENIUS: self.guard})


class SetupsTest(GuardCase):
    """A run reads each guarded provider's file once, as it starts."""

    def test_each_file_is_read_and_a_broken_one_is_noticed(self) -> None:
        lbk.write_breaker(self.path, lbk.BreakerState(run=1, reason=R.OVERLOADED))
        lrclib = lbk.breaker_path(self.environ, LRCLIB)
        lrclib.write_bytes(b"not a breaker")
        notices: list[str] = []
        setups = lbs.breaker_setups(self.environ, self.clock, notices.append)
        self.assertEqual(set(setups), set(lbk.GUARDED_SOURCES))
        self.assertEqual(setups[GENIUS].state.run, 1)
        self.assertEqual(setups[LRCLIB].state, lbk.CLOSED)
        self.assertEqual(setups[LRCLIB].path, lrclib)
        (notice,) = notices
        self.assertTrue(notice.startswith("lrclib: "), notice)
        self.assertIn("breaker file ignored", notice)
