"""Genius's keyed search: its reply read, its calls paced and retried, redirects refused.

The searcher runs on a scripted client (`genius_search_fakes`) and a clock that moves
only when its sleeper sleeps (`genius_fakes.FakeClock`), so the pacing and the retry
schedule take no time. The real client, as `build_search_client` builds it, runs with
its transport answering from memory (`http_fakes`), so the request it sends is read
without one leaving the process: the keyed API's search, with the bearer header, and
never the unkeyed site. Every title, artist and URL is invented; the key is a fake.
The breaker's half, `guarded_search`, is `test_lyrics_genius_guarded`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import requests
from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes as gf  # noqa: E402 - import follows the sys.path bootstrap above
import genius_search_fakes as gsf  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breaker as lbk  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breakers as lbs  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius_search as lgs  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_model import Source  # noqa: E402 - import follows the sys.path bootstrap above


#: A song record's own field names, which the property test's values are keyed by.
_FIELDS = (
    "hits",
    "type",
    "result",
    "title",
    "url",
    "lyrics_state",
    "instrumental",
    "primary_artist",
    "primary_artists",
    "name",
)
_VALUES = http_fakes.json_values(_FIELDS, max_size=6, max_leaves=30)
#: Replies of any JSON shape, and replies of the search's shape with any records in it.
_REPLIES = _VALUES | st.fixed_dictionaries(
    {
        "hits": st.lists(
            st.fixed_dictionaries(
                {"type": st.sampled_from(("song", "artist")), "result": _VALUES}
            ),
            max_size=4,
        )
    }
)


class ParseReplyTest(fixtures.IsolatedCase):
    """A search reply is read into hits, or refused whole when not a search's shape."""

    def test_reads_a_song_hit(self) -> None:
        """Each hit's title, credit, URL and lyrics state are read."""
        reply = gsf.search_reply(
            gsf.song_result(
                "Lanterns",
                "Invented Artist",
                primary_artists=("Invented Artist", "Made-Up Guest"),
            )
        )
        (hit,) = lgs.parse_search_reply(reply).hits
        self.assertEqual(hit.title, "Lanterns")
        self.assertEqual(hit.artist, "Invented Artist")
        self.assertEqual(lg.song_hit(hit).artists, ("Invented Artist", "Made-Up Guest"))
        self.assertEqual(hit.url, gsf.SONG_URL)
        self.assertTrue(hit.has_lyrics)
        self.assertEqual(hit.lyrics, "")

    def test_without_primary_artists_the_one_artist_is_the_credit(self) -> None:
        """A reply listing no credit records credits ``primary_artist``'s name."""
        (hit,) = lgs.parse_search_reply(gsf.ONE_SONG).hits
        self.assertEqual(lg.song_hit(hit).artists, ("Invented Artist",))

    def test_a_foreign_url_is_dropped(self) -> None:
        """A URL not on a Genius host is never kept."""
        reply = gsf.search_reply(
            gsf.song_result("Lanterns", "Invented Artist", url=gsf.FOREIGN_URL)
        )
        (hit,) = lgs.parse_search_reply(reply).hits
        self.assertIsNone(hit.url)

    def test_not_complete_or_instrumental_has_no_lyrics(self) -> None:
        """Only a complete, sung song has lyrics on Genius."""
        reply = gsf.search_reply(
            gsf.song_result("A", "Invented Artist", lyrics_state="unreleased"),
            gsf.song_result("B", "Invented Artist", instrumental=True),
        )
        first, second = lgs.parse_search_reply(reply).hits
        self.assertFalse(first.has_lyrics)
        self.assertTrue(second.complete)
        self.assertTrue(second.instrumental)
        self.assertFalse(second.has_lyrics)

    def test_hits_of_another_shape_are_skipped(self) -> None:
        """A hit that is not a song with a title is left out; the rest are read."""
        good = gsf.song_result("Lanterns", "Invented Artist")
        reply = {
            "hits": [
                "not a hit",
                {"type": "artist", "result": good},
                {"type": "song", "result": "not a record"},
                {"type": "song", "result": {"title": "  "}},
                {"type": "song", "result": {"title": 7}},
                {"type": "song", "result": good},
            ]
        }
        page = lgs.parse_search_reply(reply)
        self.assertEqual(len(page.hits), 1)
        self.assertEqual(page.listed, 6)
        self.assertTrue(page.skipped)

    def test_a_page_of_song_hits_skips_none(self) -> None:
        """Every hit read: none skipped; a page shorter than a full one is not full."""
        page = lgs.parse_search_reply(gsf.ONE_SONG)
        self.assertFalse(page.skipped)
        self.assertFalse(page.full)
        self.assertEqual(page.listed, 1)

    def test_a_page_of_the_most_hits_is_full(self) -> None:
        """A page holding `SEARCH_PER_PAGE` hits may have left one out: it is full."""
        song = gsf.song_result("Lanterns", "Invented Artist")
        page = lgs.parse_search_reply(gsf.search_reply(*[song] * lgs.SEARCH_PER_PAGE))
        self.assertTrue(page.full)

    def test_no_hit_read_from_a_drifted_reply_is_refused(self) -> None:
        """Hits of a drifted shape, none read, are an error: never no hits at all."""
        record = gsf.song_result("Lanterns", "Invented Artist")
        drifted: tuple[list[object], ...] = (
            [{"type": "Song", "result": record}],
            [{"type": "song", "song": record}],
            [{"type": "song", "result": {"full_title": "Lanterns by Invented Artist"}}],
        )
        for hits in drifted:
            with self.subTest(hits=hits):
                with self.assertRaises(lp.ProviderError) as got:
                    lgs.parse_search_reply({"hits": hits})
                self.assertEqual(got.exception.detail, lgs.HITS_UNREADABLE)

    def test_no_hits_at_all_is_an_empty_page(self) -> None:
        """An empty hit list is an answer: nothing found, nothing skipped."""
        page = lgs.parse_search_reply({"hits": []})
        self.assertEqual((page.hits, page.listed, page.skipped), ((), 0, False))

    def test_odd_fields_read_as_absent(self) -> None:
        """A field of the wrong type reads as missing, never as a value."""
        record = {
            "title": "Lanterns",
            "url": 3,
            "lyrics_state": None,
            "instrumental": "yes",
            "primary_artist": "not a record",
            "primary_artists": [{"name": 4}, "not a record"],
        }
        (hit,) = lgs.parse_search_reply(
            {"hits": [{"type": "song", "result": record}]}
        ).hits
        self.assertEqual(hit.artist, "")
        self.assertIsNone(hit.url)
        self.assertFalse(hit.complete)
        self.assertFalse(hit.instrumental)
        self.assertEqual(hit.primary_artists, ())

    def test_credit_list_not_a_list_is_none(self) -> None:
        """A ``primary_artists`` that is not a list holds no credit."""
        record = gsf.song_result("Lanterns", "Invented Artist")
        record["primary_artists"] = {"name": "Invented Artist"}
        (hit,) = lgs.parse_search_reply(
            {"hits": [{"type": "song", "result": record}]}
        ).hits
        self.assertEqual(hit.primary_artists, ())

    def test_a_reply_not_a_search_is_refused(self) -> None:
        """A reply that is no mapping, or holds no hit list, is an error, not nothing."""
        replies: tuple[object, ...] = ("text", {"sections": []}, {"hits": "none"})
        for reply in replies:
            with self.subTest(reply=reply), self.assertRaises(lp.ProviderError) as got:
                lgs.parse_search_reply(reply)
            self.assertEqual(got.exception.detail, lgs.REPLY_NOT_SEARCH)

    @given(_REPLIES)
    def test_fuzz_parse_search_reply(self, reply: object) -> None:
        """Any JSON-like reply is read into hits or refused as a `ProviderError`."""
        details: list[str | None] = []
        try:
            page = lgs.parse_search_reply(reply)
        except lp.ProviderError as error:
            page, details = lgs.SearchPage((), 0), [error.detail]
        hits = page.hits
        self.assertLessEqual(set(details), {lgs.REPLY_NOT_SEARCH, lgs.HITS_UNREADABLE})
        self.assertLessEqual(len(hits), page.listed)
        self.assertEqual(page.skipped, len(hits) < page.listed)
        self.assertTrue(hits or not page.listed)
        self.assertTrue(all(hit.title.strip() for hit in hits))
        urls = [hit.url for hit in hits if hit.url is not None]
        self.assertTrue(all(lg.genius_url_allowed(url) for url in urls))


class SearcherTest(fixtures.IsolatedCase):
    """One keyed search per term, paced, a challenge retried on the step's schedule."""

    def setUp(self) -> None:
        """A fresh fake clock for each test."""
        super().setUp()
        self.clock = gf.FakeClock()

    def test_asks_five_results_for_the_term(self) -> None:
        """The term goes to ``search_songs`` with a page of five, once."""
        client = gsf.FakeSearchClient([gsf.ONE_SONG])
        hits = gsf.searcher(client, self.clock).search(gsf.TERM)
        self.assertEqual(client.calls, [(gsf.TERM, lgs.SEARCH_PER_PAGE)])
        self.assertEqual(lgs.SEARCH_PER_PAGE, 5)
        self.assertEqual(hits.hits[0].title, "The Keeper Counts")

    def test_paces_searches_apart(self) -> None:
        """Two searches start at least the Genius interval apart."""
        client = gsf.FakeSearchClient([gsf.ONE_SONG, gsf.ONE_SONG])
        searcher = gsf.searcher(client, self.clock)
        searcher.search(gsf.TERM)
        searcher.search(gsf.TERM)
        gap = client.times[1] - client.times[0]
        self.assertGreaterEqual(gap, lg.GENIUS_MIN_INTERVAL_S)

    def test_a_challenge_is_retried_on_the_schedule(self) -> None:
        """A 403 then a 429 are retried after the schedule's waits, then answered."""
        script: list[gsf.Step] = [gf.challenge(403), gf.challenge(429), gsf.ONE_SONG]
        client = gsf.FakeSearchClient(script)
        hits = gsf.searcher(client, self.clock).search(gsf.TERM)
        self.assertEqual(len(hits.hits), 1)
        self.assertEqual(self.clock.sleeps[:1], [lg.GENIUS_RETRY_WAITS_S[0]])
        self.assertIn(lg.GENIUS_RETRY_WAITS_S[1], self.clock.sleeps)

    def test_still_challenged_after_every_retry(self) -> None:
        """A challenge past the last retry raises, counting every retry made."""
        script: list[gsf.Step] = [gf.challenge(403)] * 4
        client = gsf.FakeSearchClient(script)
        with self.assertRaises(lg.GeniusChallengeError) as raised:
            gsf.searcher(client, self.clock).search(gsf.TERM)
        self.assertEqual(raised.exception.retries, len(lg.GENIUS_RETRY_WAITS_S))
        self.assertEqual(len(client.calls), 4)

    def test_another_error_status_is_not_retried(self) -> None:
        """A 401, a refused key, raises at once."""
        error = lp.HttpStatusError(Source.GENIUS, 401)
        client = gsf.FakeSearchClient([error])
        with self.assertRaises(lp.HttpStatusError):
            gsf.searcher(client, self.clock).search(gsf.TERM)
        self.assertEqual(len(client.calls), 1)

    def test_a_retry_wait_past_the_deadline_is_not_begun(self) -> None:
        """When the next wait would end past the deadline, the challenge is the error."""
        client = gsf.FakeSearchClient([gf.challenge(429)])
        searcher = gsf.searcher(client, self.clock)
        client.clock = gf.ticker(self.clock, [searcher.step_deadline_s()])
        with self.assertRaises(lp.HttpStatusError) as raised:
            searcher.search(gsf.TERM)
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(len(client.calls), 1)

    def test_a_turn_timed_out_on_the_first_try_is_a_timeout(self) -> None:
        """A turn taken past the deadline sends no request: a timeout."""
        client = gsf.FakeSearchClient([])
        searcher = gsf.searcher(client, self.clock)
        # The deadline is set at the first reading; the turn is taken at the second,
        # long past it.
        readings = iter((0.0, 1e12))
        searcher.clock = lambda: next(readings)
        with self.assertRaises(lp.ProviderTimeout):
            searcher.search(gsf.TERM)
        self.assertEqual(client.calls, [])

    def test_a_retry_turn_timed_out_ends_on_the_challenge(self) -> None:
        """A retry whose turn times out sends nothing; the challenge is the error."""
        client = gsf.FakeSearchClient([gf.challenge(403)])
        searcher = gsf.searcher(client, self.clock)
        searcher.pacer.interval_s = searcher.step_deadline_s() + 1.0
        with self.assertRaises(lp.HttpStatusError) as raised:
            searcher.search(gsf.TERM)
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(len(client.calls), 1)


class _Search(http_fakes.RecordingServer):
    """The keyed API's search, answering one complete song, or a status alone."""

    def __init__(self, status: int = 200) -> None:
        """Answer the search with *status*: 200 with a song, else that status alone."""
        super().__init__()
        self.status = status

    def reply(self, path: str) -> object:
        """The search reply, whatever *path* was asked: the test reads the path."""
        return {"response": gsf.ONE_SONG} if self.status == 200 else self.status


class RealClientTest(fixtures.IsolatedCase):
    """The real client sends the search to the keyed API, with the key, and nowhere else."""

    def test_the_search_goes_to_the_keyed_api_with_the_bearer_key(self) -> None:
        """One GET to ``api.genius.com/search``: the term, a page of five, the key."""
        server = _Search()
        client = lgs.build_search_client(lr.Secret(gf.FAKE_TOKEN))
        with http_fakes.transport(server):
            reply = client.search_songs(gsf.TERM, per_page=lgs.SEARCH_PER_PAGE)
        (request,) = server.requests
        self.assertEqual(request.method, "GET")
        self.assertTrue(
            (request.url or "").startswith("https://api.genius.com/search?")
        )
        self.assertEqual(server.params(0), {"q": [gsf.TERM], "per_page": ["5"]})
        self.assertEqual(
            request.headers.get("Authorization"), f"Bearer {gf.FAKE_TOKEN}"
        )
        self.assertEqual(len(lgs.parse_search_reply(reply).hits), 1)

    def test_a_challenge_names_the_keyed_api(self) -> None:
        """A 403 from the search raises the status error, its host the keyed API."""
        client = lgs.build_search_client(lr.Secret(gf.FAKE_TOKEN))
        with (
            http_fakes.transport(_Search(403)),
            self.assertRaises(lp.HttpStatusError) as raised,
        ):
            client.search_songs(gsf.TERM, per_page=lgs.SEARCH_PER_PAGE)
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.host, "api.genius.com")


class _Redirecting:
    """A search answered by a 302 to *location*, then by `gsf.ONE_SONG`; records each request."""

    def __init__(self, location: str) -> None:
        """Redirect the first request to *location*; answer the next with a song."""
        self.location = location
        self.sent: list[str] = []

    def __call__(self, request: requests.PreparedRequest) -> requests.Response:
        """The redirect first, then the song, by how many requests came before."""
        self.sent.append(request.url or "")
        if len(self.sent) == 1:
            return http_fakes.response(
                request, 302, headers={"Location": self.location}
            )
        body = http_fakes.json_body({"response": gsf.ONE_SONG})
        return http_fakes.response(request, 200, body)


class SearchRedirectTest(fixtures.IsolatedCase):
    """The keyed search follows a redirect only within the keyed API, never to the site."""

    def test_a_redirect_to_the_site_is_refused_unfollowed(self) -> None:
        """A redirect to genius.com, a Genius host but not the keyed API, is refused."""
        for location in ("https://genius.com/search?q=x", "https://genius.com/"):
            with self.subTest(location=location):
                hops = _Redirecting(location)
                client = lgs.build_search_client(lr.Secret(gf.FAKE_TOKEN))
                with (
                    http_fakes.transport(hops),
                    self.assertRaises(lp.ProviderError) as raised,
                ):
                    client.search_songs(gsf.TERM, per_page=lgs.SEARCH_PER_PAGE)
                self.assertEqual(raised.exception.detail, lgs.REDIRECT_REFUSED)
                self.assertEqual(len(hops.sent), 1)

    def test_a_redirect_within_the_keyed_api_is_followed(self) -> None:
        """A redirect to another api.genius.com path is followed and answered."""
        hops = _Redirecting("/search?q=again")
        client = lgs.build_search_client(lr.Secret(gf.FAKE_TOKEN))
        with http_fakes.transport(hops):
            reply = client.search_songs(gsf.TERM, per_page=lgs.SEARCH_PER_PAGE)
        self.assertEqual(len(hops.sent), 2)
        self.assertTrue(hops.sent[1].startswith("https://api.genius.com/search"))
        self.assertEqual(len(lgs.parse_search_reply(reply).hits), 1)

    def test_a_reply_not_a_redirect_passes_the_hook(self) -> None:
        """A reply that is no redirect is left to the session's other hooks."""
        response = requests.Response()
        response.status_code = 200
        lgs.refuse_off_api_redirect(response)

    def test_a_redirect_naming_no_location_is_refused(self) -> None:
        """A redirect with no ``Location`` leads nowhere the check may go."""
        response = requests.Response()
        response.status_code = 302
        response.url = "https://api.genius.com/search?q=x"
        with self.assertRaises(lp.ProviderError) as raised:
            lgs.refuse_off_api_redirect(response)
        self.assertEqual(raised.exception.detail, lgs.REDIRECT_REFUSED)

    def test_a_refused_redirect_is_a_refused_search(self) -> None:
        """Through the breaker, a redirect to the site is the search's refusal."""
        clock = gf.FakeClock()
        searcher = lgs.GeniusSearcher(
            lgs.build_search_client(lr.Secret(gf.FAKE_TOKEN)),
            clock=clock,
            sleeper=clock.sleep,
            deadline_s=gf.DEADLINE_S,
        )
        path = self.tmp / "genius_breaker.json"
        guard = lbs.BreakerGuard(lbs.BreakerSetup(path, lbk.CLOSED, lambda: gsf.NOW))
        with http_fakes.transport(_Redirecting("https://genius.com/")):
            got = lgs.guarded_search(searcher, guard, gsf.TERM)
        assert isinstance(got, lgs.SearchRefusal)
        self.assertEqual(got.detail, lgs.REDIRECT_REFUSED)
        self.assertTrue(got.sent)


if __name__ == "__main__":  # pragma: no cover - run directly, not under the gate
    unittest.main()
