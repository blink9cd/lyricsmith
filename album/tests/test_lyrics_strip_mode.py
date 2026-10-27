"""`--mode strip`: every track backed up, then its lyrics frames and sidecars removed.

Each test drives the entry point with its real wiring, the real backup, the real strip
stage and the real undo included, so the manifest and the album on disk are the ones
an operator would get. Only the clock is fixed, so the run id is known, and a test
replaces the strip stage where it needs a failure or an interrupt. A strip is undone
by the run's own `--mode undo`, frames and sidecars byte for byte. The album is built
from silent MP3s under the test's own directory, and every lyric is invented.
"""

from __future__ import annotations

import os
import shlex
import signal
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_strip  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_summary  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

_LRC = "[00:01.00]A harbor bell rings twice\n[00:04.00]and the gulls go quiet\n"
_TXT = "A harbor bell rings twice\nand the gulls go quiet\n"
_FRAMES = (
    tag_builders.uslt("A verse kept in the tag\n"),
    tag_builders.sylt((1000, "A timed line"), (4000, "and another")),
)
#: Three tracks: frames and both sidecars, nothing at all, and a timed sidecar alone.
TRACKS = (
    TrackSpec("01 Keeper.mp3", title="The Keeper", lrc=_LRC, txt=_TXT),
    TrackSpec("02 Mill.mp3", title="The Mill"),
    TrackSpec("CD2/03 Bell.mp3", title="The Bell", lrc=_LRC),
)
RUN_ID = run_fakes.RUN_ID

STRIP_SUMMARY = """\
album strip {run_id}: 3 tracks under album

 #  track            removed
 1  01 Keeper.mp3    frames, .lrc, .txt
 2  02 Mill.mp3      nothing to remove
 3  CD2/03 Bell.mp3  .lrc
stripped 3 · errors 0
backup: .lyricsmith/{run_id}/   undo: album.py --album {album} --mode undo
"""

CHECK_REPORT = """\
album check {run_id}: 3 tracks under album

 #  track            check
 1  01 Keeper.mp3    -
 2  02 Mill.mp3      -
 3  CD2/03 Bell.mp3  -
passed 0 · flagged 0 · skipped 0 · not checked 3 · errors 0
"""


class StripCliCase(cli_fakes.RealWiringCliCase):
    """The real wiring with a fixed clock; a test may replace any stage."""

    def setUp(self) -> None:
        """Build the album, the first track's frames planted."""
        super().setUp()
        self.paths = fixtures.build_album(self.album, TRACKS)
        tag_builders.plant_frames(self.paths[0], _FRAMES)

    def strip(self, *extra: str) -> cli_fakes.Result:
        """Run this test's album in strip mode, with *extra* options."""
        return self.main("--album", str(self.album), "--mode", "strip", *extra)

    def manifest(self) -> lr.Manifest:
        """The strip run's manifest as it stands on disk."""
        return lyrics_manifest.read_manifest(lr.run_folder(self.album, RUN_ID))

    def expected(self, template: str) -> str:
        """*template* with the run id and the quoted album filled in."""
        return template.format(run_id=RUN_ID, album=shlex.quote(str(self.album)))


class StripModeTest(StripCliCase):
    """A strip backs up, removes, records each track, and an undo puts it all back."""

    def test_every_track_is_stripped_and_recorded(self) -> None:
        result = self.strip()
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertEqual(result.stdout, self.expected(STRIP_SUMMARY))
        self.assertEqual(self.provider_calls, [])
        for path in self.paths:
            self.assertEqual((rb.lyrics_frames(path), rb.sidecars(path)), ({}, {}))
        self.assertIn(
            "[1/3] 01 Keeper.mp3: stripped (frames, .lrc, .txt)", result.stderr
        )
        self.assertIn("[2/3] 02 Mill.mp3: stripped (nothing to remove)", result.stderr)

    def test_the_manifest_holds_the_backup_and_what_was_removed(self) -> None:
        self.strip()
        manifest = self.manifest()
        self.assertEqual(
            (manifest.state, manifest.lang, manifest.genius),
            (lr.RunState.COMPLETE, album.DEFAULT_LANG, lr.GeniusState.ABSENT),
        )
        keeper, mill, bell = manifest.tracks
        assert keeper.before is not None and keeper.after is not None
        self.assertEqual(len(keeper.before.frames), len(_FRAMES))
        self.assertEqual(
            sorted(copy.name for copy in keeper.before.sidecars),
            ["01 Keeper.lrc", "01 Keeper.txt"],
        )
        self.assertEqual(
            keeper.after,
            lr.TrackOutcome(
                lr.OutcomeStatus.STRIPPED,
                sidecars_removed=("01 Keeper.lrc", "01 Keeper.txt"),
                frames_written=True,
                held=lr.HeldBefore.SYNCED,
            ),
        )
        self.assertEqual(
            mill.after,
            lr.TrackOutcome(lr.OutcomeStatus.STRIPPED, held=lr.HeldBefore.NOTHING),
        )
        assert bell.after is not None
        self.assertEqual(bell.after.sidecars_removed, ("03 Bell.lrc",))

    def test_undo_puts_back_the_frames_and_sidecars_byte_for_byte(self) -> None:
        before = rb.snapshots(self.paths)
        self.strip()
        self.assertNotEqual(rb.snapshots(self.paths), before)
        result = self.main("--album", str(self.album), "--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertIn("undone 3 · already undone 0 · errors 0", result.stdout)
        self.assertNotIn("displaced", result.stdout)
        self.assertEqual(rb.snapshots(self.paths), before)
        self.assertEqual(
            rb.sidecars(self.paths[0]),
            {"01 Keeper.lrc": _LRC.encode(), "01 Keeper.txt": _TXT.encode()},
        )

    def test_check_after_a_strip_gives_no_verdict_and_flags_nothing(self) -> None:
        self.strip()
        result = self.main("--album", str(self.album), "--mode", "check")
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertEqual(result.stdout, self.expected(CHECK_REPORT))


class StripErrorTest(StripCliCase):
    """A track whose strip fails is that track's error; the next is still stripped."""

    def assert_first_failed(self, result: cli_fakes.Result, message: str) -> None:
        """Track one errored at the strip with *message*; the others were stripped."""
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn(f"ERROR strip: {message}", result.stdout)
        self.assertIn("stripped 2 · errors 1", result.stdout)
        first, *rest = self.manifest().tracks
        assert first.after is not None and first.after.error is not None
        self.assertEqual(first.after.error.stage, lr.ErrorStage.STRIP)
        self.assertEqual(first.after.error.message, message)
        self.assertEqual(
            [t.after.status for t in rest if t.after is not None],
            [lr.OutcomeStatus.STRIPPED] * 2,
        )
        self.assertEqual(rb.sidecars(self.paths[2]), {})

    def test_a_folder_under_a_sidecar_name_is_that_tracks_error(self) -> None:
        txt = self.paths[0].with_suffix(".txt")
        txt.unlink()
        txt.mkdir()
        result = self.strip()
        self.assert_first_failed(
            result, "01 Keeper.txt is not a file or a link; left as it is"
        )
        self.assertEqual(rb.lyrics_frames(self.paths[0]), Counter(_FRAMES))
        self.assertEqual(rb.sidecars(self.paths[0]), {"01 Keeper.lrc": _LRC.encode()})
        self.assertTrue(txt.is_dir())

    def test_an_injected_error_is_that_tracks_error(self) -> None:
        def strip_track(
            mp3: Path, *, frames: bool = True, sidecars: bool = True
        ) -> lr.StripResult:
            if mp3.name.startswith("01"):
                raise OSError("disk full")
            return lyrics_strip.strip_track(mp3, frames=frames, sidecars=sidecars)

        self.overrides["strip_track"] = strip_track
        self.assert_first_failed(self.strip(), "disk full")


class StripInterruptTest(StripCliCase):
    """Ctrl-C during a strip waits for it, records it, and stops the run."""

    def test_an_interrupt_records_the_strip_then_stops(self) -> None:
        before = rb.snapshots(self.paths)

        def strip_track(
            mp3: Path, *, frames: bool = True, sidecars: bool = True
        ) -> lr.StripResult:
            stripped = lyrics_strip.strip_track(mp3, frames=frames, sidecars=sidecars)
            signal.raise_signal(signal.SIGINT)
            return stripped

        self.overrides["strip_track"] = strip_track
        result = self.strip()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertIn(f"ERROR strip: {lyrics_outcomes.INTERRUPTED}", result.stdout)
        self.assertEqual(result.stdout.count(lyrics_summary.NOT_REACHED), 2)
        first = self.manifest().tracks[0].after
        assert first is not None
        self.assertEqual(
            (first.sidecars_removed, first.frames_written),
            (("01 Keeper.lrc", "01 Keeper.txt"), True),
        )
        self.assertEqual(rb.snapshots(self.paths[1:]), dict(list(before.items())[1:]))
        self.assertFalse(run_fakes.lock_held(self.album))
        self.overrides.clear()
        self.assertEqual(
            self.main("--album", str(self.album), "--mode", "undo").code,
            album.EXIT_OK,
        )
        self.assertEqual(rb.snapshots(self.paths), before)


class StripRefusalTest(StripCliCase):
    """Strip refuses every option it has no use for, before anything changes."""

    def test_every_misplaced_option_is_refused(self) -> None:
        for option in (
            ("--run-id", RUN_ID),
            ("--dry-run",),
            ("--offline",),
            ("--config", str(self.tmp / "config.toml")),
            ("--no-genius",),
            ("--lang", "deu"),
        ):
            with self.subTest(option=option[0]):
                result = self.strip(*option)
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn(
                    f"{option[0]} cannot be used with --mode strip", result.stderr
                )
                self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())


class StripLinkTest(StripCliCase):
    """A linked sidecar: the strip removes the link, undo puts the link itself back."""

    def test_undo_puts_back_the_link_to_the_same_target(self) -> None:
        target = self.tmp / "shared lyrics.lrc"
        target.write_text(_LRC, encoding="utf-8", newline="")
        link = self.paths[1].with_suffix(".lrc")
        link.symlink_to(target)
        self.assertEqual(self.strip().code, album.EXIT_OK)
        self.assertFalse(link.is_symlink() or link.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), _LRC)
        result = self.main("--album", str(self.album), "--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertEqual(os.readlink(link), str(target))
        self.assertEqual(target.read_text(encoding="utf-8"), _LRC)


class StripSummaryTest(fixtures.IsolatedCase):
    """The strip summary: what each track lost, an error, a track never reached."""

    def test_errors_and_unreached_tracks_are_shown(self) -> None:
        error = lr.StageError(lr.ErrorStage.STRIP, "OSError", "disk full")
        tracks = (
            lr.ManifestTrack(
                "01.mp3",
                (2, 4),
                None,
                lr.TrackOutcome(lr.OutcomeStatus.STRIPPED, frames_written=True),
            ),
            lr.ManifestTrack(
                "02.mp3",
                (2, 4),
                None,
                lr.TrackOutcome(lr.OutcomeStatus.ERROR, error=error),
            ),
            lr.ManifestTrack("03.mp3", (2, 4), None),
        )
        report = lyrics_run.RunReport("20261015T101500Z", tracks, interrupted=True)
        text = lyrics_summary.format_strip(
            report, album=Path("Some Album"), prog="album.py", command="album.py"
        )
        self.assertEqual(
            text,
            "album strip 20261015T101500Z: 3 tracks under Some Album\n"
            "\n"
            " #  track   removed\n"
            " 1  01.mp3  frames\n"
            " 2  02.mp3  ERROR strip: disk full\n"
            " 3  03.mp3  not reached\n"
            "stripped 1 · errors 1\n"
            "backup: .lyricsmith/20261015T101500Z/   "
            "undo: album.py --album 'Some Album' --mode undo\n",
        )

    def test_the_progress_verdict_names_what_was_removed(self) -> None:
        outcome = lr.TrackOutcome(
            lr.OutcomeStatus.STRIPPED, sidecars_removed=("01.txt",)
        )
        self.assertEqual(lyrics_outcomes.describe_outcome(outcome), "stripped (.txt)")
