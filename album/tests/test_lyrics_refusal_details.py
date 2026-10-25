"""The short detail each provider's refusal keeps, and the refusals a fetch's attempts make.

A refusal's detail says why a provider refused or failed in a few words: an HTTP
status, NetEase's refusal code, ``timeout``, or a fixed phrase for a reply in a shape
the tool does not know. It never holds a reply's body or the request's URL, so no
track title reaches it. An answer that is merely empty is no refusal. No test here
reaches the network: the HTTP transport answers from memory, so the real adapters, their
sessions and the status hook all run. Every name and lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes as lo  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    FakeClock,
    FakeGeniusClient,
    FakeSong,
    challenge,
    paced_genius,
)
from test_lyrics_lrclib import FakeLrclib  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_netease import FakeNetEase  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB, NETEASE, GENIUS = lm.Source.LRCLIB, lm.Source.NETEASE, lm.Source.GENIUS
#: A title no detail may hold: it would mean a URL or a body leaked into one.
TITLE = "Quillwort Zephyrine Lanterns"
QUERY = lm.TrackQuery("Invented Artist", TITLE, album="Lamp Room", duration_s=200.6)
#: Text a reply body carries, which no detail may hold either.
BODY_TEXT = "a refusal body naming Quillwort Zephyrine Lanterns"
NO_SIDECAR = lm.SidecarState(None)


def _fetch_with(
    source: lm.Source, server: http_fakes.RecordingServer
) -> lm.FetchResult:
    """The fetch of `QUERY` over *source*'s real synced step, answered by *server*."""
    key = lm.ProviderKey(source, S)
    table = {key: genius_fakes.instant_table()[key]}
    with http_fakes.transport(server):
        return lf.fetch_lyrics(
            QUERY, NO_SIDECAR, table, deadline_s=lf.PROVIDER_DEADLINE_S
        )


def _details(result: lm.FetchResult) -> list[str]:
    """Each refusal's detail, from *result*'s attempts."""
    return [refusal.detail for refusal in lo.fetch_refusals(result.attempts)]


class NeteaseDetailTest(fixtures.IsolatedCase):
    """NetEase's refusal keeps its code: the HTTP status, or the JSON ``code``."""

    def test_an_http_status_refusal_keeps_its_status(self) -> None:
        cases = {403: "HTTP 403", 503: "HTTP 503 after 3 retries"}
        for status, detail in cases.items():
            with self.subTest(status=status):
                result = _fetch_with(NETEASE, FakeNetEase(status))
                self.assertEqual(_details(result), [detail])

    def test_a_json_code_refusal_keeps_its_code(self) -> None:
        body = {"code": -462, "message": BODY_TEXT}
        result = _fetch_with(NETEASE, FakeNetEase(body))
        self.assertEqual(_details(result), ["code -462"])
        self.assertEqual(
            lo.fetch_refusals(result.attempts),
            (lr.ProviderRefusal(NETEASE, S, "code -462"),),
        )

    def test_a_reply_in_another_shape_says_so_in_fixed_words(self) -> None:
        result = _fetch_with(NETEASE, FakeNetEase({"result": [], "note": BODY_TEXT}))
        self.assertEqual(_details(result), ["unexpected reply"])

    def test_no_detail_holds_the_title_or_the_body(self) -> None:
        bodies: list[object] = [
            {"code": -460, "message": BODY_TEXT},
            {"result": {"songs": BODY_TEXT}},
            BODY_TEXT.encode(),
            429,
        ]
        for body in bodies:
            with self.subTest(body=body):
                for detail in _details(_fetch_with(NETEASE, FakeNetEase(body))):
                    self.assertNotIn(TITLE, detail)
                    self.assertNotIn("Quillwort", detail)
                    self.assertNotIn("://", detail)


class LrclibDetailTest(fixtures.IsolatedCase):
    """LRCLIB's refusals: its status, or a fixed phrase for a reply it cannot read."""

    def test_a_search_status_is_kept(self) -> None:
        cases = {403: "HTTP 403", 429: "HTTP 429 after 3 retries"}
        for status, detail in cases.items():
            with self.subTest(status=status):
                result = _fetch_with(LRCLIB, FakeLrclib(hits=status))
                self.assertEqual(_details(result), [detail])

    def test_a_reply_that_is_not_json_says_so(self) -> None:
        result = _fetch_with(LRCLIB, FakeLrclib(hits=BODY_TEXT.encode()))
        self.assertEqual(_details(result), ["reply not JSON"])

    def test_a_reply_in_another_shape_says_so_without_the_title(self) -> None:
        result = _fetch_with(LRCLIB, FakeLrclib(hits={"records": BODY_TEXT}))
        self.assertEqual(_details(result), ["unexpected reply"])
        self.assertNotIn(TITLE, _details(result)[0])

    def test_an_empty_search_is_no_refusal(self) -> None:
        result = _fetch_with(LRCLIB, FakeLrclib(hits=[]))
        self.assertEqual(
            [a.outcome for a in result.attempts], [lm.AttemptOutcome.EMPTY]
        )
        self.assertEqual(lo.fetch_refusals(result.attempts), ())


class GeniusDetailTest(fixtures.IsolatedCase):
    """Only a Genius step's final failure is a refusal; a retry that answered is none."""

    def _fetch(self, *script: FakeSong | Exception | None) -> lm.FetchResult:
        """A fetch over the Genius step alone, its client playing *script*."""
        genius = paced_genius(FakeGeniusClient(list(script)), FakeClock())
        table = {lm.ProviderKey(GENIUS, U): genius}
        return lf.fetch_lyrics(
            QUERY, NO_SIDECAR, table, deadline_s=lf.PROVIDER_DEADLINE_S
        )

    def test_a_challenge_outlasting_every_retry_keeps_its_status(self) -> None:
        result = self._fetch(*(challenge(429) for _ in range(4)))
        self.assertEqual(
            lo.fetch_refusals(result.attempts),
            (lr.ProviderRefusal(GENIUS, U, "HTTP 429 after 3 retries"),),
        )

    def test_a_challenge_a_retry_got_past_is_no_refusal(self) -> None:
        song = FakeSong("a lamp on the stair", title=TITLE)
        result = self._fetch(challenge(403), song)
        self.assertTrue(result.found)
        self.assertEqual(lo.fetch_refusals(result.attempts), ())

    def test_another_error_status_is_kept_at_once(self) -> None:
        result = self._fetch(lp.HttpStatusError(GENIUS, 500))
        self.assertEqual(_details(result), ["HTTP 500"])


class FetchRefusalsTest(fixtures.IsolatedCase):
    """Every attempt that raised or timed out is a refusal; any other attempt is not."""

    def test_a_timeout_is_recorded_as_timeout(self) -> None:
        timeout = lp.ProviderTimeout(f"no answer within {lf.PROVIDER_DEADLINE_S}s")
        table = {lm.ProviderKey(LRCLIB, S): fixtures.raising(timeout)}
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=1.0)
        self.assertEqual(
            lo.fetch_refusals(result.attempts),
            (lr.ProviderRefusal(LRCLIB, S, "timeout"),),
        )

    def test_an_error_of_no_known_kind_is_its_type_alone(self) -> None:
        table = {lm.ProviderKey(LRCLIB, S): fixtures.raising(OSError(TITLE))}
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=1.0)
        self.assertEqual(_details(result), ["OSError"])

    def test_refusals_keep_the_order_asked_beside_empty_answers(self) -> None:
        table = {
            lm.ProviderKey(LRCLIB, S): fixtures.raising(
                lp.HttpStatusError(LRCLIB, 503)
            ),
            lm.ProviderKey(NETEASE, S): fixtures.empty(),
            lm.ProviderKey(LRCLIB, U): fixtures.empty(),
            lm.ProviderKey(GENIUS, U): fixtures.raising(
                lp.HttpStatusError(GENIUS, 403)
            ),
        }
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=1.0)
        self.assertFalse(result.found)
        self.assertFalse(result.every_provider_failed)
        self.assertEqual(
            lo.fetch_refusals(result.attempts),
            (
                lr.ProviderRefusal(LRCLIB, S, "HTTP 503"),
                lr.ProviderRefusal(GENIUS, U, "HTTP 403"),
            ),
        )
