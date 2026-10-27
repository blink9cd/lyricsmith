"""The owed rule for an untimed-only provider: owed only where it could still answer.

A provider with only untimed steps (Tekstowo, Genius, Bandcamp) cannot improve a track
already holding untimed lyrics in an ordinary run, which looks such a track up with the
synced steps only; so its refusal there owes nothing, by the one test the cool-down and
stop halves use (`sweep_tally.could_still_answer`). A track the run asked again
(``refetch_untimed`` or ``refetch_implausible``) was asked as though it held nothing, and
any answer would have replaced its lyrics, so it owes such a provider while it holds no
synced lyrics. A provider with a synced step is owed while the track holds no synced
lyrics. A track that ended in an error was left as it was, so it is judged by what it
held, whatever the stage. Each track's refusals respect the chain's order
(`lyrics_fetch.CHAIN`): a provider answering a track was asked after every provider
that refused it. Every manifest here is invented and built in memory.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally as st  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_provider_states import CoolingState  # noqa: E402 - follows the bootstrap

_TEKSTOWO, _GENIUS = lm.Source.TEKSTOWO, lm.Source.GENIUS
_LRCLIB, _YOUTUBE = lm.Source.LRCLIB, lm.Source.YOUTUBE_MUSIC
_BANDCAMP = lm.Source.BANDCAMP
_UNTIMED_ONLY = (_TEKSTOWO, _GENIUS)
#: An untimed-only provider asked after each one, which may answer a track it refused.
_ANSWERED_AFTER = {_BANDCAMP: _GENIUS, _GENIUS: _TEKSTOWO}
_REFETCH_MARKS = ("refetch_untimed", "refetch_implausible")


def _refused(provider: lm.Source) -> lr.ProviderRefusal:
    """One overloaded refusal by *provider*, at its untimed step."""
    return lr.ProviderRefusal(
        provider, lm.LyricsKind.UNSYNCED, "HTTP 503", lrf.Refusal.OVERLOADED
    )


def _written_untimed(
    status: lr.OutcomeStatus, source: lm.Source, *refusals: lr.ProviderRefusal
) -> lr.TrackOutcome:
    """A track *source* answered with untimed lyrics, ending *status*, after *refusals*."""
    return sf.status_outcome(
        status, source=source, kind=lm.LyricsKind.UNSYNCED, refusals=refusals
    )


def _kept_untimed(*refusals: lr.ProviderRefusal) -> lr.TrackOutcome:
    """A track that kept the untimed lyrics it held, after *refusals*.

    Unmarked, this is a ``--fresh`` run's track, which asked every step; marked, a
    refetch's (`_marked`).
    """
    return lr.TrackOutcome(
        lr.OutcomeStatus.KEPT_EXISTING, refusals=refusals, held=lr.HeldBefore.UNTIMED
    )


def _untimed_tracks(provider: lm.Source) -> tuple[lr.TrackOutcome, ...]:
    """Tracks holding untimed lyrics after *provider* refused them, as a run can end.

    A track written or unchanged was answered by a provider asked after *provider*,
    when the chain has one; a kept track kept its own lyrics.
    """
    refusal = _refused(provider)
    later = _ANSWERED_AFTER.get(provider)
    answered = (
        ()
        if later is None
        else tuple(
            _written_untimed(status, later, refusal)
            for status in (lr.OutcomeStatus.WRITTEN, lr.OutcomeStatus.UNCHANGED)
        )
    )
    return (*answered, _kept_untimed(refusal))


def _owed(*outcomes: lr.TrackOutcome) -> tuple[lm.Source, ...]:
    """What an album whose tracks ended as *outcomes* owes."""
    return st.tally_manifest(sf.manifest_of(*outcomes)).owed


def _marked(outcome: lr.TrackOutcome, mark: str) -> lr.TrackOutcome:
    """*outcome*, marked asked again by the refetch option *mark* names."""
    if mark == "refetch_untimed":
        return replace(outcome, refetch_untimed=True)
    return replace(outcome, refetch_implausible=True)


def _errored(
    held: lr.HeldBefore | None, *refusals: lr.ProviderRefusal
) -> lr.TrackOutcome:
    """A track whose embed failed after *refusals*: left as it was, holding *held*."""
    error = lr.StageError(lr.ErrorStage.EMBED, "OSError", "the MP3 cannot be written")
    return lr.TrackOutcome(
        lr.OutcomeStatus.ERROR, error=error, refusals=refusals, held=held
    )


def _cooling(*outcomes: lr.TrackOutcome) -> lr.Manifest:
    """A run's manifest of *outcomes* that left Tekstowo out by its cool-down."""
    return replace(sf.manifest_of(*outcomes), tekstowo=CoolingState.COOLING_DOWN)


class RefusalHalfTest(sf.IsolatedCase):
    """An untimed-only provider's refusal owes only what it could still answer."""

    def test_an_untimed_holding_track_owes_an_untimed_only_provider_nothing(
        self,
    ) -> None:
        for provider in _UNTIMED_ONLY:
            for track in _untimed_tracks(provider):
                with self.subTest(provider=provider, status=track.status):
                    self.assertEqual(_owed(track), ())

    def test_a_refetched_untimed_track_owes_it(self) -> None:
        for mark in _REFETCH_MARKS:
            for provider in _UNTIMED_ONLY:
                for track in _untimed_tracks(provider):
                    with self.subTest(mark=mark, provider=provider, s=track.status):
                        self.assertEqual(_owed(_marked(track, mark)), (provider,))

    def test_a_track_holding_none_still_owes_it(self) -> None:
        for provider in _UNTIMED_ONLY:
            with self.subTest(provider=provider):
                self.assertEqual(_owed(sf.not_found(_refused(provider))), (provider,))

    def test_a_fetch_error_still_owes_it(self) -> None:
        # Every provider failed, so the track kept what it held: not read here, so
        # taken as none, since a debt forgotten costs the lyrics.
        self.assertEqual(_owed(sf.fetch_error(_refused(_GENIUS))), (_GENIUS,))

    def test_a_fetch_error_kept_untimed_lyrics_owes_it_nothing(self) -> None:
        track = replace(sf.fetch_error(_refused(_GENIUS)), held=lr.HeldBefore.UNTIMED)
        self.assertEqual(_owed(track), ())

    def test_an_embed_error_holding_nothing_owes_it(self) -> None:
        # Bandcamp refused, Genius answered, and the embed failed: the track holds
        # what it held before, which was nothing.
        track = _errored(lr.HeldBefore.NOTHING, _refused(_BANDCAMP))
        self.assertEqual(_owed(track), (_BANDCAMP,))

    def test_an_embed_error_holding_untimed_lyrics_owes_it_nothing(self) -> None:
        track = _errored(lr.HeldBefore.UNTIMED, _refused(_BANDCAMP))
        self.assertEqual(_owed(track), ())

    def test_a_synced_step_provider_is_owed_by_an_untimed_holding_track(self) -> None:
        for provider in (_LRCLIB, _YOUTUBE):
            refusal = sf.refusal(provider)
            tracks = (
                _written_untimed(lr.OutcomeStatus.WRITTEN, _BANDCAMP, refusal),
                _written_untimed(lr.OutcomeStatus.UNCHANGED, _BANDCAMP, refusal),
                _kept_untimed(refusal),
            )
            for track in tracks:
                with self.subTest(provider=provider, status=track.status):
                    self.assertEqual(_owed(track), (provider,))


class CoolingAndStopHalvesTest(sf.IsolatedCase):
    """The cool-down and stop halves owe an untimed-only provider for a refetched track."""

    def test_a_cool_down_owes_tekstowo_for_a_refetched_kept_track(self) -> None:
        for mark in _REFETCH_MARKS:
            with self.subTest(mark=mark):
                manifest = _cooling(_marked(_kept_untimed(), mark))
                self.assertEqual(st.cooling_owed(manifest), {_TEKSTOWO})

    def test_a_cool_down_owes_nothing_for_a_refetched_track_now_synced(self) -> None:
        track = _marked(sf.written(), "refetch_untimed")
        self.assertEqual(st.cooling_owed(_cooling(track)), frozenset())

    def test_a_cool_down_owes_tekstowo_for_an_error_holding_none(self) -> None:
        for held in (lr.HeldBefore.NOTHING, None):
            with self.subTest(held=held):
                manifest = _cooling(_errored(held))
                self.assertEqual(st.cooling_owed(manifest), {_TEKSTOWO})

    def test_a_stop_owes_tekstowo_for_a_later_refetched_kept_track(self) -> None:
        # The stopping track's genre expects no lyrics: nothing was found for it, so
        # it owes Tekstowo nothing itself, and only the later track's debt is left.
        stopping = sf.status_outcome(
            lr.OutcomeStatus.NO_LYRICS_EXPECTED, refusals=(_refused(_TEKSTOWO),)
        )
        later = _marked(_kept_untimed(), "refetch_untimed")
        stopped = CoolingState.STOPPED
        alone = replace(sf.manifest_of(stopping), tekstowo=stopped)
        both = replace(sf.manifest_of(stopping, later), tekstowo=stopped)
        self.assertEqual(st.cooling_owed(alone), frozenset())
        self.assertEqual(st.cooling_owed(both), {_TEKSTOWO})
