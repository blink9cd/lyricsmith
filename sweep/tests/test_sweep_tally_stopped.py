"""The owed rule's stop half: a provider stopped part-way owes the tracks it left unasked.

A provider with a cool-down that a refusal stops mid-run (the manifest's run-level
``stopped``) records that refusal on the stopping track, and every later track skips
it unrecorded. So the stopping track, the last reached one recording a refusal by it,
and every reached track after it owe the provider when it could still answer them,
whatever the stop's reason: a not-JSON stop recorded ``unrecognized`` included. A
track before the stop owes only by its own refusals; a stop no reached track records
leaves every reached track owing it that it could still answer. Every manifest here is
invented and built in memory, or written under the test's own temporary library.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_earlier as se  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally as st  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_provider_states import CoolingState  # noqa: E402 - follows the bootstrap

_STOPPED = CoolingState.STOPPED
_TEKSTOWO, _YOUTUBE = lm.Source.TEKSTOWO, lm.Source.YOUTUBE_MUSIC
_RUN = "20261018T120000Z"


def _youtube_stop(
    detail: str = "HTTP 429", reason: lrf.Refusal = lrf.Refusal.RATE_LIMITED
) -> lr.ProviderRefusal:
    """YouTube Music's stopping refusal, at its synced step."""
    return sf.refusal(_YOUTUBE, detail, reason)


def _tekstowo_stop() -> lr.ProviderRefusal:
    """Tekstowo's stopping refusal, at its untimed step."""
    return lr.ProviderRefusal(
        _TEKSTOWO, lm.LyricsKind.UNSYNCED, "HTTP 403", lrf.Refusal.BLOCKED
    )


def _synced(*refusals: lr.ProviderRefusal) -> lr.TrackOutcome:
    """A track LRCLIB answered with synced lyrics, beside *refusals*."""
    return sf.status_outcome(
        lr.OutcomeStatus.WRITTEN,
        source=lm.Source.LRCLIB,
        kind=lm.LyricsKind.SYNCED,
        refusals=refusals,
    )


def _untimed() -> lr.TrackOutcome:
    """A track written with untimed lyrics: it holds lyrics, but not synced ones."""
    return sf.status_outcome(
        lr.OutcomeStatus.WRITTEN, source=lm.Source.GENIUS, kind=lm.LyricsKind.UNSYNCED
    )


def _manifest(
    *outcomes: lr.TrackOutcome | None,
    tekstowo: CoolingState = CoolingState.ON,
    youtube_music: CoolingState = CoolingState.ON,
) -> lr.Manifest:
    """A run's manifest of *outcomes*, in run order, its two cooling states given."""
    return replace(
        sf.manifest_of(*outcomes), tekstowo=tekstowo, youtube_music=youtube_music
    )


class StoppedOwedTest(sf.IsolatedCase):
    """Which stopped providers a manifest owes, by the tracks the stop left unasked."""

    def test_a_stop_on_a_synced_track_owes_the_later_unanswered_ones(self) -> None:
        manifest = _manifest(
            _synced(_youtube_stop()), sf.not_found(), youtube_music=_STOPPED
        )
        self.assertEqual(st.cooling_owed(manifest), {_YOUTUBE})
        self.assertEqual(st.tally_manifest(manifest).owed, (_YOUTUBE,))

    def test_a_not_json_stop_owes_whatever_its_reason(self) -> None:
        stop = _youtube_stop("reply not JSON", lrf.Refusal.UNRECOGNIZED)
        manifest = _manifest(
            sf.written(), sf.not_found(stop), sf.not_found(), youtube_music=_STOPPED
        )
        self.assertEqual(st.tally_manifest(manifest).owed, (_YOUTUBE,))

    def test_a_not_json_stop_owes_its_own_track(self) -> None:
        stop = _youtube_stop("reply not JSON", lrf.Refusal.UNRECOGNIZED)
        manifest = _manifest(sf.not_found(stop), _synced(), youtube_music=_STOPPED)
        self.assertEqual(st.cooling_owed(manifest), {_YOUTUBE})

    def test_every_later_track_synced_owes_youtube_music_nothing(self) -> None:
        manifest = _manifest(
            _synced(_youtube_stop()), _synced(), sf.written(), youtube_music=_STOPPED
        )
        self.assertEqual(st.cooling_owed(manifest), frozenset())
        self.assertEqual(st.tally_manifest(manifest).owed, ())

    def test_tekstowo_is_owed_by_a_later_track_holding_none(self) -> None:
        manifest = _manifest(
            _synced(_tekstowo_stop()), _untimed(), sf.not_found(), tekstowo=_STOPPED
        )
        self.assertEqual(st.cooling_owed(manifest), {_TEKSTOWO})

    def test_tekstowo_is_not_owed_by_later_tracks_holding_lyrics(self) -> None:
        manifest = _manifest(
            _synced(_tekstowo_stop()), _untimed(), _synced(), tekstowo=_STOPPED
        )
        self.assertEqual(st.cooling_owed(manifest), frozenset())

    def test_tracks_before_the_stop_owe_only_by_their_own_refusals(self) -> None:
        # The first track ended not found, unrefused: YouTube Music answered it,
        # emptily, before the stop, so the stop left it nothing owing.
        manifest = _manifest(
            sf.not_found(), _synced(_youtube_stop()), _synced(), youtube_music=_STOPPED
        )
        self.assertEqual(st.tally_manifest(manifest).owed, ())

    def test_the_stopping_track_is_the_last_recording_a_refusal(self) -> None:
        # An earlier refusal by the same provider that did not stop it (a timeout,
        # only that track's error) does not make the tracks after it unasked.
        timeout = _youtube_stop("timeout", lrf.Refusal.UNREACHABLE)
        manifest = _manifest(
            _synced(timeout),
            sf.not_found(),
            _synced(_youtube_stop()),
            _synced(),
            youtube_music=_STOPPED,
        )
        self.assertEqual(st.cooling_owed(manifest), frozenset())

    def test_a_stop_no_reached_track_records_owes_every_track(self) -> None:
        manifest = _manifest(sf.not_found(), _synced(), tekstowo=_STOPPED)
        self.assertEqual(st.cooling_owed(manifest), {_TEKSTOWO})

    def test_an_unreached_track_owes_nothing(self) -> None:
        manifest = _manifest(_synced(_youtube_stop()), None, youtube_music=_STOPPED)
        self.assertEqual(st.cooling_owed(manifest), frozenset())


class EarlierStoppedDebtTest(sf.IsolatedCase):
    """An earlier run's manifest owes a stopped provider by the same rule."""

    def test_an_earlier_run_owes_the_tracks_its_stop_left_unasked(self) -> None:
        # Its line names no refusing provider, so only the manifest's stop owes.
        album = sf.needy_album(self.library, "Artist", "A1")
        sf.write_run(album, _RUN, (sf.not_found(),), youtube_music=_STOPPED)
        entry = sl.LedgerEntry(
            "Artist/A1", _RUN, 0, sl.Outcome.DONE, "t", lang=sweep.album.DEFAULT_LANG
        )
        earlier = sl.parse_record(sf.earlier_line(entry))
        assert isinstance(earlier, sl.LedgerEntry)
        notes: list[str] = []
        debt = se.earlier_debt(self.library, earlier, notes.append)
        self.assertEqual(debt, (_YOUTUBE,))
        self.assertEqual(notes, [])
