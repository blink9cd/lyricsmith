"""The entry point with its real wiring: a run, its check, and its undo, end to end.

Every stage is the real one, the run folder's included, and only the provider table is
faked, through the wiring's provider-building seam, so nothing reaches the network.
The album is built from silent MP3s under the test's own directory, HOME and the
config directories are the isolated ones, and every lyric is invented. Undo must hand
back each track's lyrics frames, record for record, and its sidecars, byte for byte,
and displace nothing: every sidecar it removes is one the run recorded writing.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: Three tracks: one found synced over an older lyric, one served by its own plain
#: sidecar, and one nobody has words for.
TRACKS = (
    TrackSpec("01 Keeper.mp3", title="The Keeper"),
    TrackSpec("CD2/02 Orchard.mp3", title="The Orchard", txt=fixtures.ORCHARD_PLAIN),
    TrackSpec("CD2/03 Mill.mp3", title="The Mill", id3_version=(2, 3)),
)
#: The lyric the first track held before the run, which undo must put back. Its words
#: are unlike the found ones, so the check flags the new lyric against it.
OLD_WORDS = tag_builders.uslt("An older verse about the keeper\n")

RUN_SUMMARY = """\
album run {run_id}: 3 tracks under album

 #  track               source   kind      check
 1  01 Keeper.mp3       LRCLIB   synced    FLAG: unlike backup (similarity 0.083 < 0.6)
 2  CD2/02 Orchard.mp3  sidecar  unsynced  PASS
 3  CD2/03 Mill.mp3     -        -         not found
synced 1 · unsynced 1 · unchanged 0 · not found 1 · no lyrics expected 0 · silent 0 · kept existing 0 · removed 0 · errors 0 · flagged 1 · genius: absent
written: upgraded 1, embedded 1
bandcamp: on
youtube music: on
tekstowo: on
silence check: off (no ffmpeg)
providers: LRCLIB 1/3 (1 synced) · NetEase - · YouTube Music - · Bandcamp - · Genius - · Tekstowo -
backup: .lyricsmith/{run_id}/   undo: album.py --album {album} --mode undo
"""

CHECK_REPORT = """\
album check {run_id}: 3 tracks under album

 #  track               check
 1  01 Keeper.mp3       FLAG: unlike backup (similarity 0.083 < 0.6)
 2  CD2/02 Orchard.mp3  PASS
 3  CD2/03 Mill.mp3     -
passed 1 · flagged 1 · skipped 0 · not checked 1 · errors 0
"""

UNDO_REPORT = """\
album undo {run_id}: 3 tracks under album

 #  track               undo
 1  01 Keeper.mp3       undone
 2  CD2/02 Orchard.mp3  undone
 3  CD2/03 Mill.mp3     undone
undone 3 · already undone 0 · errors 0
"""


class RealWiringEndToEndTest(cli_fakes.RealWiringCliCase):
    """Run, check and undo through `main` with `real_wiring`, the providers faked."""

    #: The real UTC clock too: nothing of the wiring but the providers is faked.
    real_clock = True

    def run_id(self) -> str:
        """The one run folder's name: the real clock chose it."""
        runs = [p.name for p in (self.album / lr.RUN_FOLDER_NAME).iterdir()]
        return next(name for name in runs if name != lyrics_album.LOCK_NAME)

    def test_a_run_its_check_and_its_undo_leave_the_album_as_it_was(self) -> None:
        paths = fixtures.build_album(self.album, TRACKS)
        tag_builders.plant_frames(paths[0], [OLD_WORDS])
        before = rb.snapshots(paths)
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})

        run = self.run_album()
        run_id = self.run_id()
        quoted = shlex.quote(str(self.album))
        self.assertEqual(
            (run.code, run.stdout),
            (album.EXIT_OK, RUN_SUMMARY.format(run_id=run_id, album=quoted)),
        )
        self.assertNotEqual(rb.snapshots(paths), before)

        check = self.run_album("--mode", "check")
        self.assertEqual(
            (check.code, check.stdout),
            (album.EXIT_OK, CHECK_REPORT.format(run_id=run_id)),
        )

        undo = self.run_album("--mode", "undo")
        self.assertEqual(
            (undo.code, undo.stdout),
            (album.EXIT_OK, UNDO_REPORT.format(run_id=run_id)),
        )
        self.assertEqual(rb.snapshots(paths), before)
        self.assertEqual(undo.stderr, "")
        undo_dir = lr.run_folder(self.album, run_id) / "undo"
        self.assertFalse(undo_dir.exists())
        self.assertFalse(run_fakes.lock_held(self.album))
