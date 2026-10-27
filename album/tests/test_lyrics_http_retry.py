"""The LRC providers' bounded retry: a transient refusal gets another try, within the call.

No test here reaches the network or sleeps: requests go through a real session whose
transport answers from memory (`http_fakes`), and the waits go to a fake clock that
moves only when slept on (`genius_fakes.FakeClock`).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above

#: A reserved name the transport fake answers, so nothing is ever resolved.
URL = "https://example.invalid/api"
#: More time than every scheduled wait together, so the deadline never cuts one.
ROOMY_S = 100.0

#: One scripted answer: a status (with any headers), or an exception the send raises.
Scripted = int | tuple[int, dict[str, str]] | Exception


class Script:
    """A transport answering each request with the next scripted answer, in order."""

    def __init__(self, answers: Sequence[Scripted]) -> None:
        """Answer the requests with *answers*; a 200 carries an empty JSON object."""
        self.answers = list(answers)
        self.sent = 0

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """The next answer: raised when an exception, else that status and headers."""
        answer = self.answers[self.sent]
        self.sent += 1
        if isinstance(answer, Exception):
            raise answer
        status, headers = answer if isinstance(answer, tuple) else (answer, {})
        return http_fakes.response(request, status, b"{}", headers)


class RetryCase(fixtures.IsolatedCase):
    """GETs through a real session, retried on a fake clock."""

    def setUp(self) -> None:
        """A fake clock, and a policy that waits on it."""
        super().setUp()
        self.clock = genius_fakes.FakeClock()
        self.policy = lh.RetryPolicy(clock=self.clock, sleeper=self.clock.sleep)

    def get(
        self,
        answers: Sequence[Scripted],
        *,
        bound_s: float = ROOMY_S,
        absent: frozenset[int] = frozenset(),
    ) -> tuple[lh.Reply | None, Script]:
        """GET URL answered by *answers*, retrying within *bound_s*; the reply, the script."""
        script = Script(answers)
        retry = self.policy.within(bound_s)
        with lh.new_session() as session, http_fakes.transport(script):
            reply = lh.get_json(
                session, URL, {}, lm.Source.LRCLIB, absent=absent, retry=retry
            )
        return reply, script

    def refusal(
        self, answers: Sequence[Scripted], *, bound_s: float = ROOMY_S
    ) -> tuple[Exception, Script]:
        """The error a GET answered by *answers* raises in the end, and the script."""
        script = Script(answers)
        retry = self.policy.within(bound_s)
        with (
            lh.new_session() as session,
            http_fakes.transport(script),
            self.assertRaises(Exception) as raised,
        ):
            lh.get_reply(session, URL, {}, lm.Source.LRCLIB, retry=retry)
        return raised.exception, script


class TransientRefusalTest(RetryCase):
    """A 429, 502, 503 or 504, or a dropped connection, is tried again."""

    def test_each_transient_status_is_retried(self) -> None:
        for status in (429, 502, 503, 504):
            with self.subTest(status=status):
                self.clock.sleeps.clear()
                reply, script = self.get([status, 200])
                self.assertEqual((reply, script.sent), (lh.Reply(URL, {}), 2))
                self.assertEqual(self.clock.sleeps, [lh.RETRY_WAITS_S[0]])

    def test_a_dropped_connection_is_retried(self) -> None:
        reply, script = self.get([requests.ConnectionError("reset"), 200])
        self.assertEqual((reply, script.sent), (lh.Reply(URL, {}), 2))

    def test_the_waits_escalate_until_an_answer(self) -> None:
        reply, script = self.get([503, 503, 503, 200])
        self.assertEqual((reply, script.sent), (lh.Reply(URL, {}), 4))
        self.assertEqual(self.clock.sleeps, list(lh.RETRY_WAITS_S))

    def test_the_schedule_is_two_five_and_ten_seconds(self) -> None:
        self.assertEqual(lh.RETRY_WAITS_S, (2.0, 5.0, 10.0))

    def test_a_status_the_caller_falls_back_on_is_not_retried(self) -> None:
        reply, script = self.get([503], absent=frozenset({503}))
        self.assertEqual((reply, script.sent, self.clock.sleeps), (None, 1, []))


class FinalRefusalTest(RetryCase):
    """Only the last failure is raised, its detail naming the retries made."""

    def test_a_refusal_every_retry_met_names_them_all(self) -> None:
        error, script = self.refusal([503, 503, 503, 503])
        self.assertEqual(script.sent, 4)
        self.assertEqual(lp.error_detail(error), "HTTP 503 after 3 retries")

    def test_a_dropped_connection_every_time_names_its_type(self) -> None:
        dropped = [requests.ConnectionError("reset") for _ in range(4)]
        error, _ = self.refusal(dropped)
        self.assertIsInstance(error, lp.ProviderError)
        self.assertEqual(lp.error_detail(error), "ConnectionError after 3 retries")

    def test_another_failure_after_a_retry_names_the_one_retry(self) -> None:
        error, script = self.refusal([503, 404])
        self.assertEqual(script.sent, 2)
        self.assertEqual(lp.error_detail(error), "HTTP 404 after 1 retry")

    def test_another_client_error_fails_at_once(self) -> None:
        for status in (400, 403, 404, 410):
            with self.subTest(status=status):
                self.clock.sleeps.clear()
                error, script = self.refusal([status])
                self.assertIsInstance(error, lp.HttpStatusError)
                self.assertEqual(lp.error_detail(error), f"HTTP {status}")
                self.assertEqual((script.sent, self.clock.sleeps), (1, []))

    def test_a_request_timeout_or_a_certificate_failure_fails_at_once(self) -> None:
        failures: list[Exception] = [
            requests.ConnectTimeout("slow"),
            requests.ReadTimeout("slow"),
            requests.exceptions.SSLError("bad certificate"),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                error, script = self.refusal([failure])
                self.assertIs(error, failure)
                self.assertEqual(script.sent, 1)

    def test_a_redirect_is_not_retried(self) -> None:
        error, script = self.refusal([(301, {"Location": "https://example.invalid/"})])
        self.assertEqual((lp.error_detail(error), script.sent), ("HTTP 301", 1))

    def test_with_no_retry_a_transient_status_fails_at_once(self) -> None:
        script = Script([503, 200])
        with (
            lh.new_session() as session,
            http_fakes.transport(script),
            self.assertRaises(lp.HttpStatusError) as raised,
        ):
            lh.get_reply(session, URL, {}, lm.Source.LRCLIB)
        self.assertEqual((raised.exception.status, script.sent), (503, 1))


class DeadlineTest(RetryCase):
    """A retry whose wait would end past the call's bound is not made."""

    def test_the_schedule_stops_where_the_bound_would_pass(self) -> None:
        error, script = self.refusal([503, 503, 503, 503], bound_s=6.0)
        self.assertEqual((script.sent, self.clock.sleeps), (2, [2.0]))
        self.assertEqual(lp.error_detail(error), "HTTP 503 after 1 retry")

    def test_a_wait_ending_exactly_at_the_bound_is_not_begun(self) -> None:
        error, script = self.refusal([503, 200], bound_s=lh.RETRY_WAITS_S[0])
        self.assertIsInstance(error, lp.HttpStatusError)
        self.assertEqual((script.sent, self.clock.sleeps), (1, []))

    def test_no_room_for_the_first_wait_is_the_plain_refusal(self) -> None:
        error, script = self.refusal([503, 200], bound_s=1.0)
        self.assertIsInstance(error, lp.HttpStatusError)
        self.assertEqual((lp.error_detail(error), script.sent), ("HTTP 503", 1))

    def test_the_time_the_requests_take_counts_against_the_bound(self) -> None:
        def slow_send(request: requests.PreparedRequest) -> requests.Response:
            self.clock.now += 4.0
            return http_fakes.response(request, 503)

        retry = self.policy.within(10.0)
        with (
            lh.new_session() as session,
            http_fakes.transport(slow_send),
            self.assertRaises(lp.ProviderError) as raised,
        ):
            lh.get_reply(session, URL, {}, lm.Source.LRCLIB, retry=retry)
        # 4 s, a 2 s wait, 4 s more: 10 s spent, so the 5 s wait is not begun.
        self.assertEqual(self.clock.sleeps, [2.0])
        self.assertEqual(lp.error_detail(raised.exception), "HTTP 503 after 1 retry")


class RetryAfterTest(RetryCase):
    """A Retry-After in seconds replaces the scheduled wait, up to a cap."""

    def test_a_retry_after_replaces_the_wait(self) -> None:
        self.get([(503, {"Retry-After": "7"}), 200])
        self.assertEqual(self.clock.sleeps, [7.0])

    def test_a_long_retry_after_is_capped(self) -> None:
        self.get([(429, {"Retry-After": "120"}), 200])
        self.assertEqual(self.clock.sleeps, [lh.RETRY_AFTER_CAP_S])
        self.assertEqual(lh.RETRY_AFTER_CAP_S, 10.0)

    def test_a_date_or_garbled_retry_after_keeps_the_schedule(self) -> None:
        for value in ("Wed, 21 Oct 2026 07:28:00 GMT", "-3", "soon", "²"):
            with self.subTest(value=value):
                self.clock.sleeps.clear()
                self.get([(503, {"Retry-After": value}), 200])
                self.assertEqual(self.clock.sleeps, [lh.RETRY_WAITS_S[0]])

    def test_the_hook_reads_the_header_off_the_refusal(self) -> None:
        self.assertEqual(lp.retry_after_s(" 5 "), 5.0)
        self.assertIsNone(lp.retry_after_s(None))
        self.assertIsNone(lp.retry_after_s(""))

    def test_a_retry_after_past_its_digit_bound_is_none(self) -> None:
        # 309 digits overflowed float(); 4301 passed int()'s digit limit. Both raised
        # inside the response hook, where the docstring promises None.
        longest = "9" * lp.MAX_RETRY_AFTER_DIGITS
        self.assertEqual(lp.retry_after_s(longest), float(longest))
        for digits in (lp.MAX_RETRY_AFTER_DIGITS + 1, 309, 5000):
            with self.subTest(digits=digits):
                self.assertIsNone(lp.retry_after_s("9" * digits))
