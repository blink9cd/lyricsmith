"""The real Genius client's own session: capped replies, refused redirects, no cookies.

lyricsgenius reads each reply whole, follows redirects itself and keeps a cookie jar of
its own, so these tests drive the REAL client, as `build_genius_client` builds it, with
its HTTP transport answering from memory (`http_fakes`): no request leaves the process.
Every value is invented, and the key is an obvious fake.
"""

from __future__ import annotations

import email.message
import gzip
import io
import os
import sys
import typing
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from unittest import mock

import lyricsgenius
import requests
import urllib3

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import FAKE_TOKEN, QUERY  # noqa: E402 - import follows the sys.path bootstrap above

#: What the song endpoint answers: a song record with nothing in it.
SONG = http_fakes.json_body({"response": {"song": {}}})
#: What the search endpoint answers when nothing matched.
NO_HITS = {"response": {"sections": [{"type": "top_hit", "hits": []}], "hits": []}}
#: The key-bearing request's own URL, as lyricsgenius builds it for song 1.
SONG_URL = "https://api.genius.com/songs/1"
#: How far a compressed reply inflates: far past the cap, from a small body.
BOMB_INFLATED_BYTES = 64 * lh.MAX_REPLY_BYTES
#: The cookie a reply sets, for every Genius host.
SET_COOKIE = "sid=invented; Domain=.genius.com; Path=/"


class Hop:
    """A transport answer playing back *replies* in turn; records each request sent."""

    def __init__(
        self, replies: list[Callable[[requests.PreparedRequest], requests.Response]]
    ) -> None:
        """Answer each request with the next of *replies*."""
        self.replies = replies
        self.sent: list[requests.PreparedRequest] = []

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """Record *request*, then answer it with the next scripted reply."""
        self.sent.append(request)
        return self.replies.pop(0)(request)

    def header(self, index: int, name: str) -> str | bytes | None:
        """Request *index*'s header *name*, or None when it was not sent."""
        return self.sent[index].headers.get(name)


def redirect(
    location: str | None, headers: Mapping[str, str] | None = None
) -> Callable[[requests.PreparedRequest], requests.Response]:
    """A 302 to *location*, or one naming none, with any other *headers*."""
    sent = dict(headers or {})
    if location is not None:
        sent["Location"] = location

    def answer(request: requests.PreparedRequest) -> requests.Response:
        return http_fakes.response(request, 302, headers=sent)

    return answer


def ok(
    body: bytes = SONG, headers: Mapping[str, str] | None = None
) -> Callable[[requests.PreparedRequest], requests.Response]:
    """A 200 with *body* and *headers*."""

    def answer(request: requests.PreparedRequest) -> requests.Response:
        return http_fakes.response(request, 200, body, headers=headers)

    return answer


def setting_cookie(
    reply: Callable[[requests.PreparedRequest], requests.Response],
) -> Callable[[requests.PreparedRequest], requests.Response]:
    """*reply*, setting `SET_COOKIE` where a session reads it, on the raw reply's headers."""

    def answer(request: requests.PreparedRequest) -> requests.Response:
        built = reply(request)
        built.headers["Set-Cookie"] = SET_COOKIE
        headers = email.message.Message()
        headers["Set-Cookie"] = SET_COOKIE
        # The raw stream is a BytesIO, which takes the attribute requests reads.
        built.raw._original_response = type("Original", (), {"msg": headers})()
        return built

    return answer


def _genius() -> lyricsgenius.Genius:
    """The real client, as the tool builds it, typed as the library's."""
    return typing.cast(
        lyricsgenius.Genius, lg.build_genius_client(lr.Secret(FAKE_TOKEN))
    )


class CountingHTTPResponse(urllib3.response.HTTPResponse):
    """A real urllib3 response that counts the decoded bytes its reads hand back."""

    decoded_read = 0

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        """Read as urllib3 does, counting what came back after decompression."""
        data = super().read(*args, **kwargs)
        self.decoded_read += len(data)
        return data


class ReplyCapTest(fixtures.IsolatedCase):
    """A reply over the project's cap is refused before the library reads it whole."""

    def assert_too_large(self, answer: Callable[..., requests.Response]) -> None:
        """The client refuses *answer*'s reply with the reply-too-large detail."""
        with (
            http_fakes.transport(answer),
            self.assertRaises(lp.ProviderError) as raised,
        ):
            _genius().song(1)
        self.assertEqual(raised.exception.detail, lh.REPLY_TOO_LARGE)

    def test_a_body_over_the_cap_is_refused(self) -> None:
        self.assert_too_large(Hop([ok(b" " * (lh.MAX_REPLY_BYTES + 1))]))

    def test_a_declared_length_over_the_cap_is_refused_unread(self) -> None:
        declared = {"Content-Length": str(lh.MAX_REPLY_BYTES + 1)}
        self.assert_too_large(Hop([ok(b"{}", declared)]))

    def test_a_reply_at_the_cap_is_read(self) -> None:
        body = SONG + b" " * (lh.MAX_REPLY_BYTES - len(SONG))
        with http_fakes.transport(Hop([ok(body)])):
            self.assertEqual(_genius().song(1), {"song": {}})

    def test_a_lyrics_page_over_the_cap_is_refused(self) -> None:
        page = Hop([ok(b"<p>" * lh.MAX_REPLY_BYTES)])
        with http_fakes.transport(page), self.assertRaises(lp.ProviderError) as raised:
            _genius().lyrics(song_url="https://genius.com/Invented-artist-lyrics")
        self.assertEqual(raised.exception.detail, lh.REPLY_TOO_LARGE)

    def test_a_decompression_bomb_is_refused_before_it_inflates(self) -> None:
        compressed = gzip.compress(bytes(BOMB_INFLATED_BYTES), compresslevel=9)
        self.assertLess(len(compressed), lh.MAX_REPLY_BYTES // 8)
        raw = CountingHTTPResponse(
            body=io.BytesIO(compressed),
            headers={
                "Content-Encoding": "gzip",
                "Content-Length": str(len(compressed)),
            },
            status=200,
            preload_content=False,
            decode_content=True,
        )

        def answer(request: requests.PreparedRequest) -> requests.Response:
            built = http_fakes.response(request, 200, headers=dict(raw.headers))
            built.raw = raw
            return built

        self.assert_too_large(answer)
        self.assertGreater(raw.decoded_read, lh.MAX_REPLY_BYTES)
        self.assertLessEqual(raw.decoded_read, lh.MAX_REPLY_BYTES + lh.READ_CHUNK_BYTES)

    def test_a_refused_reply_is_closed(self) -> None:
        request = requests.Request("GET", SONG_URL).prepare()
        over = http_fakes.response(request, 200, b" " * (lh.MAX_REPLY_BYTES + 1))
        with (
            mock.patch.object(over, "close", wraps=over.close) as close,
            self.assertRaises(lp.ProviderError),
        ):
            lg.read_capped(over)
        close.assert_called_once_with()

    def test_a_challenge_is_still_its_status_with_its_body_unread(self) -> None:
        huge = Hop(
            [lambda r: http_fakes.response(r, 429, b" " * (2 * lh.MAX_REPLY_BYTES))]
        )
        with (
            http_fakes.transport(huge),
            self.assertRaises(lp.HttpStatusError) as raised,
        ):
            _genius().song(1)
        self.assertEqual(raised.exception.status, 429)


class RedirectTest(fixtures.IsolatedCase):
    """A redirect is followed only over https to a Genius host, and only a few times."""

    def test_a_redirect_off_genius_or_off_https_is_refused_unfollowed(self) -> None:
        refused = (
            "https://elsewhere.invalid/songs/1",
            "http://api.genius.com/songs/1",
            "http://genius.com/songs/1",
            "https://api.genius.com:8443/songs/1",
            "https://api.genius.com@elsewhere.invalid/songs/1",
            "https://genius.com.elsewhere.invalid/songs/1",
            "//elsewhere.invalid/songs/1",
            "ftp://genius.com/songs/1",
            "https://genius.com:bad/songs/1",
            None,
        )
        for location in refused:
            with self.subTest(location=location):
                hops = Hop([redirect(location), ok()])
                with http_fakes.transport(hops):
                    with self.assertRaises(lp.ProviderError) as raised:
                        _genius().song(1)
                self.assertEqual(raised.exception.detail, lh.REDIRECT_REFUSED)
                self.assertEqual(len(hops.sent), 1)

    def test_a_redirect_to_genius_drops_the_key(self) -> None:
        hops = Hop([redirect("https://genius.com/songs/1"), ok()])
        with http_fakes.transport(hops):
            _genius().song(1)
        self.assertTrue((hops.sent[0].url or "").startswith(SONG_URL))
        self.assertEqual(hops.header(0, "Authorization"), f"Bearer {FAKE_TOKEN}")
        self.assertIsNone(hops.header(1, "Authorization"))

    def test_a_redirect_within_the_api_host_keeps_the_key(self) -> None:
        hops = Hop([redirect("/songs/2"), ok()])
        with http_fakes.transport(hops):
            _genius().song(1)
        self.assertEqual(hops.sent[1].url, "https://api.genius.com/songs/2")
        self.assertEqual(hops.header(1, "Authorization"), f"Bearer {FAKE_TOKEN}")

    def test_a_netrc_login_is_not_added_on_a_redirect(self) -> None:
        netrc = self.tmp / "invented.netrc"
        netrc.write_text(
            "machine genius.com login invented-user password invented-pass\n",
            encoding="utf-8",
            newline="\n",
        )
        netrc.chmod(0o600)
        hops = Hop([redirect("https://genius.com/songs/1"), ok()])
        with (
            mock.patch.dict(os.environ, {"NETRC": str(netrc)}),
            http_fakes.transport(hops),
        ):
            _genius().song(1)
        self.assertIsNone(hops.header(1, "Authorization"))

    def test_redirects_past_the_limit_are_refused(self) -> None:
        to_genius = redirect("https://genius.com/songs/1")
        hops = Hop([to_genius] * (lg.GENIUS_MAX_REDIRECTS + 1) + [ok()])
        with http_fakes.transport(hops), self.assertRaises(requests.TooManyRedirects):
            _genius().song(1)
        self.assertEqual(len(hops.sent), lg.GENIUS_MAX_REDIRECTS + 1)

    def test_the_limit_is_the_shared_one(self) -> None:
        self.assertEqual(lg.GENIUS_MAX_REDIRECTS, lh.MAX_REDIRECTS)


class CookieTest(fixtures.IsolatedCase):
    """The session stores no cookie, and so sends none back."""

    def test_a_cookie_set_on_a_reply_is_not_sent_next(self) -> None:
        hops = Hop([setting_cookie(ok()), ok()])
        genius = _genius()
        with http_fakes.transport(hops):
            genius.song(1)
            genius.song(2)
        self.assertIsNone(hops.header(1, "Cookie"))
        self.assertEqual(len(genius._session.cookies), 0)

    def test_a_cookie_set_on_a_redirect_is_not_sent_on_the_next_hop(self) -> None:
        hops = Hop([setting_cookie(redirect("https://genius.com/songs/1")), ok()])
        genius = _genius()
        with http_fakes.transport(hops):
            genius.song(1)
        self.assertIsNone(hops.header(1, "Cookie"))
        self.assertEqual(len(genius._session.cookies), 0)

    def test_a_plain_session_would_have_sent_it(self) -> None:
        hops = Hop([setting_cookie(redirect("https://genius.com/songs/1")), ok()])
        with requests.Session() as session, http_fakes.transport(hops):
            session.get(SONG_URL, timeout=1)
        self.assertIsNotNone(hops.header(1, "Cookie"))


class HeaderTest(fixtures.IsolatedCase):
    """The library's own ``application`` header is never sent."""

    def test_no_request_names_the_library(self) -> None:
        # The search may ask more than one endpoint; every one answers no hits.
        hops = Hop([ok(http_fakes.json_body(NO_HITS))] * 8)
        genius = _genius()
        with http_fakes.transport(hops):
            genius.search_song(QUERY.title, QUERY.artist)
            genius.song(1)
        self.assertGreaterEqual(len(hops.sent), 2)
        sent = [hops.header(i, "application") for i in range(len(hops.sent))]
        self.assertEqual(set(sent), {None})
