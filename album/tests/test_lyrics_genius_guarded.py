"""Genius's keyed search asked through its breaker (`guarded_search`).

A search is never sent while the breaker is open. An answer, a refusal, or a timeout
settles the breaker, and a `KeyboardInterrupt` raised by the search's own code reaches
the caller and settles nothing. The searcher runs on the scripted client and the fake clock
`test_lyrics_genius_search` describes; every title and artist is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes as gf  # noqa: E402 - import follows the sys.path bootstrap above
import genius_search_fakes as gsf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breaker as lbk  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breakers as lbs  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius_search as lgs  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_refusal import Refusal, classify  # noqa: E402 - import follows the sys.path bootstrap above


class GuardedSearchTest(fixtures.IsolatedCase):
    """A search asked through Genius's breaker: never sent while it is open."""

    def setUp(self) -> None:
        """A closed breaker, writing its file under the test's state folder."""
        super().setUp()
        self.clock = gf.FakeClock()
        path = self.tmp / "genius_breaker.json"
        self.guard = lbs.BreakerGuard(
            lbs.BreakerSetup(path, lbk.CLOSED, lambda: gsf.NOW)
        )

    def test_an_answer_is_the_hits(self) -> None:
        """A search answered gives its hits, the breaker still closed."""
        client = gsf.FakeSearchClient([gsf.ONE_SONG])
        got = lgs.guarded_search(gsf.searcher(client, self.clock), self.guard, gsf.TERM)
        assert isinstance(got, lgs.SearchAnswer)
        self.assertEqual(len(got.page.hits), 1)
        self.assertFalse(self.guard.state.is_open(gsf.NOW))

    def test_a_refusal_is_sent_and_opens_the_breaker(self) -> None:
        """A challenge past every retry is a sent refusal, and opens the breaker."""
        script: list[gsf.Step] = [gf.challenge(403)] * 4
        client = gsf.FakeSearchClient(script)
        got = lgs.guarded_search(gsf.searcher(client, self.clock), self.guard, gsf.TERM)
        assert isinstance(got, lgs.SearchRefusal)
        self.assertTrue(got.sent)
        self.assertEqual(got.detail, "HTTP 403 after 3 retries")
        self.assertTrue(self.guard.state.is_open(gsf.NOW))
        self.assertEqual(self.guard.state.reason, Refusal.BLOCKED)

    def test_an_open_breaker_sends_nothing(self) -> None:
        """While the breaker is open, no search is made."""
        challenge = gf.challenge(403)
        self.guard.settle(challenge, classify(challenge))
        client = gsf.FakeSearchClient([])
        got = lgs.guarded_search(gsf.searcher(client, self.clock), self.guard, gsf.TERM)
        assert isinstance(got, lgs.SearchRefusal)
        self.assertFalse(got.sent)
        self.assertEqual(client.calls, [])

    def test_a_timeout_is_a_sent_refusal(self) -> None:
        """A search abandoned at its deadline records the timeout detail."""
        client = gsf.FakeSearchClient([lp.ProviderTimeout("no answer")])
        got = lgs.guarded_search(gsf.searcher(client, self.clock), self.guard, gsf.TERM)
        assert isinstance(got, lgs.SearchRefusal)
        self.assertEqual(got.detail, lp.TIMEOUT_DETAIL)
        self.assertTrue(got.sent)

    def test_any_other_failure_is_named_by_its_type(self) -> None:
        """A failure with no detail of its own records its type's name alone."""
        client = gsf.FakeSearchClient([ValueError("a reply with the term in it")])
        got = lgs.guarded_search(gsf.searcher(client, self.clock), self.guard, gsf.TERM)
        assert isinstance(got, lgs.SearchRefusal)
        self.assertEqual(got.detail, "ValueError")

    def test_an_interrupt_is_raised_and_leaves_the_breaker_alone(self) -> None:
        """A `KeyboardInterrupt` from the search's code is raised, never a refusal."""
        client = gsf.FakeSearchClient([KeyboardInterrupt()])
        searcher = gsf.searcher(client, self.clock)
        with (
            mock.patch.object(self.guard, "settle") as settle,
            self.assertRaises(KeyboardInterrupt),
        ):
            lgs.guarded_search(searcher, self.guard, gsf.TERM)
        settle.assert_not_called()


if __name__ == "__main__":  # pragma: no cover - run directly, not under the gate
    unittest.main()
