"""Tekstowo's client: its search address, its host rule, its pace and its stop rule.

Every request is answered through `http_fakes.transport` by `tekstowo_fakes.TekstowoServer`,
so the real session and adapter run and only the socket is never reached. The clock is a
`FakeClock`, so the 5-second pace takes no time, and each cool-down file is written under
the test's own temporary directory. Every page and name is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

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
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tekstowo as lt  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_fakes as tf  # noqa: E402 - import follows the sys.path bootstrap above
from bandcamp_fakes import Redirect  # noqa: E402 - import follows the sys.path bootstrap above
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

HOST = lt.TEKSTOWO_HOST
SEARCH = tf.search_path(tf.ARTIST, tf.TITLE)
SEARCH_URL = lt.search_url(tf.ARTIST, tf.TITLE)
SONG = "/piosenka,invented_artist,the_keeper_counts.html"
PAGE = "<html><body><p>An invented page.</p></body></html>"
#: How long past now a call's deadline lies when it is not the point of the test.
ROOM_S = 100.0
#: The deadline the watchdog abandons a held call at.
SHORT_S = 0.05


def _site(path: str) -> str:
    """*path* on Tekstowo's own host."""
    return f"{lt.TEKSTOWO_URL}{path}"


def _same(text: str) -> str:
    """Read a page as itself: every page is recognized."""
    return text


def _page_only(text: str) -> str | None:
    """Read `PAGE` as itself and recognize no other page."""
    return text if text == PAGE else None


class SearchUrlTest(fixtures.IsolatedCase):
    """The search address carries the artist and title, encoded, in its path alone."""

    def test_spaces_are_percent_twenty(self) -> None:
        self.assertEqual(
            SEARCH_URL,
            "https://www.tekstowo.pl/szukaj/Invented%20Artist%20The%20Keeper%20Counts",
        )

    def test_a_slash_in_a_title_is_a_space(self) -> None:
        self.assertEqual(
            lt.search_url("Invented Artist", "Keeper/Counter"),
            "https://www.tekstowo.pl/szukaj/Invented%20Artist%20Keeper%20Counter",
        )

    def test_query_fragment_and_percent_are_encoded(self) -> None:
        self.assertEqual(
            lt.search_url("A?", "B#C%"),
            "https://www.tekstowo.pl/szukaj/A%3F%20B%23C%25",
        )

    def test_runs_of_whitespace_collapse(self) -> None:
        self.assertEqual(
            lt.search_url("  Invented \t Artist ", " Keeper\n"),
            "https://www.tekstowo.pl/szukaj/Invented%20Artist%20Keeper",
        )

    @given(st.text(), st.text())
    def test_fuzz_search_url(self, artist: str, title: str) -> None:
        url = lt.search_url(artist, title)
        self.assertTrue(lt.on_site(url))
        path = urlsplit(url).path
        self.assertTrue(path.startswith("/szukaj/"))
        self.assertEqual(path.count("/"), 2)
        self.assertEqual(urlsplit(url).query, "")


class OnSiteTest(fixtures.IsolatedCase):
    """Only an https URL on the site's own host, port 443 or none, no user info."""

    def test_the_host_itself(self) -> None:
        self.assertTrue(lt.on_site("https://www.tekstowo.pl/szukaj/x"))
        self.assertTrue(lt.on_site("https://www.tekstowo.pl:443/szukaj/x"))

    def test_everything_else_is_refused(self) -> None:
        for url in (
            "http://www.tekstowo.pl/",
            "https://example.com/",
            "https://evil.www.tekstowo.pl/",
            "https://tekstowo.pl/",
            "https://www.tekstowo.pl:8443/",
            # Built from the host, so the text holds no address-shaped literal.
            f"https://visitor@{HOST}/",
            f"https://visitor:invented@{HOST}/",
            "https://www.tekstowo.pl:x/",
        ):
            with self.subTest(url=url):
                self.assertFalse(lt.on_site(url))


class ChallengeTest(fixtures.IsolatedCase):
    """A challenge page is known by its markers, in any case."""

    def test_markers(self) -> None:
        self.assertTrue(lt.is_challenge("<div id='CF-CHL-widget'>"))
        self.assertTrue(lt.is_challenge("<TITLE>Just a Moment...</TITLE>"))
        self.assertFalse(lt.is_challenge(PAGE))


class _ClientCase(fixtures.IsolatedCase):
    """A fake clock, and a client asking a fake Tekstowo through the transport."""

    def setUp(self) -> None:
        """Start a fresh fake clock."""
        super().setUp()
        self.clock = FakeClock()

    def serve(
        self, routes: dict[tuple[str, str], object], tmp: Path | None = None
    ) -> tuple[tf.TekstowoServer, lt.TekstowoClient]:
        """A server answering *routes*, installed as the transport, and a client on it."""
        server = tf.TekstowoServer(routes, self.clock)
        self.enterContext(http_fakes.transport(server))
        return server, tf.client(tmp or self.tmp, self.clock)

    def get(
        self,
        client: lt.TekstowoClient,
        url: str = SEARCH_URL,
        unrecognized: str = lt.SEARCH_NOT_RECOGNIZED,
    ) -> str:
        """One get of *url* read as itself, with room to spare before its deadline."""
        return client.get(
            url, self.clock() + ROOM_S, read=_same, unrecognized=unrecognized
        )


class PaceTest(_ClientCase):
    """A served pace sleeps not at all; the interval itself is the conformance suite's."""

    def test_a_pace_already_waited_out_sleeps_not_at_all(self) -> None:
        _, client = self.serve({(HOST, SEARCH): PAGE})
        self.get(client)
        self.clock.now += 6.0
        self.get(client)
        self.assertEqual(self.clock.sleeps, [])

    def test_a_deadline_already_passed_is_a_timeout_unsent(self) -> None:
        server, client = self.serve({(HOST, SEARCH): PAGE})
        with self.assertRaises(lp.ProviderTimeout):
            client.get(
                SEARCH_URL,
                self.clock(),
                read=_same,
                unrecognized=lt.SEARCH_NOT_RECOGNIZED,
            )
        self.assertEqual(server.requests, [])


class StopTest(_ClientCase):
    """Each refusal or challenge stops Tekstowo for the run and writes the cool-down.

    Its reader recognizes `PAGE` alone, so a challenge page is never read as a page.
    """

    CASES: tuple[tuple[str, dict[tuple[str, str], object]], ...] = (
        ("HTTP 403", {(HOST, SEARCH): 403}),
        ("HTTP 429", {(HOST, SEARCH): 429}),
        ("HTTP 503", {(HOST, SEARCH): 503}),
        ("challenge page", {(HOST, SEARCH): "<div class='cf-chl-box'></div>"}),
        (
            "challenge page",
            {(HOST, SEARCH): "<html><title>Just a Moment...</title></html>"},
        ),
        (
            "too many redirects",
            {
                (HOST, SEARCH): Redirect(_site("/a")),
                (HOST, "/a"): Redirect(_site("/b")),
                (HOST, "/b"): Redirect(_site("/c")),
                (HOST, "/c"): PAGE,
            },
        ),
    )

    def _assert_stopped(
        self,
        what: str,
        raised: lt.TekstowoStopped,
        client: lt.TekstowoClient,
        tmp: Path,
    ) -> None:
        """*client* stopped on *what*, said so, and wrote the cool-down to *tmp*."""
        self.assertEqual(raised.detail, what)
        assert client.stop is not None
        self.assertEqual(client.stop.what, what)
        self.assertIsNone(client.stop.write_error)
        self.assertEqual(client.stop.until, tf.NOW + lyc.COOL_DOWN)
        written = lyc.read_cooldown(tf.state_path(tmp), tf.NOW).cool_down
        assert written is not None
        self.assertEqual((written.what, written.until), (what, client.stop.until))

    def _get_page(self, client: lt.TekstowoClient) -> str:
        """One get of the search, recognizing `PAGE` alone."""
        return client.get(
            SEARCH_URL,
            self.clock() + ROOM_S,
            read=_page_only,
            unrecognized=lt.SEARCH_NOT_RECOGNIZED,
        )

    def test_each_stop_signal_stops_once_and_for_the_run(self) -> None:
        for index, (what, routes) in enumerate(self.CASES):
            with self.subTest(what=what, index=index):
                tmp = self.tmp / str(index)
                server, client = self.serve(routes, tmp)
                with self.assertRaises(lt.TekstowoStopped) as raised:
                    self._get_page(client)
                self._assert_stopped(what, raised.exception, client, tmp)
                sent = len(server.requests)
                with self.assertRaises(lt.TekstowoStopped) as again:
                    self._get_page(client)
                self.assertEqual(again.exception.detail, what)
                self.assertEqual(len(server.requests), sent)

    def test_a_run_of_pages_read_as_unrecognized_stops(self) -> None:
        server, client = self.serve({(HOST, SEARCH): PAGE})
        client.unrecognized_runs[lt.SEARCH_NOT_RECOGNIZED] = (
            lt.UNRECOGNIZED_STOP_RUN - 1
        )
        with self.assertRaises(lt.TekstowoStopped) as raised:
            client.get(
                SEARCH_URL,
                self.clock() + ROOM_S,
                read=lambda text: None,
                unrecognized=lt.SEARCH_NOT_RECOGNIZED,
            )
        self._assert_stopped(
            "search page not recognized", raised.exception, client, self.tmp
        )
        with self.assertRaises(lt.TekstowoStopped):
            self.get(client)
        self.assertEqual(len(server.requests), 1)

    def test_the_read_page_is_what_get_returns(self) -> None:
        _, client = self.serve({(HOST, SEARCH): PAGE})
        page = client.get(
            SEARCH_URL,
            self.clock() + ROOM_S,
            read=len,
            unrecognized=lt.SONG_NOT_RECOGNIZED,
        )
        self.assertEqual(page, len(PAGE))

    def test_a_refusal_is_never_retried(self) -> None:
        for status in (429, 503):
            with self.subTest(status=status):
                tmp = self.tmp / str(status)
                server, client = self.serve({(HOST, SEARCH): status}, tmp)
                with self.assertRaises(lt.TekstowoStopped):
                    self.get(client)
                self.assertEqual(len(server.requests), 1)

    def test_a_failed_cool_down_write_still_stops(self) -> None:
        server, client = self.serve({(HOST, SEARCH): 429})
        failing = mock.patch.object(lyc, "write_cooldown", side_effect=OSError("full"))
        with failing, self.assertRaises(lt.TekstowoStopped):
            self.get(client)
        assert client.stop is not None
        self.assertEqual(client.stop.what, "HTTP 429")
        self.assertEqual(client.stop.until, tf.NOW + lyc.COOL_DOWN)
        self.assertIsNotNone(client.stop.write_error)
        self.assertFalse(tf.state_path(self.tmp).exists())
        with self.assertRaises(lt.TekstowoStopped):
            self.get(client)
        self.assertEqual(len(server.requests), 1)


class StopReasonTest(fixtures.IsolatedCase):
    """Only a stop status or one redirect too many is a stop; a refused one is not."""

    def test_each_form(self) -> None:
        source = lm.Source.TEKSTOWO
        cases: tuple[tuple[lp.ProviderError, str | None], ...] = (
            (lp.HttpStatusError(source, 403), "HTTP 403"),
            (lp.HttpStatusError(source, 404), None),
            (lp.ProviderError("x", detail=lh.REDIRECT_REFUSED), None),
            (
                lp.ProviderError("x", detail=lh.TOO_MANY_REDIRECTS),
                lh.TOO_MANY_REDIRECTS,
            ),
            (lp.ProviderError("x", detail=lh.REPLY_NOT_TEXT), None),
            (lp.ProviderError("x", detail=lh.URL_REFUSED), None),
        )
        for error, expected in cases:
            with self.subTest(detail=error.detail):
                self.assertEqual(lt.stop_reason(error), expected)


class NotAStopTest(_ClientCase):
    """Another error status, or a body not UTF-8, fails the track and stops nothing."""

    def test_404_and_500_are_errors_and_no_stop(self) -> None:
        for status in (404, 500):
            with self.subTest(status=status):
                tmp = self.tmp / str(status)
                _, client = self.serve(
                    {(HOST, SEARCH): status, (HOST, SONG): PAGE}, tmp
                )
                with self.assertRaises(lp.HttpStatusError) as raised:
                    self.get(client)
                self.assertEqual(raised.exception.status, status)
                self.assertIsNone(client.stop)
                self.assertFalse(tf.state_path(tmp).exists())
                self.assertEqual(self.get(client, _site(SONG)), PAGE)

    def test_a_body_not_utf8_is_an_error_and_no_stop(self) -> None:
        _, client = self.serve({(HOST, SEARCH): b"\xff\xfe\x00"})
        with self.assertRaises(lp.ProviderError) as raised:
            self.get(client)
        self.assertEqual(raised.exception.detail, "reply not text")
        self.assertNotIsInstance(raised.exception, lt.TekstowoStopped)
        self.assertIsNone(client.stop)
        self.assertFalse(tf.state_path(self.tmp).exists())


class RedirectTest(_ClientCase):
    """One or two redirects on the site's own host are followed to the page."""

    def test_one_and_two_redirects_are_followed(self) -> None:
        chains = {
            1: {(HOST, SEARCH): Redirect(_site("/a")), (HOST, "/a"): PAGE},
            2: {
                (HOST, SEARCH): Redirect(_site("/a"), 302),
                (HOST, "/a"): Redirect("/b"),
                (HOST, "/b"): PAGE,
            },
        }
        for hops, routes in chains.items():
            with self.subTest(hops=hops):
                server, client = self.serve(routes, self.tmp / str(hops))
                self.assertEqual(self.get(client), PAGE)
                self.assertEqual(len(server.requests), hops + 1)
                self.assertIsNone(client.stop)
                for request in server.requests:
                    self.assertNotIn("Cookie", request.headers)


class RequestTest(_ClientCase):
    """What a request carries: the tool's User-Agent, the path, and never a cookie."""

    def test_the_fake_does_set_a_cookie(self) -> None:
        self.serve({(HOST, SEARCH): PAGE})
        with requests.Session() as session:
            session.get(SEARCH_URL, timeout=1.0)
            self.assertEqual(len(session.cookies), 1)

    def test_no_cookie_is_sent_back(self) -> None:
        server, client = self.serve({(HOST, SEARCH): PAGE, (HOST, SONG): PAGE})
        self.get(client)
        self.get(client, _site(SONG), lt.SONG_NOT_RECOGNIZED)
        self.assertEqual(server.paths(), [SEARCH, SONG])
        for request in server.requests:
            self.assertNotIn("Cookie", request.headers)
            self.assertEqual(request.headers["User-Agent"], lh.user_agent())
        self.assertEqual(urlsplit(server.requests[0].url or "").query, "")

    def test_the_default_session_refuses_cookies(self) -> None:
        client = lt.TekstowoClient(tf.state_path(self.tmp))
        self.assertIs(client.session_factory, lh.cookie_free_session)


class AbandonedCallTest(_ClientCase):
    """A call the watchdog abandoned holds the lock; the next one gives up unsent."""

    def test_a_held_call_keeps_the_next_from_sending(self) -> None:
        held = http_fakes.HeldTransport(fixtures.HANG_LIMIT_S)
        # Only a backstop: the abandoned call is finished inside the transport.
        self.addCleanup(held.released.set)
        client = tf.client(self.tmp, self.clock)
        with http_fakes.transport(held):
            with self.assertRaises(lp.ProviderTimeout):
                lf.run_with_deadline(lambda: self.get(client), SHORT_S)
            with self.assertRaises(lp.ProviderTimeout):
                client.get(
                    SEARCH_URL,
                    self.clock() + SHORT_S,
                    read=_same,
                    unrecognized=lt.SEARCH_NOT_RECOGNIZED,
                )
            sent = len(held.sent)
            finished = held.finish()
        self.assertEqual(sent, 1)
        self.assertTrue(finished)
