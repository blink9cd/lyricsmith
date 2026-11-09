"""The text GET: Bandcamp redirects followed only within Bandcamp, each hop its own request.

No test here reaches the network: the HTTP transport's ``send`` answers from memory
(`bandcamp_fakes.BandcampServer`), so the real session and status hook still run.
Every host past ``bandcamp.com`` and every page is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import bandcamp_fakes as bf  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_musicbrainz as lmb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above

HOST = "invented.bandcamp.com"
START = f"https://{HOST}/album/x"
OTHER = "other.bandcamp.com"
PAGE = "<html><body>an invented page</body></html>"
#: A cookie a reply sets for every Bandcamp host.
SET_COOKIE = "session=x; Domain=.bandcamp.com"


def _get(
    server: bf.BandcampServer, session: requests.Session | None = None
) -> lh.TextReply:
    """`get_text` of `START` through *server*, Bandcamp's hosts allowed."""
    with http_fakes.transport(server):
        return lh.get_text(
            session or lh.cookie_free_session(),
            START,
            lm.Source.BANDCAMP,
            allowed=lmb.is_bandcamp_url,
        )


class TextReplyTest(fixtures.IsolatedCase):
    """A page comes back as text, with the URL that answered it."""

    def test_a_page(self) -> None:
        server = bf.BandcampServer({(HOST, "/album/x"): PAGE})
        self.assertEqual(_get(server), lh.TextReply(START, PAGE))

    def test_it_sends_the_plain_user_agent(self) -> None:
        server = bf.BandcampServer({(HOST, "/album/x"): PAGE})
        _get(server)
        self.assertEqual(server.requests[0].headers["User-Agent"], lh.user_agent())

    def test_an_error_status_raises(self) -> None:
        with self.assertRaises(lp.HttpStatusError) as caught:
            _get(bf.BandcampServer({}))
        self.assertEqual(caught.exception.status, 404)

    def test_a_body_not_utf8_is_not_text(self) -> None:
        with self.assertRaises(lp.ProviderError) as caught:
            _get(bf.BandcampServer({(HOST, "/album/x"): b"\xff\xfe"}))
        self.assertEqual(caught.exception.detail, lh.REPLY_NOT_TEXT)

    def test_a_body_over_the_cap_is_refused(self) -> None:
        body = b"a" * (lh.MAX_REPLY_BYTES + 1)
        with self.assertRaises(lp.ProviderError) as caught:
            _get(bf.BandcampServer({(HOST, "/album/x"): body}))
        self.assertEqual(caught.exception.detail, lh.REPLY_TOO_LARGE)

    def test_a_start_not_allowed_sends_nothing_and_is_no_redirect(self) -> None:
        # Not REDIRECT_REFUSED: no redirect was met, so the record names the refused
        # start URL, which neither provider's stop rule counts.
        server = bf.BandcampServer({})
        with (
            http_fakes.transport(server),
            self.assertRaises(lp.ProviderError) as caught,
        ):
            lh.get_text(
                lh.cookie_free_session(),
                "https://example.com/album/x",
                lm.Source.BANDCAMP,
                allowed=lmb.is_bandcamp_url,
            )
        self.assertEqual(caught.exception.detail, lh.URL_REFUSED)
        self.assertEqual(str(caught.exception), f"{lm.Source.BANDCAMP} URL refused")
        self.assertEqual(server.requests, [])


class AbsentTest(fixtures.IsolatedCase):
    """A status the caller names in *absent* is None, not a failure; others still raise."""

    GONE = frozenset({404, 410})

    def _get_absent(self, server: bf.BandcampServer) -> lh.TextReply | None:
        """`get_text` of `START` through *server*, 404 and 410 named absent."""
        with http_fakes.transport(server):
            return lh.get_text(
                lh.cookie_free_session(),
                START,
                lm.Source.BANDCAMP,
                allowed=lmb.is_bandcamp_url,
                absent=self.GONE,
            )

    def test_an_absent_status_is_none(self) -> None:
        for status in sorted(self.GONE):
            with self.subTest(status=status):
                server = bf.BandcampServer({(HOST, "/album/x"): status})
                self.assertIsNone(self._get_absent(server))
                self.assertEqual(len(server.requests), 1)

    def test_an_absent_status_after_a_redirect_is_none(self) -> None:
        moved = bf.Redirect(f"https://{OTHER}/album/x")
        server = bf.BandcampServer(
            {(HOST, "/album/x"): moved, (OTHER, "/album/x"): 404}
        )
        self.assertIsNone(self._get_absent(server))
        self.assertEqual(len(server.requests), 2)

    def test_a_status_not_absent_still_raises(self) -> None:
        for status in (403, 503):
            with self.subTest(status=status):
                server = bf.BandcampServer({(HOST, "/album/x"): status})
                with self.assertRaises(lp.HttpStatusError) as caught:
                    self._get_absent(server)
                self.assertEqual(caught.exception.status, status)

    def test_a_page_is_still_text(self) -> None:
        server = bf.BandcampServer({(HOST, "/album/x"): PAGE})
        self.assertEqual(self._get_absent(server), lh.TextReply(START, PAGE))


class RedirectTest(fixtures.IsolatedCase):
    """At most two redirects, each to an https Bandcamp URL."""

    def test_one_redirect_is_followed(self) -> None:
        target = f"https://{OTHER}/album/x"
        server = bf.BandcampServer(
            {(HOST, "/album/x"): bf.Redirect(target), (OTHER, "/album/x"): PAGE}
        )
        self.assertEqual(_get(server), lh.TextReply(target, PAGE))

    def test_a_relative_location_is_read_against_the_page(self) -> None:
        server = bf.BandcampServer(
            {(HOST, "/album/x"): bf.Redirect("/album/y", 302), (HOST, "/album/y"): PAGE}
        )
        self.assertEqual(_get(server).url, f"https://{HOST}/album/y")

    def test_two_redirects_are_followed(self) -> None:
        server = bf.BandcampServer(
            {
                (HOST, "/album/x"): bf.Redirect(f"https://{OTHER}/album/x"),
                (OTHER, "/album/x"): bf.Redirect("https://bandcamp.com/album/z"),
                ("bandcamp.com", "/album/z"): PAGE,
            }
        )
        self.assertEqual(_get(server).text, PAGE)
        self.assertEqual(len(server.requests), 3)

    def test_a_third_is_too_many(self) -> None:
        server = bf.BandcampServer(
            {
                (HOST, "/album/x"): bf.Redirect("/album/1"),
                (HOST, "/album/1"): bf.Redirect("/album/2"),
                (HOST, "/album/2"): bf.Redirect("/album/3"),
                (HOST, "/album/3"): PAGE,
            }
        )
        with self.assertRaises(lp.ProviderError) as caught:
            _get(server)
        self.assertEqual(caught.exception.detail, lh.TOO_MANY_REDIRECTS)
        self.assertEqual(len(server.requests), 3)

    def test_a_target_off_bandcamp_is_refused_unasked(self) -> None:
        for target in (
            "https://example.com/album/x",
            f"http://{HOST}/album/y",
            "https://bandcamp.com.example.com/",
        ):
            with self.subTest(target=target):
                server = bf.BandcampServer({(HOST, "/album/x"): bf.Redirect(target)})
                with self.assertRaises(lp.ProviderError) as caught:
                    _get(server)
                self.assertEqual(caught.exception.detail, lh.REDIRECT_REFUSED)
                self.assertEqual(len(server.requests), 1)

    def test_a_redirect_with_no_location_is_refused(self) -> None:
        server = bf.BandcampServer({(HOST, "/album/x"): 302})
        with self.assertRaises(lp.ProviderError) as caught:
            _get(server)
        self.assertEqual(caught.exception.detail, lh.REDIRECT_REFUSED)
        self.assertEqual(len(server.requests), 1)

    def test_a_refused_redirect_names_the_location_it_refused(self) -> None:
        # Resolved against the page that answered it, as the next hop would have been;
        # None when the redirect named no Location at all.
        cases: tuple[tuple[object, str | None], ...] = (
            (bf.Redirect("https://example.com/x"), "https://example.com/x"),
            (bf.Redirect("//example.com/y"), "https://example.com/y"),
            (302, None),
        )
        for answer, refused in cases:
            with self.subTest(answer=answer):
                server = bf.BandcampServer({(HOST, "/album/x"): answer})
                with self.assertRaises(lh.RedirectRefused) as caught:
                    _get(server)
                self.assertEqual(caught.exception.location, refused)
                self.assertEqual(caught.exception.detail, lh.REDIRECT_REFUSED)


def _redirect_setting_a_cookie() -> bf.BandcampServer:
    """A 302 that sets a cookie for every Bandcamp host, then the page it names."""
    return bf.BandcampServer(
        {
            (HOST, "/album/x"): bf.Redirect(
                f"https://{OTHER}/album/x", 302, SET_COOKIE
            ),
            (OTHER, "/album/x"): PAGE,
        }
    )


class RedirectCookieTest(fixtures.IsolatedCase):
    """A redirect's cookie is never sent on the next hop."""

    def test_the_next_hop_carries_no_cookie(self) -> None:
        server = _redirect_setting_a_cookie()
        _get(server)
        self.assertNotIn("Cookie", server.requests[1].headers)

    def test_a_plain_session_would_have_sent_it(self) -> None:
        server = _redirect_setting_a_cookie()
        _get(server, requests.Session())
        self.assertEqual(server.requests[1].headers["Cookie"], "session=x")


def _stored(session: requests.Session) -> int:
    """How many cookies *session*'s jar holds after a MusicBrainz reply sets one."""
    url = f"{lmb.MUSICBRAINZ_URL}/release"
    cookie = "session=x; Domain=.musicbrainz.org"
    return http_fakes.cookies_kept(session, method="GET", url=url, set_cookie=cookie)


class MusicBrainzSessionCookieTest(fixtures.IsolatedCase):
    """The session the MusicBrainz client builds keeps no cookie a reply sets."""

    def test_its_jar_stays_empty(self) -> None:
        kept: list[requests.Session] = []

        def keeping() -> requests.Session:
            session = lh.cookie_free_session()
            kept.append(session)
            return session

        clock = genius_fakes.FakeClock()
        client = lmb.MusicBrainzClient(
            clock=clock, sleeper=clock.sleep, session_factory=keeping
        )
        server = bf.BandcampServer({(bf.MB_HOST, bf.SEARCH_PATH): bf.search_reply()})
        with http_fakes.transport(server):
            client.find_bandcamp("The Lantern Years", "Invented Band", clock.now + 30)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(_stored(kept[0]), 0)

    def test_a_plain_session_would_have_stored_it(self) -> None:
        self.assertEqual(_stored(requests.Session()), 1)
