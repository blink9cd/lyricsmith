"""lyrics_genre's matcher and list, and read_tags' genres from a track's TCON frame."""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from mutagen.id3 import ID3, TCON

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genre as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags as lt  # noqa: E402 - import follows the sys.path bootstrap above

# A registered profile, never an @settings decorator: a fuzzing campaign that re-applies
# its own settings to each property is refused by Hypothesis on a decorated one. The
# name and settings are test_lyrics_model's, so whichever suite loads first, the other's
# properties run under the same profile. deadline=None: a per-example wall-clock
# deadline measures machine load, not correctness. too_slow is suppressed because a
# cold cache's first text draw builds Hypothesis's unicode tables inside that health
# check's window.
settings.register_profile(
    "album_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("album_properties")


class ListTest(fixtures.IsolatedCase):
    """The built-in list is the three stand-up and spoken-word genres, in that order."""

    def test_the_built_in_list(self) -> None:
        self.assertEqual(
            lg.NO_LYRICS_GENRES, ("Stand-up Comedy", "Stand-Up", "Spoken Word")
        )


class MatcherTest(fixtures.IsolatedCase):
    """A genre marks a track when it equals a listed one, whole, case and spaces aside."""

    def test_an_exact_genre_marks_and_is_returned_as_tagged(self) -> None:
        got = lg.no_lyrics_genre(("Stand-up Comedy",), lg.NO_LYRICS_GENRES)
        self.assertEqual(got, "Stand-up Comedy")

    def test_case_and_surrounding_spaces_are_ignored(self) -> None:
        got = lg.no_lyrics_genre(("  spoken WORD ",), lg.NO_LYRICS_GENRES)
        self.assertEqual(got, "  spoken WORD ")

    def test_the_first_marking_genre_of_several_is_returned(self) -> None:
        genres = ("Rock", "Stand-Up", "Spoken Word")
        self.assertEqual(lg.no_lyrics_genre(genres, lg.NO_LYRICS_GENRES), "Stand-Up")

    def test_a_genre_only_containing_an_entry_does_not_mark(self) -> None:
        for genre in ("Comedy", "Stand-up Comedy Rock", "Musical Comedy"):
            with self.subTest(genre=genre):
                self.assertIsNone(lg.no_lyrics_genre((genre,), lg.NO_LYRICS_GENRES))

    def test_no_genres_or_an_empty_list_marks_nothing(self) -> None:
        self.assertIsNone(lg.no_lyrics_genre((), lg.NO_LYRICS_GENRES))
        self.assertIsNone(lg.no_lyrics_genre(("Stand-Up",), ()))

    def test_a_listed_entry_with_spaces_still_matches(self) -> None:
        self.assertEqual(
            lg.no_lyrics_genre(("Audiobook",), (" audiobook ",)), "Audiobook"
        )


#: Whitespace the matcher trims from either side of a genre or a listed entry.
_PADDING = st.sampled_from(("", " ", "  ", "\t", " \t"))


@st.composite
def _perturbed(draw: st.DrawFn, text: str) -> str:
    """*text* case-folded or not, then padded on either side."""
    # casefold, not upper: upper() of some letters (a dotless i) folds differently.
    cased = draw(st.sampled_from((text, text.casefold())))
    return draw(_PADDING) + cased + draw(_PADDING)


@st.composite
def _genres_and_list(draw: st.DrawFn) -> tuple[list[str], list[str], bool]:
    """A list, genres some of which are drawn from it, and whether any one was."""
    entries = draw(st.lists(st.text(max_size=40), max_size=5))
    listed = [draw(_perturbed(entry)) for entry in entries]
    drawn: list[tuple[str, bool]] = []
    for _ in range(draw(st.integers(min_value=0, max_value=5))):
        from_list = draw(st.booleans())
        if entries and from_list:
            drawn.append((draw(_perturbed(draw(st.sampled_from(entries)))), True))
        else:
            drawn.append((draw(st.text(max_size=40)), False))
    return [g for g, _ in drawn], listed, any(hit for _, hit in drawn)


class MatcherPropertyTest(fixtures.IsolatedCase):
    """The first genre a listed entry matches wins, case and surrounding spaces aside.

    Some genres are drawn from the list itself, so a match is really exercised.
    """

    @given(_genres_and_list())
    def test_fuzz_no_lyrics_genre(
        self, case: tuple[list[str], list[str], bool]
    ) -> None:
        genres, listed, any_drawn = case
        got = lg.no_lyrics_genre(genres, listed)
        folded = {entry.strip().casefold() for entry in listed}
        first = next((g for g in genres if g.strip().casefold() in folded), None)
        self.assertEqual(got, first)
        if any_drawn:
            self.assertIsNotNone(got)
        padded = [f"  {genre.casefold()}\t" for genre in genres]
        again = lg.no_lyrics_genre(padded, listed)
        self.assertEqual(got is None, again is None)


class ReadGenresTest(fixtures.IsolatedCase):
    """read_tags reads TCON through mutagen's genres: split, numeric references named."""

    def _track(self, genres: tuple[str, ...], minor: int = 4) -> Path:
        return fixtures.build_silent_mp3(
            self.tmp / "01.mp3", 5.0, genres=genres, id3_version=(2, minor)
        )

    def test_no_tcon_reads_as_no_genres(self) -> None:
        self.assertEqual(lt.read_tags(self._track(())).genres, ())

    def test_several_values_in_one_v24_frame_are_split(self) -> None:
        got = lt.read_tags(self._track(("Rock", "Stand-up Comedy"))).genres
        self.assertEqual(got, ("Rock", "Stand-up Comedy"))

    def test_a_v23_frame_of_several_values_reads_as_one_joined_value(self) -> None:
        # mutagen saves several v2.3 values joined by "/", and genres does not split
        # them back, so the joined value is matched whole and marks nothing.
        got = lt.read_tags(self._track(("Rock", "Stand-up Comedy"), minor=3)).genres
        self.assertEqual(got, ("Rock/Stand-up Comedy",))

    def test_an_id3v1_numeric_reference_is_named(self) -> None:
        path = self._track(())
        tags = ID3(path)
        tags.add(TCON(encoding=3, text=["(57)"]))
        tags.save(path, v2_version=3)
        self.assertEqual(lt.read_tags(path).genres, ("Comedy",))

    def _v1_only(self, genre_byte: int) -> Path:
        """A track holding only an ID3v1 tag, its genre byte *genre_byte*."""
        path = fixtures.build_silent_mp3(self.tmp / "01.mp3", 5.0, id3_version=None)
        fields = b"Invented".ljust(30, b"\0") + b"\0" * 64 + b"\0" * 30
        with path.open("ab") as handle:
            handle.write(b"TAG" + fields + bytes((genre_byte,)))
        return path

    def test_an_id3v1_only_genre_is_named(self) -> None:
        # mutagen loads a v1-only file's genre byte as a numeric TCON, named by genres.
        self.assertEqual(lt.read_tags(self._v1_only(57)).genres, ("Comedy",))

    def test_an_id3v1_unset_genre_byte_reads_as_no_genre(self) -> None:
        # 255 is ID3v1's "no genre": mutagen loads no TCON frame for it at all.
        self.assertEqual(lt.read_tags(self._v1_only(255)).genres, ())

    def test_a_blank_genre_is_dropped(self) -> None:
        self.assertEqual(lt.read_tags(self._track(("  ",))).genres, ())
