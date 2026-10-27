"""Each cooling client classes every refusal it meets as the `Refusal` it is.

Every class but Tekstowo's unrecognized pages stops the client alike; the class is
pinned so that a policy per class would meet each refusal already sorted. A spy on
the client's one policy point, `CoolingClient.refuse`, records the class of each refusal
a faked reply draws, then lets the real policy act on it. Every reply is invented.
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date
from pathlib import Path
from unittest import mock

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooling_client as lcc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tekstowo as lt  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_music as lym  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_fakes as tf  # noqa: E402 - import follows the sys.path bootstrap above
import youtube_fakes as yf  # noqa: E402 - import follows the sys.path bootstrap above
from bandcamp_fakes import Redirect  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import FakeClock  # noqa: E402 - import follows the sys.path bootstrap above

R = lcc.Refusal
#: Far enough off that no deadline is the point of a test here.
ROOM_S = 100.0
_BODY = lym.search_body("the keeper counts", date(2026, 10, 21))
_SEARCH = (lt.TEKSTOWO_HOST, tf.search_path(tf.ARTIST, tf.TITLE))
_SEARCH_URL = lt.search_url(tf.ARTIST, tf.TITLE)
_CHALLENGE_PAGE = "<html><title>Just a moment...</title></html>"
_OTHER_PAGE = "<html><body><p>An invented page.</p></body></html>"


def _spy(client: lcc.CoolingClient) -> list[lcc.Refusal]:
    """Record the class of each refusal *client* meets, before its own policy acts."""
    seen: list[lcc.Refusal] = []
    policy = client.refuse

    def refuse(facts: lrf.RefusalFacts, what: str) -> lp.ProviderError:
        seen.append(facts.reason)
        return policy(facts, what)

    client.refuse = refuse  # type: ignore[method-assign] # spy on the policy point
    return seen


def _site(path: str) -> str:
    """An address on Tekstowo's own host."""
    return f"https://{lt.TEKSTOWO_HOST}{path}"


class YouTubeMusicRefusalTest(fixtures.IsolatedCase):
    """YouTube Music: a challenge is a block, a status its own class, not JSON unrecognized."""

    def _class_of(self, answer: yf.Answer) -> list[lcc.Refusal]:
        """The classes the one reply *answer* draws, on a fresh client."""
        clock = FakeClock()
        server = yf.ScriptedServer([answer], clock)
        client = yf.client(self.tmp, server, clock)
        seen = _spy(client)
        with http_fakes.transport(server), self.assertRaises(lym.YouTubeMusicStopped):
            client.post(lym.SEARCH, _BODY, clock() + ROOM_S)
        return seen

    def test_each_stop_reason_has_its_class(self) -> None:
        consent = {"Location": "https://consent.youtube.com/m"}
        sorry = {"Location": "https://music.youtube.com/sorry/index"}
        cases: tuple[tuple[yf.Answer, lcc.Refusal], ...] = (
            ((429, b"", {}), R.RATE_LIMITED),
            ((503, b"", {}), R.OVERLOADED),
            ((403, b"", {}), R.BLOCKED),
            ((302, b"", consent), R.BLOCKED),
            ((302, b"", sorry), R.BLOCKED),
            ((200, b"not json", {}), R.UNRECOGNIZED),
        )
        for answer, expected in cases:
            with self.subTest(answer=answer[:1] + answer[2:]):
                self.assertEqual(self._class_of(answer), [expected])


class TekstowoRefusalTest(fixtures.IsolatedCase):
    """Tekstowo: a status its own class; a challenge or a third redirect a block."""

    def _class_of(
        self, routes: dict[tuple[str, str], object], read: Callable[[str], object]
    ) -> list[lcc.Refusal]:
        """The classes a search answered by *routes* and read by *read* draws."""
        clock = FakeClock()
        server = tf.TekstowoServer(routes, clock)
        client = tf.client(self.tmp, clock)
        seen = _spy(client)
        with http_fakes.transport(server), self.assertRaises(lp.ProviderError):
            client.get(
                _SEARCH_URL,
                clock() + ROOM_S,
                read=read,
                unrecognized=lt.SEARCH_NOT_RECOGNIZED,
            )
        return seen

    def test_each_stop_status_has_its_class(self) -> None:
        cases = {429: R.RATE_LIMITED, 503: R.OVERLOADED, 403: R.BLOCKED}
        for status, expected in cases.items():
            with self.subTest(status=status):
                seen = self._class_of({_SEARCH: status}, lambda text: text)
                self.assertEqual(seen, [expected])

    def test_a_third_redirect_is_a_block(self) -> None:
        routes: dict[tuple[str, str], object] = {
            _SEARCH: Redirect(_site("/a")),
            (lt.TEKSTOWO_HOST, "/a"): Redirect(_site("/b")),
            (lt.TEKSTOWO_HOST, "/b"): Redirect(_site("/c")),
            (lt.TEKSTOWO_HOST, "/c"): _OTHER_PAGE,
        }
        self.assertEqual(self._class_of(routes, lambda text: text), [R.BLOCKED])

    def test_a_page_not_read_is_a_block_or_unrecognized(self) -> None:
        cases = {_CHALLENGE_PAGE: R.BLOCKED, _OTHER_PAGE: R.UNRECOGNIZED}
        for page, expected in cases.items():
            with self.subTest(expected=expected):
                seen = self._class_of({_SEARCH: page}, lambda text: None)
                self.assertEqual(seen, [expected])

    def test_a_run_of_unrecognized_pages_stops_through_the_policy_point(self) -> None:
        # The last of the run reaches the base policy too, so nothing stops around it.
        clock = FakeClock()
        server = tf.TekstowoServer({_SEARCH: _OTHER_PAGE}, clock)
        client = tf.client(self.tmp, clock)
        stops: list[str] = []
        base = lcc.CoolingClient.refuse

        def policy(facts: lrf.RefusalFacts, what: str) -> lp.ProviderError:
            stops.append(what)
            return base(client, facts, what)

        client.unrecognized_runs[lt.SEARCH_NOT_RECOGNIZED] = (
            lt.UNRECOGNIZED_STOP_RUN - 1
        )
        with (
            http_fakes.transport(server),
            mock_base_refuse(policy),
            self.assertRaises(lt.TekstowoStopped),
        ):
            client.get(
                _SEARCH_URL,
                clock() + ROOM_S,
                read=lambda text: None,
                unrecognized=lt.SEARCH_NOT_RECOGNIZED,
            )
        self.assertEqual(stops, [lt.SEARCH_NOT_RECOGNIZED])


class YouTubeMusicFactsTest(fixtures.IsolatedCase):
    """YouTube Music's stopped error carries its class and the refusing status."""

    def _raised(self, answer: yf.Answer) -> tuple[lp.ProviderError, lp.ProviderError]:
        """The stop the one reply *answer* draws, then the next call's, unsent."""
        clock = FakeClock()
        server = yf.ScriptedServer([answer], clock)
        client = yf.client(self.tmp, server, clock)
        errors = []
        with http_fakes.transport(server):
            for _ in range(2):
                with self.assertRaises(lym.YouTubeMusicStopped) as raised:
                    client.post(lym.SEARCH, _BODY, clock() + ROOM_S)
                errors.append(raised.exception)
        return errors[0], errors[1]

    def test_a_stop_keeps_the_reply_s_wait_and_server(self) -> None:
        headers = {"Retry-After": "120", "Server": "invented-front"}
        stop, later = self._raised((503, b"", headers))
        facts = lrf.RefusalFacts(R.OVERLOADED, 503, 0, 120.0, "invented-front")
        self.assertEqual([lrf.classify(stop), lrf.classify(later)], [facts] * 2)

    def test_a_reply_not_json_keeps_its_server(self) -> None:
        stop, _ = self._raised((200, b"not json", {"Server": "invented-front"}))
        facts = lrf.RefusalFacts(R.UNRECOGNIZED, server="invented-front")
        self.assertEqual(lrf.classify(stop), facts)

    def test_each_stop_carries_its_facts_and_so_does_the_next_call(self) -> None:
        consent = {"Location": "https://consent.youtube.com/m"}
        cases: tuple[tuple[yf.Answer, lrf.RefusalFacts], ...] = (
            ((429, b"", {}), lrf.RefusalFacts(R.RATE_LIMITED, 429)),
            ((503, b"", {}), lrf.RefusalFacts(R.OVERLOADED, 503)),
            ((403, b"", {}), lrf.RefusalFacts(R.BLOCKED, 403)),
            ((302, b"", consent), lrf.RefusalFacts(R.BLOCKED, 302)),
            ((200, b"not json", {}), lrf.RefusalFacts(R.UNRECOGNIZED)),
        )
        for answer, facts in cases:
            with self.subTest(answer=answer[:1] + answer[2:]):
                stop, later = self._raised(answer)
                self.assertEqual(lrf.classify(stop), facts)
                self.assertEqual(lrf.classify(later), facts)
                self.assertEqual(later.detail, stop.detail)


class TekstowoFactsTest(fixtures.IsolatedCase):
    """Tekstowo's stopped and unrecognized errors carry their class and status."""

    def _raised(
        self, routes: dict[tuple[str, str], object], read: Callable[[str], object]
    ) -> list[lp.ProviderError]:
        """The errors two searches answered by *routes* and read by *read* raise."""
        clock = FakeClock()
        server = tf.TekstowoServer(routes, clock)
        client = tf.client(self.tmp, clock)
        errors = []
        with http_fakes.transport(server):
            for _ in range(2):
                with self.assertRaises(lp.ProviderError) as raised:
                    client.get(
                        _SEARCH_URL,
                        clock() + ROOM_S,
                        read=read,
                        unrecognized=lt.SEARCH_NOT_RECOGNIZED,
                    )
                errors.append(raised.exception)
        return errors

    def test_a_stop_status_carries_its_class_and_status(self) -> None:
        cases = {429: R.RATE_LIMITED, 503: R.OVERLOADED, 403: R.BLOCKED}
        for status, reason in cases.items():
            with self.subTest(status=status):
                stop, later = self._raised({_SEARCH: status}, lambda text: text)
                self.assertIsInstance(later, lt.TekstowoStopped)
                facts = lrf.RefusalFacts(reason, status)
                self.assertEqual([lrf.classify(stop), lrf.classify(later)], [facts] * 2)

    def test_a_challenge_or_a_third_redirect_is_blocked_with_no_status(self) -> None:
        redirects: dict[tuple[str, str], object] = {
            _SEARCH: Redirect(_site("/a")),
            (lt.TEKSTOWO_HOST, "/a"): Redirect(_site("/b")),
            (lt.TEKSTOWO_HOST, "/b"): Redirect(_site("/c")),
            (lt.TEKSTOWO_HOST, "/c"): _OTHER_PAGE,
        }
        cases: tuple[tuple[dict[tuple[str, str], object], Callable[[str], object]], ...]
        cases = ((redirects, lambda text: text), ({_SEARCH: _CHALLENGE_PAGE}, _none))
        for routes, read in cases:
            with self.subTest(routes=len(routes)):
                errors = self._raised(routes, read)
                blocked = lrf.RefusalFacts(R.BLOCKED)
                self.assertEqual([lrf.classify(e) for e in errors], [blocked] * 2)

    def test_a_stop_status_keeps_the_reply_s_wait_and_server(self) -> None:
        def refusing(request: requests.PreparedRequest) -> requests.Response:
            headers = {"Retry-After": "7200", "Server": "invented-edge"}
            return http_fakes.response(request, 429, headers=headers)

        clock = FakeClock()
        client = tf.client(self.tmp, clock)
        errors = []
        with http_fakes.transport(refusing):
            for _ in range(2):
                with self.assertRaises(lt.TekstowoStopped) as raised:
                    client.get(
                        _SEARCH_URL,
                        clock() + ROOM_S,
                        read=lambda text: text,
                        unrecognized=lt.SEARCH_NOT_RECOGNIZED,
                    )
                errors.append(lrf.classify(raised.exception))
        facts = lrf.RefusalFacts(R.RATE_LIMITED, 429, 0, 7200.0, "invented-edge")
        self.assertEqual(errors, [facts] * 2)

    def test_an_unrecognized_page_is_unrecognized_stopping_or_not(self) -> None:
        errors = self._raised({_SEARCH: _OTHER_PAGE}, _none)
        self.assertIsInstance(errors[0], lt.TekstowoUnrecognized)
        unrecognized = lrf.RefusalFacts(R.UNRECOGNIZED)
        self.assertEqual([lrf.classify(e) for e in errors], [unrecognized] * 2)


def _none(text: str) -> None:
    """A reader that recognizes no page."""


def mock_base_refuse(
    policy: Callable[[lrf.RefusalFacts, str], lp.ProviderError],
) -> AbstractContextManager[object]:
    """Patch `CoolingClient.refuse` with *policy*, as a context manager."""
    return mock.patch.object(
        lcc.CoolingClient,
        "refuse",
        lambda self, facts, what: policy(facts, what),
    )


if __name__ == "__main__":
    unittest.main()
