"""A refusal's class and facts, read once from the error that ended a provider step.

`classify` reads only what each error carries, its type and its attributes, never its
detail text: an HTTP status and the reply's ``Retry-After`` and ``Server``, the retries a
retried request made, a cooling client's own class, and the connection errors and
timeouts that never reached a server. Reading a detail back, for a manifest written
before the facts were kept, is `lyrics_refusal_detail`'s, tested beside it.
Every value here is invented.
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooling_client as lcc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above

R = lrf.Refusal
F = lrf.RefusalFacts
LRCLIB = lm.Source.LRCLIB


class ReExportsTest(fixtures.IsolatedCase):
    """The class and the status mapping live here; the modules re-exporting them agree."""

    def test_the_cooling_client_re_exports_them(self) -> None:
        self.assertIs(lcc.Refusal, lrf.Refusal)
        self.assertIs(lcc.status_refusal, lrf.status_refusal)

    def test_the_records_re_export_the_refusal_record(self) -> None:
        self.assertIs(lr.ProviderRefusal, lrf.ProviderRefusal)
        self.assertEqual(lr.MAX_REFUSAL_DETAIL_CHARS, lrf.MAX_REFUSAL_DETAIL_CHARS)


class StoredWordsTest(fixtures.IsolatedCase):
    """A manifest stores each class by its words."""

    def test_a_class_is_stored_by_its_words(self) -> None:
        self.assertEqual(
            [member.value for member in R],
            ["rate limited", "overloaded", "unreachable", "blocked", "unrecognized"],
        )


class ClassifyStatusTest(fixtures.IsolatedCase):
    """An HTTP status error: its status's class, its status and its reply's headers."""

    def test_each_status_has_its_class(self) -> None:
        cases = {
            429: R.RATE_LIMITED,
            502: R.OVERLOADED,
            503: R.OVERLOADED,
            504: R.OVERLOADED,
            403: R.BLOCKED,
            404: R.UNRECOGNIZED,
            500: R.OVERLOADED,
        }
        for status, reason in cases.items():
            with self.subTest(status=status):
                error = lp.HttpStatusError(LRCLIB, status)
                self.assertEqual(lrf.classify(error), F(reason, status))

    def test_a_refused_redirect_is_unrecognized(self) -> None:
        for status in (301, 302, 307, 399):
            with self.subTest(status=status):
                error = lp.HttpStatusError(LRCLIB, status)
                self.assertEqual(lrf.classify(error), F(R.UNRECOGNIZED, status))

    def test_the_asked_wait_and_the_server_are_kept(self) -> None:
        error = lp.HttpStatusError(LRCLIB, 503, 3600.0, server="edge-cache")
        self.assertEqual(
            lrf.classify(error),
            F(R.OVERLOADED, 503, retry_after_s=3600.0, server="edge-cache"),
        )


class StatusRefusalTest(fixtures.IsolatedCase):
    """Each refusing status's class: a 4xx barring the client, or not; any 5xx."""

    def assert_classes(self, statuses: tuple[int, ...], reason: lrf.Refusal) -> None:
        """Assert each of *statuses* is classed as *reason*."""
        for status in statuses:
            with self.subTest(status=status):
                self.assertIs(lrf.status_refusal(status), reason)

    def test_a_client_error_that_bars_the_client_is_blocked(self) -> None:
        self.assert_classes((401, 403, 407, 451), R.BLOCKED)

    def test_too_many_requests_is_rate_limited(self) -> None:
        self.assert_classes((429,), R.RATE_LIMITED)

    def test_any_other_client_error_is_that_request_s_own(self) -> None:
        statuses = (400, 402, 404, 405, 406, 408, 409, 410, 414, 418, 422, 499)
        self.assert_classes(statuses, R.UNRECOGNIZED)

    def test_every_server_error_is_overloaded(self) -> None:
        self.assert_classes((500, 501, 502, 503, 504, 505, 520, 599), R.OVERLOADED)

    def test_a_status_outside_4xx_and_5xx_stays_blocked(self) -> None:
        # Only YouTube Music classes a redirect or other status by this function; any
        # status but 200 there is a refusal, and its redirects are blocks.
        self.assert_classes((100, 204, 301, 302, 399, 600, 999), R.BLOCKED)

    def test_a_stop_on_a_client_error_is_blocked(self) -> None:
        for status in (400, 401, 404, 410, 422, 451, 301, 999):
            with self.subTest(status=status):
                self.assertIs(lrf.stopping_status_refusal(status), R.BLOCKED)

    def test_a_stop_keeps_a_transient_status_s_class(self) -> None:
        for status in (429, 500, 503, 599):
            with self.subTest(status=status):
                self.assertIs(
                    lrf.stopping_status_refusal(status), lrf.status_refusal(status)
                )

    def test_the_transient_statuses_are_those_classed_transient(self) -> None:
        transient = {R.RATE_LIMITED, R.OVERLOADED}
        expected = {s for s in range(100, 1000) if lrf.status_refusal(s) in transient}
        self.assertEqual(lrf.TRANSIENT_STATUSES, expected)

    def test_the_http_layer_retries_exactly_the_transient_statuses(self) -> None:
        self.assertIs(lh.RETRY_STATUSES, lrf.TRANSIENT_STATUSES)


class ClassifyRetriedTest(fixtures.IsolatedCase):
    """A retried request: how it failed last, and the retries made."""

    def test_a_retried_status_keeps_its_facts_and_the_retries(self) -> None:
        last = lp.HttpStatusError(LRCLIB, 429, 20.0, server="origin")
        self.assertEqual(
            lrf.classify(lp.RetriedError(last, 3)),
            F(R.RATE_LIMITED, 429, 3, 20.0, "origin"),
        )

    def test_a_retried_connection_error_is_unreachable(self) -> None:
        last = requests.ConnectionError("reset")
        self.assertEqual(
            lrf.classify(lp.RetriedError(last, 1)), F(R.UNREACHABLE, retries=1)
        )


class ClassifyKindsTest(fixtures.IsolatedCase):
    """Every other kind of error, by its type and attributes alone."""

    def test_a_connection_error_or_a_timeout_is_unreachable(self) -> None:
        errors: tuple[Exception, ...] = (
            lp.ProviderTimeout("the step's deadline passed"),
            requests.ConnectionError("refused"),
            requests.ConnectTimeout("slow"),
            requests.ReadTimeout("slow"),
            requests.exceptions.SSLError("bad certificate"),
            requests.exceptions.ChunkedEncodingError("dropped mid-body"),
            ConnectionResetError("reset"),
            TimeoutError("slow"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(lrf.classify(error), F(R.UNREACHABLE))

    def test_a_reply_of_the_wrong_shape_is_unrecognized(self) -> None:
        details = (
            "unexpected reply",
            lh.REPLY_NOT_JSON,
            lh.REPLY_TOO_LARGE,
            lh.REDIRECT_REFUSED,
            lh.TOO_MANY_REDIRECTS,
            "code -460",
        )
        for detail in details:
            with self.subTest(detail=detail):
                error = lp.ProviderError("an invented reply", detail=detail)
                self.assertEqual(lrf.classify(error), F(R.UNRECOGNIZED))

    def test_a_detail_naming_a_class_does_not_class_the_error(self) -> None:
        for detail in ("HTTP 429", "timeout", "ConnectionError"):
            with self.subTest(detail=detail):
                error = lp.ProviderError("an invented reply", detail=detail)
                self.assertEqual(lrf.classify(error), F(R.UNRECOGNIZED))

    def test_an_error_of_no_known_kind_is_unrecognized(self) -> None:
        for error in (OSError("disk"), ValueError("odd"), AssertionError("gave up")):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(lrf.classify(error), F(R.UNRECOGNIZED))

    def test_a_classed_error_is_its_own_facts(self) -> None:
        facts = F(R.BLOCKED, 403)
        error = lrf.ClassedError("an invented stop", detail="HTTP 403", facts=facts)
        self.assertIs(lrf.classify(error), facts)
        self.assertEqual(error.detail, "HTTP 403")

    def test_a_genius_challenge_after_its_retries_is_blocked(self) -> None:
        for status in sorted(lg.GENIUS_CHALLENGE_STATUSES):
            with self.subTest(status=status):
                error = lg.GeniusChallengeError(status, 3)
                self.assertEqual(error.retries, 3)
                self.assertEqual(lrf.classify(error), F(R.BLOCKED, status, 3))

    def test_a_genius_challenge_keeps_its_last_reply_headers(self) -> None:
        error = lg.GeniusChallengeError(429, 2, retry_after_s=90.0, server="front")
        self.assertEqual(lrf.classify(error), F(R.BLOCKED, 429, 2, 90.0, "front"))


class ProviderRefusalFactsTest(fixtures.IsolatedCase):
    """The record's new facts: absent by default, the server held to one capped line."""

    def test_the_facts_default_to_absent(self) -> None:
        refusal = lr.ProviderRefusal(LRCLIB, lm.LyricsKind.SYNCED, "x", R.BLOCKED)
        self.assertEqual(
            (refusal.status, refusal.retries, refusal.retry_after_s, refusal.server),
            (None, 0, None, None),
        )

    def test_the_server_is_one_capped_line(self) -> None:
        server = "  edge\n cache " + "z" * (2 * lr.MAX_REFUSAL_DETAIL_CHARS)
        refusal = lr.ProviderRefusal(
            LRCLIB, lm.LyricsKind.SYNCED, "HTTP 503", R.OVERLOADED, server=server
        )
        assert refusal.server is not None
        self.assertEqual(len(refusal.server), lr.MAX_REFUSAL_DETAIL_CHARS)
        self.assertTrue(refusal.server.startswith("edge cache z"))

    def test_a_blank_server_is_none(self) -> None:
        refusal = lr.ProviderRefusal(
            LRCLIB, lm.LyricsKind.SYNCED, "HTTP 503", R.OVERLOADED, server=" \t\n"
        )
        self.assertIsNone(refusal.server)

    def test_the_record_is_built_from_facts(self) -> None:
        facts = F(R.OVERLOADED, 503, 3, 30.0, "edge")
        refusal = lrf.ProviderRefusal.of(LRCLIB, lm.LyricsKind.SYNCED, "d", facts)
        self.assertEqual(
            refusal,
            lr.ProviderRefusal(
                LRCLIB, lm.LyricsKind.SYNCED, "d", R.OVERLOADED, 503, 3, 30.0, "edge"
            ),
        )

    def test_a_refusal_is_sent_unless_its_facts_say_otherwise(self) -> None:
        refusal = lr.ProviderRefusal(LRCLIB, lm.LyricsKind.SYNCED, "x", R.BLOCKED)
        self.assertEqual((F(R.BLOCKED).unsent, refusal.unsent), (False, False))

    def test_the_record_carries_an_unsent_step_from_its_facts(self) -> None:
        facts = F(R.BLOCKED, unsent=True)
        refusal = lrf.ProviderRefusal.of(LRCLIB, lm.LyricsKind.SYNCED, "d", facts)
        self.assertTrue(refusal.unsent)

    def test_a_detail_keeps_the_eighty_characters_album_md_states(self) -> None:
        refusal = lr.ProviderRefusal(LRCLIB, lm.LyricsKind.SYNCED, "x" * 200, R.BLOCKED)
        self.assertEqual(len(refusal.detail), 80)

    def test_the_record_cannot_be_changed_once_made(self) -> None:
        # Its normalized detail and server hold only because nothing can set them again.
        refusal = lr.ProviderRefusal(LRCLIB, lm.LyricsKind.SYNCED, "x", R.BLOCKED)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            refusal.detail = "y" * 200  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
