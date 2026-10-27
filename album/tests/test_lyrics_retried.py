"""Refusals a retry got past: counted per provider and reason, kept and shown.

The retry hook is driven through a real session whose transport answers from memory
(`http_fakes`), its waits on a fake clock. The manifest's ``retried`` map round-trips,
and a manifest written before it reads as none. No test reaches the network.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import threading
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_retried as lrt  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tally as lt  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_http_retry import Script, Scripted  # noqa: E402 - follows the bootstrap

R = lrf.Refusal
LRCLIB, NETEASE, BANDCAMP = lm.Source.LRCLIB, lm.Source.NETEASE, lm.Source.BANDCAMP
URL = "https://example.invalid/api"


class HookTest(fixtures.IsolatedCase):
    """Each retry made is counted by the provider asked and the refusal's class."""

    def get(self, answers: list[Scripted]) -> lrt.RetryTally:
        """A GET answered by *answers*, retried on a fake clock; the tally it filled."""
        tally = lrt.RetryTally()
        clock = genius_fakes.FakeClock()
        policy = lh.RetryPolicy(clock, clock.sleep, on_retry=tally.count)
        with lh.new_session() as session, http_fakes.transport(Script(answers)):
            try:
                lh.get_reply(session, URL, {}, LRCLIB, retry=policy.within(100.0))
            except lh.ProviderError:
                pass  # the final failure is another test's; the tally is this one's
        return tally

    def test_retries_that_ended_in_an_answer_are_counted(self) -> None:
        tally = self.get([503, 429, 503, 200])
        self.assertEqual(tally.counts(), {LRCLIB: {R.OVERLOADED: 2, R.RATE_LIMITED: 1}})

    def test_a_dropped_connection_counts_as_unreachable(self) -> None:
        tally = self.get([requests.ConnectionError("reset"), 200])
        self.assertEqual(tally.counts(), {LRCLIB: {R.UNREACHABLE: 1}})

    def test_retries_that_ended_in_a_refusal_are_counted_too(self) -> None:
        tally = self.get([503, 503, 503, 503])
        self.assertEqual(tally.counts(), {LRCLIB: {R.OVERLOADED: 3}})

    def test_a_failure_not_retried_is_not_counted(self) -> None:
        self.assertEqual(self.get([404]).counts(), {})

    def test_with_no_hook_nothing_is_counted_and_the_retry_still_runs(self) -> None:
        clock = genius_fakes.FakeClock()
        policy = lh.RetryPolicy(clock, clock.sleep)
        script = Script([503, 200])
        with lh.new_session() as session, http_fakes.transport(script):
            lh.get_reply(session, URL, {}, LRCLIB, retry=policy.within(100.0))
        self.assertEqual(script.sent, 2)


class TallyTest(fixtures.IsolatedCase):
    """The tally is safe across threads, and hands out copies."""

    def test_counts_from_many_threads_all_land(self) -> None:
        tally = lrt.RetryTally()

        def count() -> None:
            for _ in range(200):
                tally.count(NETEASE, R.OVERLOADED)

        threads = [threading.Thread(target=count) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(fixtures.HANG_LIMIT_S)
        self.assertEqual(tally.counts(), {NETEASE: {R.OVERLOADED: 800}})

    def test_counts_are_a_copy(self) -> None:
        tally = lrt.RetryTally()
        tally.count(LRCLIB, R.OVERLOADED)
        tally.counts()[LRCLIB][R.OVERLOADED] = 99
        self.assertEqual(tally.counts(), {LRCLIB: {R.OVERLOADED: 1}})

    def test_fresh_tallies_compare_equal(self) -> None:
        self.assertEqual(lrt.RetryTally(), lrt.RetryTally())

    def test_totals_by_provider(self) -> None:
        counts = {LRCLIB: {R.OVERLOADED: 2, R.UNREACHABLE: 5}}
        self.assertEqual(lrt.total(counts, LRCLIB), 7)
        self.assertEqual(lrt.total(counts, NETEASE), 0)
        self.assertEqual(lrt.no_retries(), {})


class TableTest(fixtures.IsolatedCase):
    """The table's retrying clients count into the run's tally."""

    def test_the_lrc_steps_and_musicbrainz_count_into_the_tally(self) -> None:
        tally = lrt.RetryTally()
        settings = ltable.ProviderSettings(bandcamp=True, retried=tally)
        table = ltable.build_provider_table(None, settings=settings)
        for key, provider in table.items():
            with self.subTest(key=key):
                client = getattr(provider, "musicbrainz", provider)
                policy = getattr(client, "retry")
                self.assertEqual(policy.on_retry, tally.count)

    def test_the_genius_step_counts_into_the_tally(self) -> None:
        tally = lrt.RetryTally()
        settings = ltable.ProviderSettings(retried=tally)
        table = ltable.build_provider_table(
            lr.Secret("not-a-real-key"),
            genius_factory=lambda _key: genius_fakes.FakeGeniusClient([]),
            settings=settings,
        )
        genius = table[lm.ProviderKey(lm.Source.GENIUS, lm.LyricsKind.UNSYNCED)]
        self.assertEqual(getattr(genius, "on_retry"), tally.count)

    def test_no_tally_leaves_the_hook_off(self) -> None:
        table = ltable.build_provider_table(None)
        provider = table[lm.ProviderKey(LRCLIB, lm.LyricsKind.SYNCED)]
        self.assertIsNone(getattr(provider, "retry").on_retry)


class ManifestTest(fixtures.IsolatedCase):
    """The manifest keeps the map, and an older one reads as none."""

    def test_the_map_round_trips(self) -> None:
        counts = {LRCLIB: {R.OVERLOADED: 7}, BANDCAMP: {R.UNREACHABLE: 1}}
        manifest = dataclasses.replace(mb.FULL, retried=counts)
        data = lmf.manifest_bytes(manifest)
        self.assertEqual(
            json.loads(data)["retried"],
            {"Lrclib": {"overloaded": 7}, "Bandcamp": {"unreachable": 1}},
        )
        self.assertEqual(lmf.parse_manifest(data), manifest)

    def test_an_older_manifest_reads_as_none_retried(self) -> None:
        doc = mb.document()
        del doc["retried"]
        self.assertEqual(lmf.parse_manifest(mb.encode(doc)).retried, {})

    def test_a_malformed_map_is_refused(self) -> None:
        cases: dict[str, object] = {
            "not an object": [],
            "an unknown provider": {"Nobody": {"overloaded": 1}},
            "a provider not an object": {"Lrclib": 3},
            "an unknown reason": {"Lrclib": {"tired": 1}},
            "a negative count": {"Lrclib": {"overloaded": -1}},
            "a count not a number": {"Lrclib": {"overloaded": "1"}},
            "a boolean count": {"Lrclib": {"overloaded": True}},
            "null": None,
        }
        for name, value in cases.items():
            with self.subTest(name=name), self.assertRaises(lm.ManifestSchemaError):
                doc = mb.document()
                doc["retried"] = value
                lmf.parse_manifest(mb.encode(doc))


class LineTest(fixtures.IsolatedCase):
    """The provider line shows the retries beside the refusals, each only when non-zero."""

    def test_refused_and_retried(self) -> None:
        count = lt.ProviderCount(LRCLIB, 40, 50, refused=3, retried=7)
        self.assertEqual(count.describe(), "LRCLIB 40/50 (3 refused, 7 retried)")

    def test_retried_alone(self) -> None:
        count = lt.ProviderCount(LRCLIB, 40, 50, retried=7)
        self.assertEqual(count.describe(), "LRCLIB 40/50 (7 retried)")

    def test_none_of_either(self) -> None:
        self.assertEqual(lt.ProviderCount(LRCLIB, 40, 50).describe(), "LRCLIB 40/50")

    def test_the_tally_takes_each_providers_total(self) -> None:
        retried = {LRCLIB: {R.OVERLOADED: 2, R.RATE_LIMITED: 1}}
        tally = lt.tally_providers({LRCLIB, NETEASE}, [], retried=retried)
        by_source = {count.source: count.retried for count in tally}
        self.assertEqual((by_source[LRCLIB], by_source[NETEASE]), (3, 0))


class RunTest(cli_fakes.CliCase):
    """A run's tally reaches its manifest and its summary."""

    def setUp(self) -> None:
        """A one-track album."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))

    def build_providers(
        self,
        key: lr.Secret | None,
        deadline_s: float,
        settings: ltable.ProviderSettings = ltable.ProviderSettings(),
    ) -> lm.ProviderTable:
        """The test's table, two retries counted as though its calls had made them."""
        table = super().build_providers(key, deadline_s, settings)
        assert settings.retried is not None
        settings.retried.count(LRCLIB, R.OVERLOADED)
        settings.retried.count(LRCLIB, R.OVERLOADED)
        return table

    def test_the_manifest_and_the_line_carry_the_count(self) -> None:
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.folder.manifests[-1].retried, {LRCLIB: {R.OVERLOADED: 2}})
        self.assertIn("LRCLIB 0/1 (2 retried)", result.stdout)
