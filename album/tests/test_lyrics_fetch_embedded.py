"""The fetch with a track's embedded lyrics: they serve like a sidecar of the same kind.

The order, for a run that is not fresh: a timed ``.lrc``, then embedded synced lyrics,
then the timed steps, then a ``.txt``, then embedded untimed lyrics, then the untimed
steps and Genius. Every provider is a fake from fixtures.py, so each step runs without
the network and a test can say which providers were asked. Every lyric is invented.
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

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
DEADLINE_S = 5.0
#: A title with a trailing group, so a stripped retry, were one to run, would show.
QUERY = lm.TrackQuery("Invented Artist", "The Keeper Counts (Live)")
#: The synced lyrics the frames hold, and a timed answer unlike them.
FRAME_SYNCED = lm.SyncedLyrics(
    (
        lm.SyncedLine(1000, "A lamp swings on the landing"),
        lm.SyncedLine(4000, "and holds"),
    )
)
FRAME_UNTIMED = lm.PlainLyrics("A lamp swings on the landing\nand holds\n")
TIMED_ANSWER = "[00:01.00]The tide turns in the harbor\n[00:04.00]and waits\n"
PLAIN_ANSWER = "The tide turns in the harbor\nand waits\n"
SIDECAR_SYNCED = lm.parse_lrc("[00:02.00]A gull on the railing\n")
SIDECAR_PLAIN = lm.PlainLyrics("A gull on the railing\n")


def _answering_all() -> dict[lm.ProviderKey, fixtures.FakeProvider]:
    """Every chain step answering: the timed steps timed, the rest untimed."""
    return {
        key: fixtures.answering(TIMED_ANSWER if key.kind is S else PLAIN_ANSWER)
        for key in lf.CHAIN
    }


def _untimed_only() -> dict[lm.ProviderKey, fixtures.FakeProvider]:
    """The timed steps finding nothing; the untimed steps and Genius answering."""
    return {
        key: fixtures.empty() if key.kind is S else fixtures.answering(PLAIN_ANSWER)
        for key in lf.CHAIN
    }


def _asked(table: dict[lm.ProviderKey, fixtures.FakeProvider]) -> list[lm.ProviderKey]:
    """Each step whose provider was called, in chain order."""
    return [key for key in lf.CHAIN if table[key].calls]


def _fetch(
    table: dict[lm.ProviderKey, fixtures.FakeProvider],
    *,
    sidecar: lm.Lyrics | None = None,
    synced: lm.SyncedLyrics | None = None,
    untimed: lm.PlainLyrics | None = None,
) -> lm.FetchResult:
    """The fetch for `QUERY` with *sidecar* beside the track and the frames given."""
    return lf.fetch_lyrics(
        QUERY,
        lm.SidecarState(sidecar),
        table,
        deadline_s=DEADLINE_S,
        embedded=li.EmbeddedLyrics(synced, untimed),
    )


class FramesSourceTest(fixtures.IsolatedCase):
    """The source a frames-served track records."""

    def test_the_value_and_the_label_are_frames(self) -> None:
        self.assertEqual(
            (lm.Source.FRAMES.value, str(lm.Source.FRAMES)), ("frames", "frames")
        )

    def test_frames_are_no_provider(self) -> None:
        self.assertNotIn(lm.Source.FRAMES, {key.source for key in lf.CHAIN})


class EmbeddedSyncedTest(fixtures.IsolatedCase):
    """Embedded synced lyrics serve the track with no provider asked, but a timed .lrc wins."""

    def test_embedded_synced_lyrics_are_served_with_no_provider_asked(self) -> None:
        table = _answering_all()
        result = _fetch(table, synced=FRAME_SYNCED, untimed=FRAME_UNTIMED)
        self.assertEqual(
            (result.lyrics, result.source, result.attempts),
            (FRAME_SYNCED, lm.Source.FRAMES, ()),
        )
        self.assertEqual(_asked(table), [])

    def test_a_timed_lrc_beats_embedded_synced_lyrics(self) -> None:
        table = _answering_all()
        result = _fetch(table, sidecar=SIDECAR_SYNCED, synced=FRAME_SYNCED)
        self.assertEqual(
            (result.lyrics, result.source), (SIDECAR_SYNCED, lm.Source.SIDECAR)
        )
        self.assertEqual(_asked(table), [])

    def test_embedded_synced_lyrics_beat_a_txt_sidecar(self) -> None:
        table = _answering_all()
        result = _fetch(table, sidecar=SIDECAR_PLAIN, synced=FRAME_SYNCED)
        self.assertEqual(
            (result.lyrics, result.source), (FRAME_SYNCED, lm.Source.FRAMES)
        )
        self.assertEqual(_asked(table), [])


class EmbeddedUntimedTest(fixtures.IsolatedCase):
    """Embedded untimed lyrics give way to a timed answer and to a .txt, and beat the rest."""

    def test_a_timed_answer_beats_embedded_untimed_lyrics(self) -> None:
        table = _answering_all()
        result = _fetch(table, untimed=FRAME_UNTIMED)
        self.assertEqual(result.source, lm.Source.LRCLIB)
        self.assertEqual(result.lyrics, lm.parse_lrc(TIMED_ANSWER))
        self.assertEqual(_asked(table), [lf.CHAIN[0]])

    def test_embedded_untimed_lyrics_beat_the_untimed_steps_and_genius(self) -> None:
        table = _untimed_only()
        result = _fetch(table, untimed=FRAME_UNTIMED)
        self.assertEqual(
            (result.lyrics, result.source), (FRAME_UNTIMED, lm.Source.FRAMES)
        )
        self.assertEqual(_asked(table), [key for key in lf.CHAIN if key.kind is S])
        self.assertEqual(
            [a.outcome for a in result.attempts], [lm.AttemptOutcome.EMPTY] * 3
        )

    def test_no_stripped_retry_runs_over_embedded_untimed_lyrics(self) -> None:
        table = _untimed_only()
        _fetch(table, untimed=FRAME_UNTIMED)
        self.assertEqual(
            [len(table[key].calls) for key in lf.CHAIN if key.kind is S], [1, 1, 1]
        )

    def test_a_txt_sidecar_beats_embedded_untimed_lyrics(self) -> None:
        table = _untimed_only()
        result = _fetch(table, sidecar=SIDECAR_PLAIN, untimed=FRAME_UNTIMED)
        self.assertEqual(
            (result.lyrics, result.source), (SIDECAR_PLAIN, lm.Source.SIDECAR)
        )
        self.assertEqual(_asked(table), [key for key in lf.CHAIN if key.kind is S])

    def test_with_no_lyrics_anywhere_the_untimed_steps_answer(self) -> None:
        table = _untimed_only()
        result = _fetch(table)
        self.assertEqual(result.source, lm.Source.LRCLIB)
        self.assertEqual(result.lyrics, lm.PlainLyrics(PLAIN_ANSWER.strip()))

    def test_offline_embedded_untimed_lyrics_serve(self) -> None:
        result = _fetch({}, untimed=FRAME_UNTIMED)
        self.assertEqual(
            (result.lyrics, result.source, result.attempts),
            (FRAME_UNTIMED, lm.Source.FRAMES, ()),
        )
