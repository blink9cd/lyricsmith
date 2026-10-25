"""The status hook: an error status raises `HttpStatusError`, unless the endpoint says absent.

No test here reaches the network: each request goes through a real ``requests`` session
whose transport answers from memory (`http_fakes`), so the hook runs where it runs for
real, inside the send.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above

#: A reserved name the transport fake answers, so nothing is ever resolved.
URL = "https://example.invalid/api"
#: Long enough for a fake that answers at once.
TIMEOUT_S = 1.0


#: Where a faked redirect points: another host, never contacted.
ELSEWHERE = "https://elsewhere.invalid/moved"


def _get(status: int, hook: lp.ResponseHook) -> requests.Response:
    """GET URL through a fresh session whose transport answers *status*, with *hook*."""
    with requests.Session() as session, http_fakes.transport(http_fakes.always(status)):
        return session.get(URL, timeout=TIMEOUT_S, hooks={"response": [hook]})


def _moved_once(request: requests.PreparedRequest) -> requests.Response:
    """A 301 to ELSEWHERE for URL, and 200 for ELSEWHERE itself."""
    if request.url == URL:
        return http_fakes.response(request, 301, headers={"Location": ELSEWHERE})
    return http_fakes.response(request, 200)


class StatusHookTest(fixtures.IsolatedCase):
    """Raise on 400 and above, naming the source and the status."""

    def test_an_error_status_raises_with_its_source_and_status(self) -> None:
        with self.assertRaises(lp.HttpStatusError) as raised:
            _get(429, lp.status_hook(lm.Source.NETEASE))
        self.assertEqual(
            (raised.exception.source, raised.exception.status),
            (lm.Source.NETEASE, 429),
        )
        self.assertEqual(str(raised.exception), "NetEase answered HTTP 429")

    def test_an_ok_status_passes(self) -> None:
        self.assertEqual(_get(200, lp.status_hook(lm.Source.LRCLIB)).status_code, 200)

    def test_a_status_named_absent_passes_and_no_other_does(self) -> None:
        hook = lp.status_hook(lm.Source.LRCLIB, absent=frozenset({404}))
        self.assertEqual(_get(404, hook).status_code, 404)
        with self.assertRaises(lp.HttpStatusError):
            _get(410, hook)

    def test_http_status_reads_only_a_status_error(self) -> None:
        error = lp.HttpStatusError(lm.Source.LRCLIB, 500)
        self.assertEqual(lp.http_status(error), 500)
        self.assertIsNone(lp.http_status(lp.ProviderError("x")))

    def test_a_redirect_raises_even_when_named_absent(self) -> None:
        hook = lp.status_hook(lm.Source.LRCLIB, absent=frozenset({302, 404}))
        for status in (300, 301, 302, 307, 308, 399):
            with self.subTest(status=status):
                with self.assertRaises(lp.HttpStatusError) as raised:
                    _get(status, hook)
                self.assertEqual(raised.exception.status, status)


class HookedSessionTest(fixtures.IsolatedCase):
    """A hooked session (Genius's) still follows a redirect, as its library expects."""

    def test_a_hooked_session_follows_a_redirect(self) -> None:
        with requests.Session() as session, http_fakes.transport(_moved_once):
            lp.hook_status_errors(session, lm.Source.GENIUS)
            final = session.get(URL, timeout=TIMEOUT_S)
        self.assertEqual((final.status_code, final.url), (200, ELSEWHERE))

    def test_a_hooked_session_still_raises_on_an_error_status(self) -> None:
        with (
            requests.Session() as session,
            http_fakes.transport(http_fakes.always(500)),
        ):
            lp.hook_status_errors(session, lm.Source.GENIUS)
            with self.assertRaises(lp.HttpStatusError):
                session.get(URL, timeout=TIMEOUT_S)
