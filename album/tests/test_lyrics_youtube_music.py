"""YouTube Music's client: paced, cookie-free, stopping at the first refusal.

Every request is answered through `http_fakes.transport`, so the real session and
adapter run and only the socket is never reached. The clock is a `FakeClock`, so the
5-second pace takes no time, and each cool-down file is written under the test's own
temporary directory. Every reply is invented.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_music as lym  # noqa: E402 - import follows the sys.path bootstrap above
import youtube_fakes as yf  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import FakeClock  # noqa: E402 - import follows the sys.path bootstrap above

# A registered profile, never an @settings decorator: the name and settings are
# test_lyrics_genre's, so whichever suite loads first, the other's properties run under
# the same profile (its comment gives the reasons).
settings.register_profile(
    "album_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("album_properties")

#: The day the dated client version is built from, and a search body for it.
TODAY = date(2026, 10, 21)
BODY = lym.search_body("the keeper counts", TODAY)
#: How long past now a call's deadline lies when it is not the point of the test.
ROOM_S = 100.0
#: The deadline the watchdog abandons a held call at.
SHORT_S = 0.05


def _cookies_kept(session: requests.Session) -> int:
    """How many cookies *session*'s jar holds after a YouTube reply sets one."""
    url = f"{lym.YOUTUBE_MUSIC_URL}/search"
    cookie = "SOCS=CAI; Domain=.youtube.com"
    return http_fakes.cookies_kept(session, method="POST", url=url, set_cookie=cookie)


def _names_a_form(location: str | None) -> bool:
    """Whether *location* names a consent host or a sorry path: the test's own oracle."""
    if location is None:
        return False
    try:
        parts = urlsplit(location)
    except ValueError:
        return False
    host = parts.hostname or ""
    return host.startswith("consent.") or parts.path.startswith("/sorry")


class _ClientCase(fixtures.IsolatedCase):
    """A fake clock, and a client asking a scripted server through the transport."""

    def setUp(self) -> None:
        """Start a fresh fake clock."""
        super().setUp()
        self.clock = FakeClock()

    def serve(
        self, script: list[yf.Answer], tmp: Path | None = None
    ) -> tuple[yf.ScriptedServer, lym.YouTubeMusicClient]:
        """A server answering *script*, installed as the transport, and a client on it."""
        server = yf.ScriptedServer(script, self.clock)
        self.enterContext(http_fakes.transport(server))
        return server, yf.client(tmp or self.tmp, server, self.clock)

    def post(
        self, client: lym.YouTubeMusicClient, endpoint: str = lym.SEARCH
    ) -> object:
        """One post of `BODY` with room to spare before its deadline."""
        return client.post(endpoint, BODY, self.clock() + ROOM_S)


class PaceTest(_ClientCase):
    """The pace counts time already passed; the interval itself is the conformance suite's."""

    def test_time_already_passed_counts_toward_the_pace(self) -> None:
        _, client = self.serve([yf.ok({}), yf.ok({})])
        self.post(client)
        self.clock.now += 3.0
        self.post(client)
        self.assertEqual(self.clock.sleeps, [2.0])

    def test_a_pace_already_waited_out_sleeps_not_at_all(self) -> None:
        _, client = self.serve([yf.ok({}), yf.ok({})])
        self.post(client)
        self.clock.now += 6.0
        self.post(client)
        self.assertEqual(self.clock.sleeps, [])

    def test_a_deadline_already_passed_is_a_timeout_unsent(self) -> None:
        server, client = self.serve([yf.ok({})])
        with self.assertRaises(lp.ProviderTimeout):
            client.post(lym.SEARCH, BODY, self.clock())
        self.assertEqual(server.requests, [])


class StopTest(_ClientCase):
    """Each refusal or challenge stops YouTube Music for the run and writes the cool-down."""

    CASES: tuple[tuple[str, yf.Answer], ...] = (
        ("HTTP 429", (429, b"", {})),
        ("HTTP 503", (503, b"", {})),
        (
            "redirect to consent",
            (302, b"", {"Location": "https://consent.youtube.com/m?x=1"}),
        ),
        (
            "redirect to sorry",
            (302, b"", {"Location": "https://www.google.com/sorry/index"}),
        ),
        ("HTTP 301", (301, b"", {"Location": "https://music.youtube.com/else"})),
        ("reply not JSON", (200, b"<html>", {})),
        ("reply not JSON", (200, b"[" * 200_000, {})),
    )

    def test_each_stop_signal_stops_once_and_for_the_run(self) -> None:
        for index, (what, answer) in enumerate(self.CASES):
            with self.subTest(what=what):
                tmp = self.tmp / str(index)
                server, client = self.serve([answer], tmp)
                with self.assertRaises(lym.YouTubeMusicStopped) as raised:
                    self.post(client)
                self.assertEqual(raised.exception.detail, what)
                assert client.stop is not None
                self.assertEqual(client.stop.what, what)
                self.assertIsNone(client.stop.write_error)
                self.assertEqual(client.stop.until, yf.NOW + lyc.COOL_DOWN)
                written = lyc.read_cooldown(yf.state_path(tmp), yf.NOW).cool_down
                assert written is not None
                self.assertEqual(
                    (written.what, written.until), (what, client.stop.until)
                )
                with self.assertRaises(lym.YouTubeMusicStopped) as again:
                    self.post(client)
                self.assertEqual(again.exception.detail, what)
                self.assertEqual(len(server.requests), 1)

    def test_a_refusal_is_never_retried(self) -> None:
        for status in (429, 503):
            with self.subTest(status=status):
                tmp = self.tmp / str(status)
                server, client = self.serve([(status, b"", {}), yf.ok({})], tmp)
                with self.assertRaises(lym.YouTubeMusicStopped):
                    self.post(client)
                self.assertEqual(len(server.requests), 1)

    def test_a_failed_cool_down_write_still_stops(self) -> None:
        (self.tmp / "state").write_bytes(b"")
        server, client = self.serve([(429, b"", {}), yf.ok({})])
        with self.assertRaises(lym.YouTubeMusicStopped):
            self.post(client)
        assert client.stop is not None
        self.assertEqual(client.stop.what, "HTTP 429")
        self.assertEqual(client.stop.until, yf.NOW + lyc.COOL_DOWN)
        self.assertIsNotNone(client.stop.write_error)
        with self.assertRaises(lym.YouTubeMusicStopped):
            self.post(client)
        self.assertEqual(len(server.requests), 1)

    def test_a_failed_write_keeps_its_end_to_the_second(self) -> None:
        (self.tmp / "state").write_bytes(b"")
        server = yf.ScriptedServer([(429, b"", {})], self.clock)
        self.enterContext(http_fakes.transport(server))
        client = yf.client(self.tmp, server, self.clock)
        client.now = lambda: yf.NOW.replace(microsecond=123_456)
        with self.assertRaises(lym.YouTubeMusicStopped):
            self.post(client)
        assert client.stop is not None
        self.assertIsNotNone(client.stop.write_error)
        self.assertEqual(client.stop.until, yf.NOW + lyc.COOL_DOWN)
        self.assertEqual(client.stop.until.microsecond, 0)


class NotAStopTest(_ClientCase):
    """A reply too large, or a connection that failed, is the track's error and no stop."""

    def test_a_reply_too_large_is_an_error_and_no_stop(self) -> None:
        large = {"Content-Length": str(lh.MAX_REPLY_BYTES + 1)}
        _, client = self.serve([(200, b"{}", large), yf.ok({"next": 1})])
        with self.assertRaises(lp.ProviderError) as raised:
            self.post(client)
        self.assertEqual(raised.exception.detail, lh.REPLY_TOO_LARGE)
        self.assertNotIsInstance(raised.exception, lym.YouTubeMusicStopped)
        self.assertIsNone(client.stop)
        self.assertFalse(yf.state_path(self.tmp).exists())
        self.assertEqual(self.post(client), {"next": 1})

    def test_a_connection_error_propagates_and_is_no_stop(self) -> None:
        def refuse(request: requests.PreparedRequest) -> requests.Response:
            raise requests.ConnectionError("refused")

        self.enterContext(http_fakes.transport(refuse))
        client = yf.client(self.tmp, refuse, self.clock)
        with self.assertRaises(requests.ConnectionError):
            self.post(client)
        self.assertIsNone(client.stop)
        self.assertTrue(client.lock.acquire(blocking=False))
        client.lock.release()


class RequestTest(_ClientCase):
    """What a request carries: JSON in, JSON out, and nothing that identifies a visitor."""

    def test_a_json_reply_comes_back_decoded(self) -> None:
        server, client = self.serve([yf.ok({"contents": [1, 2]})])
        self.assertEqual(self.post(client), {"contents": [1, 2]})
        request = server.requests[0]
        parts = urlsplit(request.url or "")
        self.assertEqual(
            (request.method, parts.scheme, parts.netloc, parts.path),
            ("POST", "https", "music.youtube.com", "/youtubei/v1/search"),
        )
        self.assertEqual(parse_qs(parts.query), {"alt": ["json"]})
        headers = request.headers
        self.assertEqual(headers["User-Agent"], lh.user_agent())
        self.assertEqual(headers["Origin"], "https://music.youtube.com")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("X-Goog-Visitor-Id", headers)
        self.assertEqual(server.bodies[0], BODY)
        self.assertNotIn("visitorData", json.dumps(server.bodies[0]))

    def test_the_jar_refuses_cookies(self) -> None:
        client = lym.YouTubeMusicClient(yf.state_path(self.tmp))
        self.assertIs(client.session_factory, lh.cookie_free_session)
        self.assertEqual(_cookies_kept(client.session_factory()), 0)
        self.assertEqual(_cookies_kept(requests.Session()), 1)

    def test_the_dated_client_version(self) -> None:
        self.assertEqual(lym.web_remix_version(TODAY), "1.20261021.01.00")


class BodyTest(fixtures.IsolatedCase):
    """Each request body is the probe's, whole, with no visitor data anywhere."""

    def test_the_search_body(self) -> None:
        self.assertEqual(
            lym.search_body("the keeper counts", TODAY),
            {
                "context": {
                    "client": {
                        "clientName": "WEB_REMIX",
                        "clientVersion": "1.20261021.01.00",
                        "hl": "en",
                    },
                    "user": {},
                },
                "query": "the keeper counts",
                "params": "EgWKAQIIAWoKEAkQBRAKEAMQBA%3D%3D",
            },
        )

    def test_the_next_body(self) -> None:
        self.assertEqual(
            lym.next_body("abcDEF12345", TODAY),
            {
                "context": {
                    "client": {
                        "clientName": "WEB_REMIX",
                        "clientVersion": "1.20261021.01.00",
                        "hl": "en",
                    },
                    "user": {},
                },
                "videoId": "abcDEF12345",
                "playlistId": "RDAMVMabcDEF12345",
                "isAudioOnly": True,
                "watchEndpointMusicSupportedConfigs": {
                    "watchEndpointMusicConfig": {
                        "hasPersistentPlaylistPanel": True,
                        "musicVideoType": "MUSIC_VIDEO_TYPE_ATV",
                    }
                },
            },
        )

    def test_the_browse_body(self) -> None:
        self.assertEqual(
            lym.browse_body("MPLYt_invented"),
            {
                "context": {
                    "client": {
                        "clientName": "ANDROID_MUSIC",
                        "clientVersion": "7.21.50",
                        "hl": "en",
                    },
                    "user": {},
                },
                "browseId": "MPLYt_invented",
            },
        )


class AbandonedCallTest(_ClientCase):
    """A call the watchdog abandoned holds the lock; the next one gives up unsent."""

    def test_a_held_call_keeps_the_next_from_sending(self) -> None:
        held = http_fakes.HeldTransport(fixtures.HANG_LIMIT_S)
        # Only a backstop: the abandoned call is finished inside the transport.
        self.addCleanup(held.released.set)
        client = yf.client(self.tmp, held, self.clock)
        with http_fakes.transport(held):
            with self.assertRaises(lp.ProviderTimeout):
                lf.run_with_deadline(lambda: self.post(client), SHORT_S)
            with self.assertRaises(lp.ProviderTimeout):
                client.post(lym.SEARCH, BODY, self.clock() + SHORT_S)
            sent = len(held.sent)
            finished = held.finish()
        self.assertEqual(sent, 1)
        self.assertTrue(finished)


class StopReasonTest(fixtures.IsolatedCase):
    """The stop rule reads the status and the Location, and never raises."""

    def test_each_form(self) -> None:
        cases = {
            (200, None): None,
            (200, "https://music.youtube.com/watch"): None,
            (200, "https://consent.youtube.com/m"): "redirect to consent",
            (302, "/sorry/index"): "redirect to sorry",
            (500, None): "HTTP 500",
            (302, "http://[broken"): "HTTP 302",
        }
        for (status, location), expected in cases.items():
            with self.subTest(status=status, location=location):
                self.assertEqual(lym.stop_reason(status, location), expected)

    @given(st.integers(), st.none() | st.text(max_size=40))
    def test_fuzz_stop_reason(self, status: int, location: str | None) -> None:
        reason = lym.stop_reason(status, location)
        self.assertEqual(reason is None, status == 200 and not _names_a_form(location))
        if reason is not None and not _names_a_form(location):
            self.assertEqual(reason, f"HTTP {status}")
