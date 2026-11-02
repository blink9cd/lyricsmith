"""The LRCLIB step's search by ``q``, after the field search fails with a server error.

The fakes are `test_lyrics_lrclib`'s, so no test here reaches the network either, and
every name and lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lhttp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_lrclib as ll  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lr  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_lrclib import (  # noqa: E402 - import follows the sys.path bootstrap above
    NO_ALBUM,
    PLAIN_TEXT,
    QUERY,
    SYNCED_TEXT,
    FakeLrclib,
    S,
    U,
    _ask,
    _step,
    record,
)

#: The field search's path, then the same after each of its three retries.
FIELD_SEARCHES = ["/api/search"] * 4
#: The search by ``q`` the fallback sends: the artist, then the title.
Q_PARAMS = {"q": ["Invented Artist The Keeper Counts"]}
#: How far under the floor the just-under budget is: well above float rounding.
JUST_UNDER_S = 0.01


def _per_request_s(budget_s: float) -> float:
    """Each field search's time that leaves the search by ``q`` *budget_s* of the call.

    The four field searches and the waits between them spend the rest of the default
    deadline, and the headroom is kept back from what is left.
    """
    spent_s = lhttp.PROVIDER_DEADLINE_S - sum(lhttp.RETRY_WAITS_S)
    return (spent_s - ll.Q_SEARCH_HEADROOM_S - budget_s) / len(FIELD_SEARCHES)


class QSearchFallbackTest(fixtures.IsolatedCase):
    """A field search failing with a server error asks the search by ``q`` once.

    Its answer is the search's, an empty list included; its failure leaves the field
    search's failure as the provider's, so the refusal reads exactly as it did.
    """

    def test_a_503_field_search_then_a_q_match_finds_the_text(self) -> None:
        server = FakeLrclib(hits=503, q_hits=[record()])
        self.assertEqual(_ask(server, S, NO_ALBUM), SYNCED_TEXT)
        self.assertEqual(server.paths(), [*FIELD_SEARCHES, "/api/search"])
        self.assertEqual(server.params(4), Q_PARAMS)

    def test_the_q_search_follows_a_503_lookup_and_field_search(self) -> None:
        server = FakeLrclib(exact=503, hits=503, q_hits=[record()])
        self.assertEqual(_ask(server, U), PLAIN_TEXT)
        self.assertEqual(server.paths(), ["/api/get", *FIELD_SEARCHES, "/api/search"])

    def test_an_empty_q_search_is_no_match_and_no_error(self) -> None:
        server = FakeLrclib(exact=503, hits=503, q_hits=[])
        self.assertIsNone(_ask(server, S))

    def test_q_hits_go_through_the_matcher(self) -> None:
        server = FakeLrclib(hits=503, q_hits=[record(artist="Other Singer")])
        self.assertIsNone(_ask(server, S, NO_ALBUM))

    def test_a_failing_q_search_leaves_todays_overloaded_refusal(self) -> None:
        malformed = [record() | {"trackName": 5}]
        for q_hits in (503, 500, 404, {"error": "x"}, malformed, b"not json"):
            with self.subTest(q_hits=q_hits):
                server = FakeLrclib(hits=503, q_hits=q_hits)
                with self.assertRaises(lp.RetriedError) as raised:
                    _ask(server, S, NO_ALBUM)
                self.assertEqual(lp.http_status(raised.exception.last), 503)
                self.assertEqual(raised.exception.detail, "HTTP 503 after 3 retries")
                self.assertEqual(server.paths(), [*FIELD_SEARCHES, "/api/search"])

    def test_the_chain_records_both_failing_as_lrclib_overloaded(self) -> None:
        table = {lm.ProviderKey(lm.Source.LRCLIB, S): _step(S)}
        with http_fakes.transport(FakeLrclib(exact=503, hits=503)):
            result = lf.fetch_lyrics(
                QUERY, lm.SidecarState(None), table, deadline_s=lf.PROVIDER_DEADLINE_S
            )
        self.assertEqual(
            [(a.outcome, a.detail) for a in result.attempts],
            [(lm.AttemptOutcome.ERROR, "HTTP 503 after 3 retries")],
        )
        facts = result.attempts[0].facts
        assert facts is not None
        self.assertEqual((facts.reason, facts.retries), (lr.Refusal.OVERLOADED, 3))

    def test_a_q_search_whose_request_fails_leaves_the_overloaded_refusal(
        self,
    ) -> None:
        for failure in (requests.ConnectionError("reset"), requests.Timeout("slow")):
            with self.subTest(failure=type(failure).__name__):
                server = FakeLrclib(hits=503, q_hits=[record()])

                def failing(request: requests.PreparedRequest) -> requests.Response:
                    answer = server(request)
                    if "q" in server.params(len(server.requests) - 1):
                        raise failure
                    return answer

                with (
                    http_fakes.transport(failing),
                    self.assertRaises(lp.RetriedError) as raised,
                ):
                    _step(S)(NO_ALBUM)
                self.assertEqual(raised.exception.detail, "HTTP 503 after 3 retries")
                self.assertEqual(server.paths(), [*FIELD_SEARCHES, "/api/search"])

    def test_an_answering_field_search_sends_no_q_search(self) -> None:
        server = FakeLrclib(hits=[record()], q_hits=503)
        self.assertEqual(_ask(server, S, NO_ALBUM), SYNCED_TEXT)
        self.assertEqual(server.paths(), ["/api/search"])

    def test_a_field_search_failing_but_not_overloaded_sends_no_q_search(
        self,
    ) -> None:
        for status in (404, 429):
            with self.subTest(status=status):
                server = FakeLrclib(hits=status, q_hits=[record()])
                with self.assertRaises(lp.ProviderError):
                    _ask(server, S, NO_ALBUM)
                self.assertNotIn("q", server.params(len(server.requests) - 1))

    def test_with_no_retry_policy_one_of_each_search_is_sent(self) -> None:
        server = FakeLrclib(hits=503, q_hits=[record()])
        with http_fakes.transport(server):
            self.assertEqual(ll.LrclibProvider(S)(NO_ALBUM), SYNCED_TEXT)
        self.assertEqual(server.paths(), ["/api/search", "/api/search"])

    def test_no_q_search_is_sent_once_the_calls_deadline_has_passed(self) -> None:
        clock = genius_fakes.FakeClock()
        server = FakeLrclib(hits=503, q_hits=[record()])
        deadline_s = 30.0

        def slow(request: requests.PreparedRequest) -> requests.Response:
            clock.now += deadline_s + 1.0
            return server(request)

        policy = lhttp.RetryPolicy(clock=clock, sleeper=clock.sleep)
        step = ll.LrclibProvider(S, retry=policy, deadline_s=deadline_s)
        with (
            http_fakes.transport(slow),
            self.assertRaises(lp.HttpStatusError) as raised,
        ):
            step(NO_ALBUM)
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(server.paths(), ["/api/search"])


class QSearchDeadlineTest(fixtures.IsolatedCase):
    """The search by ``q`` fits in what the call has left, or is not sent."""

    def ask(self, per_request_s: float) -> tuple[FakeLrclib, list[object], str | None]:
        """The server, each request's timeout, and the detail of any error raised.

        The field search is refused, and each request takes *per_request_s* on a fake
        clock, so the field search spends a known part of the 30-second call.
        """
        clock = genius_fakes.FakeClock()
        server = FakeLrclib(hits=503, q_hits=[])
        timeouts: list[object] = []

        def slow(request: requests.PreparedRequest) -> requests.Response:
            clock.now += per_request_s
            return server(request)

        policy = lhttp.RetryPolicy(clock=clock, sleeper=clock.sleep)
        step = ll.LrclibProvider(S, retry=policy, deadline_s=lhttp.PROVIDER_DEADLINE_S)
        with http_fakes.transport(slow, timeouts):
            try:
                self.assertIsNone(step(NO_ALBUM))
            except lp.RetriedError as error:
                return server, timeouts, error.detail
        return server, timeouts, None

    def test_the_q_searchs_timeout_is_capped_at_the_time_left(self) -> None:
        # Four 2-second searches and 17 seconds of waits leave 5 of the 30.
        server, timeouts, detail = self.ask(2.0)
        self.assertEqual(
            (server.paths(), detail), ([*FIELD_SEARCHES, "/api/search"], None)
        )
        self.assertEqual(timeouts[:4], [lhttp.REQUEST_TIMEOUT_S] * 4)
        # Both halves cut by the same share, so together they take the 5 seconds left
        # less the headroom kept for the watchdog's lag and the unwinding.
        budget_s = 5.0 - ll.Q_SEARCH_HEADROOM_S
        share = budget_s / sum(lhttp.REQUEST_TIMEOUT_S)
        self.assertEqual(timeouts[4], tuple(s * share for s in lhttp.REQUEST_TIMEOUT_S))

    def test_a_budget_exactly_at_the_floor_still_sends_the_q_search(self) -> None:
        server, timeouts, detail = self.ask(_per_request_s(ll.Q_SEARCH_MIN_BUDGET_S))
        self.assertEqual(
            (server.paths(), detail), ([*FIELD_SEARCHES, "/api/search"], None)
        )
        share = ll.Q_SEARCH_MIN_BUDGET_S / sum(lhttp.REQUEST_TIMEOUT_S)
        self.assertEqual(timeouts[4], tuple(s * share for s in lhttp.REQUEST_TIMEOUT_S))

    def test_a_budget_just_under_the_floor_sends_no_q_search(self) -> None:
        budget_s = ll.Q_SEARCH_MIN_BUDGET_S - JUST_UNDER_S
        server, _timeouts, detail = self.ask(_per_request_s(budget_s))
        self.assertEqual(
            (server.paths(), detail), (FIELD_SEARCHES, "HTTP 503 after 3 retries")
        )

    def test_a_silent_q_search_times_out_before_the_calls_deadline(self) -> None:
        # The waits and the whole default timeout fill the call exactly, so only the
        # headroom keeps a q search that waits out its timeout inside the deadline.
        clock = genius_fakes.FakeClock()
        server = FakeLrclib(hits=503, q_hits=[])
        timeouts: list[object] = []
        deadline_s = sum(lhttp.RETRY_WAITS_S) + sum(lhttp.REQUEST_TIMEOUT_S)
        give_up_at = clock.now + deadline_s

        def silent(request: requests.PreparedRequest) -> requests.Response:
            answer = server(request)
            if "q" in server.params(len(server.requests) - 1):
                allowed = timeouts[-1]
                assert isinstance(allowed, tuple)
                clock.now += sum(allowed)
                raise requests.Timeout("no byte within the timeout")
            return answer

        policy = lhttp.RetryPolicy(clock=clock, sleeper=clock.sleep)
        step = ll.LrclibProvider(S, retry=policy, deadline_s=deadline_s)
        with (
            http_fakes.transport(silent, timeouts),
            self.assertRaises(lp.RetriedError) as raised,
        ):
            step(NO_ALBUM)
        self.assertLess(clock.now, give_up_at)
        self.assertEqual(server.paths(), [*FIELD_SEARCHES, "/api/search"])
        self.assertEqual(lp.http_status(raised.exception.last), 503)
        self.assertEqual(raised.exception.detail, "HTTP 503 after 3 retries")

    def test_no_q_search_is_sent_with_less_than_the_floor_left(self) -> None:
        # Four 3-second searches and 17 seconds of waits leave 1 of the 30.
        self.assertLess(1.0 - ll.Q_SEARCH_HEADROOM_S, ll.Q_SEARCH_MIN_BUDGET_S)
        server, _timeouts, detail = self.ask(3.0)
        self.assertEqual(
            (server.paths(), detail), (FIELD_SEARCHES, "HTTP 503 after 3 retries")
        )
