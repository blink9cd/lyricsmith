"""The search query a track is asked with: its artist and title, and its album and length.

LRCLIB's exact lookup needs the album and the length, and the matcher compares a hit's
length to the track's, so both travel from the tags into the query and survive the
stripped-title retry. Every track is a silent MP3 built at test time, with invented tags.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mutagen.id3 import ID3, TALB

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run as lrun  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags as lt  # noqa: E402 - import follows the sys.path bootstrap above

#: A built track's length; mutagen reads it back within a frame or two.
SECONDS = 12.0


def _with_album(path: Path, album: str) -> Path:
    """Add an album tag *album* to *path*'s existing ID3 tag."""
    tags = ID3(path)
    tags.add(TALB(encoding=3, text=[album]))
    tags.save(path)
    return path


class AlbumTagTest(fixtures.IsolatedCase):
    """The album tag is read like the artist and title: blank or absent is None."""

    def test_the_album_tag_is_read(self) -> None:
        path = fixtures.build_silent_mp3(self.tmp / "a.mp3", SECONDS)
        self.assertEqual(
            lt.read_tags(_with_album(path, "Lamp Room")).album, "Lamp Room"
        )

    def test_a_missing_or_blank_album_is_none(self) -> None:
        bare = fixtures.build_silent_mp3(self.tmp / "b.mp3", SECONDS)
        blank = _with_album(fixtures.build_silent_mp3(self.tmp / "c.mp3", SECONDS), " ")
        for path in (bare, blank):
            with self.subTest(path=path.name):
                self.assertIsNone(lt.read_tags(path).album)


class TrackQueryTest(fixtures.IsolatedCase):
    """The query carries the album and the length, whichever name it searches by."""

    def test_a_tagged_track_carries_its_album_and_length(self) -> None:
        tags = lm.TrackTags(
            "Invented Artist", "Lamp Song", 12.5, (2, 4), (), album="Lamp Room"
        )
        self.assertEqual(
            lrun.track_query(tags, Path("01 x.mp3")),
            lm.TrackQuery(
                "Invented Artist", "Lamp Song", album="Lamp Room", duration_s=12.5
            ),
        )

    def test_a_filename_query_still_carries_the_length(self) -> None:
        tags = lm.TrackTags(None, None, 12.5, (2, 4), (), album="Lamp Room")
        self.assertEqual(
            lrun.track_query(tags, Path("01 x.mp3")),
            lm.TrackQuery(
                "", "01 x", from_filename=True, album="Lamp Room", duration_s=12.5
            ),
        )


class StrippedQueryTest(fixtures.IsolatedCase):
    """The stripped-title retry asks with the same album and length."""

    def test_the_retry_keeps_the_album_and_the_length(self) -> None:
        query = lm.TrackQuery(
            "Invented Artist", "Lamp Song (Live)", album="Lamp Room", duration_s=12.5
        )
        provider = fixtures.empty()
        key = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.SYNCED)
        lf.fetch_lyrics(query, lm.SidecarState(None), {key: provider}, deadline_s=5.0)
        self.assertEqual(
            provider.calls[1],
            lm.TrackQuery(
                "Invented Artist",
                "Lamp Song",
                stripped=True,
                album="Lamp Room",
                duration_s=12.5,
            ),
        )
