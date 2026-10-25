"""The dry run plans a labeled transcript as the fetch takes it.

A sidecar whose header carries the machine-transcript label is still asked of the
synced steps, so online its plan says the providers are asked; offline it serves
itself. Synced frames holding other lyrics outrank it. Every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory as li  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above

TrackSpec = fixtures.TrackSpec
MT = lm.Source.MACHINE_TRANSCRIPT


TRANSCRIPT = fixtures.synced_lrc(fixtures.MACHINE_LRC)
OTHER = fixtures.synced_lrc("[00:02.000]A lamp swings on the landing\n")


class PlanLabelTest(fixtures.IsolatedCase):
    """A labeled transcript says the providers are asked, unless offline."""

    def plan(self, *, offline: bool) -> dict[str, str]:
        """Each track's plan, by path, for the album under this test."""
        stages = run_fakes.make_stages(run_fakes.FakeRunFolder())
        tracks = lyrics_album.discover_tracks(self.tmp).tracks
        planned = lyrics_modes.plan_album(
            self.tmp, tracks, stages, offline=offline, lang=run_fakes.LANG
        )
        return {track.path: track.plan for track in planned}

    def build(self) -> None:
        """A labeled transcript and an unlabeled timed sidecar."""
        fixtures.build_album(
            self.tmp,
            (
                TrackSpec("01 A.mp3", lrc=fixtures.MACHINE_LRC),
                TrackSpec("02 B.mp3", lrc=fixtures.LIGHTHOUSE_LRC),
            ),
        )

    def test_online_the_transcript_plans_a_provider_lookup(self) -> None:
        self.build()
        self.assertEqual(
            self.plan(offline=False),
            {
                "01 A.mp3": "machine transcript (synced), providers asked",
                "02 B.mp3": lyrics_modes.PLAN_SIDECAR_SYNCED,
            },
        )

    def test_offline_the_transcript_plans_itself(self) -> None:
        self.build()
        self.assertEqual(
            self.plan(offline=True)["01 A.mp3"], "machine transcript (synced)"
        )


class PlanForLabelTest(fixtures.IsolatedCase):
    """_plan_for reads synced frames beside a transcript as the fetch does."""

    def _plan(self, synced: lm.SyncedLyrics | None) -> str:
        embedded = li.EmbeddedLyrics(synced, None)
        return lyrics_modes._plan_for(TRANSCRIPT, embedded, offline=False, label=MT)

    def test_other_synced_frames_plan_the_frames(self) -> None:
        self.assertEqual(self._plan(OTHER), lyrics_modes.PLAN_FRAMES_SYNCED)

    def test_frames_holding_the_transcript_plan_the_transcript(self) -> None:
        self.assertEqual(
            self._plan(TRANSCRIPT), lyrics_modes.plan_labeled(MT, offline=False)
        )
