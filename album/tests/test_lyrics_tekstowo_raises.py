"""Tekstowo's lost cool-down write is still noticed when the run or compare then raises.

Each test drives the entry point on the real wiring with a hand-built provider table, so
no test reaches the network, and reads each notice where it is sent: main's own stream
capture would swallow stderr once the album raises. Every title and lyric is invented.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_compare  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_tekstowo_wiring import (  # noqa: E402 - import follows the sys.path bootstrap above
    KEY,
    UNTIL,
    UNTIMED,
    StoppingStep,
)

#: The notice a failed Tekstowo cool-down write gives.
LOST_WRITE = "tekstowo: could not write the cool-down file: invented write failure"
#: The key a YouTube Music step that already stopped sits under.
YTM_KEY = lm.ProviderKey(lm.Source.YOUTUBE_MUSIC, lm.LyricsKind.UNSYNCED)


@dataclass
class StoppedYouTube:
    """A YouTube Music step that stopped before the run and could not write its file."""

    def __call__(self, query: lm.TrackQuery) -> str | None:
        """No answer: the step stopped."""
        del query
        return None

    def provider_stop(self) -> lyc.Stop:
        """A stop whose cool-down write failed, so its report gives a notice."""
        return lyc.Stop("HTTP 429", UNTIL, "invented youtube write failure")


@dataclass
class Notices:
    """A stderr that keeps every notice, but refuses YouTube Music's when told to."""

    closed_for_youtube: bool = False
    lines: list[str] = field(default_factory=list)

    def __call__(self, text: str) -> None:
        """Keep *text*, or raise as a closed stream would for YouTube Music's notice."""
        if self.closed_for_youtube and text.startswith("youtube music: "):
            raise OSError("invented closed stderr")
        self.lines.append(text)


class _RaisingCase(cli_fakes.RealWiringCliCase):
    """A stopping Tekstowo step whose stop is followed by the album raising."""

    #: The album's tracks: ones the mode under test asks Tekstowo about.
    tracks: tuple[TrackSpec, ...] = UNTIMED

    def setUp(self) -> None:
        """The album; a Tekstowo step whose cool-down write fails."""
        super().setUp()
        fixtures.build_album(self.album, self.tracks)
        self.step = StoppingStep(write_error="invented write failure")
        self.providers = {KEY: self.step}
        self.notices = Notices()

    def youtube_lost(self) -> None:
        """Add a stopped YouTube Music step whose notice the stream refuses."""
        self.providers = {YTM_KEY: StoppedYouTube(), **self.providers}
        self.notices.closed_for_youtube = True

    def raising(self, *mode: str) -> None:
        """Run *mode* on the album with notices kept; the album's own failure raises."""
        # main's own stream capture would swallow stderr when the run raises, so each
        # notice is read where it is sent.
        with (
            mock.patch.object(album, "_notice", self.notices),
            self.assertRaisesRegex(RuntimeError, "invented album failure"),
        ):
            self.main("--album", str(self.album), *mode)
        self.assertIn(LOST_WRITE, self.notices.lines)


class RunRaisesTest(_RaisingCase):
    """A run that raises after Tekstowo stopped still notices the failed write."""

    tracks = (TrackSpec("01 One.mp3"), TrackSpec("02 Two.mp3"))

    def setUp(self) -> None:
        """Every manifest write after the stop raises."""
        super().setUp()
        self.overrides["write_manifest"] = self.failing_write

    def failing_write(self, run_dir: Path, manifest: lr.Manifest) -> None:
        """Write *manifest*, unless the step stopped: then the album fails."""
        if self.step.stop is not None:  # the failure comes after the stop
            raise RuntimeError("invented album failure")
        lmf.write_manifest(run_dir, manifest)

    def test_the_notice_survives_an_album_that_then_raises(self) -> None:
        self.raising()

    def test_a_lost_youtube_notice_still_gives_tekstowo_its_own(self) -> None:
        self.youtube_lost()
        self.raising()


class CompareRaisesTest(_RaisingCase):
    """A compare that raises after Tekstowo stopped still notices the failed write."""

    def setUp(self) -> None:
        """The real compare runs, then the album fails."""
        super().setUp()
        real = lyrics_compare.compare_album

        def failing_compare(*args: object, **kwargs: object) -> object:
            real(*args, **kwargs)  # type: ignore[arg-type] # the real call, passed through
            raise RuntimeError("invented album failure")

        self.enterContext(
            mock.patch.object(lyrics_compare, "compare_album", failing_compare)
        )

    def test_the_notice_survives_a_compare_that_then_raises(self) -> None:
        self.raising("--mode", "compare")
        self.assertEqual(len(self.step.calls), 1)

    def test_a_lost_youtube_notice_neither_hides_tekstowo_nor_the_failure(
        self,
    ) -> None:
        self.youtube_lost()
        self.raising("--mode", "compare")
