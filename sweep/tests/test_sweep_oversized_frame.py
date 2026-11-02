"""The survey names a lyrics frame over the cap implausible under ``--refetch-implausible``.

A ``--refetch-implausible`` run judges a track holding a lyrics frame over the album
tool's lyrics cap implausible junk, and asks it again, so the survey names it as one
serving implausible lyrics, with the album tool's own reason, and counts it by what the
track holds beside that frame; so does ``--refetch-untimed``, whose run asks it again
too. Without either option the run ends such a track as its error, so the survey counts
it unreadable, as before. Every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mutagen.id3 import ID3, USLT

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_survey  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_cap import MAX_LYRICS_CHARS  # noqa: E402 - import follows the sys.path bootstrap above

#: Why the album tool judges the frame junk.
BIG_FRAME = f"a USLT frame is too large: over {MAX_LYRICS_CHARS} characters"


def oversized_track(album: Path, name: str, *, txt: str | bytes | None = None) -> Path:
    """`sweep_fixtures.add_track`, holding a USLT one word past the lyrics cap."""
    mp3 = sf.add_track(album, name, txt=txt)
    tags = ID3()
    tags.add(
        USLT(encoding=3, lang="eng", desc="", text="w " * (MAX_LYRICS_CHARS // 2 + 1))
    )
    tags.save(mp3)
    return mp3


class OversizedSurveyTest(sf.IsolatedCase):
    """Under the option it is implausible and counted; without it, unreadable."""

    def survey(
        self, *, implausible: bool, untimed: bool = False
    ) -> sweep_survey.AlbumSurvey:
        """Survey the album, with or without each refetch option."""
        return sweep_survey.survey_album(
            self.album,
            lang="eng",
            refetch_untimed=untimed,
            refetch_implausible=implausible,
        )

    def setUp(self) -> None:
        """An album to plant tracks in."""
        super().setUp()
        self.album = self.library / "Artist" / "Junk"

    def test_without_the_option_it_is_unreadable(self) -> None:
        oversized_track(self.album, "01.mp3")
        survey = self.survey(implausible=False)
        self.assertEqual(survey.unreadable, ("01.mp3 (FrameTooLargeError)",))
        self.assertEqual(survey.implausible, ())

    def test_under_the_option_it_is_implausible_and_counted_by_the_rest(self) -> None:
        oversized_track(self.album, "01.mp3")
        survey = self.survey(implausible=True)
        self.assertEqual(survey.unreadable, ())
        self.assertEqual(survey.implausible, (f"01.mp3 ({BIG_FRAME})",))
        self.assertEqual((survey.synced, survey.untimed, survey.none), (0, 0, 1))
        self.assertTrue(survey.needs_visit)

    def test_refetch_untimed_alone_names_it_implausible_too(self) -> None:
        oversized_track(self.album, "01.mp3")
        survey = self.survey(implausible=False, untimed=True)
        self.assertEqual(survey.unreadable, ())
        self.assertEqual(survey.implausible, (f"01.mp3 ({BIG_FRAME})",))

    def test_refetch_untimed_alone_judges_no_serving_lyrics(self) -> None:
        sf.add_track(self.album, "01.mp3", txt=sf.UNTIMED_TXT)
        survey = self.survey(implausible=False, untimed=True)
        self.assertEqual((survey.untimed, survey.implausible), (1, ()))

    def test_the_library_survey_hands_on_refetch_untimed(self) -> None:
        oversized_track(self.album, "01.mp3")
        (survey,) = sweep_survey.survey_library(
            self.library, lang="eng", refetch_untimed=True
        )
        self.assertEqual(survey.implausible, (f"01.mp3 ({BIG_FRAME})",))

    def test_a_txt_beside_it_counts(self) -> None:
        oversized_track(self.album, "01.mp3", txt=sf.UNTIMED_TXT)
        survey = self.survey(implausible=True)
        self.assertEqual((survey.synced, survey.untimed, survey.none), (0, 1, 0))

    def test_an_unreadable_txt_beside_it_holds_nothing(self) -> None:
        # The run asks it again either way; the sidecar it cannot read is no lyrics.
        oversized_track(self.album, "01.mp3", txt=sf.NOT_UTF8)
        survey = self.survey(implausible=True)
        self.assertEqual(survey.implausible, (f"01.mp3 ({BIG_FRAME})",))
        self.assertEqual(survey.none, 1)
