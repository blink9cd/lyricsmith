"""An earlier run's debt: what an album swept before the owed outcome existed owes.

Under ``--owe-earlier-runs`` (`sweep_earlier`), an album whose newest ledger line is
``done`` and holds no ``owed_providers`` field owes each refusing provider its line
names, and each cooling provider its run's manifest owes by the owed rule's own test
(`sweep_tally.cooling_owed`, whose cases are its own suite's). Every album, run and
line here is invented, and each manifest is written under the test's own temporary
library.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_earlier as se  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally as st  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_provider_states import CoolingState  # noqa: E402 - follows the bootstrap

_LANG = sweep.album.DEFAULT_LANG
_NORMAL = sl.SweepPass.NORMAL
_RUN = "20261018T120000Z"
_COOLING = CoolingState.COOLING_DOWN


def _done(
    album: str = "Artist/A1",
    providers: tuple[str, ...] = (),
    run_id: str | None = _RUN,
    lang: str = _LANG,
    pass_: sl.SweepPass = _NORMAL,
) -> sl.LedgerEntry:
    """A done entry for *album*, naming *providers* as refusing."""
    return sl.LedgerEntry(
        album,
        run_id,
        0,
        sl.Outcome.DONE,
        "t",
        refusing_providers=providers,
        lang=lang,
        pass_=pass_,
    )


def _earlier(entry: sl.LedgerEntry) -> sl.LedgerEntry:
    """*entry* as read back from a line written before ``owed_providers`` was."""
    record = sl.parse_record(sf.earlier_line(entry))
    assert isinstance(record, sl.LedgerEntry)
    return record


class RecordedFieldTest(sf.IsolatedCase):
    """A line tells apart a debt recorded as none and one never recorded."""

    def test_a_line_without_the_field_reads_as_not_recorded(self) -> None:
        self.assertFalse(_earlier(_done()).owed_recorded)

    def test_a_line_with_an_empty_list_reads_as_recorded(self) -> None:
        record = sl.parse_record(sl.encode_record(_done()))
        assert isinstance(record, sl.LedgerEntry)
        self.assertTrue(record.owed_recorded)

    def test_the_flag_is_never_written_to_a_line(self) -> None:
        line = sl.encode_record(_earlier(_done()))
        self.assertNotIn("owed_recorded", json.loads(line))
        self.assertIn("owed_providers", json.loads(line))


class IsEarlierTest(sf.IsolatedCase):
    """Only a done entry whose line held no owed providers is an earlier one."""

    def test_a_done_line_without_the_field_is_earlier(self) -> None:
        self.assertTrue(se.is_earlier(_earlier(_done())))

    def test_a_done_line_with_the_field_is_not(self) -> None:
        self.assertFalse(se.is_earlier(_done(providers=("LRCLIB",))))

    def test_any_other_outcome_is_not(self) -> None:
        refused = sl.LedgerEntry("Artist/A1", None, 1, sl.Outcome.REFUSED, "t")
        self.assertFalse(se.is_earlier(_earlier(refused)))


class RefusalDebtTest(sf.IsolatedCase):
    """Each refusing label maps to the askable provider of that label."""

    def test_labels_map_to_their_providers(self) -> None:
        debt = se.refusal_debt(("Tekstowo", "LRCLIB", "Genius"))
        self.assertEqual(debt, {lm.Source.LRCLIB, lm.Source.GENIUS, lm.Source.TEKSTOWO})

    def test_a_label_no_askable_provider_has_is_dropped(self) -> None:
        self.assertEqual(
            se.refusal_debt(("Elsewhere", "Bandcamp")), {lm.Source.BANDCAMP}
        )


class EarlierDebtTest(sf.IsolatedCase):
    """An earlier entry's whole debt, in chain order, its manifest read in the album."""

    def setUp(self) -> None:
        """An album folder, A1, under the library."""
        super().setUp()
        self.album = sf.needy_album(self.library, "Artist", "A1")
        self.notes: list[str] = []

    def _debt(self, entry: sl.LedgerEntry) -> tuple[lm.Source, ...]:
        return se.earlier_debt(self.library, entry, self.notes.append)

    def test_refusals_and_a_cooling_provider_join_in_chain_order(self) -> None:
        sf.write_run(self.album, _RUN, (sf.not_found(),), tekstowo=_COOLING)
        debt = self._debt(_earlier(_done(providers=("Genius", "LRCLIB"))))
        self.assertEqual(debt, (lm.Source.LRCLIB, lm.Source.GENIUS, lm.Source.TEKSTOWO))
        self.assertEqual(self.notes, [])

    def test_the_cool_down_debt_is_the_owed_rule_s_own(self) -> None:
        untimed = sf.status_outcome(
            lr.OutcomeStatus.WRITTEN,
            source=lm.Source.GENIUS,
            kind=lm.LyricsKind.UNSYNCED,
        )
        sf.write_run(self.album, _RUN, (untimed,), tekstowo=_COOLING)
        self.assertIs(se.cooling_owed, st.cooling_owed)
        self.assertEqual(self._debt(_earlier(_done())), ())

    def test_a_provider_both_refusing_and_cooling_is_named_once(self) -> None:
        sf.write_run(self.album, _RUN, (sf.not_found(),), tekstowo=_COOLING)
        debt = self._debt(_earlier(_done(providers=("Tekstowo",))))
        self.assertEqual(debt, (lm.Source.TEKSTOWO,))

    def test_a_missing_manifest_keeps_the_refusals_and_says_so(self) -> None:
        debt = self._debt(_earlier(_done(providers=("LRCLIB",))))
        self.assertEqual(debt, (lm.Source.LRCLIB,))
        self.assertEqual(len(self.notes), 1)
        self.assertIn("Artist/A1", self.notes[0])

    def test_a_null_run_id_reads_no_manifest_and_says_so(self) -> None:
        debt = self._debt(_earlier(_done(run_id=None)))
        self.assertEqual(debt, ())
        self.assertEqual(len(self.notes), 1)
        self.assertIn("Artist/A1", self.notes[0])

    def test_a_run_id_that_is_no_run_s_name_is_never_followed(self) -> None:
        outside = self.tmp / "outside"
        sf.write_run(outside, _RUN, (sf.not_found(),), tekstowo=_COOLING)
        escape = f"../../../outside/.lyricsmith/{_RUN}"
        debt = self._debt(_earlier(_done(run_id=escape)))
        self.assertEqual(debt, ())
        self.assertEqual(len(self.notes), 1)


class PlanLedgerTest(sf.IsolatedCase):
    """The ledger's done set and debts, with and without the option."""

    def setUp(self) -> None:
        """Albums A1 and A2 under the library, each a run's manifest beside it."""
        super().setUp()
        self.notes: list[str] = []
        for name in ("A1", "A2"):
            folder = sf.needy_album(self.library, "Artist", name)
            sf.write_run(folder, _RUN, (sf.not_found(),), tekstowo=_COOLING)

    def _plan(
        self,
        *records: sl.LedgerRecord,
        owe: bool = True,
        needing: tuple[str, ...] = ("Artist/A1", "Artist/A2"),
    ) -> se.LedgerPlan:
        return se.plan_ledger(
            records,
            self.library,
            needing,
            lang=_LANG,
            pass_=_NORMAL,
            owe_earlier=owe,
            progress=self.notes.append,
        )

    def test_without_the_option_an_earlier_entry_stays_done(self) -> None:
        plan = self._plan(_earlier(_done(providers=("LRCLIB",))), owe=False)
        self.assertEqual(plan, se.LedgerPlan(frozenset({"Artist/A1"}), {}, {}))

    def test_with_it_an_earlier_debt_makes_the_album_owed(self) -> None:
        plan = self._plan(_earlier(_done(providers=("LRCLIB",))))
        debt = (lm.Source.LRCLIB, lm.Source.TEKSTOWO)
        self.assertEqual(
            plan, se.LedgerPlan(frozenset(), {"Artist/A1": debt}, {"Artist/A1": debt})
        )

    def test_an_empty_debt_leaves_the_album_done(self) -> None:
        folder = sf.needy_album(self.library, "Artist", "A3")
        sf.write_run(folder, _RUN, (sf.not_found(),))
        plan = self._plan(_earlier(_done("Artist/A3")), needing=("Artist/A3",))
        self.assertEqual(plan.done, {"Artist/A3"})
        self.assertEqual((plan.owed, plan.earlier), ({}, {}))

    def test_a_line_holding_the_field_is_never_reinterpreted(self) -> None:
        plan = self._plan(_done(providers=("LRCLIB",)))
        self.assertEqual(plan.done, {"Artist/A1"})
        self.assertEqual(plan.earlier, {})

    def test_an_album_needing_no_visit_is_left_as_it_is(self) -> None:
        plan = self._plan(_earlier(_done(providers=("LRCLIB",))), needing=())
        self.assertEqual(plan.done, {"Artist/A1"})
        self.assertEqual(plan.earlier, {})

    def test_the_debts_follow_the_order_of_the_albums_needing_a_visit(self) -> None:
        plan = self._plan(
            _earlier(_done("Artist/A1")),
            _earlier(_done("Artist/A2")),
            needing=("Artist/A2", "Artist/A1"),
        )
        self.assertEqual(list(plan.earlier), ["Artist/A2", "Artist/A1"])

    def test_an_owed_entry_keeps_its_own_debt(self) -> None:
        owed = sl.LedgerEntry(
            "Artist/A2",
            None,
            1,
            sl.Outcome.OWED,
            "t",
            owed_providers=(lm.Source.GENIUS,),
        )
        plan = self._plan(_earlier(_done(providers=("LRCLIB",))), owed)
        self.assertEqual(plan.owed["Artist/A2"], (lm.Source.GENIUS,))
        self.assertEqual(list(plan.earlier), ["Artist/A1"])

    def test_a_restart_mark_and_another_pass_or_language_are_respected(self) -> None:
        before_restart = _earlier(_done(providers=("LRCLIB",)))
        other_lang = _earlier(_done("Artist/A2", ("LRCLIB",), lang="deu"))
        plan = self._plan(before_restart, sl.RestartMark("t"), other_lang)
        self.assertEqual(plan, se.LedgerPlan(frozenset(), {}, {}))


def _owed(
    album: str, owed: tuple[lm.Source, ...], challenged: tuple[lm.Source, ...]
) -> sl.LedgerEntry:
    """An owed entry for *album*, owing *owed*, its visit challenged by *challenged*."""
    return sl.LedgerEntry(
        album,
        None,
        1,
        sl.Outcome.OWED,
        "t",
        owed_providers=owed,
        challenged_providers=challenged,
    )


class ChallengedPlanTest(sf.IsolatedCase):
    """The albums whose latest counted visit was challenged by a provider still owed."""

    def _plan(self, *records: sl.LedgerRecord) -> se.LedgerPlan:
        return se.plan_ledger(
            records,
            self.library,
            ("Artist/A1", "Artist/A2"),
            lang=_LANG,
            pass_=_NORMAL,
            owe_earlier=True,
            progress=lambda _line: None,
        )

    def test_a_challenge_by_a_provider_owed_names_the_album(self) -> None:
        genius = (lm.Source.GENIUS,)
        plan = self._plan(_owed("Artist/A1", genius, genius))
        self.assertEqual(plan.challenged, {"Artist/A1"})
        self.assertEqual(plan.challenged_by, {"Artist/A1": genius})

    def test_only_the_providers_still_owed_are_named(self) -> None:
        both = (lm.Source.LRCLIB, lm.Source.GENIUS)
        plan = self._plan(_owed("Artist/A1", (lm.Source.GENIUS,), both))
        self.assertEqual(plan.challenged_by, {"Artist/A1": (lm.Source.GENIUS,)})

    def test_a_challenge_by_a_provider_no_longer_owed_names_none(self) -> None:
        plan = self._plan(_owed("Artist/A1", (lm.Source.LRCLIB,), (lm.Source.GENIUS,)))
        self.assertEqual(plan.challenged, frozenset())

    def test_only_the_latest_entry_counts(self) -> None:
        genius = (lm.Source.GENIUS,)
        plan = self._plan(
            _owed("Artist/A1", genius, genius), _owed("Artist/A1", genius, ())
        )
        self.assertEqual(plan.challenged, frozenset())

    def test_a_done_album_is_never_named(self) -> None:
        done = sl.LedgerEntry(
            "Artist/A1",
            None,
            0,
            sl.Outcome.DONE,
            "t",
            challenged_providers=(lm.Source.GENIUS,),
        )
        self.assertEqual(self._plan(done).challenged, frozenset())

    def test_an_earlier_entry_never_counts(self) -> None:
        plan = self._plan(_earlier(_done(providers=("Genius",))))
        self.assertIn("Artist/A1", plan.earlier)
        self.assertEqual(plan.challenged, frozenset())

    def test_another_language_or_a_restart_is_respected(self) -> None:
        genius = (lm.Source.GENIUS,)
        other = sl.LedgerEntry(
            "Artist/A2",
            None,
            1,
            sl.Outcome.OWED,
            "t",
            lang="deu",
            owed_providers=genius,
            challenged_providers=genius,
        )
        plan = self._plan(
            _owed("Artist/A1", genius, genius), sl.RestartMark("t"), other
        )
        self.assertEqual(plan.challenged, frozenset())
