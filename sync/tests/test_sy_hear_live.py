"""Live: one short provider-timed track of the test library heard by the real child, offline.

Skipped unless everything the live timing suite needs is here (`test_sy_live.READY`)
and the test library holds a track to hear. The library is read only: the track found
is copied with its sidecars into the test's temporary folder, and heard there, so
nothing in the library is written. It is found at run time, never named: the first
MP3, in sorted order, holding a provider's timed .lrc and under `MAX_SECONDS` long.
The library is `LIBRARY_ENV` when that is set, else the one under the home folder,
read when this module loads, before the isolated case points HOME elsewhere.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_hearing_log as hl  # noqa: E402 - import follows the sys.path bootstrap
import sy_engine  # noqa: E402 - import follows the sys.path bootstrap above
import sy_kind  # noqa: E402 - import follows the sys.path bootstrap above
import sync  # noqa: E402 - import follows the sys.path bootstrap above
import test_sy_live  # noqa: E402 - the live timing suite's readiness and model folders
from lyrics_sidecar import UNREADABLE  # noqa: E402 - follows the bootstrap above

#: The variable naming another test library.
LIBRARY_ENV = "LYRICSMITH_TEST_LIBRARY"
LIBRARY = Path(os.environ[LIBRARY_ENV]) if os.environ.get(LIBRARY_ENV) else None
#: The longest track heard: separating a few minutes of audio takes about a minute.
MAX_SECONDS = 240.0
TURBO = at_models.WHISPER_MODEL_NAME


def _provider_timed(audio: Path) -> bool:
    """True when *audio*'s sidecars hold a provider's timed text."""
    try:
        found = sy_kind.text_kind(audio)
    except (*UNREADABLE, OSError):
        return False
    return found is not None and found.kind is hl.HeardKind.PROVIDER_TIMED


def short_provider_track(library: Path) -> Path | None:
    """The first MP3 under *library*, sorted, with a provider's .lrc and short enough."""
    for folder, folders, names in os.walk(library):
        folders.sort()
        for name in sorted(names):
            audio = Path(folder) / name
            if audio.suffix.lower() != ".mp3" or not _provider_timed(audio):
                continue
            seconds = sy_engine.track_seconds(audio)
            if seconds is not None and seconds <= MAX_SECONDS:
                return audio
    return None


@unittest.skipUnless(
    test_sy_live.READY,
    "needs the sync install, ffmpeg, the fetched models and whisper.cpp's jfk.wav",
)
class LiveHearTest(sy_cases.IsolatedCase):
    def test_a_test_library_track_is_heard_logged_and_left_as_it_was(self) -> None:
        source = (
            short_provider_track(LIBRARY)
            if LIBRARY is not None and LIBRARY.is_dir()
            else None
        )
        if source is None:
            self.skipTest(
                f"no short provider-timed track in the library ({LIBRARY_ENV})"
            )
        album = self.tmp / "Artist" / "Album"
        album.mkdir(parents=True)
        track = album / "01 Track.mp3"
        shutil.copyfile(source, track)
        shutil.copyfile(source.with_suffix(".lrc"), track.with_suffix(".lrc"))
        lrc = track.with_suffix(".lrc").read_bytes()
        log = self.tmp / "hearings"
        argv = [
            "--mode",
            "hear",
            "--path",
            str(self.tmp),
            "--hearing-log",
            str(log),
            "--model-dir",
            str(test_sy_live.MODELS),
            "--whisper-model-dir",
            str(test_sy_live.WHISPER_MODELS),
        ]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sync.main(argv), 0)
        record = hl.read_hearing(log, str(track), TURBO)
        assert record is not None, out.getvalue()
        self.assertIs(record.kind, hl.HeardKind.PROVIDER_TIMED)
        heard = lh.Heard(record.pairs, record.share)
        expected = (
            hl.HeardVerdict.LITTLE_HEARD
            if lh.little_heard(heard)
            else hl.HeardVerdict.NOT_SUNG
            if lh.not_sung(heard)
            else hl.HeardVerdict.SUNG
        )
        self.assertIs(record.verdict, expected)
        self.assertEqual(track.with_suffix(".lrc").read_bytes(), lrc)
        left = sorted(entry.name for entry in album.iterdir())
        self.assertEqual(left, sorted([track.name, track.with_suffix(".lrc").name]))
        with contextlib.redirect_stdout(io.StringIO()) as again:
            self.assertEqual(sync.main(argv), 0)
        self.assertIn("skip: heard before", again.getvalue())
