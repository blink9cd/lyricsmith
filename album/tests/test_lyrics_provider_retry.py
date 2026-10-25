"""LRCLIB and NetEase retry a transient refusal, as the real provider table builds them.

No test here reaches the network or sleeps: the HTTP transport answers from memory
(`http_fakes`), each endpoint from its own queue of answers, and the table is built on a
fake clock whose sleeps only advance it (`genius_fakes.FakeClock`). Every name and
lyric is invented.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above

S = lm.LyricsKind.SYNCED
QUERY = lm.TrackQuery(
    "Invented Artist", "The Keeper Counts", album="Lamp Room", duration_s=200.6
)
SYNCED_TEXT = "[00:01.00]the keeper counts the lamps\n[00:04.00]and none are lit"
#: One LRCLIB record for QUERY, in the shape both its endpoints answer with.
RECORD: dict[str, object] = {
    "trackName": "The Keeper Counts",
    "artistName": "Invented Artist",
    "duration": 200.0,
    "instrumental": False,
    "plainLyrics": "the keeper counts the lamps\nand none are lit",
    "syncedLyrics": SYNCED_TEXT,
}
#: NetEase's search hit for QUERY, and its lyric reply.
NETEASE_SEARCH = {
    "code": 200,
    "result": {
        "songs": [
            {
                "id": 11,
                "name": "The Keeper Counts",
                "artists": [{"id": 1, "name": "Invented Artist"}],
                "duration": 200_000,
            }
        ]
    },
}
NETEASE_LYRIC = {"code": 200, "lrc": {"lyric": SYNCED_TEXT}}


class QueuedServer(http_fakes.RecordingServer):
    """Answers each path from its own queue, in order; its last answer then repeats."""

    def __init__(self, answers: Mapping[str, Sequence[object]]) -> None:
        """Answer each path in *answers* with its answers, in turn."""
        super().__init__()
        self.queues = {path: list(queue) for path, queue in answers.items()}

    def reply(self, path: str) -> object:
        """The next answer for *path*, or its last once the queue runs out."""
        queue = self.queues[path]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def count(self, path: str) -> int:
        """How many requests asked *path*."""
        return self.paths().count(path)


class ProviderRetryCase(fixtures.IsolatedCase):
    """The real table, built on a fake clock."""

    def setUp(self) -> None:
        """A fake clock, and the real table waiting on it."""
        super().setUp()
        self.clock = genius_fakes.FakeClock()
        self.table = ltable.build_provider_table(
            None, clock=self.clock, sleeper=self.clock.sleep
        )

    def ask(self, source: lm.Source, server: QueuedServer) -> str | None:
        """The table's synced step of *source*, asked for QUERY, answered by *server*."""
        with http_fakes.transport(server):
            return self.table[lm.ProviderKey(source, S)](QUERY)


class LrclibRetryTest(ProviderRetryCase):
    """LRCLIB's search, and a rate-limited lookup, get another try."""

    def test_a_search_answering_503_is_retried(self) -> None:
        server = QueuedServer({"/api/get": [404], "/api/search": [503, [RECORD]]})
        self.assertEqual(self.ask(lm.Source.LRCLIB, server), SYNCED_TEXT)
        self.assertEqual(server.count("/api/search"), 2)
        self.assertEqual(self.clock.sleeps, [lh.RETRY_WAITS_S[0]])

    def test_a_lookup_answering_429_is_retried(self) -> None:
        server = QueuedServer({"/api/get": [429, RECORD], "/api/search": [[]]})
        self.assertEqual(self.ask(lm.Source.LRCLIB, server), SYNCED_TEXT)
        self.assertEqual(server.paths(), ["/api/get", "/api/get"])

    def test_a_lookup_server_error_still_falls_to_the_search_unretried(self) -> None:
        server = QueuedServer({"/api/get": [503], "/api/search": [[RECORD]]})
        self.assertEqual(self.ask(lm.Source.LRCLIB, server), SYNCED_TEXT)
        self.assertEqual(server.paths(), ["/api/get", "/api/search"])
        self.assertEqual(self.clock.sleeps, [])


class NeteaseRetryTest(ProviderRetryCase):
    """NetEase's search and its lyric request each get another try."""

    def test_each_request_answering_503_is_retried(self) -> None:
        paths = {"search": "/api/search/get", "lyric": "/api/song/lyric"}
        for name, failing in paths.items():
            with self.subTest(request=name):
                self.clock.sleeps.clear()
                answers: dict[str, list[object]] = {
                    paths["search"]: [NETEASE_SEARCH],
                    paths["lyric"]: [NETEASE_LYRIC],
                }
                answers[failing].insert(0, 503)
                server = QueuedServer(answers)
                self.assertEqual(self.ask(lm.Source.NETEASE, server), SYNCED_TEXT)
                self.assertEqual(server.count(failing), 2)
                self.assertEqual(self.clock.sleeps, [lh.RETRY_WAITS_S[0]])


class ChainRefusalTest(ProviderRetryCase):
    """Through the chain, only the final failure is the step's refusal."""

    def fetch(self, answer: http_fakes.Answer) -> lm.FetchResult:
        """One track's fetch over the real table's LRC steps, answered by *answer*."""
        with http_fakes.transport(answer):
            return lf.fetch_lyrics(
                QUERY,
                lm.SidecarState(None),
                self.table,
                deadline_s=lh.PROVIDER_DEADLINE_S,
            )

    def details(self, result: lm.FetchResult) -> dict[lm.Source, set[str]]:
        """Each provider's attempt details in *result*."""
        found: dict[lm.Source, set[str]] = {}
        for attempt in result.attempts:
            found.setdefault(attempt.key.source, set()).add(attempt.detail)
        return found

    def test_a_refusal_every_retry_met_records_the_retries(self) -> None:
        def answer(request: requests.PreparedRequest) -> requests.Response:
            path = urlsplit(request.url or "").path
            return http_fakes.response(request, 404 if path == "/api/get" else 503)

        result = self.fetch(answer)
        self.assertEqual(
            self.details(result),
            {
                lm.Source.LRCLIB: {"HTTP 503 after 3 retries"},
                lm.Source.NETEASE: {"HTTP 503 after 3 retries"},
            },
        )

    def test_a_refusal_a_retry_got_past_is_no_refusal(self) -> None:
        server = QueuedServer(
            {
                "/api/get": [404],
                "/api/search": [503, [RECORD]],
                "/api/search/get": [NETEASE_SEARCH],
                "/api/song/lyric": [NETEASE_LYRIC],
            }
        )
        result = self.fetch(server)
        self.assertEqual(result.source, lm.Source.LRCLIB)
        self.assertEqual(
            [a.outcome for a in result.attempts], [lm.AttemptOutcome.ANSWERED]
        )
