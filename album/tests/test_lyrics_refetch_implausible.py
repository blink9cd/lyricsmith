"""Telling a track whose serving lyrics are implausible, as ``--refetch-implausible`` asks.

`implausible_target` picks a track by the lyrics that would serve it: a timed ``.lrc``
first, then its synced frames, then a ``.txt``, then its untimed frames. Synced
lyrics are implausible past the track's end or past the word rate, untimed ones past
the word rate; `ends short` is no reason. The sidecar side and the frame side are each
judged apart, so a miss removes only the implausible one. `afresh_target` is the union
a run asks with, a stored placeholder first. The manifest records the option per track,
and a manifest from before it reads as without it. Sidecars are written into a
temporary album; every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refetch  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_refetch import AlbumCase  # noqa: E402 - import follows the sys.path bootstrap above

#: The track's length; a song's lines end inside it.
_SECONDS = 30.0
#: Lines that run a minute in: another, longer song's on this track.
_LATE_LRC = "[00:01.00]A lantern swings\n[01:00.00]over the quay\n"
_LATE_SYLT = tag_builders.sylt((1000, "A lantern swings"), (60000, "over the quay"))
_LATE = "last line 60.000 s, track 30.000 s"
#: Lines that fit the track.
_GOOD_LRC = "[00:01.00]A gull sits on the harbor post\n[00:04.00]and waits\n"
_GOOD_SYLT = tag_builders.sylt((1000, "A gull sits"), (4000, "on the harbor post"))
_GOOD_TXT = "A gull sits on the harbor post\n"
#: A saved page: far past the word rate over the track.
_PAGE = "la " * 400
_PAGE_SYLT = tag_builders.sylt((1000, _PAGE))
_PAGE_RATE = "13.333 words per second: 400 words in 30.000 s"
#: Bytes that are not UTF-8.
_NOT_UTF8 = b"caf\xe9 by the quay\n"


def _target(
    sidecar: str | None = None, frames: str | None = None
) -> lyrics_refetch.RefetchTarget:
    """The target `implausible_target` gives, *sidecar* and *frames* saying why."""
    return lyrics_refetch.RefetchTarget(
        None, sidecar, frames, refetch=False, refetch_implausible=True
    )


class ImplausibleCase(AlbumCase):
    """The album's one track judged through the real stages."""

    def tags(
        self, frames: tuple[lm.FrameRecord, ...], duration_s: float | None
    ) -> lm.TrackTags:
        """The track's tags: *frames* and *duration_s*."""
        return lm.TrackTags("Invented Artist", "Gull", duration_s, (2, 4), frames)

    def target(
        self,
        *frames: lm.FrameRecord,
        duration_s: float | None = _SECONDS,
    ) -> lyrics_refetch.RefetchTarget | None:
        """The track as ``--refetch-implausible`` picks it, or None."""
        stages = album.real_wiring().stages
        tags = self.tags(frames, duration_s)
        return lyrics_refetch.implausible_target(
            stages, self.album, self.mp3, tags, "eng"
        )


class ImplausibleTargetTest(ImplausibleCase):
    """Which tracks are picked, and which side of each a miss removes."""

    def test_synced_frames_past_the_end(self) -> None:
        self.assertEqual(self.target(_LATE_SYLT), _target(frames=_LATE))

    def test_synced_frames_too_wordy(self) -> None:
        self.assertEqual(self.target(_PAGE_SYLT), _target(frames=_PAGE_RATE))

    def test_a_timed_lrc_past_the_end(self) -> None:
        self.write(".lrc", _LATE_LRC.encode())
        self.assertEqual(self.target(), _target(sidecar=_LATE))

    def test_untimed_frames_too_wordy(self) -> None:
        self.assertEqual(
            self.target(tag_builders.uslt(_PAGE)), _target(frames=_PAGE_RATE)
        )

    def test_a_txt_too_wordy(self) -> None:
        self.write(".txt", _PAGE.encode())
        self.assertEqual(self.target(), _target(sidecar=_PAGE_RATE))

    def test_plausible_lyrics_are_not_picked(self) -> None:
        self.assertIsNone(self.target(_GOOD_SYLT))
        self.assertIsNone(self.target(tag_builders.uslt(_GOOD_TXT)))
        self.write(".lrc", _GOOD_LRC.encode())
        self.assertIsNone(self.target())

    def test_no_lyrics_are_not_picked(self) -> None:
        self.assertIsNone(self.target())

    def test_lyrics_ending_short_are_not_picked(self) -> None:
        # The good lines end four seconds into a ten-minute track: ends short, and
        # that is no reason to ask again.
        self.assertIsNone(self.target(_GOOD_SYLT, duration_s=600.0))

    def test_an_unreadable_duration_picks_nothing(self) -> None:
        for duration_s in (None, 0.0, float("nan")):
            with self.subTest(duration_s=duration_s):
                self.assertIsNone(self.target(_LATE_SYLT, duration_s=duration_s))

    def test_a_late_lrc_goes_and_plausible_frames_stay(self) -> None:
        self.write(".lrc", _LATE_LRC.encode())
        self.assertEqual(self.target(_GOOD_SYLT), _target(sidecar=_LATE))

    def test_late_frames_go_and_a_plausible_txt_stays(self) -> None:
        self.write(".txt", _GOOD_TXT.encode())
        self.assertEqual(self.target(_LATE_SYLT), _target(frames=_LATE))

    def test_a_plausible_lrc_serves_so_late_frames_are_not_picked(self) -> None:
        self.write(".lrc", _GOOD_LRC.encode())
        self.assertIsNone(self.target(_LATE_SYLT))

    def test_both_sides_late_go_together(self) -> None:
        self.write(".lrc", _LATE_LRC.encode())
        target = self.target(_LATE_SYLT)
        self.assertEqual(target, _target(_LATE, _LATE))
        assert target is not None
        self.assertEqual(target.implausible, _LATE)

    def test_a_wordy_txt_beside_late_frames_goes_with_them(self) -> None:
        self.write(".txt", _PAGE.encode())
        self.assertEqual(self.target(_LATE_SYLT), _target(_PAGE_RATE, _LATE))

    def test_a_sidecar_that_cannot_be_read_picks_nothing(self) -> None:
        self.write(".lrc", _NOT_UTF8)
        self.assertIsNone(self.target(_LATE_SYLT))

    def test_the_target_is_recorded_as_this_option(self) -> None:
        state = lyrics_outcomes.TrackState()
        _target(frames=_LATE).record(state)
        self.assertEqual(
            (state.refetch_untimed, state.refetch_implausible, state.remove_frames),
            (False, True, True),
        )


class AfreshTargetTest(ImplausibleCase):
    """The union a run asks with: a placeholder, then each option's own target."""

    def afresh(
        self, *frames: lm.FrameRecord, untimed: bool, implausible: bool
    ) -> lyrics_refetch.RefetchTarget | None:
        """The track's target under the options given."""
        stages = album.real_wiring().stages
        return lyrics_refetch.afresh_target(
            stages,
            self.album,
            self.mp3,
            self.tags(frames, _SECONDS),
            "eng",
            refetch_untimed=untimed,
            refetch_implausible=implausible,
        )

    def test_an_untimed_track_is_the_untimed_option_s_alone(self) -> None:
        self.write(".txt", _GOOD_TXT.encode())
        self.assertIsNone(self.afresh(untimed=False, implausible=True))
        for implausible in (False, True):
            with self.subTest(implausible=implausible):
                target = self.afresh(untimed=True, implausible=implausible)
                self.assertEqual(target, lyrics_refetch.RefetchTarget())

    def test_late_synced_frames_are_the_implausible_option_s_alone(self) -> None:
        self.assertIsNone(self.afresh(_LATE_SYLT, untimed=True, implausible=False))
        for untimed in (False, True):
            with self.subTest(untimed=untimed):
                target = self.afresh(_LATE_SYLT, untimed=untimed, implausible=True)
                self.assertEqual(target, _target(frames=_LATE))

    def test_a_stored_placeholder_needs_no_option(self) -> None:
        self.write(".txt", b"[au:comedy]\n")
        target = self.afresh(untimed=False, implausible=False)
        assert target is not None
        self.assertEqual(target.implausible, lyrics_refetch.PLACEHOLDER)


class ManifestFieldTest(fixtures.IsolatedCase):
    """The option is recorded per track, and an older manifest reads as without it."""

    def after(self, document: dict[str, object]) -> lr.TrackOutcome | None:
        """The first track's outcome, read back from *document*."""
        return lmf.parse_manifest(mb.encode(document)).tracks[0].after

    def test_the_field_round_trips(self) -> None:
        after = self.after(mb.document())
        assert after is not None
        self.assertTrue(after.refetch_implausible)

    def test_a_manifest_from_before_the_option_reads_as_without_it(self) -> None:
        document = mb.document()
        del cast(dict[str, object], mb.first_track(document)["after"])[
            "refetch_implausible"
        ]
        after = self.after(document)
        assert after is not None
        self.assertFalse(after.refetch_implausible)
