"""The fetch for a labeled transcript: providers asked, synced answers win, frames aside.

A timed sidecar whose header labels it a machine transcript is taken as an untimed one
is: the synced steps run, a synced answer replaces it, and otherwise it serves
under its label. Synced frames holding other lyrics serve, as beside a ``.txt``;
frames holding the transcript itself never credit the track to the frames. Every
provider is a fake from fixtures.py, and every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory as li  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import step_key  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
MT, NETEASE = lm.Source.MACHINE_TRANSCRIPT, lm.Source.NETEASE
DEADLINE_S = 5.0
QUERY = lm.TrackQuery("Invented Artist", "The Keeper Counts")
SYNCED_STEPS = sum(key.kind is S for key in lf.CHAIN)


TRANSCRIPT = fixtures.synced_lrc(fixtures.MACHINE_LRC)
OTHER = fixtures.synced_lrc("[00:02.000]A lamp swings on the landing\n")


def _fetch(
    table: dict[lm.ProviderKey, fixtures.FakeProvider],
    *,
    label: lm.Source | None = MT,
    synced: lm.SyncedLyrics | None = None,
) -> lm.FetchResult:
    """The fetch for `QUERY`, the transcript beside the track carrying *label*."""
    return lf.fetch_lyrics(
        QUERY,
        lm.SidecarState(TRANSCRIPT, label=label),
        table,
        deadline_s=DEADLINE_S,
        embedded=li.EmbeddedLyrics(synced, None),
    )


class LabeledFetchTest(fixtures.IsolatedCase):
    """The synced steps are asked; only a synced answer replaces the transcript."""

    def test_with_no_answer_the_transcript_serves_after_the_synced_steps(self) -> None:
        table = fixtures.chain_table()
        result = _fetch(table)
        self.assertEqual((result.lyrics, result.source), (TRANSCRIPT, MT))
        self.assertEqual({a.key.kind for a in result.attempts}, {S})
        self.assertEqual(len(result.attempts), SYNCED_STEPS)

    def test_a_synced_answer_wins(self) -> None:
        table = fixtures.chain_table(
            {step_key(NETEASE, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC)}
        )
        result = _fetch(table)
        self.assertIs(result.source, NETEASE)
        self.assertIsInstance(result.lyrics, lm.SyncedLyrics)

    def test_an_untimed_answer_never_replaces_it_and_is_never_asked(self) -> None:
        plain = {
            k: fixtures.answering(fixtures.ORCHARD_PLAIN)
            for k in lf.CHAIN
            if k.kind is U
        }
        result = _fetch(fixtures.chain_table(dict(plain)))
        self.assertIs(result.source, MT)
        self.assertTrue(all(not fake.calls for fake in plain.values()))

    def test_with_no_provider_it_serves_at_once(self) -> None:
        result = _fetch({})
        self.assertEqual((result.source, result.attempts), (MT, ()))

    def test_frames_holding_the_transcript_still_credit_it(self) -> None:
        result = _fetch(fixtures.chain_table(), synced=TRANSCRIPT)
        self.assertIs(result.source, MT)
        self.assertEqual(len(result.attempts), SYNCED_STEPS)

    def test_other_synced_frames_serve_with_no_provider_asked(self) -> None:
        table = fixtures.chain_table()
        result = _fetch(table, synced=OTHER)
        self.assertEqual((result.lyrics, result.source), (OTHER, lm.Source.FRAMES))
        self.assertTrue(all(not fake.calls for fake in table.values()))

    def test_without_the_label_frames_holding_it_serve_as_today(self) -> None:
        result = _fetch(fixtures.chain_table(), label=None, synced=TRANSCRIPT)
        self.assertIs(result.source, lm.Source.FRAMES)
