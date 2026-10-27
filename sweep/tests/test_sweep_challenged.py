"""A challenged owed album is visited last: the challenge, its tally, and the run's order.

A provider's challenge (a ``403`` from a challenge page, or a Tekstowo ``blocked`` with
no status) stops the album tool asking that provider: its breaker opens, or, for a
cooling source such as Tekstowo, it stops for the run. So an owed album whose last
visit drew one is visited after every other album to visit, each group in walk order.
A lookup kept unsent after that stop is no challenge: it was never sent.
Every album, artist and refusal here is invented, and the album tool is played by
`sweep_fixtures.FakeAlbumTool`, so nothing runs a process or reaches a provider.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from dataclasses import replace
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_challenged as sc  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_survey import AlbumSurvey  # noqa: E402 - follows the sys.path bootstrap

_GENIUS, _TEKSTOWO = lm.Source.GENIUS, lm.Source.TEKSTOWO
#: Genius's challenge, as a manifest records it: sent, retried, refused by a challenge.
_CHALLENGE = lr.ProviderRefusal(
    _GENIUS,
    lm.LyricsKind.UNSYNCED,
    "HTTP 403 after 3 retries",
    lrf.Refusal.BLOCKED,
    status=403,
    retries=3,
    server="cloudflare",
)
#: Tekstowo's challenge: blocked, with no status.
_TEKSTOWO_BLOCKED = lr.ProviderRefusal(
    _TEKSTOWO, lm.LyricsKind.UNSYNCED, "challenge page", lrf.Refusal.BLOCKED
)
#: An album that drew Genius's challenge while NetEase answered: owed Genius.
_CHALLENGED = sf.Script(
    outcomes=(sf.not_found(_CHALLENGE), sf.written(lm.Source.NETEASE))
)
#: An album owed Genius only through lookups its open breaker kept unsent.
_BREAKER_ONLY = sf.Script(
    outcomes=(
        sf.not_found(sf.breaker_open()),
        sf.not_found(sf.breaker_open()),
        sf.written(lm.Source.NETEASE),
    )
)
#: An album owed LRCLIB, overloaded: no challenge at all.
_OVERLOADED = sf.Script(
    outcomes=(
        sf.not_found(sf.refusal(detail="HTTP 503", reason=lrf.Refusal.OVERLOADED)),
        sf.written(lm.Source.NETEASE),
    )
)
#: The line that heads the survey's list of albums a run would visit.
_VISIT_LINE = "albums a run would visit:"


def _challenged(*outcomes: lr.TrackOutcome) -> tuple[lm.Source, ...]:
    """The providers the tally of a run ending as *outcomes* names as challenged."""
    return sweep_tally.tally_manifest(sf.manifest_of(*outcomes)).challenged


class WasChallengedTest(sf.IsolatedCase):
    """Only a refusal sent and answered by a block is a challenge."""

    def test_a_sent_403_is_a_challenge(self) -> None:
        self.assertTrue(sc.was_challenged(_CHALLENGE))

    def test_a_blocked_refusal_with_no_status_is_a_challenge(self) -> None:
        self.assertTrue(sc.was_challenged(_TEKSTOWO_BLOCKED))

    def test_a_lookup_the_breaker_kept_unsent_is_none(self) -> None:
        self.assertFalse(sc.was_challenged(sf.breaker_open()))

    def test_another_reason_is_none(self) -> None:
        for reason in (lrf.Refusal.OVERLOADED, lrf.Refusal.UNRECOGNIZED):
            with self.subTest(reason=reason):
                self.assertFalse(sc.was_challenged(sf.refusal(_GENIUS, "x", reason)))


class TallyTest(sf.IsolatedCase):
    """The tally names each askable provider a track's sent lookup was challenged by."""

    def test_only_breaker_open_refusals_name_no_challenge(self) -> None:
        self.assertEqual(
            _challenged(
                sf.not_found(sf.breaker_open()), sf.not_found(sf.breaker_open())
            ),
            (),
        )

    def test_a_tekstowo_block_with_no_status_is_a_challenge(self) -> None:
        self.assertEqual(_challenged(sf.not_found(_TEKSTOWO_BLOCKED)), (_TEKSTOWO,))

    def test_the_challenges_follow_the_chain_s_order_once_each(self) -> None:
        lrclib = sf.refusal(detail="HTTP 403", reason=lrf.Refusal.BLOCKED)
        self.assertEqual(
            _challenged(
                sf.not_found(_CHALLENGE, lrclib),
                sf.not_found(_CHALLENGE, sf.breaker_open()),
            ),
            (lm.Source.LRCLIB, _GENIUS),
        )

    def test_a_run_with_no_block_names_none(self) -> None:
        self.assertEqual(_challenged(*sf.ANSWERED), ())


class NoticeTest(sf.IsolatedCase):
    """The line the run says of each album it visits last."""

    def test_one_provider(self) -> None:
        self.assertEqual(
            sc.challenged_notice("Artist/A1", (_GENIUS,)),
            "Artist/A1: visited last, its last visit challenged by Genius",
        )

    def test_providers_are_joined_as_a_dropped_debt_s_are(self) -> None:
        self.assertEqual(
            sc.challenged_notice("Artist/A1", (_GENIUS, _TEKSTOWO)),
            "Artist/A1: visited last, its last visit challenged by Genius, Tekstowo",
        )

    def test_the_notices_follow_the_visit_order_of_those_named(self) -> None:
        library = Path("library")
        to_visit = [library / "Artist" / name for name in ("A2", "A3", "A1")]
        notices = sc.last_notices(
            library, to_visit, {"Artist/A1": (_GENIUS,), "Artist/A3": (_TEKSTOWO,)}
        )
        self.assertEqual(
            notices,
            [
                sc.challenged_notice("Artist/A3", (_TEKSTOWO,)),
                sc.challenged_notice("Artist/A1", (_GENIUS,)),
            ],
        )


class RunOrderTest(sf.SweepCase):
    """A resume visits a challenged owed album after every other album to visit."""

    def setUp(self) -> None:
        """A library of two needy albums, A1 then A2 in walk order."""
        super().setUp()
        self.albums = self.albums[:2]

    def _first_then_resume(self, second: sf.Script) -> list[str]:
        """Sweep with A1 challenged and A2 scripted *second*, then resume; the order."""
        self.fake.scripts.update({"A1": _CHALLENGED, "A2": second})
        self._sweep()
        self.fake.scripts.clear()
        self._sweep()
        return self.fake.albums()[2:]

    def test_the_challenge_is_recorded(self) -> None:
        self.fake.scripts["A1"] = _CHALLENGED
        self._sweep()
        entry = self._ledger()[0]
        self.assertIs(entry.outcome, sl.Outcome.OWED)
        self.assertEqual(entry.challenged_providers, (_GENIUS,))

    def test_the_challenged_album_is_visited_after_the_other(self) -> None:
        self.assertEqual(self._first_then_resume(_OVERLOADED), ["A2", "A1"])

    def test_the_run_says_so_once_before_the_first_visit(self) -> None:
        self._first_then_resume(_OVERLOADED)
        notice = sc.challenged_notice("Artist/A1", (_GENIUS,))
        self.assertEqual(self.notes.count(notice), 1)
        # Only the resume visits A2 first.
        self.assertLess(self.notes.index(notice), self.notes.index("[1/2] Artist/A2"))
        # The resume's count line comes first, then the notice.
        count = len(self.notes) - 1 - self.notes[::-1].index("2 of 2 albums to visit")
        self.assertLess(count, self.notes.index(notice))

    def test_a_breaker_only_album_is_not_put_last(self) -> None:
        self.assertEqual(self._first_then_resume(_BREAKER_ONLY), ["A2", "A1"])
        self.assertEqual(self._ledger()[1].challenged_providers, ())

    def test_a_restart_forgets_the_order(self) -> None:
        self.fake.scripts.update({"A1": _CHALLENGED, "A2": _OVERLOADED})
        self._sweep()
        self._sweep(restart=True)
        self.assertEqual(self.fake.albums()[2:], ["A1", "A2"])
        self.assertNotIn(sc.challenged_notice("Artist/A1", (_GENIUS,)), self.notes)

    def test_a_turned_off_challenger_moves_nothing(self) -> None:
        # A1 owes only Genius, which challenged it; a resume without Genius keeps
        # A1 in its place and says nothing of a challenge.
        self.fake.scripts.update({"A1": _CHALLENGED, "A2": _OVERLOADED})
        self._sweep()
        self.fake.scripts.clear()
        self._sweep(skip=("--no-genius",))
        self.assertEqual(self.fake.albums()[2:], ["A1", "A2"])
        self.assertNotIn(sc.challenged_notice("Artist/A1", (_GENIUS,)), self.notes)

    def test_the_shared_plan_drops_a_turned_off_challenger(self) -> None:
        # The survey and the run both read the plan through this one function.
        self.fake.scripts["A1"] = _CHALLENGED
        self._sweep()
        records = sl.read_ledger(self.ledger).records
        surveys = [AlbumSurvey(album, none=1) for album in self.albums]
        options = replace(sf.OPTIONS, skip=("--no-genius",))
        notes: list[str] = []
        read = sweep_run.ledger_plan(
            records, self.library, surveys, options, notes.append
        )
        self.assertEqual(read.challenged_by, {})
        self.assertEqual(read.challenged, frozenset())

    def test_a_challenge_no_longer_owed_moves_nothing(self) -> None:
        # A1 drew Genius's challenge but owes only LRCLIB now: it keeps its place.
        self.fake.scripts["A2"] = _OVERLOADED
        self._sweep()
        entry = sl.LedgerEntry(
            "Artist/A1",
            None,
            1,
            sl.Outcome.OWED,
            "t",
            owed_providers=(lm.Source.LRCLIB,),
            challenged_providers=(_GENIUS,),
        )
        sl.append_record(self.ledger, entry)
        self._sweep()
        self.assertEqual(self.fake.albums()[2:], ["A1", "A2"])


class SurveyOrderTest(sf.IsolatedCase):
    """The survey lists a challenged owed album last, as the run visits it."""

    def test_the_survey_lists_it_last(self) -> None:
        for name in ("A1", "A2"):
            sf.needy_album(self.library, "Artist", name)
        ledger = sl.ledger_path(self.library, os.environ)
        for name, challenged in (("Artist/A1", (_GENIUS,)), ("Artist/A2", ())):
            entry = sl.LedgerEntry(
                name,
                None,
                1,
                sl.Outcome.OWED,
                "t",
                owed_providers=(_GENIUS,),
                challenged_providers=challenged,
            )
            sl.append_record(ledger, entry)
        stdout = io.StringIO()
        wiring = sweep.Wiring(sf.FakeAlbumTool(), sf.FakeSleep(), sf.fixed_now)
        argv = ["--mode", "survey", "--library", str(self.library)]
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(sweep.main(argv, wiring=wiring), 0)
        lines = stdout.getvalue().splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith(_VISIT_LINE))
        self.assertEqual(
            [line.strip() for line in lines[start + 1 : start + 3]],
            ["Artist/A2", "Artist/A1"],
        )
