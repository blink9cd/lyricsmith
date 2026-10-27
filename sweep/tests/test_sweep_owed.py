"""The owed rule: which providers an album still owes answers from, and its outcome.

A track owes a provider when that provider refused it for any reason but unrecognized
and could still answer it (`sweep_tally.could_still_answer`; here, LRCLIB, owed
while the track does not now hold synced lyrics); the album owes the union, in
chain order. An album that finished owing any is `owed`, not `done`, unless its providers
only refused, which stays `refused`. Every outcome here is built in memory, so nothing
runs a process or reaches a provider.
"""

from __future__ import annotations

import io
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import requests
from requests.adapters import BaseAdapter

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_categories as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_music as lym  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_outcome  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally  # noqa: E402 - import follows the sys.path bootstrap above

_OVERLOADED = lrf.Refusal.OVERLOADED
_SYNCED, _UNTIMED = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED


def _refused(
    provider: lm.Source = lm.Source.LRCLIB, reason: lrf.Refusal = _OVERLOADED
) -> lr.ProviderRefusal:
    """One refusal by *provider* for *reason*."""
    return sf.refusal(provider, "HTTP 503", reason)


def _outcome(
    status: lr.OutcomeStatus,
    *refusals: lr.ProviderRefusal,
    kind: lm.LyricsKind | None = None,
    held: lc.HeldBefore | None = None,
    source: lm.Source | None = None,
) -> lr.TrackOutcome:
    """A track that ended as *status* after meeting *refusals*."""
    return lr.TrackOutcome(
        status, source=source, kind=kind, refusals=refusals, held=held
    )


def _owed(*outcomes: lr.TrackOutcome | None) -> tuple[lm.Source, ...]:
    """The providers an album whose tracks ended as *outcomes* owes."""
    return sweep_tally.tally_manifest(sf.manifest_of(*outcomes)).owed


class OwedRuleTest(sf.IsolatedCase):
    """Each branch of the rule, track by track."""

    def test_a_refused_track_left_without_synced_lyrics_owes_the_provider(
        self,
    ) -> None:
        self.assertEqual(_owed(sf.not_found(_refused())), (lm.Source.LRCLIB,))

    def test_every_reason_but_unrecognized_is_owed(self) -> None:
        for reason in lrf.Refusal:
            with self.subTest(reason=reason):
                owed = _owed(sf.not_found(_refused(reason=reason)))
                expected = (
                    () if reason is lrf.Refusal.UNRECOGNIZED else (lm.Source.LRCLIB,)
                )
                self.assertEqual(owed, expected)

    def test_a_lookup_an_open_breaker_left_unsent_is_still_owed(self) -> None:
        unsent = replace(_refused(lm.Source.GENIUS, lrf.Refusal.BLOCKED), unsent=True)
        self.assertEqual(_owed(sf.not_found(unsent)), (lm.Source.GENIUS,))

    def test_a_track_now_holding_synced_lyrics_owes_nothing(self) -> None:
        for status in (lr.OutcomeStatus.WRITTEN, lr.OutcomeStatus.UNCHANGED):
            with self.subTest(status=status):
                track = _outcome(
                    status, _refused(), kind=_SYNCED, source=lm.Source.NETEASE
                )
                self.assertEqual(_owed(track), ())

    def test_a_track_written_or_kept_untimed_still_owes(self) -> None:
        for status in (lr.OutcomeStatus.WRITTEN, lr.OutcomeStatus.UNCHANGED):
            with self.subTest(status=status):
                track = _outcome(
                    status, _refused(), kind=_UNTIMED, source=lm.Source.GENIUS
                )
                self.assertEqual(_owed(track), (lm.Source.LRCLIB,))

    def test_a_kept_track_reads_what_it_holds_from_its_held_kind(self) -> None:
        kept = lr.OutcomeStatus.KEPT_EXISTING
        cases = {
            lc.HeldBefore.SYNCED: (),
            lc.HeldBefore.UNTIMED: (lm.Source.LRCLIB,),
            lc.HeldBefore.NOTHING: (lm.Source.LRCLIB,),
            None: (lm.Source.LRCLIB,),
        }
        for held, expected in cases.items():
            with self.subTest(held=held):
                self.assertEqual(_owed(_outcome(kept, _refused(), held=held)), expected)

    def test_a_kept_track_s_kind_is_a_declined_answer_never_what_it_holds(
        self,
    ) -> None:
        # A --fresh run that declined a synced answer records the answer's kind on
        # the kept track, which still holds only what it held.
        kept = _outcome(
            lr.OutcomeStatus.KEPT_EXISTING,
            _refused(),
            kind=_SYNCED,
            held=lc.HeldBefore.UNTIMED,
        )
        self.assertEqual(_owed(kept), (lm.Source.LRCLIB,))

    def test_a_track_that_errored_owes(self) -> None:
        self.assertEqual(_owed(sf.fetch_error(_refused())), (lm.Source.LRCLIB,))

    def test_the_album_owes_the_union_in_chain_order_each_once(self) -> None:
        owed = _owed(
            sf.not_found(_refused(lm.Source.TEKSTOWO)),
            sf.not_found(_refused(lm.Source.GENIUS), _refused(lm.Source.LRCLIB)),
            sf.not_found(_refused(lm.Source.LRCLIB)),
            None,
            sf.not_found(),
        )
        self.assertEqual(owed, (lm.Source.LRCLIB, lm.Source.GENIUS, lm.Source.TEKSTOWO))

    def test_a_provider_no_option_can_name_is_never_owed(self) -> None:
        # No step of the chain asks Musixmatch, and --only-providers cannot name it.
        self.assertEqual(_owed(sf.not_found(_refused(lm.Source.MUSIXMATCH))), ())

    def test_an_album_with_no_refusal_owes_nothing(self) -> None:
        self.assertEqual(_owed(*sf.ANSWERED), ())


class _Replying(BaseAdapter):
    """A transport answering every request with an empty reply of one *status*."""

    def __init__(self, status: int) -> None:
        """Answer each request with *status*."""
        super().__init__()
        self.status = status

    def send(
        self, request: requests.PreparedRequest, *args: object, **kwargs: object
    ) -> requests.Response:
        """The reply of `status`, its body empty; nothing leaves the process."""
        del args, kwargs
        response = requests.Response()
        response.status_code = self.status
        response.raw = io.BytesIO(b"")
        response.request = request
        return response

    def close(self) -> None:
        """Nothing to release."""


class YouTubeMusicStopOwedTest(sf.IsolatedCase):
    """A YouTube Music stop on a client error leaves the album owing YouTube Music."""

    def _stop_refusal(self, status: int) -> lr.ProviderRefusal:
        """The refusal a track records when YouTube Music stops on a *status* reply."""

        def session() -> requests.Session:
            made = requests.Session()
            made.mount("https://", _Replying(status))
            return made

        client = lym.YouTubeMusicClient(
            self.tmp / "youtube_music.json",
            clock=lambda: 0.0,
            sleeper=lambda _s: None,
            now=lambda: datetime(2026, 10, 21, tzinfo=UTC),
            session_factory=session,
        )
        with self.assertRaises(lym.YouTubeMusicStopped) as raised:
            client.post(lym.SEARCH, {}, 100.0)
        stop = raised.exception
        assert stop.detail is not None  # a stop always records its words
        return lr.ProviderRefusal.of(
            lm.Source.YOUTUBE_MUSIC, _SYNCED, stop.detail, stop.facts
        )

    def test_a_client_error_stop_is_owed(self) -> None:
        for status in (400, 404):
            with self.subTest(status=status):
                refused = sf.not_found(self._stop_refusal(status))
                self.assertEqual(_owed(refused), (lm.Source.YOUTUBE_MUSIC,))


class TallyFieldsTest(sf.IsolatedCase):
    """The tally keeps the refusals by provider and reason, and the manifest's retries."""

    def test_refusals_are_counted_by_provider_and_reason_once_per_track(self) -> None:
        tally = sweep_tally.tally_manifest(
            sf.manifest_of(
                sf.not_found(_refused(), _refused()),
                sf.not_found(_refused(reason=lrf.Refusal.UNRECOGNIZED)),
                sf.not_found(_refused(lm.Source.GENIUS, lrf.Refusal.BLOCKED)),
            )
        )
        self.assertEqual(
            tally.refusals,
            {
                lm.Source.LRCLIB: {_OVERLOADED: 1, lrf.Refusal.UNRECOGNIZED: 1},
                lm.Source.GENIUS: {lrf.Refusal.BLOCKED: 1},
            },
        )

    def test_the_manifest_s_retries_are_kept(self) -> None:
        retried = {lm.Source.LRCLIB: {_OVERLOADED: 7}}
        manifest = replace(sf.manifest_of(sf.not_found()), retried=retried)
        self.assertEqual(sweep_tally.tally_manifest(manifest).retried, retried)

    def test_a_manifest_with_no_retries_keeps_none(self) -> None:
        tally = sweep_tally.tally_manifest(sf.manifest_of(sf.not_found()))
        self.assertEqual((tally.refusals, tally.retried), ({}, {}))


class OutcomePrecedenceTest(sf.IsolatedCase):
    """Refused first, then owed, then done; an offline run is offline whatever it owes."""

    def _outcome_of(
        self, tally: sweep_tally.AlbumTally, *, offline: bool = False
    ) -> sl.Outcome:
        result = sweep_child.ChildResult(album.EXIT_OK)
        return sweep_outcome.outcome_of(result, tally, offline=offline)

    def _tally(
        self, *, refused: bool, owed: tuple[lm.Source, ...]
    ) -> sweep_tally.AlbumTally:
        return sweep_tally.AlbumTally(sweep_tally.Counts(), 1, (), refused, owed=owed)

    def test_an_album_owing_a_provider_is_owed(self) -> None:
        tally = self._tally(refused=False, owed=(lm.Source.LRCLIB,))
        self.assertIs(self._outcome_of(tally), sl.Outcome.OWED)

    def test_refused_comes_before_owed(self) -> None:
        tally = self._tally(refused=True, owed=(lm.Source.LRCLIB,))
        self.assertIs(self._outcome_of(tally), sl.Outcome.REFUSED)

    def test_an_album_owing_nothing_is_done(self) -> None:
        self.assertIs(
            self._outcome_of(self._tally(refused=False, owed=())), sl.Outcome.DONE
        )

    def test_offline_comes_before_owed(self) -> None:
        tally = self._tally(refused=False, owed=(lm.Source.LRCLIB,))
        self.assertIs(self._outcome_of(tally, offline=True), sl.Outcome.OFFLINE)

    def test_track_errors_owing_a_provider_are_owed(self) -> None:
        result = sweep_child.ChildResult(album.EXIT_TRACK_ERRORS)
        tally = self._tally(refused=False, owed=(lm.Source.GENIUS,))
        self.assertIs(
            sweep_outcome.outcome_of(result, tally, offline=False), sl.Outcome.OWED
        )
