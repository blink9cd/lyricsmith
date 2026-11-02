"""Which Genius request a refusal came from: its host, kept and named in the summary.

Genius's lookup asks two hosts: ``api.genius.com``, the one request that carries the
key, and ``genius.com``, the unkeyed site (the searches and the lyrics page). A refusal
keeps the refusing reply's host, a hostname alone and only a Genius one, so the logs
say whether the key or the site was refused. The manifest keeps it, an entry written
before it reads as none, and the summary's ``refusals:`` line names the request. The
transport answers from memory (`http_fakes`), so no request leaves the process; every
value is invented and the key is an obvious fake.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal_tally as lrt  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal_words as lrw  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    DEADLINE_S,
    FAKE_TOKEN,
    QUERY,
    FakeClock,
)
from test_lyrics_run_fresh import FreshCliCase  # noqa: E402 - follows the sys.path bootstrap

R = lrf.Refusal
U = lm.LyricsKind.UNSYNCED
GENIUS, LRCLIB = lm.Source.GENIUS, lm.Source.LRCLIB
API, SITE = "api.genius.com", "genius.com"
#: Long enough for a fake that answers at once.
TIMEOUT_S = 1.0
#: A key-bearing request, as lyricsgenius builds it for song 1.
SONG_URL = "https://api.genius.com/songs/1"
#: An unkeyed page request whose query holds invented track words.
PAGE_URL = "https://genius.com/invented-artist-invented-title-lyrics?q=invented+words"


def _refused_get(url: str, status: int = 403) -> lp.HttpStatusError:
    """The error a Genius session raises when *url* is answered with *status*."""
    with lg.GeniusSession() as session, http_fakes.transport(http_fakes.always(status)):
        try:
            session.get(url, timeout=TIMEOUT_S)
        except lp.HttpStatusError as error:
            return error
    raise AssertionError("the request was not refused")


class RequestLabelsTest(fixtures.IsolatedCase):
    """The labels are kept once, keyed by host; the redirect hosts are the same set."""

    def test_the_genius_hosts_are_the_labeled_ones(self) -> None:
        self.assertEqual(lg.GENIUS_HOSTS, frozenset(lrw.REQUEST_LABELS))

    def test_each_host_names_its_request(self) -> None:
        self.assertEqual(lrw.request_label(API), "keyed API")
        self.assertEqual(lrw.request_label(SITE), "unkeyed site")

    def test_no_host_or_another_names_none(self) -> None:
        for host in (None, "example.invalid", ""):
            with self.subTest(host=host):
                self.assertIsNone(lrw.request_label(host))


class GeniusHostTest(fixtures.IsolatedCase):
    """The Genius session's error names the refusing host, a hostname alone."""

    def test_a_genius_host_is_read_from_a_url(self) -> None:
        self.assertEqual(lg.genius_host(PAGE_URL), SITE)
        self.assertEqual(lg.genius_host("https://API.Genius.com:443/search"), API)

    def test_any_other_host_is_none(self) -> None:
        for url in ("https://example.invalid/x", "not a url", "", "https://genius.co"):
            with self.subTest(url=url):
                self.assertIsNone(lg.genius_host(url))

    def test_a_refused_keyed_request_names_the_api_host(self) -> None:
        error = _refused_get(SONG_URL, 401)
        self.assertEqual((error.status, error.host), (401, API))

    def test_a_refused_page_names_the_site_and_never_the_path(self) -> None:
        error = _refused_get(PAGE_URL)
        self.assertEqual(error.host, SITE)
        self.assertEqual(lrf.classify(error).host, SITE)

    def test_a_reply_from_any_other_host_names_none(self) -> None:
        self.assertIsNone(_refused_get("https://example.invalid/x").host)

    def test_a_status_error_names_no_host_by_default(self) -> None:
        self.assertIsNone(lp.HttpStatusError(LRCLIB, 503).host)

    def test_a_challenge_after_its_retries_keeps_the_last_host(self) -> None:
        clock = FakeClock()
        provider = lg.GeniusProvider(
            lg.build_genius_client(lr.Secret(FAKE_TOKEN)),
            clock=clock,
            sleeper=clock.sleep,
            deadline_s=DEADLINE_S,
        )
        with http_fakes.transport(http_fakes.always(403)):
            with self.assertRaises(lg.GeniusChallengeError) as raised:
                provider(QUERY)
        # The library's search goes to the unkeyed site first.
        self.assertEqual(raised.exception.facts.host, SITE)

    def test_a_challenge_error_names_no_host_unless_given(self) -> None:
        self.assertIsNone(lg.GeniusChallengeError(403, 3).facts.host)
        self.assertEqual(lg.GeniusChallengeError(403, 3, host=API).facts.host, API)


class RecordHostTest(fixtures.IsolatedCase):
    """The refusal record carries the host from its facts, as one capped line."""

    def test_the_record_is_built_with_the_host(self) -> None:
        facts = lrf.RefusalFacts(R.BLOCKED, 403, host=SITE)
        refusal = lr.ProviderRefusal.of(GENIUS, U, "HTTP 403", facts)
        self.assertEqual(refusal.host, SITE)

    def test_the_host_defaults_to_absent_and_a_blank_one_is_none(self) -> None:
        self.assertIsNone(lrf.RefusalFacts(R.BLOCKED).host)
        blank = lr.ProviderRefusal(GENIUS, U, "HTTP 403", R.BLOCKED, host=" ")
        self.assertIsNone(blank.host)

    def test_a_retried_error_keeps_its_last_host(self) -> None:
        last = lp.HttpStatusError(GENIUS, 503, host=SITE)
        self.assertEqual(lrf.classify(lp.RetriedError(last, 2)).host, SITE)


def _first_entry(document: dict[str, object]) -> dict[str, object]:
    """The first refusal entry of *document*'s first track, to edit in place."""
    after = cast(dict[str, object], mb.first_track(document)["after"])
    return cast(list[dict[str, object]], after["refusals"])[0]


def _first_read(document: dict[str, object]) -> lr.ProviderRefusal:
    """The first refusal *document* reads back as."""
    after = lmf.parse_manifest(mb.encode(document)).tracks[0].after
    assert after is not None
    return after.refusals[0]


class ManifestHostTest(fixtures.IsolatedCase):
    """The manifest keeps the host; an entry written before it reads as none."""

    def test_a_host_round_trips(self) -> None:
        hosted = replace(mb.FULL_AFTER.refusals[0], host=SITE)
        after = replace(mb.FULL_AFTER, refusals=(hosted,))
        manifest = replace(mb.FULL, tracks=(replace(mb.FULL.tracks[0], after=after),))
        lmf.write_manifest(self.tmp, manifest)
        read = lmf.read_manifest(self.tmp).tracks[0].after
        assert read is not None
        self.assertEqual(read.refusals, (hosted,))

    def test_an_entry_written_before_the_field_reads_as_no_host(self) -> None:
        document = mb.document()
        entry = _first_entry(document)
        self.assertIn("host", entry)
        del entry["host"]
        self.assertIsNone(_first_read(document).host)

    def test_a_stored_host_is_read_and_a_malformed_one_refused(self) -> None:
        document = mb.document()
        _first_entry(document)["host"] = API
        self.assertEqual(_first_read(document).host, API)
        for value in (5, [API], True):
            with self.subTest(value=value):
                _first_entry(document)["host"] = value
                with self.assertRaisesRegex(lm.ManifestSchemaError, r"\.host"):
                    lmf.parse_manifest(mb.encode(document))


def _track(name: str, *refusals: lr.ProviderRefusal) -> lr.ManifestTrack:
    """A not-found track that met *refusals*."""
    after = lr.TrackOutcome(lr.OutcomeStatus.NOT_FOUND, refusals=refusals)
    return lr.ManifestTrack(name, (2, 4), lr.TrackBefore((), ()), after)


def _genius(host: str | None, reason: R = R.BLOCKED) -> lr.ProviderRefusal:
    """A Genius refusal of *reason* from *host*."""
    return lr.ProviderRefusal(GENIUS, U, "HTTP 403", reason, host=host)


def _line(*tracks: lr.ManifestTrack) -> str | None:
    """The ``refusals:`` line *tracks* print."""
    return lrt.format_refusals(
        lrt.count_refusals(tracks), requests=lrt.count_requests(tracks)
    )


class RequestTallyTest(fixtures.IsolatedCase):
    """The ``refusals:`` line names the request a Genius refusal came from."""

    def test_one_request_is_named_once(self) -> None:
        tracks = [_track(f"{n:02d}.mp3", _genius(SITE)) for n in range(1, 4)]
        self.assertEqual(_line(*tracks), "refusals: Genius blocked 3 (unkeyed site)")

    def test_mixed_requests_are_each_counted(self) -> None:
        tracks = [
            _track("01.mp3", _genius(SITE)),
            _track("02.mp3", _genius(SITE)),
            _track("03.mp3", _genius(API)),
        ]
        self.assertEqual(
            _line(*tracks),
            "refusals: Genius blocked 3 (keyed API 1, unkeyed site 2)",
        )

    def test_a_refusal_with_no_host_is_counted_but_not_named(self) -> None:
        tracks = [_track("01.mp3", _genius(SITE)), _track("02.mp3", _genius(None))]
        self.assertEqual(_line(*tracks), "refusals: Genius blocked 2 (unkeyed site 1)")

    def test_no_host_at_all_reads_as_before(self) -> None:
        tracks = [_track("01.mp3", _genius(None))]
        self.assertEqual(_line(*tracks), "refusals: Genius blocked 1")
        self.assertEqual(lrt.count_requests(tracks), {})

    def test_each_reason_names_its_own_requests(self) -> None:
        unsent = replace(_genius(None), unsent=True)
        tracks = [
            _track("01.mp3", _genius(SITE, R.RATE_LIMITED)),
            _track("02.mp3", _genius(API)),
            _track("03.mp3", unsent),
            _track("04.mp3", lr.ProviderRefusal(LRCLIB, U, "HTTP 503", R.OVERLOADED)),
        ]
        self.assertEqual(
            _line(*tracks),
            "refusals: LRCLIB overloaded 1; Genius rate limited 1 (unkeyed site), "
            "blocked 1 (keyed API), breaker open 1",
        )

    def test_a_track_counts_once_per_request(self) -> None:
        tracks = [_track("01.mp3", _genius(SITE), _genius(SITE))]
        self.assertEqual(
            lrt.count_requests(tracks), {GENIUS: {R.BLOCKED: {"unkeyed site": 1}}}
        )


class SummaryHostTest(FreshCliCase):
    """A run's summary names the Genius request that refused."""

    def test_the_summary_names_the_refused_request(self) -> None:
        fixtures.build_album(self.album, (TrackSpec("01 One.mp3", title="One"),))
        refusing = fixtures.raising(lp.HttpStatusError(GENIUS, 403, host=SITE))
        self.providers = {lm.ProviderKey(GENIUS, U): refusing}
        result = self.run_album()
        self.assertIn(result.code, (album.EXIT_OK, album.EXIT_TRACK_ERRORS))
        self.assertIn("refusals: Genius blocked 1 (unkeyed site)", result.stdout)


class RequestsSentTest(fixtures.IsolatedCase):
    """The hook leaves an answered request alone."""

    def test_an_ok_reply_passes(self) -> None:
        with (
            lg.GeniusSession() as session,
            http_fakes.transport(http_fakes.always(200)),
        ):
            reply = session.get(SONG_URL, timeout=TIMEOUT_S)
        self.assertIsInstance(reply, requests.Response)
        self.assertEqual(reply.status_code, 200)
