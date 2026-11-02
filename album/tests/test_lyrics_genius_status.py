"""The real Genius client's error statuses: a challenge is retried, anything else is not.

lyricsgenius answers a non-OK API status with a bare ``AssertionError``, so these tests
drive the real client, as `build_genius_client` builds it, rather than a fake that says
what the library is supposed to raise. Its HTTP transport answers from memory
(`http_fakes`), so no request leaves the process, and the clock and sleeper are fakes,
so the retry schedule runs in no time.
"""

from __future__ import annotations

import os
import sys
import typing
from pathlib import Path
from unittest import mock

import lyricsgenius
import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    DEADLINE_S,
    FAKE_TOKEN,
    QUERY,
    FakeClock,
)

#: What the search endpoints answer when nothing matched.
NO_HITS = {"response": {"sections": [{"type": "top_hit", "hits": []}], "hits": []}}


class Recorder:
    """A transport answer that plays back *statuses*, then answers no hits; counts calls."""

    def __init__(self, statuses: list[int]) -> None:
        """Answer each of *statuses* in turn, then 200 with no hits."""
        self.statuses = statuses
        self.calls = 0
        self.authorizations: list[str | bytes | None] = []
        self.user_agents: list[str | bytes | None] = []

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """The next scripted response to *request*."""
        self.calls += 1
        self.authorizations.append(request.headers.get("Authorization"))
        self.user_agents.append(request.headers.get("User-Agent"))
        if self.statuses:
            return http_fakes.response(request, self.statuses.pop(0))
        return http_fakes.response(request, 200, http_fakes.json_body(NO_HITS))


def _genius(clock: FakeClock) -> lg.GeniusProvider:
    """The Genius step on the real client, with the fake clock and sleeper."""
    client = lg.build_genius_client(lr.Secret(FAKE_TOKEN))
    return lg.GeniusProvider(
        client, clock=clock, sleeper=clock.sleep, deadline_s=DEADLINE_S
    )


class RealGeniusStatusTest(fixtures.IsolatedCase):
    """The schedule runs on the real library's failures, with no network."""

    def test_a_challenge_is_retried_on_the_whole_schedule(self) -> None:
        for status in sorted(lg.GENIUS_CHALLENGE_STATUSES):
            with self.subTest(status=status):
                clock = FakeClock()
                answer = Recorder([status] * 4)
                with http_fakes.transport(answer):
                    with self.assertRaises(lg.GeniusChallengeError):
                        _genius(clock)(QUERY)
                self.assertEqual(clock.sleeps, list(lg.GENIUS_RETRY_WAITS_S))
                self.assertEqual(answer.calls, len(lg.GENIUS_RETRY_WAITS_S) + 1)

    def test_a_challenge_then_an_answer_stops_retrying(self) -> None:
        clock = FakeClock()
        answer = Recorder([429])
        with http_fakes.transport(answer), self.assertLogs("lyricsgenius"):
            self.assertIsNone(_genius(clock)(QUERY))
        self.assertEqual(clock.sleeps, [lg.GENIUS_RETRY_WAITS_S[0]])

    def test_a_refused_key_is_raised_at_once(self) -> None:
        clock = FakeClock()
        with http_fakes.transport(http_fakes.always(401)):
            with self.assertRaises(lp.HttpStatusError) as raised:
                _genius(clock)(QUERY)
        self.assertEqual(raised.exception.status, 401)
        self.assertEqual(clock.sleeps, [])

    def test_a_netrc_login_is_never_sent_and_the_bearer_key_stays(self) -> None:
        netrc = self.tmp / "invented.netrc"
        logins = (
            f"machine {host} login invented-user password invented-pass\n"
            for host in ("api.genius.com", "genius.com")
        )
        netrc.write_text("".join(logins), encoding="utf-8", newline="\n")
        netrc.chmod(0o600)
        answer = Recorder([])
        client = lg.build_genius_client(lr.Secret(FAKE_TOKEN))
        with (
            mock.patch.dict(os.environ, {"NETRC": str(netrc)}),
            http_fakes.transport(answer),
        ):
            # The search goes to the key-free public API; a song lookup carries the key.
            client.search_song(QUERY.title, QUERY.artist)
            typing.cast(lyricsgenius.Genius, client).song(1)
        self.assertEqual(answer.authorizations[-1], f"Bearer {FAKE_TOKEN}")
        self.assertEqual(set(answer.authorizations[:-1]), {None})

    def test_every_request_names_the_tool_and_nothing_about_the_machine(self) -> None:
        answer = Recorder([])
        client = lg.build_genius_client(lr.Secret(FAKE_TOKEN))
        with http_fakes.transport(answer):
            client.search_song(QUERY.title, QUERY.artist)
            typing.cast(lyricsgenius.Genius, client).song(1)
        # lyricsgenius' own header names the operating system and the Python release.
        self.assertGreaterEqual(answer.calls, 2)
        self.assertEqual(set(answer.user_agents), {lh.user_agent()})
