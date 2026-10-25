"""Each cooling client classes every refusal it meets as the `Refusal` it is.

The class has no effect yet: every class stops the client but Tekstowo's unrecognized
pages. It is pinned now so a policy per class meets each refusal already sorted. A spy on
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

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooling_client as lcc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
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

    def refuse(refusal: lcc.Refusal, what: str) -> lp.ProviderError:
        seen.append(refusal)
        return policy(refusal, what)

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

        def policy(refusal: lcc.Refusal, what: str) -> lp.ProviderError:
            stops.append(what)
            return base(client, refusal, what)

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


def mock_base_refuse(
    policy: Callable[[lcc.Refusal, str], lp.ProviderError],
) -> AbstractContextManager[object]:
    """Patch `CoolingClient.refuse` with *policy*, as a context manager."""
    return mock.patch.object(
        lcc.CoolingClient, "refuse", lambda self, refusal, what: policy(refusal, what)
    )


if __name__ == "__main__":
    unittest.main()
