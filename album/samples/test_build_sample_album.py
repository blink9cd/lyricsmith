"""The sample album builder, and the walk-through its README shows, run for real.

The builder is run into the test's own directory, then the entry point is run on the
album it built with the real wiring, a run with ``--offline`` and then a strip, so
nothing reaches the network and no key is read. Each report must match the one the
README shows, the run id and the album's path filled in, and undo must hand the folder
back as it was built: the same files, each MP3 with the same tag frames, and every file
with the same bytes.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import shlex
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

_SAMPLES = Path(__file__).resolve().parent
_TOOL = _SAMPLES.parent
sys.path[:0] = [str(_SAMPLES), str(_TOOL), str(_TOOL / "src"), str(_TOOL / "tests")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import build_sample_album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_silence  # noqa: E402 - import follows the sys.path bootstrap above
from mutagen.id3 import ID3  # noqa: E402 - import follows the sys.path bootstrap above

#: What the README shows for each step, with the run id and album path as placeholders.
DRY_RUN = """\
album dry run: 6 tracks under sample_album

 #  track                     plan
 1  01 The Keeper Counts.mp3  sidecar (synced)
 2  02 Orchard Wall.mp3       nothing (offline, no sidecar or frames)
 3  03 The Mill Bell.mp3      nothing (offline, no sidecar or frames)
 4  04 The Long Bit.mp3       nothing (offline, no sidecar or frames)
 5  05 The Short Bit.mp3      sidecar (unsynced)
 6  06 The Quiet Reel.mp3     fetch, else remove (implausible: placeholder lyrics)
errors 0 · genius: offline
dry run: nothing was written
"""

RUN = """\
album run <run-id>: 6 tracks under sample_album

 #  track                     source   kind      check
 1  01 The Keeper Counts.mp3  sidecar  synced    PASS
 2  02 Orchard Wall.mp3       -        -         not found
 3  03 The Mill Bell.mp3      -        -         not found
 4  04 The Long Bit.mp3       -        -         no lyrics expected
 5  05 The Short Bit.mp3      sidecar  unsynced  FLAG: lyrics on a no-lyrics genre (Stand-up Comedy)
 6  06 The Quiet Reel.mp3     -        -         removed: implausible old lyrics (placeholder lyrics)
synced 1 · unsynced 1 · unchanged 0 · not found 2 · no lyrics expected 1 · silent 0 · kept existing 0 · removed 1 · errors 0 · flagged 1 · genius: offline
bandcamp: off (offline)
youtube music: off (offline)
tekstowo: off (offline)
silence check: off (no ffmpeg)
providers: LRCLIB - · NetEase - · YouTube Music - · Bandcamp - · Genius - · Tekstowo -
backup: .lyricsmith/<run-id>/   undo: album.py --album <album> --mode undo
"""

#: The run with an ffmpeg found: the tracks no provider answered, silent.
RUN_WITH_FFMPEG = """\
album run <run-id>: 6 tracks under sample_album

 #  track                     source   kind      check
 1  01 The Keeper Counts.mp3  sidecar  synced    PASS
 2  02 Orchard Wall.mp3       -        -         silent (peak -91.0 dBFS)
 3  03 The Mill Bell.mp3      -        -         silent (peak -91.0 dBFS)
 4  04 The Long Bit.mp3       -        -         silent (peak -91.0 dBFS)
 5  05 The Short Bit.mp3      sidecar  unsynced  FLAG: lyrics on a no-lyrics genre (Stand-up Comedy)
 6  06 The Quiet Reel.mp3     -        -         removed: implausible old lyrics (placeholder lyrics)
synced 1 · unsynced 1 · unchanged 0 · not found 0 · no lyrics expected 0 · silent 3 · kept existing 0 · removed 1 · errors 0 · flagged 1 · genius: offline
bandcamp: off (offline)
youtube music: off (offline)
tekstowo: off (offline)
silence check: on
providers: LRCLIB - · NetEase - · YouTube Music - · Bandcamp - · Genius - · Tekstowo -
backup: .lyricsmith/<run-id>/   undo: album.py --album <album> --mode undo
"""

#: The progress that run prints on stderr.
PROGRESS_WITH_FFMPEG = """\
backing up 6 tracks
[1/6] 01 The Keeper Counts.mp3: synced (sidecar)
[2/6] 02 Orchard Wall.mp3: silent (peak -91.0 dBFS)
[3/6] 03 The Mill Bell.mp3: silent (peak -91.0 dBFS)
[4/6] 04 The Long Bit.mp3: silent (peak -91.0 dBFS)
[5/6] 05 The Short Bit.mp3: unsynced (sidecar)
[6/6] 06 The Quiet Reel.mp3: removed: implausible old lyrics (placeholder lyrics)
"""

CHECK = """\
album check <run-id>: 6 tracks under sample_album

 #  track                     check
 1  01 The Keeper Counts.mp3  PASS
 2  02 Orchard Wall.mp3       -
 3  03 The Mill Bell.mp3      -
 4  04 The Long Bit.mp3       -
 5  05 The Short Bit.mp3      PASS
 6  06 The Quiet Reel.mp3     -
passed 2 · flagged 0 · skipped 0 · not checked 4 · errors 0
"""

UNDO = """\
album undo <run-id>: 6 tracks under sample_album

 #  track                     undo
 1  01 The Keeper Counts.mp3  undone
 2  02 Orchard Wall.mp3       undone
 3  03 The Mill Bell.mp3      undone
 4  04 The Long Bit.mp3       undone
 5  05 The Short Bit.mp3      undone
 6  06 The Quiet Reel.mp3     undone
undone 6 · already undone 0 · errors 0
"""

STRIP = """\
album strip <run-id>: 6 tracks under sample_album

 #  track                     removed
 1  01 The Keeper Counts.mp3  .lrc
 2  02 Orchard Wall.mp3       nothing to remove
 3  03 The Mill Bell.mp3      nothing to remove
 4  04 The Long Bit.mp3       nothing to remove
 5  05 The Short Bit.mp3      .txt
 6  06 The Quiet Reel.mp3     .txt
stripped 6 · errors 0
backup: .lyricsmith/<run-id>/   undo: album.py --album <album> --mode undo
"""

#: The progress the strip step prints on stderr, as the README shows it.
STRIP_PROGRESS = """\
backing up 6 tracks
[1/6] 01 The Keeper Counts.mp3: stripped (.lrc)
[2/6] 02 Orchard Wall.mp3: stripped (nothing to remove)
[3/6] 03 The Mill Bell.mp3: stripped (nothing to remove)
[4/6] 04 The Long Bit.mp3: stripped (nothing to remove)
[5/6] 05 The Short Bit.mp3: stripped (.txt)
[6/6] 06 The Quiet Reel.mp3: stripped (.txt)
"""

#: The progress the run step prints on stderr, as the README shows it.
PROGRESS = """\
backing up 6 tracks
[1/6] 01 The Keeper Counts.mp3: synced (sidecar)
[2/6] 02 Orchard Wall.mp3: not found
[3/6] 03 The Mill Bell.mp3: not found
[4/6] 04 The Long Bit.mp3: no lyrics expected
[5/6] 05 The Short Bit.mp3: unsynced (sidecar)
[6/6] 06 The Quiet Reel.mp3: removed: implausible old lyrics (placeholder lyrics)
"""

#: The one-track loop's track, relative to the directory the README works from.
ONE_TRACK = "sample_album/05 The Short Bit.mp3"

#: The one-track loop: a strip of one track, a dry run of it, and the strip's undo.
ONE_STRIP = """\
album strip <run-id>: 1 track under sample_album

 #  track                 removed
 1  05 The Short Bit.mp3  .txt
stripped 1 · errors 0
backup: .lyricsmith/<run-id>/   undo: album.py --album <album> --mode undo
"""

ONE_PLAN = """\
album dry run: 1 track under sample_album

 #  track                 plan
 1  05 The Short Bit.mp3  nothing (offline, no sidecar or frames)
errors 0 · genius: offline
dry run: nothing was written
"""

ONE_UNDO = """\
album undo <run-id>: 1 track under sample_album

 #  track                 undo
 1  05 The Short Bit.mp3  undone
undone 1 · already undone 0 · errors 0
"""

#: The run id of the run the README captured; every run takes its own start time.
README_RUN_ID = "20261016T034745Z"
#: The run id of the strip the README captured, a run of its own.
README_STRIP_ID = "20261016T034748Z"
#: The run id of the one-track strip the README captured.
README_ONE_TRACK_ID = "20261021T151338Z"
#: The album folder's name inside the test directory, as the README builds it.
_ALBUM_NAME = "sample_album"


def _wiring_with_ffmpeg() -> album.Wiring:
    """The real wiring finding an ffmpeg, every measured track reading as silence.

    The measuring stage is replaced, so no ffmpeg runs and the sample needs none.
    """
    real = album.real_wiring()
    stages = dataclasses.replace(
        real.stages, measure_level=lambda track, ffmpeg: lyrics_silence.Peak(-91.0)
    )
    return dataclasses.replace(real, stages=stages, find_ffmpeg=lambda: "/x/ffmpeg")


def _built_state(album: Path) -> dict[str, tuple[object, bytes]]:
    """Every visible file under *album*: its bytes, and an MP3's tag version and frames.

    The run folder is left out: it is kept after an undo, by design. The frames are
    there as well as the bytes so that a failure names what changed in a tag.
    """
    state: dict[str, tuple[object, bytes]] = {}
    for path in sorted(album.rglob("*")):
        rel = path.relative_to(album)
        if rel.parts[0] == lyrics_records.RUN_FOLDER_NAME or path.is_dir():
            continue
        frames: object = None
        if path.suffix == ".mp3":
            tags = ID3(path)
            frames = (tags.version, sorted(map(repr, tags.values())))
        state[rel.as_posix()] = (frames, path.read_bytes())
    return state


class SampleAlbumTest(fixtures.IsolatedCase):
    """The README's walk-through, step by step, on a freshly built sample album."""

    def setUp(self) -> None:
        """Build the sample album into this test's directory, working from there."""
        super().setUp()
        self.album = self.tmp / _ALBUM_NAME
        self.assertEqual(build_sample_album.main(["--out", str(self.album)]), 0)
        cwd = Path.cwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)

    maxDiff = None
    #: What the last `_tool` call printed on stderr.
    stderr = ""

    def _tool(
        self, *options: str, wiring: album.Wiring | None = None
    ) -> tuple[int, str]:
        """Run the entry point on the sample album; its exit code and stdout.

        Its stderr is kept in `stderr`. *wiring* is the real one unless given.
        """
        argv: Sequence[str] = ["--album", _ALBUM_NAME, *options]
        out, err = io.StringIO(), io.StringIO()
        # The reports name the tool by argv[0], as the README's shell shows it.
        argv0 = mock.patch.object(sys, "argv", ["album.py"])
        with argv0, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = album.main(argv, wiring=wiring)
        self.stderr = err.getvalue()
        return code, out.getvalue()

    def _filled(self, report: str) -> str:
        """*report* with the newest run's id and the album's resolved path filled in."""
        runs = sorted((self.album / lyrics_records.RUN_FOLDER_NAME).iterdir())
        run_id = [run.name for run in runs if run.is_dir()][-1]
        # Quoted for the shell, as the run summary quotes it in its undo line.
        album = shlex.quote(str(self.album.resolve()))
        return report.replace("<run-id>", run_id).replace("<album>", album)

    def test_the_built_album_holds_six_tracks_and_three_sidecars(self) -> None:
        names = sorted(p.name for p in self.album.iterdir())
        self.assertEqual(
            names,
            [
                "01 The Keeper Counts.lrc",
                "01 The Keeper Counts.mp3",
                "02 Orchard Wall.mp3",
                "03 The Mill Bell.mp3",
                "04 The Long Bit.mp3",
                "05 The Short Bit.mp3",
                "05 The Short Bit.txt",
                "06 The Quiet Reel.mp3",
                "06 The Quiet Reel.txt",
            ],
        )

    def test_each_step_prints_what_the_readme_shows(self) -> None:
        self.assertEqual(self._tool("--dry-run", "--offline"), (0, DRY_RUN))
        self.assertFalse((self.album / ".lyricsmith").exists())
        code, out = self._tool("--mode", "run", "--offline")
        self.assertEqual((code, out), (0, self._filled(RUN)))
        self.assertEqual(self.stderr, PROGRESS)
        # The placeholder nothing replaced is gone, and the undo below brings it back.
        self.assertFalse((self.album / "06 The Quiet Reel.txt").exists())
        self.assertEqual(self._tool("--mode", "check"), (0, self._filled(CHECK)))
        self.assertEqual(self._tool("--mode", "undo"), (0, self._filled(UNDO)))
        code, out = self._tool("--mode", "strip")
        self.assertEqual((code, out), (0, self._filled(STRIP)))
        self.assertEqual(self.stderr, STRIP_PROGRESS)
        self.assertEqual(self._tool("--mode", "undo"), (0, self._filled(UNDO)))

    def test_a_run_with_ffmpeg_prints_what_the_readme_shows(self) -> None:
        wiring = _wiring_with_ffmpeg()
        code, out = self._tool("--mode", "run", "--offline", wiring=wiring)
        self.assertEqual((code, out), (0, self._filled(RUN_WITH_FFMPEG)))
        self.assertEqual(self.stderr, PROGRESS_WITH_FFMPEG)

    def test_undoing_the_strip_returns_the_album_to_its_built_state(self) -> None:
        built = _built_state(self.album)
        self.assertEqual(self._tool("--mode", "strip")[0], 0)
        self.assertEqual(
            sorted(p.name for p in self.album.glob("*.lrc")),
            [],
            "the strip left a .lrc",
        )
        self.assertEqual(self._tool("--mode", "undo")[0], 0)
        self.assertEqual(_built_state(self.album), built)

    def test_undo_returns_the_album_to_its_built_state(self) -> None:
        built = _built_state(self.album)
        self.assertEqual(self._tool("--mode", "run", "--offline")[0], 0)
        self.assertNotEqual(_built_state(self.album), built)
        self.assertEqual(self._tool("--mode", "undo")[0], 0)
        self.assertEqual(_built_state(self.album), built)

    def test_the_one_track_loop_prints_what_the_readme_shows(self) -> None:
        built = _built_state(self.album)
        code, out = self._tool("--mode", "strip", "--track", ONE_TRACK)
        self.assertEqual((code, out), (0, self._filled(ONE_STRIP)))
        self.assertFalse((self.album / "05 The Short Bit.txt").exists())
        self.assertTrue((self.album / "01 The Keeper Counts.lrc").exists())
        plan = self._tool("--dry-run", "--offline", "--track", ONE_TRACK)
        self.assertEqual(plan, (0, ONE_PLAN))
        self.assertEqual(self._tool("--mode", "undo"), (0, self._filled(ONE_UNDO)))
        self.assertEqual(_built_state(self.album), built)

    def test_the_readme_shows_every_report_verbatim(self) -> None:
        readme = (_SAMPLES / "README.md").read_text(encoding="utf-8")
        # Both runs show the one id, so the check and undo reports that follow fit either.
        reports = (DRY_RUN, RUN, PROGRESS, RUN_WITH_FFMPEG, PROGRESS_WITH_FFMPEG)
        for report in (*reports, CHECK, UNDO):
            self.assertIn(report.replace("<run-id>", README_RUN_ID), readme)
        for report in (STRIP, STRIP_PROGRESS, UNDO):
            self.assertIn(report.replace("<run-id>", README_STRIP_ID), readme)
        for report in (ONE_STRIP, ONE_PLAN, ONE_UNDO):
            self.assertIn(report.replace("<run-id>", README_ONE_TRACK_ID), readme)

    def test_every_lyric_line_is_the_invented_one(self) -> None:
        sidecar = self.album / "01 The Keeper Counts.lrc"
        self.assertEqual(sidecar.read_text(encoding="utf-8"), fixtures.LIGHTHOUSE_LRC)
        untimed = self.album / "05 The Short Bit.txt"
        self.assertEqual(untimed.read_text(encoding="utf-8"), fixtures.ORCHARD_PLAIN)
        placeholder = self.album / "06 The Quiet Reel.txt"
        self.assertEqual(placeholder.read_text(encoding="utf-8"), "[au:instrumental]\n")

    def test_a_non_empty_out_is_refused(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = build_sample_album.main(["--out", str(self.album)])
        self.assertEqual(code, 2)
        self.assertIn("--out must be a missing or empty folder", err.getvalue())

    def test_an_out_naming_a_file_is_refused(self) -> None:
        # Regression: a file at --out once escaped as NotADirectoryError, exit 1.
        not_a_folder = self.tmp / "not_a_folder.txt"
        not_a_folder.write_bytes(b"invented\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = build_sample_album.main(["--out", str(not_a_folder)])
        self.assertEqual(code, 2)
        self.assertIn("--out must be a missing or empty folder", err.getvalue())
        self.assertEqual(not_a_folder.read_bytes(), b"invented\n")


if __name__ == "__main__":
    unittest.main()
