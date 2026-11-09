"""The hearing report's arithmetic: each group's tally, the weighted estimate, the comparison.

Pure functions over invented hearing records and strata rows: a record whose path is in
no strata row is counted apart and never used; a group's rate is its `not sung` share of
the tracks heard; the library-wide estimate weights each drawn group's rate by its size
in the library, adds the wrong-text group's own count as it stands, and leaves out a
drawn group none of whose tracks was heard; the two-model comparison counts the tracks
crossing the hearing's share under the second model alone. Every path is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_hear_tally as ht  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import MIN_HEARD_SHARE  # noqa: E402 - follows the bootstrap
from lyrics_hearing_log import (  # noqa: E402 - follows the bootstrap
    HeardKind,
    HeardVerdict,
    HearingRecord,
)
from sweep_strata import StrataGroup, StrataRow  # noqa: E402 - follows the bootstrap

_GROUP = StrataGroup
_VERDICT = HeardVerdict
_MODEL, _OTHER = "model-a.bin", "model-b.bin"
_FINGERPRINT = "0" * 64


def _record(
    path: str,
    verdict: HeardVerdict = HeardVerdict.SUNG,
    *,
    model: str = _MODEL,
    share: float = 0.5,
) -> HearingRecord:
    """An invented hearing of *path* by *model*."""
    return HearingRecord(
        path, _FINGERPRINT, HeardKind.PROVIDER_TIMED, model, 30, share, verdict, ""
    )


def _row(path: str, group: StrataGroup = _GROUP.NETEASE) -> StrataRow:
    """An invented strata row."""
    return StrataRow(path, group, "Invented Artist/Invented Album")


class IndexTest(sf.IsolatedCase):
    """Records are found by path and model; one in no strata row is counted apart."""

    def test_a_record_in_no_strata_row_is_counted_and_not_kept(self) -> None:
        rows = (_row("/m/a.mp3"),)
        found = ht.index_hearings(
            (
                _record("/m/a.mp3"),
                _record("/m/b.mp3"),
                _record("/m/b.mp3", model=_OTHER),
            ),
            rows,
        )
        self.assertEqual(found.outside, 2)
        self.assertEqual(set(found.by_key), {("/m/a.mp3", _MODEL)})


class TallyTest(sf.IsolatedCase):
    """A group's counts, and its rate over the tracks heard."""

    def test_verdicts_skips_and_shift_outcomes_are_counted(self) -> None:
        tally = ht.GroupTally()
        tally.add_record(_record("/m/a.mp3", HeardVerdict.NOT_SUNG))
        tally.add_record(_record("/m/b.mp3", HeardVerdict.LITTLE_HEARD))
        tally.add_record(_record("/m/c.mp3"))
        tally.add_record(_record("/m/d.mp3"))
        tally.add_skip(ht.NOT_HEARD)
        tally.add_skip(ht.NOT_HEARD)
        tally.add_skip("frames-only")
        tally.add_shift(shifted=True)
        tally.add_shift(shifted=False)
        self.assertEqual(
            (tally.rows, tally.heard, tally.not_sung, tally.little_heard),
            (7, 4, 1, 1),
        )
        self.assertEqual(tally.skipped, {ht.NOT_HEARD: 2, "frames-only": 1})
        self.assertEqual((tally.shifts, tally.suspects), (1, 1))
        self.assertEqual(tally.rate(), 0.25)

    def test_a_group_none_heard_has_no_rate(self) -> None:
        tally = ht.GroupTally()
        tally.add_skip(ht.NOT_HEARD)
        self.assertIsNone(tally.rate())


def _tally(heard: int, not_sung: int) -> ht.GroupTally:
    """A group with *heard* tracks heard, *not_sung* of them not sung."""
    tally = ht.GroupTally()
    for number in range(heard):
        verdict = HeardVerdict.NOT_SUNG if number < not_sung else HeardVerdict.SUNG
        tally.add_record(_record(f"/m/{number}.mp3", verdict))
    return tally


class EstimateTest(sf.IsolatedCase):
    """Each drawn group's rate weighted by its size; the wrong-text count added whole."""

    def test_each_rate_is_weighted_by_its_groups_size_in_the_library(self) -> None:
        tallies = {
            _GROUP.WRONG_TEXT_ALBUMS: _tally(heard=6, not_sung=3),
            _GROUP.NETEASE: _tally(heard=4, not_sung=1),
            _GROUP.LRCLIB: _tally(heard=10, not_sung=0),
            _GROUP.OLDER_SIDECARS: _tally(heard=5, not_sung=2),
        }
        sizes = {
            _GROUP.WRONG_TEXT_ALBUMS: 7,
            _GROUP.NETEASE: 400,
            _GROUP.YOUTUBE_MUSIC: 0,
            _GROUP.LRCLIB: 900,
            _GROUP.OLDER_SIDECARS: 1000,
        }
        found = ht.estimate(tallies, sizes)
        # 400 x 1/4 + 900 x 0 + 1000 x 2/5, and the wrong-text group's 3 as heard.
        self.assertAlmostEqual(found.not_sung, 100 + 0 + 400 + 3)
        self.assertEqual(found.tracks, 7 + 400 + 900 + 1000)
        self.assertEqual(
            found.weighted,
            (
                (_GROUP.NETEASE, 400, 0.25),
                (_GROUP.LRCLIB, 900, 0.0),
                (_GROUP.OLDER_SIDECARS, 1000, 0.4),
            ),
        )
        self.assertEqual(found.unheard, ())
        self.assertEqual(found.wrong_text, (3, 7))

    def test_a_drawn_group_none_heard_is_left_out_and_named(self) -> None:
        tallies = {_GROUP.NETEASE: _tally(heard=2, not_sung=1)}
        sizes = {_GROUP.NETEASE: 10, _GROUP.YOUTUBE_MUSIC: 30, _GROUP.LRCLIB: 0}
        found = ht.estimate(tallies, sizes)
        self.assertAlmostEqual(found.not_sung, 5)
        self.assertEqual(found.unheard, ((_GROUP.YOUTUBE_MUSIC, 30),))
        # No wrong-text group in the sample adds nothing; its size still counts.
        self.assertEqual(found.wrong_text, (0, 0))
        # The unheard group's 30 are named, never in the total: 5 of 10, not of 40.
        self.assertEqual(found.tracks, 10)

    def test_the_withheld_group_never_enters_the_estimate(self) -> None:
        tallies = {_GROUP.WITHHELD: _tally(heard=3, not_sung=3)}
        found = ht.estimate(tallies, {_GROUP.NETEASE: 0})
        self.assertEqual((found.not_sung, found.tracks), (0, 0))


class CompareTest(sf.IsolatedCase):
    """Each track heard by both models, and those crossing the share under the second."""

    def test_only_a_track_rising_past_the_share_under_the_second_counts(self) -> None:
        under, over = MIN_HEARD_SHARE / 2, MIN_HEARD_SHARE
        no, yes, little = _VERDICT.NOT_SUNG, _VERDICT.SUNG, _VERDICT.LITTLE_HEARD
        records = (
            _record("/m/rises.mp3", no, share=under),
            _record("/m/rises.mp3", yes, share=over, model=_OTHER),
            _record("/m/stays.mp3", no, share=under),
            _record("/m/stays.mp3", no, share=under, model=_OTHER),
            _record("/m/falls.mp3", yes, share=over),
            _record("/m/falls.mp3", no, share=under, model=_OTHER),
            _record("/m/one-model.mp3", no, share=under),
            # Over the share, but on too few pairs to judge: little heard, no crossing.
            _record("/m/little.mp3", no, share=under),
            _record("/m/little.mp3", little, share=over, model=_OTHER),
        )
        rows = tuple(
            _row(path, _GROUP.WITHHELD)
            for path in (
                "/m/falls.mp3",
                "/m/little.mp3",
                "/m/one-model.mp3",
                "/m/rises.mp3",
                "/m/stays.mp3",
            )
        )
        hearings = ht.index_hearings(records, rows)
        found = ht.compare(rows, hearings, _MODEL, _OTHER)
        self.assertEqual(
            found.tracks,
            (
                ht.Compared("/m/falls.mp3", ht.Side(over, yes), ht.Side(under, no)),
                ht.Compared("/m/little.mp3", ht.Side(under, no), ht.Side(over, little)),
                ht.Compared("/m/rises.mp3", ht.Side(under, no), ht.Side(over, yes)),
                ht.Compared("/m/stays.mp3", ht.Side(under, no), ht.Side(under, no)),
            ),
        )
        self.assertEqual(found.crossed, 1)
