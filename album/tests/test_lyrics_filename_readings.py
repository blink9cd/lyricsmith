"""The readings of a file name: the ways a stem standing in for missing tags can be read.

A track missing its artist or title tag is asked by its file stem, whole, as the title.
Stems usually hold more than the title (a track number, an artist before a dash), so
the matcher tries each reading in turn: the stem whole, the stem without its track
number, and each artist-then-title split at a spaced dash. Every name is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_match as lmatch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above


def _readings(stem: str) -> list[tuple[str, str]]:
    """The (artist, title) pairs `filename_readings` gives for *stem*."""
    query = lm.TrackQuery("", stem, from_filename=True)
    return [(r.artist, r.title) for r in lmatch.filename_readings(query)]


class FilenameReadingsTest(fixtures.IsolatedCase):
    """The stem whole, then without its track number, then artist-and-title splits."""

    def test_a_bare_title_is_its_only_reading(self) -> None:
        self.assertEqual(_readings("The Keeper Counts"), [("", "The Keeper Counts")])

    def test_a_leading_track_number_is_dropped_in_the_second_reading(self) -> None:
        for stem in (
            "03 The Keeper Counts",
            "03 - The Keeper Counts",
            "03. The Keeper Counts",
            "03_The Keeper Counts",
            "1-03 The Keeper Counts",
            "1.03 The Keeper Counts",
        ):
            with self.subTest(stem=stem):
                self.assertEqual(
                    _readings(stem), [("", stem), ("", "The Keeper Counts")]
                )

    def test_an_artist_before_a_dash_is_read_as_the_artist(self) -> None:
        self.assertEqual(
            _readings("Invented Artist - The Keeper Counts"),
            [
                ("", "Invented Artist - The Keeper Counts"),
                ("Invented Artist", "The Keeper Counts"),
            ],
        )

    def test_number_artist_and_title_give_every_reading(self) -> None:
        stem = "03 - Invented Artist - The Keeper Counts"
        self.assertEqual(
            _readings(stem),
            [
                ("", stem),
                ("", "Invented Artist - The Keeper Counts"),
                ("Invented Artist", "The Keeper Counts"),
            ],
        )

    def test_each_earlier_part_is_tried_as_the_artist_but_never_a_number(self) -> None:
        stem = "Lamp Room - 2 - Invented Artist - The Keeper Counts"
        self.assertEqual(
            _readings(stem)[1:],
            [
                ("Lamp Room", "The Keeper Counts"),
                ("Invented Artist", "The Keeper Counts"),
            ],
        )

    def test_a_number_alone_is_not_stripped_to_nothing(self) -> None:
        self.assertEqual(_readings("99"), [("", "99")])
        self.assertEqual(
            _readings("99 Lanterns"), [("", "99 Lanterns"), ("", "Lanterns")]
        )

    def test_a_dash_with_nothing_after_it_gives_no_split(self) -> None:
        self.assertEqual(_readings("Invented Artist - "), [("", "Invented Artist - ")])

    def test_a_blank_stem_has_no_reading(self) -> None:
        self.assertEqual(_readings("  "), [])

    def test_the_querys_other_fields_are_kept(self) -> None:
        query = lm.TrackQuery(
            "", "03 - A - B", from_filename=True, album="Lamp Room", duration_s=200.0
        )
        for reading in lmatch.filename_readings(query):
            with self.subTest(reading=reading):
                self.assertEqual(reading.duration_s, 200.0)
                self.assertEqual(reading.album, "Lamp Room")
                self.assertTrue(reading.from_filename)


#: Stems in the shapes file names take: numbers, dashes, dots, words.
_STEMS = st.lists(
    st.one_of(
        st.text(min_size=1, max_size=8),
        st.sampled_from(["03", "1-03", " - ", " – ", ".", "_", "-", " ", "Song"]),
    ),
    max_size=10,
).map("".join)


class FilenameReadingsPropertyTest(fixtures.IsolatedCase):
    """Never raises; every reading has a title, and a split reading an artist too."""

    @given(st.one_of(st.text(max_size=200), _STEMS))
    def test_fuzz_filename_readings(self, stem: str) -> None:
        readings = lmatch.filename_readings(lm.TrackQuery("", stem))
        for reading in readings:
            self.assertTrue(reading.title.strip())
            self.assertEqual(reading.artist, reading.artist.strip())
        if stem.strip():
            self.assertEqual(readings[0], lm.TrackQuery("", stem))
        else:
            self.assertEqual(readings, ())
