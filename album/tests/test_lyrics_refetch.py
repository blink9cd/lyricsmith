"""Telling an untimed-only track, which of its sidecars could not be read, and why not.

`unreadable_txt` names the error of the ``.txt`` that stops a sidecar plan, and nothing
else: a plan that reads, and one stopped by its ``.lrc``, name none. `refetch_target`
asks it only once the plan has failed, so the readable case is reached here alone. It
also says which old untimed lyrics are implausible, the sidecar and the frames each
judged alone: a ``.txt`` too large to read, or lyrics past the check's word rate over
the track's duration. Sidecars are written into a temporary album; every lyric is
invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refetch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above

#: Bytes that are not UTF-8: a Latin-1 e-acute in an otherwise plain line.
_NOT_UTF8 = b"caf\xe9 by the quay\n"
_PLAIN = "A gull sits on the harbor post\n"
_TIMED = "[00:01.00]A gull sits on the harbor post\n"
#: A track's length, and words past the most a song sings in it: a saved page.
_SECONDS = 30.0
_PAGE = "la " * 400
#: Why the page is implausible over the track's length, as the check row says it.
_PAGE_RATE = "13.333 words per second: 400 words in 30.000 s"
#: More characters than any sidecar may hold, in bytes a backup still copies.
_OVER_CAP = "la " * (lr.MAX_LYRICS_CHARS // 3 + 1)
_TOO_LARGE = f"01 Gull.txt is too large: over {lr.MAX_LYRICS_CHARS} characters"


class AlbumCase(fixtures.IsolatedCase):
    """An album folder with one MP3 name in it, and its sidecars written by each test."""

    def setUp(self) -> None:
        """An album folder with one MP3 name in it; the MP3 itself is never read."""
        super().setUp()
        self.album = self.tmp / "album"
        self.album.mkdir()
        self.mp3 = self.album / "01 Gull.mp3"

    def write(self, suffix: str, data: bytes) -> None:
        """Write *data* as the sidecar with *suffix*."""
        self.mp3.with_suffix(suffix).write_bytes(data)


class UnreadableTxtTest(AlbumCase):
    """Only a ``.txt`` that stops the plan is named."""

    def unreadable(self) -> str | None:
        """What `unreadable_txt` says of this album's track, as text."""
        error = lyrics_sidecar.unreadable_txt(self.mp3, self.album)
        return None if error is None else str(error)

    def test_a_plan_that_reads_names_nothing(self) -> None:
        self.write(".txt", _PLAIN.encode())
        self.assertIsNone(self.unreadable())

    def test_no_sidecar_names_nothing(self) -> None:
        self.assertIsNone(self.unreadable())

    def test_an_undecodable_txt_alone_is_named(self) -> None:
        self.write(".txt", _NOT_UTF8)
        reason = self.unreadable()
        assert reason is not None
        self.assertTrue(reason.startswith("01 Gull.txt is not UTF-8"), reason)

    def test_an_undecodable_lrc_names_nothing(self) -> None:
        self.write(".lrc", _NOT_UTF8)
        self.write(".txt", _NOT_UTF8)
        self.assertIsNone(self.unreadable())

    def test_a_txt_beside_a_timed_lrc_is_never_read(self) -> None:
        self.write(".lrc", _TIMED.encode())
        self.write(".txt", _NOT_UTF8)
        self.assertIsNone(self.unreadable())


class WordedSyltTest(fixtures.IsolatedCase):
    """A worded SYLT in any language or time unit; stamps alone are none."""

    def test_each_kind_of_frame(self) -> None:
        cases = {
            "worded": ((tag_builders.sylt((1000, "A gull")),), True),
            "mpeg frames": (
                (tag_builders.sylt((1000, "A gull"), time_format=1),),
                True,
            ),
            "another language": (
                (tag_builders.sylt((1000, "A gull"), lang="fra"),),
                True,
            ),
            "stamps alone": ((tag_builders.sylt((1000, " ")),), False),
            "a uslt": ((tag_builders.uslt(_PLAIN),), False),
            "nothing": ((), False),
        }
        for name, (frames, expected) in cases.items():
            with self.subTest(name):
                self.assertIs(lyrics_refetch.worded_sylt(frames), expected)


class RefetchTargetImplausibleTest(AlbumCase):
    """Which old untimed lyrics a refetch would remove when no provider answers."""

    def target(
        self,
        frames: tuple[lm.FrameRecord, ...] = (),
        duration_s: float | None = _SECONDS,
    ) -> lyrics_refetch.RefetchTarget | None:
        """The track as a refetch asks it again, through the real stages."""
        stages = album.real_wiring().stages
        tags = lm.TrackTags("Invented Artist", "Gull", duration_s, (2, 4), frames)
        return lyrics_refetch.refetch_target(stages, self.album, self.mp3, tags, "eng")

    def test_plausible_lyrics_are_not_implausible(self) -> None:
        self.write(".txt", _PLAIN.encode())
        self.assertEqual(self.target(), lyrics_refetch.RefetchTarget())

    def test_a_page_in_a_txt_is_implausible_by_its_rate(self) -> None:
        self.write(".txt", _PAGE.encode())
        self.assertEqual(self.target(), lyrics_refetch.RefetchTarget(None, _PAGE_RATE))

    def test_a_page_in_an_untimed_lrc_is_implausible(self) -> None:
        self.write(".lrc", _PAGE.encode())
        self.assertEqual(self.target(), lyrics_refetch.RefetchTarget(None, _PAGE_RATE))

    def test_a_page_in_a_uslt_alone_is_implausible(self) -> None:
        target = self.target((tag_builders.uslt(_PAGE),))
        expected = lyrics_refetch.RefetchTarget(frames_implausible=_PAGE_RATE)
        self.assertEqual(target, expected)

    def test_a_page_in_a_uslt_beside_a_plausible_txt_is_judged_alone(self) -> None:
        self.write(".txt", _PLAIN.encode())
        target = self.target((tag_builders.uslt(_PAGE),))
        expected = lyrics_refetch.RefetchTarget(frames_implausible=_PAGE_RATE)
        self.assertEqual(target, expected)

    def test_a_page_in_a_txt_beside_a_plausible_uslt_is_judged_alone(self) -> None:
        self.write(".txt", _PAGE.encode())
        target = self.target((tag_builders.uslt(_PLAIN),))
        self.assertEqual(target, lyrics_refetch.RefetchTarget(None, _PAGE_RATE))

    def test_two_pages_say_why_once(self) -> None:
        self.write(".txt", _PAGE.encode())
        target = self.target((tag_builders.uslt(_PAGE),))
        self.assertEqual(
            target, lyrics_refetch.RefetchTarget(None, _PAGE_RATE, _PAGE_RATE)
        )
        assert target is not None
        self.assertEqual(target.implausible, _PAGE_RATE)

    def test_an_unreadable_duration_cannot_decide(self) -> None:
        self.write(".txt", _PAGE.encode())
        for duration_s in (None, 0.0, float("nan")):
            with self.subTest(duration_s=duration_s):
                target = self.target(duration_s=duration_s)
                self.assertEqual(target, lyrics_refetch.RefetchTarget())

    def test_a_txt_too_large_to_read_is_implausible(self) -> None:
        self.write(".txt", _OVER_CAP.encode())
        self.assertEqual(
            self.target(duration_s=None),
            lyrics_refetch.RefetchTarget(_TOO_LARGE, _TOO_LARGE),
        )

    def test_a_txt_too_large_and_a_page_uslt_name_both(self) -> None:
        self.write(".txt", _OVER_CAP.encode())
        target = self.target((tag_builders.uslt(_PAGE),))
        assert target is not None
        self.assertEqual(target.implausible, f"{_TOO_LARGE}; {_PAGE_RATE}")

    def test_a_txt_too_large_keeps_a_plausible_uslt(self) -> None:
        self.write(".txt", _OVER_CAP.encode())
        target = self.target((tag_builders.uslt(_PLAIN),))
        self.assertEqual(target, lyrics_refetch.RefetchTarget(_TOO_LARGE, _TOO_LARGE))

    def test_a_txt_not_utf8_is_not_implausible(self) -> None:
        self.write(".txt", _NOT_UTF8)
        target = self.target()
        assert target is not None
        self.assertIsNone(target.implausible)
        self.assertIsNotNone(target.unreadable)

    def test_a_page_uslt_beside_a_txt_not_utf8_is_implausible(self) -> None:
        self.write(".txt", _NOT_UTF8)
        target = self.target((tag_builders.uslt(_PAGE),))
        assert target is not None
        self.assertEqual(
            (target.sidecar_implausible, target.frames_implausible),
            (None, _PAGE_RATE),
        )
