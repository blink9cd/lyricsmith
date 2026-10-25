"""A run records each provider's refusal on the track it met it on, whatever the track's end.

A refusal is a provider call that raised or timed out instead of answering. The track's
outcome keeps every one in its manifest entry, so a later reader can tell "the providers
refused" from "nothing was found": a refusal beside an empty answer still ends `not
found`, and a track every provider failed for is still `ERROR fetch`. A track served from
what it holds with no provider asked has none. Each test drives the entry point with its
real wiring; only the clock and the provider table are faked, and every lyric is
invented.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh import FreshCliCase  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB, NETEASE = lm.Source.LRCLIB, lm.Source.NETEASE
_SYNCED = lm.ProviderKey(LRCLIB, S)
_PLAIN = lm.ProviderKey(LRCLIB, U)
_NETEASE = lm.ProviderKey(NETEASE, S)
#: LRCLIB's synced step refusing with a 503, as every test's first step does.
_REFUSED = lr.ProviderRefusal(LRCLIB, S, "HTTP 503")
_UNTIMED_TEXT = "A kettle sings on the hob\nand the window fogs\n"
TRACK = TrackSpec("01 Keeper.mp3", title="The Keeper")


def _refusing() -> fixtures.FakeProvider:
    """A provider refusing every call with a 503."""
    return fixtures.raising(lp.HttpStatusError(LRCLIB, 503))


class RefusalRunCase(FreshCliCase):
    """LRCLIB's synced step refuses; its plain step finds nothing, unless a test says."""

    def setUp(self) -> None:
        """The refusing synced step and an empty plain step."""
        super().setUp()
        self.providers = {_SYNCED: _refusing(), _PLAIN: run_fakes.TitleProvider({})}

    def keeper(self) -> lr.TrackOutcome:
        """Run the album; its one track's outcome, read back from the manifest on disk."""
        result = self.run_album()
        self.assertIn(
            result.code,
            (album.EXIT_OK, album.EXIT_TRACK_ERRORS),
            result.stderr,
        )
        (after,) = self.afters()
        assert after is not None
        return after


class RefusalOutcomeTest(RefusalRunCase):
    """Every way a track can end after a fetch carries the refusals the fetch met."""

    def test_a_refusal_beside_an_empty_answer_stays_not_found(self) -> None:
        fixtures.build_album(self.album, (TRACK,))
        after = self.keeper()
        self.assertEqual(after.status, lr.OutcomeStatus.NOT_FOUND)
        self.assertIsNone(after.error)
        self.assertEqual(after.refusals, (_REFUSED,))

    def test_every_provider_failing_is_still_the_fetch_error_with_its_refusals(
        self,
    ) -> None:
        timeout = lp.ProviderTimeout("no answer within 5.0s")
        self.providers = {_SYNCED: _refusing(), _PLAIN: fixtures.raising(timeout)}
        fixtures.build_album(self.album, (TRACK,))
        after = self.keeper()
        self.assertEqual(after.status, lr.OutcomeStatus.ERROR)
        assert after.error is not None
        self.assertEqual(after.error.stage, lr.ErrorStage.FETCH)
        self.assertEqual(
            after.refusals, (_REFUSED, lr.ProviderRefusal(LRCLIB, U, "timeout"))
        )

    def test_a_written_track_keeps_the_refusal_met_before_its_answer(self) -> None:
        self.providers = {
            _SYNCED: _refusing(),
            _NETEASE: run_fakes.TitleProvider({"The Keeper": fixtures.LIGHTHOUSE_LRC}),
        }
        fixtures.build_album(self.album, (TRACK,))
        after = self.keeper()
        self.assertEqual(
            (after.status, after.source), (lr.OutcomeStatus.WRITTEN, NETEASE)
        )
        self.assertEqual(after.refusals, (_REFUSED,))

    def test_an_error_after_the_fetch_keeps_the_refusal_met_before_it(self) -> None:
        def write_lyrics(path: Path, lyrics: lm.Lyrics, lang: str) -> bool:
            raise OSError("disk full")

        self.overrides["write_lyrics"] = write_lyrics
        self.providers = {
            _SYNCED: _refusing(),
            _NETEASE: run_fakes.TitleProvider({"The Keeper": fixtures.LIGHTHOUSE_LRC}),
        }
        fixtures.build_album(self.album, (TRACK,))
        after = self.keeper()
        self.assertEqual(after.status, lr.OutcomeStatus.ERROR)
        assert after.error is not None
        self.assertEqual(after.error.stage, lr.ErrorStage.EMBED)
        self.assertEqual(after.refusals, (_REFUSED,))

    def test_a_fresh_run_that_keeps_existing_lyrics_keeps_its_refusals(self) -> None:
        self.build(TRACK)
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (after,) = self.afters()
        assert after is not None
        self.assertEqual(after.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertEqual(after.refusals, (_REFUSED,))

    def test_an_unchanged_track_whose_timed_lookup_was_refused_keeps_it(self) -> None:
        # Untimed frames and their own .txt: the synced steps are still asked, since
        # timed lines would win, and the frames then serve the track as they are.
        track = TrackSpec(TRACK.relpath, title="The Keeper", txt=_UNTIMED_TEXT)
        (path,) = fixtures.build_album(self.album, (track,))
        tag_builders.plant_frames(path, (tag_builders.uslt(_UNTIMED_TEXT),))
        after = self.keeper()
        self.assertEqual(
            (after.status, after.source),
            (lr.OutcomeStatus.UNCHANGED, lm.Source.FRAMES),
        )
        self.assertEqual(after.refusals, (_REFUSED,))

    def test_the_manifest_on_disk_holds_each_refusal_by_value(self) -> None:
        fixtures.build_album(self.album, (TRACK,))
        self.keeper()
        manifest = lr.run_folder(self.album, run_fakes.RUN_ID) / lr.MANIFEST_NAME
        stored = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(
            stored["tracks"][0]["after"]["refusals"],
            [{"provider": LRCLIB.value, "step": S.value, "detail": "HTTP 503"}],
        )

    def test_the_summary_tallies_the_refusal(self) -> None:
        fixtures.build_album(self.album, (TRACK,))
        result = self.run_album()
        self.assertEqual(
            result.stdout.splitlines()[-2],
            "providers: LRCLIB 0/1 (1 refused) · NetEase - · YouTube Music - · Bandcamp - · Genius - · Tekstowo -",
        )


class ServedWithoutAskingTest(RefusalRunCase):
    """A track its sidecar or its frames serve, with no provider asked, has no refusal."""

    def test_a_sidecar_served_track_has_no_refusal(self) -> None:
        track = TrackSpec(
            TRACK.relpath, title="The Keeper", lrc=fixtures.LIGHTHOUSE_LRC
        )
        fixtures.build_album(self.album, (track,))
        after = self.keeper()
        self.assertEqual(after.source, lm.Source.SIDECAR)
        self.assertEqual(after.refusals, ())

    def test_a_frames_served_track_has_no_refusal(self) -> None:
        (path,) = fixtures.build_album(self.album, (TRACK,))
        sylt = tag_builders.sylt(
            (1000, "A kettle sings"), (4000, "and the window fogs")
        )
        tag_builders.plant_frames(path, (sylt,))
        after = self.keeper()
        self.assertEqual(after.source, lm.Source.FRAMES)
        self.assertEqual(after.refusals, ())
