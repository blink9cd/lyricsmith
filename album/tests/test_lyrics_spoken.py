"""`lyrics_spoken`: whether an album looks spoken, by a genre tag or by most tracks heard.

The rule is pure (`spoken_reason`); the genres are read through the real tag stage
(`album_genres`), a track whose tags cannot be read giving none. Every name is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_spoken as sp  # noqa: E402 - import follows the sys.path bootstrap above


class SpokenReasonTest(fixtures.IsolatedCase):
    """The genre is named first, else the share heard not sung, else nothing."""

    def test_a_spoken_genre_is_named_as_tagged(self) -> None:
        genres = [("Rock",), (" spoken word ",)]
        self.assertEqual(
            sp.spoken_reason(genres, 0, 2), "spoken word (genre  spoken word )"
        )

    def test_the_genre_is_named_before_the_share(self) -> None:
        reason = sp.spoken_reason([("Comedy",)], 3, 3)
        self.assertEqual(reason, "spoken word (genre Comedy)")

    def test_every_built_in_no_lyrics_genre_is_spoken(self) -> None:
        for genre in ("Stand-up Comedy", "Stand-Up"):
            with self.subTest(genre=genre):
                self.assertIsNotNone(sp.spoken_reason([(genre,)], 0, 1))

    def test_a_genre_merely_holding_comedy_is_not_spoken(self) -> None:
        self.assertIsNone(sp.spoken_reason([("Musical Comedy",)], 0, 1))

    def test_more_than_half_heard_is_spoken(self) -> None:
        self.assertEqual(
            sp.spoken_reason([], 3, 5), "spoken word (3 of 5 tracks not sung)"
        )

    def test_half_heard_is_not(self) -> None:
        self.assertIsNone(sp.spoken_reason([], 2, 4))

    def test_an_album_of_no_track_is_not(self) -> None:
        self.assertIsNone(sp.spoken_reason([], 0, 0))


class AlbumGenresTest(fixtures.IsolatedCase):
    """Each track's genres through the real tag stage; an unreadable track gives none."""

    def test_an_unreadable_track_gives_no_genre(self) -> None:
        tagged = fixtures.build_silent_mp3(
            self.tmp / "01 Tagged.mp3", 5.0, genres=("Comedy",)
        )
        missing = self.tmp / "02 Missing.mp3"
        stages = album.real_wiring().stages
        self.assertEqual(sp.album_genres(stages, [missing, tagged]), [("Comedy",)])


if __name__ == "__main__":
    unittest.main()
