"""The owed rule's cool-down half: the providers a run's cool-down left a debt with.

A provider a run left out by its cool-down (the manifest's run-level ``cooling down``)
is owed by a reached track only when it could still answer it: one with a synced step
in the chain (YouTube Music) by a track that does not hold synced lyrics, one with only
untimed steps (Tekstowo) by a track that holds no lyrics at all: one that ended not
found or removed, or kept what it held, or ended in an error, when that was wordless
frames or not read. The tally owes these beside its refusals, so a run a cool-down
missed ends owed, not done and not refused. Every manifest here is invented and built
in memory.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import lyrics_cooldown  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally as st  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_provider_states import CoolingState  # noqa: E402 - follows the bootstrap
from sweep_outcome import outcome_of  # noqa: E402 - follows the sys.path bootstrap

_COOLING = CoolingState.COOLING_DOWN
_TEKSTOWO, _YOUTUBE = lm.Source.TEKSTOWO, lm.Source.YOUTUBE_MUSIC


def _untimed() -> lr.TrackOutcome:
    """A track written with untimed lyrics: it holds lyrics, but not synced ones."""
    return sf.status_outcome(
        lr.OutcomeStatus.WRITTEN, source=lm.Source.GENIUS, kind=lm.LyricsKind.UNSYNCED
    )


def _kept(held: lr.HeldBefore | None) -> lr.TrackOutcome:
    """A track that kept what it held before the run, *held* (None: not read)."""
    return lr.TrackOutcome(lr.OutcomeStatus.KEPT_EXISTING, held=held)


def _kept_untimed() -> lr.TrackOutcome:
    """A track that kept the untimed lyrics it held before the run."""
    return _kept(lr.HeldBefore.UNTIMED)


def _manifest(
    *outcomes: lr.TrackOutcome | None,
    tekstowo: CoolingState = CoolingState.ON,
    youtube_music: CoolingState = CoolingState.ON,
) -> lr.Manifest:
    """A complete run's manifest of *outcomes*, its two cooling states given."""
    return replace(
        sf.manifest_of(*outcomes), tekstowo=tekstowo, youtube_music=youtube_music
    )


class CoolingOwedTest(sf.IsolatedCase):
    """Which cooling providers a manifest owes, by what each could still answer."""

    def test_the_cooling_providers_are_those_with_a_cool_down(self) -> None:
        self.assertEqual(set(st.COOLING), set(lyrics_cooldown.STATE_FILES))

    def test_tekstowo_is_not_owed_by_an_untimed_only_track(self) -> None:
        kept_synced = _kept(lr.HeldBefore.SYNCED)
        for outcome in (_untimed(), _kept_untimed(), kept_synced):
            with self.subTest(outcome=outcome.status):
                manifest = _manifest(outcome, tekstowo=_COOLING)
                self.assertEqual(st.cooling_owed(manifest), frozenset())

    def test_tekstowo_is_owed_by_a_track_ending_not_found_or_removed(self) -> None:
        for status in (lr.OutcomeStatus.NOT_FOUND, lr.OutcomeStatus.REMOVED):
            with self.subTest(status=status):
                outcome = sf.status_outcome(status)
                manifest = _manifest(sf.written(), outcome, tekstowo=_COOLING)
                self.assertEqual(st.cooling_owed(manifest), {_TEKSTOWO})

    def test_tekstowo_is_owed_by_a_kept_track_holding_no_lyrics(self) -> None:
        # Wordless frames are no real lyrics, and a held kind not read is taken as
        # none: a debt forgotten costs the lyrics, one paid needlessly a request.
        for held in (lr.HeldBefore.NOTHING, None):
            with self.subTest(held=held):
                manifest = _manifest(_kept(held), tekstowo=_COOLING)
                self.assertEqual(st.cooling_owed(manifest), {_TEKSTOWO})

    def test_tekstowo_is_not_owed_by_any_other_end_holding_no_synced(self) -> None:
        # An error is not one of these: it left the track as it was, so it is judged
        # by what it held, as the error cases of test_sweep_owed_untimed show.
        others = (
            lr.OutcomeStatus.NO_LYRICS_EXPECTED,
            lr.OutcomeStatus.SILENT,
        )
        for status in others:
            with self.subTest(status=status):
                manifest = _manifest(sf.status_outcome(status), tekstowo=_COOLING)
                self.assertEqual(st.cooling_owed(manifest), frozenset())

    def test_youtube_music_is_owed_by_an_untimed_only_track(self) -> None:
        for outcome in (_untimed(), _kept_untimed(), sf.not_found()):
            with self.subTest(outcome=outcome.status):
                manifest = _manifest(outcome, youtube_music=_COOLING)
                self.assertEqual(st.cooling_owed(manifest), {_YOUTUBE})

    def test_every_track_synced_or_unreached_owes_nothing(self) -> None:
        manifest = _manifest(
            sf.written(), None, tekstowo=_COOLING, youtube_music=_COOLING
        )
        self.assertEqual(st.cooling_owed(manifest), frozenset())

    def test_a_provider_on_or_off_owes_nothing(self) -> None:
        # A stopped one owes by the stop half, whose cases are their own suite's.
        for state in (CoolingState.ON, CoolingState.OFF):
            with self.subTest(state=state):
                manifest = _manifest(
                    sf.not_found(), tekstowo=state, youtube_music=state
                )
                self.assertEqual(st.cooling_owed(manifest), frozenset())


class TallyOwesTheCoolDownTest(sf.IsolatedCase):
    """The tally's owed rule owes a cool-down's debt beside its refusals, every run."""

    def test_a_run_tekstowo_missed_owes_it_and_is_not_refused(self) -> None:
        tally = st.tally_manifest(_manifest(sf.not_found(), tekstowo=_COOLING))
        self.assertEqual(tally.owed, (_TEKSTOWO,))
        self.assertFalse(tally.refused)

    def test_refusals_and_the_cool_down_join_in_chain_order(self) -> None:
        refused = sf.not_found(sf.refusal(lm.Source.GENIUS))
        manifest = _manifest(refused, tekstowo=_COOLING, youtube_music=_COOLING)
        owed = st.tally_manifest(manifest).owed
        self.assertEqual(owed, (_YOUTUBE, lm.Source.GENIUS, _TEKSTOWO))

    def test_a_cool_down_debt_alone_ends_the_visit_owed(self) -> None:
        tally = st.tally_manifest(_manifest(sf.not_found(), tekstowo=_COOLING))
        result = sweep_child.ChildResult(sweep.album.EXIT_OK)
        self.assertIs(outcome_of(result, tally, offline=False), sl.Outcome.OWED)

    def test_an_untimed_only_album_tekstowo_missed_is_done(self) -> None:
        tally = st.tally_manifest(_manifest(_untimed(), tekstowo=_COOLING))
        result = sweep_child.ChildResult(sweep.album.EXIT_OK)
        self.assertIs(outcome_of(result, tally, offline=False), sl.Outcome.DONE)
