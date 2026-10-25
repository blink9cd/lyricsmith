"""The embed's pre-flight: an answer the writer cannot encode is refused before any change.

Three answers pass every earlier stage and still cannot be embedded: a `SYLT` stamp past
the frame's four-byte range, a lone surrogate no Unicode encoding writes, and a NUL,
which ends an ID3 text field early. A fresh run used to strip the track's old lyrics
before the embed found that out, leaving the track bare until an undo; and a NUL wrote a
truncated frame that only the read-back caught. The pre-flight builds the frames the
writer would, with no I/O, and refuses all three first, so the track keeps its lyrics
and sidecars exactly as they were. The fetch stage is faked to hand each answer over
directly; every other stage is real. Every lyric is invented.
"""

from __future__ import annotations

import struct
import sys
import unittest
from collections import Counter
from pathlib import Path

from mutagen import MutagenError

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_frames  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags as lt  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: The first millisecond a `SYLT` stamp's four bytes cannot hold.
_PAST_SYLT_RANGE_MS = 2**32
#: A UTF-16 high surrogate with no low one after it: no encoding writes it.
_LONE_SURROGATE = "\ud800"
#: Each answer the writer cannot encode, by what is wrong with it.
_BAD_ANSWERS: dict[str, lm.Lyrics] = {
    "stamp out of range": lm.SyncedLyrics(
        (
            lm.SyncedLine(1000, "A lamp is lit"),
            lm.SyncedLine(_PAST_SYLT_RANGE_MS, "and never out"),
        )
    ),
    "lone surrogate": lm.PlainLyrics(f"A gull calls {_LONE_SURROGATE} twice\n"),
    "NUL": lm.PlainLyrics("A rope pulls taut\x00and the rest is cut\n"),
}
#: What each bad answer raises: mutagen's own save errors, and the NUL's own refusal.
_REFUSED_AS: dict[str, type[Exception]] = {
    "stamp out of range": struct.error,
    "lone surrogate": MutagenError,
    "NUL": lyrics_frames.UnencodableLyricsError,
}
#: The old lyrics a track holds before the run.
_OLD_LRC = "[00:01.00]A harbor bell rings twice\n"
_OLD_TXT = "A harbor bell rings twice\n"
_FRAMES = (
    tag_builders.uslt("A verse kept in the tag\n"),
    tag_builders.sylt((1000, "A timed line"), (4000, "and another")),
)


def _fetching(lyrics: lm.Lyrics) -> lyrics_stages.FetchLyrics:
    """A fetch stage that answers every track with *lyrics*, from LRCLIB."""

    def fetch(
        query: lm.TrackQuery,
        sidecar: lm.SidecarState,
        providers: lm.ProviderTable,
        *,
        deadline_s: float,
        embedded: lyrics_inventory.EmbeddedLyrics,
        duration_s: float | None,
    ) -> lm.FetchResult:
        return lm.FetchResult(lyrics, lm.Source.LRCLIB, query, ())

    return fetch


class CheckEncodableTest(fixtures.IsolatedCase):
    """The pre-flight alone: pure, and refusing exactly what the writer cannot encode."""

    def test_each_bad_answer_is_refused_as_the_save_would_refuse_it(self) -> None:
        for name, lyrics in _BAD_ANSWERS.items():
            with self.subTest(name), self.assertRaises(_REFUSED_AS[name]):
                lyrics_frames.check_encodable(lyrics, "eng")

    def test_a_nul_is_refused_by_name(self) -> None:
        with self.assertRaisesRegex(lyrics_frames.UnencodableLyricsError, "NUL"):
            lyrics_frames.check_encodable(_BAD_ANSWERS["NUL"], "eng")

    def test_ordinary_lyrics_pass(self) -> None:
        synced = lm.SyncedLyrics(
            (
                lm.SyncedLine(0, "Ein Leuchtturm, é, 灯台 🌊"),
                lm.SyncedLine(_PAST_SYLT_RANGE_MS - 1, "the last stamp that fits"),
            )
        )
        for lyrics in (synced, lm.PlainLyrics(fixtures.ORCHARD_PLAIN)):
            with self.subTest(kind=lyrics.kind):
                lyrics_frames.check_encodable(lyrics, "eng")


class WriteLyricsPreflightTest(fixtures.IsolatedCase):
    """`write_lyrics` refuses a bad answer before it touches the file."""

    def test_a_nul_answer_writes_nothing(self) -> None:
        (path,) = fixtures.build_album(self.tmp, (TrackSpec("t.mp3"),))
        tag_builders.plant_frames(path, _FRAMES)
        before = path.read_bytes()
        with self.assertRaises(lyrics_frames.UnencodableLyricsError):
            lt.write_lyrics(path, _BAD_ANSWERS["NUL"], "eng")
        self.assertEqual(path.read_bytes(), before)


class PreflightRunTest(cli_fakes.RealWiringCliCase):
    """A run refuses a bad answer at the embed, the track left exactly as it was."""

    def build(self, track: TrackSpec) -> Path:
        """Build a one-track album, the track's old frames planted."""
        (path,) = fixtures.build_album(self.album, (track,))
        tag_builders.plant_frames(path, _FRAMES)
        return path

    def outcome(self) -> lr.TrackOutcome:
        """The track's outcome in the run's manifest on disk."""
        run_dir = lr.run_folder(self.album, run_fakes.RUN_ID)
        (track,) = lyrics_manifest.read_manifest(run_dir).tracks
        assert track.after is not None
        return track.after

    def assert_embed_error(self, result: cli_fakes.Result) -> None:
        """The run exited with a track error, recorded at the embed stage."""
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stderr)
        after = self.outcome()
        assert after.error is not None
        self.assertEqual(after.error.stage, lr.ErrorStage.EMBED)
        self.assertFalse(after.frames_written)
        self.assertEqual(after.sidecars_removed, ())

    def test_fresh_keeps_the_old_frames_and_sidecars_for_each_bad_answer(
        self,
    ) -> None:
        track = TrackSpec(
            "01 Keeper.mp3", title="The Keeper", lrc=_OLD_LRC, txt=_OLD_TXT
        )
        for name, lyrics in _BAD_ANSWERS.items():
            with self.subTest(name):
                # A fresh album per answer: the run folder takes a fixed id.
                self.album = self.tmp / name.replace(" ", "-")
                path = self.build(track)
                before = rb.snapshot(path)
                self.overrides["fetch"] = _fetching(lyrics)
                result = self.run_album("--fresh")
                self.assert_embed_error(result)
                self.assertEqual(rb.snapshot(path), before)
                self.assertEqual(rb.lyrics_frames(path), Counter(_FRAMES))

    def test_a_nul_answer_without_fresh_writes_nothing(self) -> None:
        path = self.build(TrackSpec("01 Mill.mp3", title="The Mill"))
        before = (path.read_bytes(), rb.snapshot(path))
        self.overrides["fetch"] = _fetching(_BAD_ANSWERS["NUL"])
        self.assert_embed_error(self.run_album())
        self.assertEqual((path.read_bytes(), rb.snapshot(path)), before)
        self.assertEqual(rb.sidecars(path), {})


if __name__ == "__main__":
    unittest.main()
