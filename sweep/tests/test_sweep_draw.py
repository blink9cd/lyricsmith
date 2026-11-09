"""The seeded draw: the wrong-text group whole, each provenance group to its share.

Pure over classified tracks built here, so a group far past its share costs nothing
to build: a short group taken whole, a long one drawn by the seed alone, the
withheld list, a path the files cannot hold left out, and the printed
summary's approximate counts. Every path here is invented.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import sweep_draw as sd  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_draw_classify as dc  # noqa: E402 - import follows the sys.path bootstrap
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_draw_library import ClassifiedTrack  # noqa: E402 - follows the bootstrap
from sweep_strata import StrataError, StrataGroup, StrataRow  # noqa: E402 - follows the bootstrap

#: This host's root, so an invented path is absolute on every platform.
_ROOT = Path(os.path.abspath(os.sep))
_GROUP = StrataGroup


def _tracks(
    stratum: dc.Stratum,
    count: int,
    *,
    album: str = "Artist/Album",
    timing: dc.Timing = dc.Timing.PROVIDER_TIMED,
    withheld: bool = False,
) -> list[ClassifiedTrack]:
    """*count* invented tracks of *stratum*, each its own path under *album*."""
    folder = _ROOT / "library" / album
    return [
        ClassifiedTrack(
            folder / f"{stratum} {n:04d}.mp3", album, timing, stratum, withheld
        )
        for n in range(count)
    ]


def _groups(rows: tuple[StrataRow, ...]) -> dict[StrataGroup, int]:
    """How many *rows* each group holds."""
    counts: dict[StrataGroup, int] = {}
    for row in rows:
        counts[row.group] = counts.get(row.group, 0) + 1
    return counts


class DrawSampleTest(sf.IsolatedCase):
    """Groups and sizes as the design's table rules them."""

    def setUp(self) -> None:
        """A library past every share but LRCLIB's, with every left-out reason."""
        super().setUp()
        self.tracks = [
            *_tracks(_GROUP.WRONG_TEXT_ALBUMS, 7, album="Artist/Wrong"),
            *_tracks(_GROUP.NETEASE, 260),
            *_tracks(_GROUP.YOUTUBE_MUSIC, 255),
            *_tracks(_GROUP.LRCLIB, 3),
            *_tracks(_GROUP.OLDER_SIDECARS, 400),
            *(
                track
                for reason in (
                    dc.LeftOut.MACHINE_TIMED,
                    dc.LeftOut.FRAMES_ONLY,
                    dc.LeftOut.NO_MANIFEST_RECORD,
                    dc.LeftOut.ANOTHER_SOURCE,
                )
                for track in _tracks(reason, 2)
            ),
        ]

    def test_the_wrong_text_group_and_a_short_group_are_taken_whole(self) -> None:
        draw = sd.draw_sample(self.tracks, seed=5)
        counts = _groups(draw.rows)
        self.assertEqual(counts[_GROUP.WRONG_TEXT_ALBUMS], 7)
        self.assertEqual(counts[_GROUP.LRCLIB], 3)

    def test_each_long_group_is_drawn_to_its_share(self) -> None:
        counts = _groups(sd.draw_sample(self.tracks, seed=5).rows)
        for group in (_GROUP.NETEASE, _GROUP.YOUTUBE_MUSIC, _GROUP.OLDER_SIDECARS):
            with self.subTest(group=group):
                self.assertEqual(counts[group], sd.GROUP_SHARE)

    def test_the_sizes_and_reasons_are_counted(self) -> None:
        draw = sd.draw_sample(self.tracks, seed=5)
        self.assertEqual(draw.sizes[_GROUP.OLDER_SIDECARS], 400)
        self.assertEqual(draw.sizes[_GROUP.WRONG_TEXT_ALBUMS], 7)
        self.assertEqual(draw.left_out[dc.LeftOut.FRAMES_ONLY], 2)
        self.assertEqual(draw.left_out[dc.LeftOut.NOT_TIMED], 0)

    def test_the_sizes_are_the_draws_own_and_need_no_seed(self) -> None:
        sizes, left_out = sd.sample_sizes(self.tracks)
        draw = sd.draw_sample(self.tracks, seed=5)
        self.assertEqual((sizes, left_out), (draw.sizes, draw.left_out))

    def test_the_same_seed_draws_the_same_rows_and_another_seed_others(self) -> None:
        first = sd.draw_sample(self.tracks, seed=5)
        self.assertEqual(sd.draw_sample(list(reversed(self.tracks)), seed=5), first)
        self.assertNotEqual(sd.draw_sample(self.tracks, seed=6).rows, first.rows)

    def test_rows_name_the_album_and_come_in_group_then_path_order(self) -> None:
        rows = sd.draw_sample(self.tracks, seed=5).rows
        self.assertEqual(rows[0].album, "Artist/Wrong")
        order = list(_GROUP)
        keys = [(order.index(row.group), row.path) for row in rows]
        self.assertEqual(keys, sorted(keys))

    def test_a_path_the_files_cannot_hold_is_left_out_before_the_draw(self) -> None:
        held = _tracks(_GROUP.LRCLIB, 1)
        # A linefeed the track list refuses; a tab only the strata file refuses; and
        # an album name the strata file refuses, under a path both could hold.
        unheld = (
            ("A\nB.mp3", "Artist/Album"),
            ("A\tB.mp3", "Artist/Album"),
            ("C.mp3", "Bad\nAlbum"),
        )
        refused = [
            ClassifiedTrack(
                _ROOT / "library" / name,
                album,
                dc.Timing.PROVIDER_TIMED,
                _GROUP.LRCLIB,
                False,
            )
            for name, album in unheld
        ]
        draw = sd.draw_sample([*held, *refused], seed=1)
        self.assertEqual([row.path for row in draw.rows], [str(held[0].path)])
        self.assertEqual(draw.left_out[dc.LeftOut.UNLISTABLE], 3)
        self.assertEqual(draw.sizes[_GROUP.LRCLIB], 1)


class DrawWithheldTest(sf.IsolatedCase):
    """The withheld list: the tracks marked withheld on an entry not undone, still untimed."""

    def test_only_marked_untimed_tracks_are_listed_whole(self) -> None:
        marked = _tracks(
            dc.LeftOut.NOT_TIMED, 3, timing=dc.Timing.UNTIMED, withheld=True
        )
        timed_since = _tracks(_GROUP.NETEASE, 2, withheld=True)
        unmarked = _tracks(dc.LeftOut.NOT_TIMED, 4, timing=dc.Timing.UNTIMED)
        draw = sd.draw_withheld([*unmarked, *timed_since, *marked])
        self.assertEqual(
            [row.path for row in draw.rows], sorted(str(t.path) for t in marked)
        )
        self.assertEqual({row.group for row in draw.rows}, {_GROUP.WITHHELD})
        self.assertEqual(draw.sizes, {_GROUP.WITHHELD: 3})
        self.assertEqual(draw.left_out[dc.LeftOut.NO_LONGER_UNTIMED], 2)

    def test_a_marked_track_the_files_cannot_hold_is_counted(self) -> None:
        held = _tracks(dc.LeftOut.NOT_TIMED, 1, timing=dc.Timing.UNTIMED, withheld=True)
        refused = [
            ClassifiedTrack(
                _ROOT / "library" / name,
                "Artist/Album",
                dc.Timing.UNTIMED,
                dc.LeftOut.NOT_TIMED,
                True,
            )
            for name in ("A\tB.mp3", "A\nB.mp3")
        ]
        draw = sd.draw_withheld([*held, *refused])
        self.assertEqual([row.path for row in draw.rows], [str(held[0].path)])
        self.assertEqual(
            draw.left_out,
            {dc.LeftOut.NO_LONGER_UNTIMED: 0, dc.LeftOut.UNLISTABLE: 2},
        )


class WriteTest(sf.IsolatedCase):
    """The two files are formatted before either is written."""

    def test_a_strata_file_refused_writes_no_track_list(self) -> None:
        out = self.tmp
        row = StrataRow(str(_ROOT / "library" / "A.mp3"), _GROUP.NETEASE, "Album")
        # A path twice: the track list would hold it once, the strata file refuses it.
        with self.assertRaises(StrataError):
            sd._write(out, 1, (row, row))
        self.assertFalse((out / sd.TRACKS_NAME).exists())


class ApproximateTest(sf.IsolatedCase):
    """No exact count is printed: two figures, at least to the ten."""

    def test_counts_are_rounded(self) -> None:
        cases = {
            0: "none",
            1: "under 10",
            9: "under 10",
            42: "about 40",
            580: "about 580",
            16_123: "about 16,000",
        }
        for count, text in cases.items():
            with self.subTest(count=count):
                self.assertEqual(sd.approximate(count), text)


class SummaryTest(sf.IsolatedCase):
    """The printed summary: the seed, each group, each left-out reason."""

    def test_the_summary_names_the_seed_each_group_and_each_reason(self) -> None:
        tracks = [
            *_tracks(_GROUP.NETEASE, 300),
            *_tracks(_GROUP.LRCLIB, 12),
            *_tracks(dc.LeftOut.MACHINE_TIMED, 3),
        ]
        text = sd.format_summary(sd.draw_sample(tracks, seed=-3), seed=-3)
        lines = text.splitlines()
        self.assertEqual(lines[0], "seed: -3")
        self.assertIn(f"netease: {sd.GROUP_SHARE} drawn of about 300", lines)
        self.assertIn(
            f"lrclib: about 10, taken whole, short of {sd.GROUP_SHARE}", lines
        )
        self.assertIn("wrong-text-albums: none, taken whole", lines)
        self.assertIn("left out, machine-timed: under 10", lines)
        self.assertIn("left out, no manifest record: none", lines)
        self.assertTrue(text.endswith("\n"))

    def test_the_withheld_summary_names_its_one_group(self) -> None:
        marked = _tracks(
            dc.LeftOut.NOT_TIMED, 2, timing=dc.Timing.UNTIMED, withheld=True
        )
        text = sd.format_summary(sd.draw_withheld(marked), seed=8)
        self.assertEqual(
            text.splitlines(),
            [
                "seed: 8",
                "withheld: under 10, taken whole",
                "left out, no longer untimed: none",
                "left out, a path the files cannot hold: none",
            ],
        )
