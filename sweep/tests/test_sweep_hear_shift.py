"""The shift test: a `not sung` transcript judged against every other text of its album.

On an invented album whose tracks hold timed and untimed sidecars: a transcript that
sings a sibling's text, timed or untimed, is a shift; one that sings none is a suspect
miss, its track's genre tags kept for the tally, unless the track's own stored text is
word for word a sibling's, which is a shift too; the track's own text is never one of
the others; a sibling that cannot be read is counted and passed over; and an album that
cannot be listed is not tested. Every name, word and genre here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import hear_fixtures as hf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_hear_shift as hs  # noqa: E402 - import follows the sys.path bootstrap above

_OUTCOME = hs.ShiftOutcome


class ShiftCase(sf.IsolatedCase):
    """An album of a heard track, a timed sibling and an untimed one."""

    def setUp(self) -> None:
        """Build the album."""
        super().setUp()
        self.album = self.library / "Invented Artist" / "Invented Album"
        self.track = sf.add_track(self.album, "01.mp3", lrc=hf.lrc_of(hf.song("ka")))
        sf.add_track(self.album, "02.mp3", lrc=hf.lrc_of(hf.song("lo")))
        sf.add_track(self.album, "03.mp3", txt=hf.song("mi"))
        self.stages = hs.tag_stages()

    def shift(self, transcript: str) -> hs.ShiftFound:
        """The shift test of this album's heard track on *transcript*."""
        return hs.shift_test(transcript, self.track, self.album, self.stages)


class ShiftTest(ShiftCase):
    """A shift is a sibling's text sung; a suspect miss is none sung."""

    def test_a_timed_siblings_text_sung_is_a_shift(self) -> None:
        found = self.shift(hf.song("lo"))
        self.assertIs(found.outcome, _OUTCOME.SHIFT)
        self.assertEqual(found.genres, ())

    def test_an_untimed_siblings_text_sung_is_a_shift(self) -> None:
        self.assertIs(self.shift(hf.song("mi")).outcome, _OUTCOME.SHIFT)

    def test_no_text_sung_is_a_suspect_miss(self) -> None:
        found = self.shift(hf.song("zu"))
        self.assertIs(found.outcome, _OUTCOME.SUSPECT)
        self.assertEqual(found.unread, 0)

    def test_the_tracks_own_text_is_not_one_of_the_others(self) -> None:
        self.assertIs(self.shift(hf.song("ka")).outcome, _OUTCOME.SUSPECT)

    def test_a_siblings_text_held_only_in_its_frame_is_one_of_the_others(self) -> None:
        # Read as the fix reads a sibling: no sidecar, its untimed frame served.
        hf.frame_text(sf.add_track(self.album, "04.mp3"), hf.song("pu"))
        found = self.shift(hf.song("pu"))
        self.assertIs(found.outcome, _OUTCOME.SHIFT)
        self.assertEqual(found.unread, 0)

    def test_the_tracks_own_unreadable_text_is_not_counted(self) -> None:
        self.track.with_suffix(".txt").write_bytes(sf.NOT_UTF8)
        self.assertEqual(self.shift(hf.song("zu")).unread, 0)

    def test_a_placeholder_and_a_track_with_no_text_are_passed_over(self) -> None:
        sf.add_track(self.album, "04.mp3", txt="[Instrumental]\n")
        sf.add_track(self.album, "05.mp3")
        found = self.shift(hf.song("zu"))
        self.assertIs(found.outcome, _OUTCOME.SUSPECT)
        self.assertEqual(found.unread, 0)

    def test_a_sibling_that_cannot_be_read_is_counted_and_passed_over(self) -> None:
        sf.add_track(self.album, "04.mp3", txt=sf.NOT_UTF8)
        found = self.shift(hf.song("zu"))
        self.assertIs(found.outcome, _OUTCOME.SUSPECT)
        self.assertEqual(found.unread, 1)
        self.assertIs(self.shift(hf.song("lo")).outcome, _OUTCOME.SHIFT)

    def test_holding_a_siblings_text_it_keeps_too_is_a_shift(self) -> None:
        # The track holds a neighbor's text word for word while the neighbor
        # keeps it, so its transcript sings no sibling's text and is a shift anyway.
        held = self.album / "05.mp3"
        sf.add_track(self.album, "04.mp3", txt=hf.song("pu"))
        sf.add_track(self.album, "05.mp3", txt=hf.song("pu"))
        found = hs.shift_test(hf.song("zu"), held, self.album, self.stages)
        self.assertIs(found.outcome, _OUTCOME.SHIFT)
        self.assertEqual(found.genres, ())

    def test_a_timed_text_a_sibling_holds_untimed_is_a_shift(self) -> None:
        # The heard track's own timed words equal an untimed sibling's text.
        sf.add_track(self.album, "04.mp3", txt=hf.song("ka"))
        self.assertIs(self.shift(hf.song("zu")).outcome, _OUTCOME.SHIFT)

    def test_holding_a_text_no_sibling_holds_is_still_a_suspect_miss(self) -> None:
        self.track.with_suffix(".lrc").unlink()
        sf.add_track(self.album, "04.mp3", txt="[Instrumental]\n")
        self.assertIs(self.shift(hf.song("zu")).outcome, _OUTCOME.SUSPECT)

    def test_two_wordless_texts_alike_are_no_shift(self) -> None:
        # A text of no words fingerprints as every other: it names no song to share.
        held = sf.add_track(self.album, "04.mp3", txt="...\n")
        sf.add_track(self.album, "05.mp3", txt="...\n")
        found = hs.shift_test(hf.song("zu"), held, self.album, self.stages)
        self.assertIs(found.outcome, _OUTCOME.SUSPECT)

    def test_an_album_that_cannot_be_listed_is_not_tested(self) -> None:
        gone = self.library / "Invented Artist" / "Gone"
        found = hs.shift_test(hf.song("lo"), gone / "01.mp3", gone, self.stages)
        self.assertIs(found.outcome, _OUTCOME.UNTESTED)


class GenreTest(ShiftCase):
    """A suspect miss keeps its track's genre tags; an untagged one is named so."""

    def test_a_suspect_miss_keeps_each_of_its_genre_tags(self) -> None:
        hf.tag_genre(self.track, "Invented Noise", "Invented Drone")
        found = self.shift(hf.song("zu"))
        self.assertEqual(found.genres, ("Invented Noise", "Invented Drone"))

    def test_an_untagged_or_unreadable_track_has_no_genre_tag(self) -> None:
        self.assertEqual(self.shift(hf.song("zu")).genres, (hs.NO_GENRE,))
        self.track.write_bytes(b"ID3\x04not a tag")
        self.assertEqual(self.shift(hf.song("zu")).genres, (hs.NO_GENRE,))

    def test_genres_are_tallied_by_tag_across_suspect_misses(self) -> None:
        found = (
            hs.ShiftFound(_OUTCOME.SUSPECT, 0, ("Invented Noise",)),
            hs.ShiftFound(_OUTCOME.SUSPECT, 0, ("Invented Noise", "Invented Drone")),
            hs.ShiftFound(_OUTCOME.SUSPECT, 0, (hs.NO_GENRE,)),
            hs.ShiftFound(_OUTCOME.SHIFT, 0, ()),
        )
        self.assertEqual(
            hs.genre_tally(found),
            {"Invented Noise": 2, "Invented Drone": 1, hs.NO_GENRE: 1},
        )
