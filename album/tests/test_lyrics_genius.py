"""The Genius step: title and artist, the minimum interval, the challenge retry schedule.

No test here reaches the network: the Genius client is a scripted fake (`genius_fakes`),
or the real client built and never asked, and the clock and sleeper are injected, so the
pacing and its 403 / 429 retry schedule run in no time at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    FAKE_TOKEN,
    QUERY,
    FakeClock,
    FakeGeniusClient,
    FakeSong,
    challenge,
    paced_genius,
)


class GeniusDeadlineTest(fixtures.IsolatedCase):
    """The step's whole-call bound: room for every attempt and every wait between."""

    def test_the_genius_step_is_given_room_for_its_retries(self) -> None:
        genius = lg.GeniusProvider(FakeGeniusClient([]), deadline_s=30.0)
        # Four attempts of 30 s plus the 3 s pacing each, then 15 + 45 + 90 s of waits.
        self.assertEqual(genius.step_deadline_s(), 282.0)
        self.assertIsInstance(genius, lf.OwnsDeadline)

    def test_the_bound_is_derivable_without_a_provider(self) -> None:
        # A caller sizing its own bound around a run has no key and builds no step.
        for deadline_s in (30.0, 7.5):
            genius = lg.GeniusProvider(FakeGeniusClient([]), deadline_s=deadline_s)
            self.assertEqual(
                lg.genius_step_deadline_s(deadline_s), genius.step_deadline_s()
            )


class GeniusAdapterTest(fixtures.IsolatedCase):
    """Title and artist, the minimum interval, and the challenge retry schedule."""

    def test_it_searches_by_title_and_artist_and_returns_the_lyrics(self) -> None:
        clock = FakeClock()
        client = FakeGeniusClient([FakeSong("words")])
        self.assertEqual(paced_genius(client, clock)(QUERY), "words")
        self.assertEqual(
            client.calls, [("The Keeper Counts", "Invented Artist", 1000.0)]
        )
        self.assertEqual(clock.sleeps, [])

    def test_no_song_is_none(self) -> None:
        self.assertIsNone(paced_genius(FakeGeniusClient([None]), FakeClock())(QUERY))

    def test_calls_keep_the_minimum_interval_on_the_monotonic_clock(self) -> None:
        clock = FakeClock()
        client = FakeGeniusClient([None, None, None])
        genius = paced_genius(client, clock)
        genius(QUERY)
        clock.now += 1.0
        genius(QUERY)
        clock.now += 10.0
        genius(QUERY)
        self.assertEqual(clock.sleeps, [lg.GENIUS_MIN_INTERVAL_S - 1.0])
        times = [when for _, _, when in client.calls]
        self.assertEqual(times, [1000.0, 1000.0 + lg.GENIUS_MIN_INTERVAL_S, 1013.0])

    def test_a_challenge_is_retried_on_the_escalating_schedule(self) -> None:
        for status in sorted(lg.GENIUS_CHALLENGE_STATUSES):
            with self.subTest(status=status):
                clock = FakeClock()
                client = FakeGeniusClient(
                    [challenge(status), challenge(status), FakeSong("words")]
                )
                self.assertEqual(paced_genius(client, clock)(QUERY), "words")
                self.assertEqual(clock.sleeps, [15.0, 45.0])

    def test_the_schedule_is_fifteen_forty_five_ninety_then_an_error(self) -> None:
        self.assertEqual(lg.GENIUS_RETRY_WAITS_S, (15.0, 45.0, 90.0))
        self.assertEqual(lg.GENIUS_CHALLENGE_STATUSES, frozenset({403, 429}))
        clock = FakeClock()
        client = FakeGeniusClient([challenge(429) for _ in range(4)])
        with self.assertRaises(lg.GeniusChallengeError):
            paced_genius(client, clock)(QUERY)
        self.assertEqual(clock.sleeps, [15.0, 45.0, 90.0])
        self.assertEqual(len(client.calls), 4)

    def test_pacing_and_the_call_run_under_the_providers_lock(self) -> None:
        clock = FakeClock()
        held: list[bool] = []
        client = FakeGeniusClient([None, None])
        genius = paced_genius(client, clock)

        def search_song(title: str | None = None, artist: str = "") -> None:
            held.append(genius.lock.locked())

        def sleep(seconds: float) -> None:
            held.append(genius.lock.locked())
            clock.sleep(seconds)

        client.search_song = search_song  # type: ignore[method-assign] # observe the lock
        # The second call's pace wait is the sleep observed: the pacer's own sleeper.
        genius.sleeper = genius.pacer.sleeper = sleep
        genius(QUERY)
        genius(QUERY)
        self.assertEqual(held, [True, True, True])
        self.assertFalse(genius.lock.locked())

    def test_other_errors_are_raised_at_once(self) -> None:
        for error in (challenge(404), ValueError(403), challenge(500)):
            with self.subTest(error=repr(error)):
                clock = FakeClock()
                client = FakeGeniusClient([error])
                with self.assertRaises(type(error)):
                    paced_genius(client, clock)(QUERY)
                self.assertEqual(clock.sleeps, [])


class RealGeniusClientTest(fixtures.IsolatedCase):
    """The real client, built offline: our timeout, no retry of its own, headers kept."""

    def test_the_real_genius_client_is_built_offline_with_our_settings(self) -> None:
        client = lg.build_genius_client(lr.Secret(FAKE_TOKEN))
        self.assertEqual(client.timeout, lg.GENIUS_TIMEOUT_S)  # type: ignore[attr-defined] # the real client's attribute
        self.assertEqual(client.retries, 0)  # type: ignore[attr-defined] # the real client's attribute
        self.assertFalse(client.remove_section_headers)  # type: ignore[attr-defined] # the real client's attribute
