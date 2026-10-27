"""An owed album revisited: only its owed providers asked, until it owes nothing more.

`done_albums` never counts an owed album, so a resumed sweep visits it again, handing
the album tool ``--only-providers`` with the providers it owes. The revisit's own
outcome replaces the last. An owed album neither adds to nor keeps up ``--stop-after``'s
run of refused albums, ``--restart`` forgets it, and a provider the sweep turns off stays
off. The album tool is played by `sweep_fixtures.FakeAlbumTool`, so nothing here runs a
process or reaches a provider.
"""

from __future__ import annotations

import contextlib
import io
import shlex
import sys
from dataclasses import replace
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_options  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run as sr  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_survey import AlbumSurvey  # noqa: E402 - follows the sys.path bootstrap

_ONLY = lyrics_options.ONLY_FLAG


def _refused(provider: lm.Source, reason: lrf.Refusal) -> lr.ProviderRefusal:
    """One refusal by *provider* for *reason*."""
    return sf.refusal(provider, "HTTP 503", reason)


#: An album LRCLIB (overloaded) and Genius (blocked) each refused a track of, while
#: NetEase answered another: owed LRCLIB and Genius.
_OWES_TWO = sf.Script(
    outcomes=(
        sf.not_found(_refused(lm.Source.LRCLIB, lrf.Refusal.OVERLOADED)),
        sf.not_found(_refused(lm.Source.GENIUS, lrf.Refusal.BLOCKED)),
        sf.written(lm.Source.NETEASE),
    )
)
#: The same album, LRCLIB alone still refusing.
_OWES_LRCLIB = sf.Script(
    outcomes=(
        sf.not_found(_refused(lm.Source.LRCLIB, lrf.Refusal.UNREACHABLE)),
        sf.written(lm.Source.NETEASE),
    )
)
_REFUSED = sf.Script(outcomes=sf.REFUSED, exit_code=album.EXIT_TRACK_ERRORS)


def _only_value(argv: list[str]) -> str | None:
    """The value *argv* gives ``--only-providers``, or None when it gives none."""
    return sf.option_value(argv, _ONLY) if _ONLY in argv else None


class OwedCase(sf.SweepCase):
    """`SweepCase`, with each sweep's options open to the pass and the skip options."""

    def sweep_with(
        self, *, refetch_untimed: bool = False, skip: tuple[str, ...] = ()
    ) -> sr.SweepReport:
        """A sweep of A1 to A5 in the pass *refetch_untimed* names, given *skip*."""
        seams = sr.SweepSeams(
            run_child=self.fake,
            sleep=self.sleep,
            now=sf.fixed_now,
            progress=self.notes.append,
            entry=self.tmp / "album.py",
        )
        options = replace(sf.OPTIONS, refetch_untimed=refetch_untimed, skip=skip)
        surveys = [AlbumSurvey(folder, none=1) for folder in self.albums]
        return sr.sweep(self.library, surveys, self.ledger, options, seams)

    def calls_of(self, name: str) -> list[list[str]]:
        """Every argv the fake album tool was run with on album *name*, in order."""
        return [
            argv
            for argv in self.fake.calls
            if Path(sf.option_value(argv, "--album")).name == name
        ]


class RecordTest(OwedCase):
    """An album owing providers is recorded owed, its providers listed in chain order."""

    def test_an_owed_album_is_recorded_owed_with_its_providers(self) -> None:
        self.fake.scripts["A1"] = _OWES_TWO
        self._sweep()
        entry = self._ledger()[0]
        self.assertIs(entry.outcome, sl.Outcome.OWED)
        self.assertEqual(entry.owed_providers, (lm.Source.LRCLIB, lm.Source.GENIUS))
        self.assertEqual(
            entry.refusals,
            {
                lm.Source.LRCLIB: {lrf.Refusal.OVERLOADED: 1},
                lm.Source.GENIUS: {lrf.Refusal.BLOCKED: 1},
            },
        )

    def test_a_first_visit_names_no_provider(self) -> None:
        self.fake.scripts["A1"] = _OWES_TWO
        self._sweep()
        self.assertTrue(all(_only_value(argv) is None for argv in self.fake.calls))

    def test_an_owed_album_makes_the_sweep_exit_one(self) -> None:
        self.fake.scripts["A1"] = _OWES_TWO
        self.assertEqual(sr.sweep_exit_code(self._sweep()), sr.EXIT_ALBUM_ERRORS)


class RevisitTest(OwedCase):
    """A resume revisits an owed album, asking only the providers it owes."""

    def setUp(self) -> None:
        """A first sweep leaves A1 owed LRCLIB and Genius, the rest done."""
        super().setUp()
        self.fake.scripts["A1"] = _OWES_TWO
        self._sweep()

    def test_a_resume_revisits_it_naming_exactly_the_owed_providers(self) -> None:
        self._sweep()
        self.assertEqual(self.fake.albums()[5:], ["A1"])
        self.assertEqual(_only_value(self.fake.calls[5]), "lrclib,genius")

    def test_a_revisit_owing_nothing_more_is_done_and_not_visited_again(self) -> None:
        self.fake.scripts.clear()
        self._sweep()
        self.assertIs(self._ledger()[-1].outcome, sl.Outcome.DONE)
        self.assertEqual(self._ledger()[-1].owed_providers, ())
        report = self._sweep()
        self.assertEqual(report.visited, ())
        self.assertEqual(len(self.fake.calls), 6)

    def test_a_revisit_still_owing_stays_owed_and_names_what_it_still_owes(
        self,
    ) -> None:
        self.fake.scripts["A1"] = _OWES_LRCLIB
        self._sweep()
        self.assertIs(self._ledger()[-1].outcome, sl.Outcome.OWED)
        self._sweep()
        self.assertEqual(
            [_only_value(argv) for argv in self.calls_of("A1")],
            [None, "lrclib,genius", "lrclib"],
        )

    def test_a_revisit_the_providers_only_refused_is_refused(self) -> None:
        self.fake.scripts["A1"] = _REFUSED
        self._sweep()
        self.assertIs(self._ledger()[-1].outcome, sl.Outcome.REFUSED)
        # A refused album's next visit asks every provider again.
        self._sweep()
        self.assertIsNone(_only_value(self.calls_of("A1")[-1]))

    def test_a_restart_forgets_it(self) -> None:
        self._sweep(restart=True)
        self.assertEqual(self.fake.albums()[5:], ["A1", "A2", "A3", "A4", "A5"])
        self.assertTrue(all(_only_value(argv) is None for argv in self.fake.calls))

    def test_an_offline_revisit_names_no_provider(self) -> None:
        # The album tool refuses --only-providers with --offline, which asks none.
        self._sweep(offline=True)
        offline = self.calls_of("A1")[-1]
        self.assertIn("--offline", offline)
        self.assertIsNone(_only_value(offline))
        self.assertIs(self._ledger()[-1].outcome, sl.Outcome.OFFLINE)

    def test_an_owed_album_in_another_language_is_its_own(self) -> None:
        self._sweep(lang="deu")
        self.assertTrue(all(_only_value(argv) is None for argv in self.fake.calls[5:]))


class PassTest(OwedCase):
    """Each pass keeps its own owed albums, and its revisit is that same pass."""

    def test_the_refetch_pass_revisits_its_own_owed_album_in_that_pass(self) -> None:
        self.fake.scripts["A1"] = _OWES_TWO
        self.sweep_with(refetch_untimed=True)
        self.sweep_with(refetch_untimed=True)
        revisit = self.calls_of("A1")[-1]
        self.assertIn(sweep_child.REFETCH_OPTION, revisit)
        self.assertEqual(_only_value(revisit), "lrclib,genius")
        self.assertIs(self._ledger()[-1].pass_, sl.SweepPass.REFETCH_UNTIMED)

    def test_a_normal_pass_s_owed_album_is_asked_in_full_by_the_refetch_pass(
        self,
    ) -> None:
        self.fake.scripts["A1"] = _OWES_TWO
        self.sweep_with()
        self.sweep_with(refetch_untimed=True)
        self.assertIsNone(_only_value(self.calls_of("A1")[-1]))


class TurnedOffTest(OwedCase):
    """A provider the sweep turns off stays off on the revisit and is never owed."""

    def test_a_turned_off_provider_is_never_owed_and_stays_off(self) -> None:
        # The album tool given --no-netease asks NetEase nothing, so records nothing
        # for it; a refusal of NetEase here is still never taken for a debt.
        self.fake.scripts["A1"] = sf.Script(
            outcomes=(
                sf.not_found(_refused(lm.Source.LRCLIB, lrf.Refusal.OVERLOADED)),
                sf.not_found(_refused(lm.Source.NETEASE, lrf.Refusal.OVERLOADED)),
                sf.written(lm.Source.GENIUS),
            )
        )
        skip = (sweep_child.NO_NETEASE_OPTION,)
        self.sweep_with(skip=skip)
        self.assertEqual(self._ledger()[0].owed_providers, (lm.Source.LRCLIB,))
        self.sweep_with(skip=skip)
        revisit = self.calls_of("A1")[-1]
        self.assertEqual(_only_value(revisit), "lrclib")
        self.assertEqual(revisit[-1], sweep_child.NO_NETEASE_OPTION)

    def test_a_debt_a_later_leg_turns_off_is_dropped_and_said(self) -> None:
        self.fake.scripts["A1"] = _OWES_TWO
        self.sweep_with()
        self.notes.clear()
        self.fake.scripts.clear()
        self.sweep_with(skip=("--no-genius",))
        self.assertEqual(_only_value(self.calls_of("A1")[-1]), "lrclib,genius")
        self.assertIs(self._ledger()[-1].outcome, sl.Outcome.DONE)
        self.assertIn(
            "Artist/A1: no longer owed Genius, which this sweep turns off", self.notes
        )

    def test_a_revisit_owing_nothing_turned_off_says_nothing_of_it(self) -> None:
        self.fake.scripts["A1"] = _OWES_TWO
        self.sweep_with()
        self.notes.clear()
        self.sweep_with(skip=(sweep_child.NO_NETEASE_OPTION,))
        self.assertFalse(any("no longer owed" in note for note in self.notes))


class StreakTest(OwedCase):
    """An owed album never adds to the run of refused albums, and ends it."""

    def test_owed_albums_never_stop_the_sweep(self) -> None:
        for name in ("A1", "A2", "A3"):
            self.fake.scripts[name] = _OWES_TWO
        report = self._sweep()
        self.assertIs(report.stop, sr.StopReason.FINISHED)

    def test_an_owed_album_between_refused_ones_ends_their_run(self) -> None:
        # --stop-after is 2: refused, owed, refused never makes two in a row.
        self.fake.scripts.update(A1=_REFUSED, A2=_OWES_TWO, A3=_REFUSED)
        report = self._sweep()
        self.assertIs(report.stop, sr.StopReason.FINISHED)
        self.assertEqual(len(report.visited), 5)


class ResumeLineTest(sf.IsolatedCase):
    """The printed resume command revisits the owed album, and keeps the changed list."""

    def setUp(self) -> None:
        """A1 owed, changing its first track; A2 left, so each leg stops at its limit."""
        super().setUp()
        self.a1 = sf.needy_album(self.library, "Artist", "A1")
        sf.needy_album(self.library, "Artist", "A2")
        self.fake = sf.FakeAlbumTool()
        # Genius rate limited, not blocked: a challenged album would be visited last.
        owes = sf.Script(
            outcomes=(
                sf.not_found(_refused(lm.Source.LRCLIB, lrf.Refusal.OVERLOADED)),
                sf.not_found(_refused(lm.Source.GENIUS, lrf.Refusal.RATE_LIMITED)),
                sf.written(lm.Source.NETEASE),
            ),
            changed=("01 Timed.mp3",),
        )
        self.fake.scripts["A1"] = owes
        self.listed = self.tmp / "changed.txt"

    def main(self, argv: list[str]) -> tuple[int, str]:
        """Run `main` with *argv*; its exit code and stdout."""
        wiring = sweep.Wiring(self.fake, sf.FakeSleep(), sf.fixed_now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sweep.main(argv, wiring=wiring)
        return code, stdout.getvalue()

    def test_the_resume_line_revisits_the_owed_album_and_merges_the_list(
        self,
    ) -> None:
        argv = ["--mode", "run", "--library", str(self.library)]
        code, stdout = self.main(
            [*argv, "--changed-list", str(self.listed), "--limit", "1"]
        )
        self.assertEqual(code, sr.EXIT_ALBUM_ERRORS)
        resume = stdout.split("resume: ")[1].splitlines()[0]
        # The revisit answers in full, saving another track: the list keeps both.
        self.fake.scripts["A1"] = sf.Script(changed=("02 Plain.mp3",))
        # The resume carries --limit 1 as well: the owed album comes first again.
        self.assertEqual(self.main(shlex.split(resume)[1:])[0], sr.EXIT_OK)
        self.assertEqual(self.fake.albums(), ["A1", "A1"])
        revisit = self.fake.calls[1]
        self.assertEqual(_only_value(revisit), "lrclib,genius")
        self.assertIn(sweep_child.CHANGED_LIST_OPTION, revisit)
        read = lyrics_changed.parse_changed_list(self.listed.read_bytes())
        assert read is not None
        self.assertEqual(
            list(read), [str(self.a1 / "01 Timed.mp3"), str(self.a1 / "02 Plain.mp3")]
        )
