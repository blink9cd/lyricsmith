"""The LRC providers' HTTP limits: the reply-size cap, no redirect, no netrc credentials.

No test here reaches the network or a proxy: requests go through a real session whose
transport answers from memory (`http_fakes`), and every netrc file is invented, under
the test's own temporary directory, never the real one.
"""

from __future__ import annotations

import gzip
import io
import os
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import requests
import requests.adapters
import urllib3.response

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above

#: A reserved name the transport fake answers, so nothing is ever resolved.
URL = "https://example.invalid/api"
#: The hosts an invented netrc file holds credentials for: the LRC providers' own.
NETRC_HOSTS = ("lrclib.net", "music.163.com", "api.genius.com")
#: A documentation address standing in for a proxy, never contacted.
FAKE_PROXY = "http://192.0.2.10:3128"
#: Every proxy variable a lookup may read, in both spellings, and the bypass list.
PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)
#: What a compressed bomb inflates to: 50 MiB of zeros, from about 50 KiB on the wire.
BOMB_INFLATED_BYTES = 50 * 1024 * 1024


def write_netrc(directory: Path, hosts: tuple[str, ...] = NETRC_HOSTS) -> Path:
    """An invented netrc file under *directory* with a login for each of *hosts*."""
    netrc = directory / "invented.netrc"
    lines = [
        f"machine {host} login invented-user password invented-pass" for host in hosts
    ]
    netrc.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    netrc.chmod(0o600)
    return netrc


class CountingStream(io.BytesIO):
    """A raw reply body that counts the bytes read from it."""

    def __init__(self, body: bytes) -> None:
        """Hold *body*, none of it read yet."""
        super().__init__(body)
        self.bytes_read = 0

    def read(self, size: int | None = -1) -> bytes:
        """Read as a stream does, counting what was read."""
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


class ReplySizeTest(fixtures.IsolatedCase):
    """A reply over the cap is refused, before it is read whole or decoded."""

    def _get(self, answer: http_fakes.Answer) -> lh.Reply | None:
        """GET URL, answered by *answer*."""
        with lh.new_session() as session, http_fakes.transport(answer):
            return lh.get_json(session, URL, {}, lm.Source.LRCLIB)

    def _assert_refused(self, answer: http_fakes.Answer) -> None:
        """*answer*'s reply is refused with the fixed text naming the source."""
        with self.assertRaises(lp.ProviderError) as raised:
            self._get(answer)
        self.assertEqual(
            str(raised.exception),
            f"{lm.Source.LRCLIB} answered a reply over {lh.MAX_REPLY_BYTES} bytes",
        )

    def test_an_oversize_declared_length_is_refused_unread(self) -> None:
        stream = CountingStream(b"[]")

        def answer(request: requests.PreparedRequest) -> requests.Response:
            built = http_fakes.response(
                request, 200, headers={"Content-Length": str(lh.MAX_REPLY_BYTES + 1)}
            )
            built.raw = stream
            return built

        self._assert_refused(answer)
        self.assertEqual(stream.bytes_read, 0)

    def test_a_declared_length_of_thousands_of_digits_is_refused_unread(self) -> None:
        # int() of 4301 or more digits raised past CPython's digit limit.
        stream = CountingStream(b"[]")

        def answer(request: requests.PreparedRequest) -> requests.Response:
            built = http_fakes.response(
                request, 200, headers={"Content-Length": "9" * 5000}
            )
            built.raw = stream
            return built

        self._assert_refused(answer)
        self.assertEqual(stream.bytes_read, 0)

    def test_leading_zeros_do_not_make_a_length_too_large(self) -> None:
        def answer(request: requests.PreparedRequest) -> requests.Response:
            headers = {"Content-Length": "0" * 5000 + "3"}
            return http_fakes.response(request, 200, b"[1]", headers=headers)

        self.assertEqual(self._get(answer), lh.Reply(URL, [1]))

    def test_an_oversize_body_with_no_length_is_refused(self) -> None:
        stream = CountingStream(b" " * (lh.MAX_REPLY_BYTES * 2))

        def answer(request: requests.PreparedRequest) -> requests.Response:
            built = http_fakes.response(request, 200)
            built.raw = stream
            return built

        self._assert_refused(answer)
        # The read stops once past the cap, without the rest of the body.
        self.assertLess(stream.bytes_read, lh.MAX_REPLY_BYTES + lh.READ_CHUNK_BYTES + 1)

    def test_a_body_exactly_at_the_cap_is_accepted(self) -> None:
        text = "a" * (lh.MAX_REPLY_BYTES - 2)
        body = http_fakes.json_body(text)
        self.assertEqual(len(body), lh.MAX_REPLY_BYTES)

        def answer(request: requests.PreparedRequest) -> requests.Response:
            return http_fakes.response(
                request, 200, body, headers={"Content-Length": str(len(body))}
            )

        reply = self._get(answer)
        assert reply is not None
        self.assertEqual(reply.body, text)

    def test_an_unreadable_declared_length_is_counted_instead(self) -> None:
        # A superscript two is a digit to str.isdigit(), but no number int() reads.
        for declared in ("lots", "²"):
            with self.subTest(declared=declared):
                headers = {"Content-Length": declared}

                def answer(
                    request: requests.PreparedRequest, headers: dict[str, str] = headers
                ) -> requests.Response:
                    return http_fakes.response(request, 200, b"[1]", headers=headers)

                self.assertEqual(self._get(answer), lh.Reply(URL, [1]))


class CountingHTTPResponse(urllib3.response.HTTPResponse):
    """A real urllib3 response that counts the decoded bytes its reads hand back."""

    decoded_read = 0

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        """Read as urllib3 does, counting what came back after decompression."""
        data = super().read(*args, **kwargs)
        self.decoded_read += len(data)
        return data


class CompressedReplyTest(fixtures.IsolatedCase):
    """A small compressed reply that inflates far past the cap is refused part-inflated.

    This holds only because urllib3, from 2.6, inflates no more than a read asks for:
    the project pins it there, and this test runs the real library to prove it.
    """

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

        with lh.new_session() as session, http_fakes.transport(answer):
            with self.assertRaises(lp.ProviderError):
                lh.get_json(session, URL, {}, lm.Source.LRCLIB)
        self.assertGreater(raw.decoded_read, lh.MAX_REPLY_BYTES)
        self.assertLessEqual(raw.decoded_read, lh.MAX_REPLY_BYTES + lh.READ_CHUNK_BYTES)


class RedirectTest(fixtures.IsolatedCase):
    """A redirect is never followed: it is that step's error, absent or not."""

    def test_a_redirect_to_another_host_errors_with_no_second_request(self) -> None:
        asked: list[str] = []

        def answer(request: requests.PreparedRequest) -> requests.Response:
            asked.append(request.url or "")
            return http_fakes.response(
                request, 302, headers={"Location": "https://elsewhere.invalid/x"}
            )

        for absent in (frozenset(), frozenset({302})):
            with self.subTest(absent=sorted(absent)):
                asked.clear()
                with lh.new_session() as session, http_fakes.transport(answer):
                    with self.assertRaises(lp.HttpStatusError) as raised:
                        lh.get_json(session, URL, {}, lm.Source.NETEASE, absent=absent)
                self.assertEqual(raised.exception.status, 302)
                self.assertEqual(asked, [URL])


class NetrcTest(fixtures.IsolatedCase):
    """No credential from a netrc file is sent, while the proxy settings still apply."""

    def _send_kwargs_and_headers(
        self, url: str
    ) -> tuple[dict[str, object], dict[str, str | bytes]]:
        """GET *url* through a new session; the transport's kwargs and request headers."""
        seen: list[tuple[dict[str, object], dict[str, str | bytes]]] = []

        def send(
            adapter: requests.adapters.HTTPAdapter,
            request: requests.PreparedRequest,
            **kwargs: object,
        ) -> requests.Response:
            seen.append((kwargs, dict(request.headers)))
            return http_fakes.response(request, 200, b"[]")

        with (
            lh.new_session() as session,
            mock.patch.object(requests.adapters.HTTPAdapter, "send", send),
        ):
            lh.get_json(session, url, {}, lm.Source.LRCLIB)
        return seen[0]

    def test_a_netrc_login_for_the_host_is_not_sent(self) -> None:
        netrc = write_netrc(self.tmp)
        with mock.patch.dict(os.environ, {"NETRC": str(netrc)}):
            for host in NETRC_HOSTS[:2]:
                with self.subTest(host=host):
                    url = f"https://{host}/api"
                    # The invented file does name the host, so requests would use it.
                    self.assertIsNotNone(requests.utils.get_netrc_auth(url))
                    _, headers = self._send_kwargs_and_headers(url)
                    self.assertNotIn("Authorization", headers)

    def test_the_proxy_environment_still_applies(self) -> None:
        # Every inherited proxy variable is cleared first: the lookup prefers a lowercase
        # spelling over the uppercase one set here, and a contained run sets both.
        with mock.patch.dict(os.environ):
            for key in PROXY_KEYS:
                os.environ.pop(key, None)
            os.environ["HTTPS_PROXY"] = FAKE_PROXY
            kwargs, _ = self._send_kwargs_and_headers(URL)
        proxies = kwargs["proxies"]
        assert isinstance(proxies, dict)
        self.assertEqual(proxies.get("https"), FAKE_PROXY)

    def test_the_session_has_no_netrc_but_trusts_the_environment(self) -> None:
        with lh.new_session() as session:
            self.assertTrue(session.trust_env)
            request = requests.PreparedRequest()
            self.assertIs(lh.send_unchanged(request), request)
