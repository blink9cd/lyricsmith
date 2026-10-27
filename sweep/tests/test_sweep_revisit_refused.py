"""A revisit that asks only some providers counts as refused by those it asked alone.

A revisit names the providers its album owes (`sweep_owed.revisit_only`), so the album
tool asks only their steps. A track holding its own untimed lyrics is looked up with the
synced steps only; when each synced step the revisit asked refused, that track is no
answer, even though a provider the revisit never asked recorded nothing. The album tool
is played by `sweep_fixtures.FakeAlbumTool`, so nothing here runs a process or reaches a
provider.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_owed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally  # noqa: E402 - import follows the sys.path bootstrap above

_LRCLIB, _NETEASE = lm.Source.LRCLIB, lm.Source.NETEASE
_GENIUS = lm.Source.GENIUS


def _overloaded(provider: lm.Source) -> lr.ProviderRefusal:
    """One synced-step refusal by *provider*, overloaded."""
    return sf.refusal(provider, "HTTP 503", lrf.Refusal.OVERLOADED)


def _own_untimed(*refusals: lr.ProviderRefusal) -> lr.TrackOutcome:
    """A track left holding its own untimed lyrics, after *refusals*."""
    return sf.status_outcome(
        lr.OutcomeStatus.UNCHANGED,
        source=lm.Source.SIDECAR,
        kind=lm.LyricsKind.UNSYNCED,
        refusals=refusals,
    )


#: A first visit: LRCLIB refused a track NetEase could not answer either, while NetEase
#: answered another, so the album owes LRCLIB alone.
_OWES_LRCLIB = sf.Script(
    outcomes=(sf.not_found(_overloaded(_LRCLIB)), sf.written(_NETEASE))
)
#: The revisit, asking LRCLIB alone: it refused both tracks, one kept its own untimed
#: lyrics, the other failed outright.
_LRCLIB_REFUSED = sf.Script(
    outcomes=(_own_untimed(_overloaded(_LRCLIB)), sf.fetch_error(_overloaded(_LRCLIB))),
    exit_code=album.EXIT_TRACK_ERRORS,
)


class RevisitRefusedTest(sf.SweepCase):
    """A revisit whose asked providers only refused is recorded refused."""

    def test_an_lrclib_only_revisit_refused_on_every_track_is_refused(self) -> None:
        self.fake.scripts["A1"] = _OWES_LRCLIB
        self._sweep()
        self.assertEqual(self._ledger()[0].owed_providers, (_LRCLIB,))
        self.fake.scripts["A1"] = _LRCLIB_REFUSED
        self._sweep()
        self.assertIs(self._ledger()[-1].outcome, sl.Outcome.REFUSED)


class SyncedAskedTest(sf.IsolatedCase):
    """The LRC providers whose synced step a visit asks."""

    def test_a_first_visit_asks_every_lrc_provider(self) -> None:
        self.assertEqual(sweep_owed.synced_asked((), ()), {_LRCLIB, _NETEASE})

    def test_a_revisit_asks_only_the_owed_lrc_providers(self) -> None:
        self.assertEqual(sweep_owed.synced_asked((_LRCLIB, _GENIUS), ()), {_LRCLIB})

    def test_a_turned_off_provider_is_not_asked(self) -> None:
        self.assertEqual(sweep_owed.synced_asked((), ("--no-netease",)), {_LRCLIB})

    def test_an_untimed_only_debt_asks_no_synced_step(self) -> None:
        self.assertEqual(sweep_owed.synced_asked((_GENIUS,), ()), frozenset())

    def test_an_owed_provider_turned_off_is_not_asked(self) -> None:
        self.assertEqual(
            sweep_owed.synced_asked((_NETEASE,), ("--no-netease",)), frozenset()
        )


class TallySyncedTest(sf.IsolatedCase):
    """The tally reads an untimed-holding track by the synced steps actually asked."""

    def test_every_asked_synced_step_refusing_is_no_answer(self) -> None:
        manifest = sf.manifest_of(_own_untimed(_overloaded(_LRCLIB)))
        tally = sweep_tally.tally_manifest(manifest, synced=frozenset({_LRCLIB}))
        self.assertTrue(tally.refused)

    def test_no_synced_step_asked_is_no_answer(self) -> None:
        manifest = sf.manifest_of(_own_untimed(sf.refusal(_GENIUS)))
        self.assertTrue(
            sweep_tally.tally_manifest(manifest, synced=frozenset()).refused
        )

    def test_by_default_every_lrc_provider_was_asked(self) -> None:
        manifest = sf.manifest_of(_own_untimed(_overloaded(_LRCLIB)))
        self.assertFalse(sweep_tally.tally_manifest(manifest).refused)
