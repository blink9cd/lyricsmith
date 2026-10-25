"""The real provider table, the deadline watchdog, and the answer cleanup.

No test here reaches the network: the Genius client is replaced at its adapter's seam by
the fakes in `genius_fakes`, and the LRC adapters' HTTP transport answers from memory
(`http_fakes`). The adapters' own suites are `test_lyrics_lrclib`, `test_lyrics_netease`
and `test_lyrics_genius`, here in tests/.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_lrclib as ll  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_netease as ln  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    FAKE_TOKEN,
    QUERY,
    FakeGeniusClient,
    FakeSong,
)

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
#: Short enough that a blocked request is abandoned in milliseconds.
SHORT_DEADLINE_S = 0.05


# --- The watchdog ----------------------------------------------------------------------


class DeadlineTest(fixtures.IsolatedCase):
    """run_with_deadline returns, re-raises, or abandons the call at its deadline."""

    def test_a_prompt_call_returns_its_value(self) -> None:
        self.assertEqual(lf.run_with_deadline(lambda: "answer", 5.0), "answer")

    def test_a_none_answer_comes_back_as_none(self) -> None:
        self.assertIsNone(lf.run_with_deadline(lambda: None, 5.0))

    def test_the_calls_own_error_is_re_raised(self) -> None:
        def fail() -> str:
            raise KeyError("missing")

        with self.assertRaises(KeyError):
            lf.run_with_deadline(fail, 5.0)

    def test_a_hanging_call_is_abandoned_as_a_timeout(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        with self.assertRaises(lp.ProviderTimeout):
            lf.run_with_deadline(lambda: release.wait(fixtures.HANG_LIMIT_S), 0.02)

    def test_the_call_runs_on_a_daemon_thread(self) -> None:
        seen: list[bool] = []
        lf.run_with_deadline(
            lambda: seen.append(threading.current_thread().daemon), 5.0
        )
        self.assertEqual(seen, [True])


# --- Text cleanup ----------------------------------------------------------------------


class GeniusCleanupTest(fixtures.IsolatedCase):
    """The contributors header and the Embed trailer go; section headers stay."""

    def test_header_and_trailer_are_removed(self) -> None:
        raw = "1,204 ContributorsTranslationsThe Keeper Counts Lyrics[Verse 1]\nline one\nline two42Embed"
        self.assertEqual(lf.clean_genius_text(raw), "[Verse 1]\nline one\nline two")

    def test_a_single_contributor_and_a_bare_embed(self) -> None:
        raw = "1 Contributor Song Lyrics\n[Chorus]\nla la\nEmbed\n"
        self.assertEqual(lf.clean_genius_text(raw), "[Chorus]\nla la")

    def test_text_without_either_is_only_trimmed(self) -> None:
        self.assertEqual(
            lf.clean_genius_text("\n" + fixtures.ORCHARD_PLAIN + "\n"),
            fixtures.ORCHARD_PLAIN,
        )

    def test_a_title_holding_the_word_lyrics_is_removed_whole(self) -> None:
        raw = "12 ContributorsLyrics for the Keeper Lyrics[Verse 1]\nline one"
        self.assertEqual(lf.clean_genius_text(raw), "[Verse 1]\nline one")

    def test_the_header_is_read_from_the_first_line_only(self) -> None:
        text = "First line\n3 Contributors on a later line Lyrics"
        self.assertEqual(lf.clean_genius_text(text), text)

    def test_embed_inside_a_word_mid_text_is_kept(self) -> None:
        text = "Embedded in the ice\nthe keeper waits"
        self.assertEqual(lf.clean_genius_text(text), text)


# --- The real table --------------------------------------------------------------------


class ProviderTableTest(fixtures.IsolatedCase):
    """Every LRC step always; Genius only with a key; each optional step when on."""

    def test_without_a_key_the_table_holds_every_lrc_step(self) -> None:
        table = ltable.build_provider_table(None)
        optional = {lm.Source.GENIUS, lm.Source.BANDCAMP, lm.Source.YOUTUBE_MUSIC}
        optional.add(lm.Source.TEKSTOWO)
        self.assertEqual(
            list(table), [key for key in lf.CHAIN if key.source not in optional]
        )
        for key, step in table.items():
            with self.subTest(key=key):
                # Each retries on the real clock, within the chain's default deadline.
                retry = lh.RetryPolicy()
                if key.source is lm.Source.LRCLIB:
                    expected: lm.Provider = ll.LrclibProvider(key.kind, retry=retry)
                else:
                    expected = ln.NeteaseProvider(retry=retry)
                self.assertEqual(step, expected)

    def test_with_a_key_the_genius_step_uses_the_factory_on_the_secret(self) -> None:
        received: list[lr.Secret] = []

        def factory(secret: lr.Secret) -> FakeGeniusClient:
            received.append(secret)
            return FakeGeniusClient([FakeSong("words")])

        secret = lr.Secret(FAKE_TOKEN)
        table = ltable.build_provider_table(secret, genius_factory=factory)
        off = {lm.Source.BANDCAMP, lm.Source.YOUTUBE_MUSIC, lm.Source.TEKSTOWO}
        self.assertEqual(list(table), [k for k in lf.CHAIN if k.source not in off])
        self.assertEqual(received, [secret])
        self.assertEqual(table[lm.ProviderKey(lm.Source.GENIUS, U)](QUERY), "words")


class RealAdapterTimeoutTest(fixtures.IsolatedCase):
    """A real LRC adapter whose request never answers ends as one TIMEOUT attempt."""

    def test_a_blocked_request_is_abandoned_as_the_steps_timeout(self) -> None:
        for source in lf.LRC_PROVIDERS:
            with self.subTest(source=source):
                held = http_fakes.HeldTransport(fixtures.HANG_LIMIT_S)
                # Only a backstop: the abandoned call is finished inside the transport.
                self.addCleanup(held.released.set)
                key = lm.ProviderKey(source, S)
                table = {key: ltable.build_provider_table(None)[key]}
                with http_fakes.transport(held):
                    result = lf.fetch_lyrics(
                        QUERY,
                        lm.SidecarState(None),
                        table,
                        deadline_s=SHORT_DEADLINE_S,
                    )
                    sent = len(held.sent)
                    finished = held.finish()
                self.assertEqual(
                    [(a.key, a.outcome) for a in result.attempts],
                    [(key, lm.AttemptOutcome.TIMEOUT)],
                )
                self.assertIsNone(result.lyrics)
                self.assertEqual(sent, 1)
                self.assertTrue(finished)


class NoArtistTest(fixtures.IsolatedCase):
    """A query with no artist: the LRC adapters send nothing, and no attempt is recorded.

    So the run's tally does not count them as asked for a track they never asked about.
    """

    def test_the_real_lrc_steps_record_no_attempt_and_genius_is_still_asked(
        self,
    ) -> None:
        sent: list[str] = []

        def answer(request: requests.PreparedRequest) -> requests.Response:
            sent.append(request.url or "")
            return http_fakes.response(request, 404)

        genius = fixtures.empty()
        table: dict[lm.ProviderKey, lm.Provider] = {
            **ltable.build_provider_table(None),
            lm.ProviderKey(lm.Source.GENIUS, U): genius,
        }
        query = lm.TrackQuery("", "01 The Keeper Counts", from_filename=True)
        with http_fakes.transport(answer):
            result = lf.fetch_lyrics(
                query, lm.SidecarState(None), table, deadline_s=SHORT_DEADLINE_S
            )
        self.assertEqual(sent, [])
        self.assertEqual([a.key.source for a in result.attempts], [lm.Source.GENIUS])
        self.assertEqual(genius.calls, [query])

    def test_each_lrc_adapter_asks_only_a_query_with_an_artist(self) -> None:
        for provider in (ll.LrclibProvider(S), ln.NeteaseProvider()):
            with self.subTest(provider=provider):
                self.assertFalse(provider.asks(lm.TrackQuery(" ", "Lamp")))
                self.assertTrue(provider.asks(QUERY))
