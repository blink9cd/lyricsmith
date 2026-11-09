"""What the entry point writes: the summary alone on stdout, everything else on stderr.

The run summary of a mixed album is pinned as exact text, as are the dry-run, check and
undo reports. The dry run is compared byte for byte with the album before it. Every
lyric is invented.
"""

from __future__ import annotations

import logging
import os
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_config  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_summary  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_modes import snapshot  # noqa: E402 - import follows the sys.path bootstrap above

#: A mixed album: found synced and plain, not found, kept, stripped, by file name,
#: an embed error, and a flagged lyric.
MIXED = (
    TrackSpec("01 Keeper.mp3", title="The Keeper"),
    TrackSpec("02 Orchard.mp3", title="The Orchard"),
    TrackSpec("03 Mill.mp3", title="The Mill"),
    TrackSpec("04 Bell.mp3", title="The Bell"),
    TrackSpec("05 Gate.mp3", title="The Gate (Live)"),
    TrackSpec("06 Wall.mp3", artist=None),
    TrackSpec("07 Fence.mp3", title="The Fence"),
    TrackSpec("08 Tower.mp3", seconds=200.0, title="The Tower"),
)
SYNCED = {
    title: fixtures.LIGHTHOUSE_LRC
    for title in ("The Keeper", "The Gate", "The Fence", "The Tower")
}

MIXED_SUMMARY = """\
album run 20261014T153012Z: 8 tracks under album

 #  track           source  kind      check
 1  01 Keeper.mp3   LRCLIB  synced    PASS
 2  02 Orchard.mp3  LRCLIB  unsynced  PASS
 3  03 Mill.mp3     -       -         not found
 4  04 Bell.mp3     frames  unsynced  PASS
 5  05 Gate.mp3     LRCLIB  synced    PASS  query: stripped
 6  06 Wall.mp3     -       -         not found  query: filename
 7  07 Fence.mp3    LRCLIB  synced    ERROR embed: frames differ after the save
 8  08 Tower.mp3    LRCLIB  synced    FLAG: ends short (last line 25.750 s, track 199.993 s)
synced 3 · unsynced 2 · unchanged 0 · not found 2 · no lyrics expected 0 · silent 0 · kept existing 0 · removed 0 · errors 1 · flagged 1 · genius: absent
written: new synced 3, embedded 1, new untimed 1
bandcamp: on
youtube music: on
tekstowo: on
silence check: off (no ffmpeg)
providers: LRCLIB 5/8 (4 synced, 1 untimed) · NetEase - · YouTube Music - · Bandcamp - · Genius - · Tekstowo -
backup: .lyricsmith/20261014T153012Z/   undo: album.py --album {album} --mode undo
"""


class RunSummaryTest(cli_fakes.CliCase):
    """The run's summary, exactly, and only it on stdout."""

    def build_mixed(self) -> None:
        """The mixed album, the bell's untimed lyrics embedded before the run to serve it."""
        paths = fixtures.build_album(self.album, MIXED)
        lyrics_tags.write_lyrics(
            paths[3], lm.PlainLyrics(fixtures.ORCHARD_PLAIN), "eng"
        )
        self.providers = run_fakes.table(
            SYNCED, {"The Orchard": fixtures.ORCHARD_PLAIN}
        )

        def embed(path: Path, lyrics: lm.Lyrics, lang: str) -> bool:
            if path.name.startswith("07"):
                raise lm.ReadBackError("frames differ after the save")
            return lyrics_tags.write_lyrics(path, lyrics, lang)

        self.overrides["write_lyrics"] = embed

    def test_a_mixed_album_summary_is_exact_and_alone_on_stdout(self) -> None:
        self.build_mixed()
        result = self.run_album()
        album = shlex.quote(str(self.album))
        self.assertEqual(result.stdout, MIXED_SUMMARY.format(album=album))
        self.assertEqual(result.code, 1)

    def test_progress_and_notices_go_to_stderr(self) -> None:
        self.build_mixed()
        stderr = self.run_album().stderr
        self.assertIn("[1/8] 01 Keeper.mp3: synced (LRCLIB)", stderr)
        self.assertIn("genius: no key found", stderr)
        self.assertNotIn("PASS", stderr)

    def test_no_root_logging_handler_is_installed(self) -> None:
        root = logging.getLogger()
        before = (list(root.handlers), root.level)
        self.build_mixed()
        self.run_album()
        self.assertEqual((list(root.handlers), root.level), before)
        self.assertEqual(root.getEffectiveLevel(), logging.WARNING)

    def test_the_providers_are_built_with_the_runs_deadline(self) -> None:
        fixtures.build_album(self.album, (TrackSpec("01 A.mp3"),))
        self.run_album()
        self.assertEqual(self.provider_calls, [(None, cli_fakes.album.DEADLINE_S)])


class GeniusTest(cli_fakes.CliCase):
    """Where the Genius key came from, or why the step was skipped, as reported."""

    def setUp(self) -> None:
        """A one-track album."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 A.mp3"),))

    def counts_line(self, result: cli_fakes.Result) -> str:
        """The summary's counts line."""
        return result.stdout.splitlines()[-7]

    def test_a_key_in_the_environment_reaches_the_providers(self) -> None:
        with mock.patch.dict(os.environ, {"GENIUS_ACCESS_TOKEN": "not-a-real-key"}):
            result = self.run_album()
        ((key, _deadline),) = self.provider_calls
        assert key is not None
        self.assertEqual(key.reveal(), "not-a-real-key")
        self.assertTrue(self.counts_line(result).endswith("genius: configured"))
        self.assertEqual(self.folder.manifests[0].genius, lr.GeniusState.CONFIGURED)

    def write_shared_key_file(self) -> Path:
        """A key file whose POSIX bits let group and others read it."""
        config = self.tmp / "config.toml"
        config.write_text(
            '[genius]\naccess_token = "not-a-real-key"\n', encoding="utf-8", newline=""
        )
        config.chmod(0o644)
        return config

    @unittest.skipIf(os.name == "nt", fixtures.NO_POSIX_MODES)
    def test_a_shared_key_file_is_warned_about(self) -> None:
        config = self.write_shared_key_file()
        result = self.run_album("--config", str(config))
        self.assertIn("readable by other users", result.stderr)

    def test_a_key_file_warning_reaches_stderr_on_any_host(self) -> None:
        # The bits read as POSIX reads them on every host: NTFS reports a writable file
        # as 0o666, so the warning is drawn there too, and its path to stderr is held.
        real = lyrics_config.permission_warnings

        def as_on_posix(path: Path) -> tuple[str, ...]:
            return real(path, posix=True)

        config = self.write_shared_key_file()
        with mock.patch.object(lyrics_config, "permission_warnings", as_on_posix):
            result = self.run_album("--config", str(config))
        self.assertIn("readable by other users", result.stderr)

    def test_no_genius_skips_the_key(self) -> None:
        result = self.run_album("--no-genius")
        self.assertEqual(self.provider_calls, [(None, cli_fakes.album.DEADLINE_S)])
        self.assertTrue(self.counts_line(result).endswith("genius: skipped"))
        self.assertNotIn("no key found", result.stderr)

    def test_offline_builds_no_provider_at_all(self) -> None:
        result = self.run_album("--offline")
        self.assertEqual(self.provider_calls, [])
        self.assertTrue(self.counts_line(result).endswith("genius: offline"))


DRY_SUMMARY = """\
album dry run: 2 tracks under album

 #  track     plan
 1  01 A.mp3  sidecar (synced)
 2  02 B.mp3  fetch  query: filename
errors 0 · genius: absent
dry run: nothing was written
"""


class DryRunTest(cli_fakes.CliCase):
    """A dry run leaves the album byte-identical, with no run folder, and says what it would do."""

    def test_the_album_is_unchanged_and_the_plan_is_reported(self) -> None:
        fixtures.build_album(
            self.album,
            (
                TrackSpec("01 A.mp3", lrc=fixtures.LIGHTHOUSE_LRC),
                TrackSpec("02 B.mp3", artist=None),
            ),
        )
        before = snapshot(self.album)
        result = self.run_album("--dry-run")
        self.assertEqual(snapshot(self.album), before)
        self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())
        self.assertEqual((result.code, result.stdout), (0, DRY_SUMMARY))
        self.assertEqual((self.folder.backed_up, self.provider_calls), ([], []))

    def test_a_track_error_in_a_dry_run_exits_1(self) -> None:
        fixtures.build_album(self.album, (TrackSpec("01 A.mp3"),))
        (self.album / "01 A.txt").write_bytes(b"\xff\xfe\xfa")
        result = self.run_album("--dry-run", "--offline")
        self.assertEqual(result.code, 1)
        self.assertIn("ERROR sidecar", result.stdout)
        self.assertIn("genius: offline", result.stdout)


CHECK_SUMMARY = """\
album check 20261014T153012Z: 2 tracks under album

 #  track     check
 1  01 A.mp3  PASS
 2  02 B.mp3  -
passed 1 · flagged 0 · skipped 0 · not checked 1 · errors 0
"""

UNDO_SUMMARY = """\
album undo 20261014T153012Z: 2 tracks under album

 #  track     undo
 1  01 A.mp3  undone  displaced: .lyricsmith/20261014T153012Z/undo/01 A.txt
 2  02 B.mp3  ERROR undo: track missing  displaced: .lyricsmith/20261014T153012Z/undo/02 B.lrc, .lyricsmith/20261014T153012Z/undo/02 B-2.lrc
undone 1 · already undone 0 · errors 1
"""


#: Where the faked run's undo keeps displaced copies, by album path.
_UNDO = f"{lr.RUN_FOLDER_NAME}/{run_fakes.RUN_ID}/undo"


class OtherModesTest(cli_fakes.CliCase):
    """The check and undo reports, after a real run of a two-track album."""

    def setUp(self) -> None:
        """Run a two-track album: the first is written, the second finds nothing."""
        super().setUp()
        self.paths = fixtures.build_album(
            self.album,
            (
                TrackSpec("01 A.mp3", title="The Keeper"),
                TrackSpec("02 B.mp3", title="The Mill"),
            ),
        )
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        self.run_album()

    def test_the_check_report(self) -> None:
        result = self.run_album("--mode", "check")
        self.assertEqual((result.code, result.stdout), (0, CHECK_SUMMARY))

    def test_a_check_error_exits_1(self) -> None:
        self.paths[0].unlink()
        result = self.run_album("--mode", "check", "--track", str(self.paths[0]))
        self.assertEqual(result.code, 1)
        self.assertIn("ERROR check", result.stdout)

    def test_the_undo_report_with_no_newer_run_sharing_a_track(self) -> None:
        self.folder.undo_report = lr.UndoReport(
            run_fakes.RUN_ID,
            False,
            (
                lr.UndoTrackResult(
                    "01 A.mp3", lr.UndoStatus.UNDONE, displaced=(f"{_UNDO}/01 A.txt",)
                ),
                lr.UndoTrackResult(
                    "02 B.mp3",
                    lr.UndoStatus.ERROR,
                    lr.StageError(lr.ErrorStage.UNDO, "OSError", "track missing"),
                    (f"{_UNDO}/02 B.lrc", f"{_UNDO}/02 B-2.lrc"),
                ),
            ),
        )
        result = self.run_album("--mode", "undo")
        self.assertEqual((result.code, result.stdout), (1, UNDO_SUMMARY))
        # Not the newest, yet no newer run on disk shares a track it restored: no
        # warning (test_lyrics_undo_newer holds the cases that warn).
        self.assertNotIn("warning:", result.stderr)

    def test_an_undo_that_could_not_record_reports_then_exits_3(self) -> None:
        self.folder.undo_report = lr.UndoReport(
            run_fakes.RUN_ID,
            True,
            (lr.UndoTrackResult("01 A.mp3", lr.UndoStatus.UNDONE),),
            record_error="could not record the undo: disk full",
        )
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, 3)
        self.assertIn(" 1  01 A.mp3  undone\n", result.stdout)
        self.assertEqual(
            result.stderr, "album.py: could not record the undo: disk full\n"
        )

    def test_an_undo_record_failure_beside_a_track_error_still_exits_3(self) -> None:
        # Undo depends on the record, so its failure outranks a track's error.
        error = lr.StageError(lr.ErrorStage.UNDO, "OSError", "track missing")
        self.folder.undo_report = lr.UndoReport(
            run_fakes.RUN_ID,
            True,
            (
                lr.UndoTrackResult("01 A.mp3", lr.UndoStatus.UNDONE),
                lr.UndoTrackResult("02 B.mp3", lr.UndoStatus.ERROR, error),
            ),
            record_error="could not record the undo: disk full",
        )
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, 3)
        self.assertIn("track missing", result.stdout)

    def test_a_clean_undo_exits_0_with_no_warning(self) -> None:
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, 0)
        self.assertEqual(result.stderr, "")


class SummaryShapeTest(fixtures.IsolatedCase):
    """Corners of the summary a full run does not reach."""

    def test_a_track_the_run_never_reached_says_so(self) -> None:
        track = lr.ManifestTrack("01 A.mp3", (2, 4), lr.TrackBefore((), ()))
        self.assertEqual(lyrics_summary.run_row(track), ("-", "-", "not reached"))
