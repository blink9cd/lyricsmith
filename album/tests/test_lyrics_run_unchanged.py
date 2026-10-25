"""A track a run would leave exactly as it found it is reported unchanged, and not saved.

When the lyrics frames a run would write equal those the track holds, its MP3 is not
saved: its bytes and its modification time stay. A sidecar whose bytes already match
is not written again either. A track for which nothing at all changed is `unchanged`:
its row says so beside its source and kind, the counts line counts it once, and undo
of it changes nothing. Each test drives the entry point with its real wiring; only the
clock and the provider table are faked, and every lyric is invented.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import cast

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_embedded import (  # noqa: E402 - import follows the sys.path bootstrap above
    KEEPER,
    EmbeddedCase,
)

#: An mtime long before any test runs, in nanoseconds, so a save or a write would show.
OLD_NS = 1_000_000_000 * 1_000_000_000
#: The keeper's own timed sidecar, and another transcription that replaces it.
_LRC = "[00:01.00]A lamp swings on the landing\n[00:04.50]and the stairs hold still\n"
_EDITED_LRC = (
    "[00:01.00]A lamp sways on the landing\n[00:04.50]and the stairs go still\n"
)
#: The keeper's embedded timed lines, which a .lrc cannot hold whole.
_BROKEN = tag_builders.sylt((1000, "A lamp swings\non the landing"))
#: The ID3v2.2 keeper's embedded untimed words.
_V22_TEXT = "A kettle ticks on the stove\nand the window fogs\n"


def track_files(mp3: Path) -> dict[str, tuple[bytes, int]]:
    """*mp3* and each sidecar beside it, by name: its bytes and modification time."""
    found = {}
    for path in (mp3, mp3.with_suffix(".lrc"), mp3.with_suffix(".txt")):
        if path.is_file():
            found[path.name] = (path.read_bytes(), path.stat().st_mtime_ns)
    return found


def age_track_files(mp3: Path) -> dict[str, tuple[bytes, int]]:
    """Set *mp3*'s and its sidecars' mtimes back to `OLD_NS`; return `track_files`."""
    for name in track_files(mp3):
        os.utime(mp3.parent / name, ns=(OLD_NS, OLD_NS))
    return track_files(mp3)


class UnchangedCase(EmbeddedCase):
    """Runs the album twice, reading each run's manifest by its own id."""

    def setUp(self) -> None:
        """No provider answers anything, so what a track holds is what serves it."""
        super().setUp()
        self.answer_steps(synced={}, plain={})

    def newest_afters(self) -> list[lr.TrackOutcome | None]:
        """Each track's outcome in the newest run's manifest."""
        run_dir = lyrics_backup.select_run(self.album, None)
        return [t.after for t in lyrics_manifest.read_manifest(run_dir).tracks]

    def run_ok(self, *extra: str) -> cli_fakes.Result:
        """Run with *extra*; assert it exited 0."""
        result = self.run_album(*extra)
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        return result

    def keeper_after(self) -> lr.TrackOutcome:
        """The keeper's outcome in the newest run."""
        keeper = self.newest_afters()[0]
        assert keeper is not None
        return keeper

    def assert_unchanged(self, keeper: lr.TrackOutcome) -> None:
        """*keeper* is unchanged: no frames saved, no sidecar written or removed."""
        self.assertEqual(keeper.status, lr.OutcomeStatus.UNCHANGED)
        self.assertFalse(keeper.frames_written)
        self.assertIsNone(keeper.sidecar_written)
        self.assertEqual((keeper.sidecars_removed, keeper.sidecar_hashes), ((), ()))


class SecondRunTest(UnchangedCase):
    """A second run over what a first run wrote saves nothing."""

    def first_run(self) -> Path:
        """The keeper with its own .lrc, run once: its frames written from it."""
        paths = self.keeper(lrc=_LRC)
        self.run_ok()
        self.assertEqual(self.keeper_after().status, lr.OutcomeStatus.WRITTEN)
        return paths[0]

    def test_the_mp3_and_its_sidecar_keep_their_bytes_and_mtimes(self) -> None:
        mp3 = self.first_run()
        before = age_track_files(mp3)
        self.run_ok()
        self.assertEqual(track_files(mp3), before)
        keeper = self.keeper_after()
        self.assert_unchanged(keeper)
        # The .lrc holds the frames' own lyrics now, so the frames serve the track.
        self.assertEqual(
            (keeper.source, keeper.kind), (lm.Source.FRAMES, lm.LyricsKind.SYNCED)
        )

    def test_the_unchanged_track_is_still_checked(self) -> None:
        self.first_run()
        self.run_ok()
        check = self.keeper_after().check
        assert check is not None
        self.assertEqual(check.verdict, lr.CheckVerdict.PASS)
        result = self.run_ok("--mode", "check")
        rows = [line.split() for line in result.stdout.splitlines()]
        self.assertIn(["1", "01", "Keeper.mp3", "PASS"], rows)

    def test_the_row_and_the_counts_say_unchanged(self) -> None:
        self.first_run()
        result = self.run_ok()
        (row,) = [line for line in result.stdout.splitlines() if KEEPER in line]
        self.assertEqual(row.split()[3:], ["frames", "synced", "unchanged", "(PASS)"])
        (counts,) = [r for r in result.stdout.splitlines() if r.startswith("synced")]
        self.assertTrue(
            counts.startswith("synced 0 · unsynced 0 · unchanged 1 · not found 1"),
            counts,
        )

    def test_a_changed_sidecar_is_still_embedded_and_read_back(self) -> None:
        mp3 = self.first_run()
        mp3.with_suffix(".lrc").write_text(_EDITED_LRC, encoding="utf-8", newline="")
        age_track_files(mp3)
        self.run_ok()
        keeper = self.keeper_after()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertTrue(keeper.frames_written)
        self.assertNotEqual(mp3.stat().st_mtime_ns, OLD_NS)
        self.assertEqual(self.embedded(mp3), lm.parse_lrc(_EDITED_LRC))

    def test_undo_of_the_unchanged_run_changes_nothing(self) -> None:
        mp3 = self.first_run()
        self.run_ok()
        before = age_track_files(mp3)
        result = self.run_ok("--mode", "undo")
        self.assertNotIn("displaced", result.stdout)
        self.assertEqual(track_files(mp3), before)


class FreshUnchangedTest(UnchangedCase):
    """A fresh run whose answer the track already holds strips and writes nothing."""

    def setUp(self) -> None:
        """The timed step answers the keeper."""
        super().setUp()
        self.answer_steps(synced={"The Keeper": fixtures.LIGHTHOUSE_LRC}, plain={})

    def test_an_identical_answer_leaves_the_track_unchanged(self) -> None:
        mp3 = self.keeper()[0]
        self.run_ok("--fresh")
        self.assertEqual(self.keeper_after().status, lr.OutcomeStatus.WRITTEN)
        before = age_track_files(mp3)
        self.run_ok("--fresh")
        self.assertEqual(track_files(mp3), before)
        keeper = self.keeper_after()
        self.assert_unchanged(keeper)
        self.assertEqual(keeper.source, lm.Source.LRCLIB)

    def test_the_identical_sidecar_survives_undo(self) -> None:
        mp3 = self.keeper()[0]
        self.run_ok("--fresh")
        self.run_ok("--fresh")
        before = age_track_files(mp3)
        self.assertIn("01 Keeper.lrc", before)
        result = self.run_ok("--mode", "undo")
        self.assertNotIn("displaced", result.stdout)
        self.assertEqual(track_files(mp3), before)

    def test_undo_brings_back_the_txt_beside_an_identical_lrc(self) -> None:
        # The .lrc already holds the answer's bytes, so it is left; only the .txt goes.
        mp3 = self.keeper()[0]
        self.run_ok("--fresh")
        txt = b"Older invented words\n"
        mp3.with_suffix(".txt").write_bytes(txt)
        before = age_track_files(mp3)
        self.run_ok("--fresh")
        self.assertEqual(self.keeper_after().sidecars_removed, ("01 Keeper.txt",))
        kept = {name: before[name] for name in before if name != "01 Keeper.txt"}
        self.assertEqual(track_files(mp3), kept)
        result = self.run_ok("--mode", "undo")
        self.assertNotIn("displaced", result.stdout)
        after = track_files(mp3)
        self.assertEqual(after.pop("01 Keeper.txt")[0], txt)
        self.assertEqual(after, kept)

    def test_a_folder_under_a_sidecar_name_is_still_refused(self) -> None:
        mp3 = self.keeper()[0]
        self.run_ok("--fresh")
        mp3.with_suffix(".txt").mkdir()
        before = mp3.read_bytes()
        result = self.run_album("--fresh")
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        keeper = self.keeper_after()
        assert keeper.error is not None
        self.assertIs(keeper.error.stage, lr.ErrorStage.STRIP)
        self.assertEqual(mp3.read_bytes(), before)


class DescribeUnchangedTest(fixtures.IsolatedCase):
    """How an unchanged outcome reads in a row and in a progress line."""

    def test_the_status_carries_the_verdict_when_there_is_one(self) -> None:
        bare = lr.TrackOutcome(lr.OutcomeStatus.UNCHANGED)
        checked = lr.TrackOutcome(lr.OutcomeStatus.UNCHANGED, check=mb.CHECK)
        self.assertEqual(lyrics_outcomes.describe_status(bare), "unchanged")
        self.assertEqual(
            lyrics_outcomes.describe_status(checked),
            f"unchanged ({mb.CHECK.summary()})",
        )

    def test_the_progress_line_names_the_kind_and_source(self) -> None:
        outcome = lr.TrackOutcome(
            lr.OutcomeStatus.UNCHANGED, lm.Source.SIDECAR, lm.LyricsKind.SYNCED
        )
        self.assertEqual(
            lyrics_outcomes.describe_outcome(outcome), "unchanged synced (sidecar)"
        )


class FramesServedUnchangedTest(UnchangedCase):
    """A track served from its frames, with no sidecar to write, is unchanged."""

    def test_frames_a_lrc_cannot_hold_leave_the_track_unchanged(self) -> None:
        mp3 = self.keeper(_BROKEN)[0]
        before = age_track_files(mp3)
        result = self.run_ok()
        self.assertEqual(track_files(mp3), before)
        keeper = self.keeper_after()
        self.assert_unchanged(keeper)
        self.assertEqual(
            (keeper.source, keeper.kind), (lm.Source.FRAMES, lm.LyricsKind.SYNCED)
        )
        assert keeper.check is not None
        (row,) = [line for line in result.stdout.splitlines() if KEEPER in line]
        self.assertEqual(row.split()[3:6], ["frames", "synced", "unchanged"])

    def test_a_failed_sidecar_write_is_the_sidecar_stage_error(self) -> None:
        def write_sidecar(mp3: Path, lyrics: lm.Lyrics) -> lm.SidecarWrite:
            raise OSError("the disk is full")

        self.overrides["write_sidecar"] = write_sidecar
        mp3 = self.keeper(tag_builders.sylt((1000, "A lamp swings")))[0]
        before = mp3.read_bytes()
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        keeper = self.keeper_after()
        assert keeper.error is not None
        self.assertIs(keeper.error.stage, lr.ErrorStage.SIDECAR)
        self.assertFalse(keeper.frames_written)
        self.assertEqual(mp3.read_bytes(), before)


class FramesServedUndoTest(UnchangedCase):
    """Undo of a frames-served track puts back what its sidecar write replaced."""

    def test_undo_brings_back_the_txt_the_synced_frames_replaced(self) -> None:
        txt = "Older invented words\n"
        mp3 = self.keeper(tag_builders.sylt((1000, "A lamp swings")), txt=txt)[0]
        before = rb.snapshot(mp3)
        self.run_ok()
        self.assertEqual(list(rb.sidecars(mp3)), ["01 Keeper.lrc"])
        self.run_ok("--mode", "undo")
        self.assertEqual(rb.snapshot(mp3), before)
        self.assertEqual(rb.sidecars(mp3), {"01 Keeper.txt": txt.encode()})

    def test_undo_of_an_id3v2_2_track_leaves_its_bytes(self) -> None:
        fixtures.build_album(self.album, ())
        mp3 = tag_builders.build_v22_mp3(self.album / KEEPER, lyrics=_V22_TEXT)
        before = mp3.read_bytes()
        self.run_ok()
        self.assertIn("01 Keeper.txt", rb.sidecars(mp3))
        result = self.run_ok("--mode", "undo")
        self.assertNotIn("ERROR", result.stdout)
        self.assertEqual(mp3.read_bytes(), before)
        self.assertEqual(rb.sidecars(mp3), {})


class UnchangedRecordTest(fixtures.IsolatedCase):
    """The unchanged status goes through the manifest, and older manifests still read."""

    def test_an_unchanged_outcome_round_trips(self) -> None:
        after = lr.TrackOutcome(
            lr.OutcomeStatus.UNCHANGED,
            lm.Source.FRAMES,
            lm.LyricsKind.SYNCED,
            check=mb.CHECK,
        )
        track = lr.ManifestTrack("01.mp3", (2, 4), lr.TrackBefore((), ()), after)
        manifest = lr.Manifest(
            "20261014T153012Z",
            "eng",
            lr.RunState.COMPLETE,
            lr.GeniusState.ABSENT,
            (track,),
        )
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        lyrics_manifest.write_manifest(run_dir, manifest)
        self.assertEqual(lyrics_manifest.read_manifest(run_dir), manifest)
        self.assertEqual(str(lr.OutcomeStatus.UNCHANGED), "unchanged")

    def test_a_manifest_written_before_the_status_still_reads(self) -> None:
        document = mb.document()
        statuses = {str(s) for s in lr.OutcomeStatus} - {"unchanged"}
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        for status in sorted(statuses):
            with self.subTest(status=status):
                after = cast(dict[str, object], mb.first_track(document)["after"])
                after["status"] = status
                (run_dir / lr.MANIFEST_NAME).write_bytes(mb.encode(document))
                read = lyrics_manifest.read_manifest(run_dir)
                first = read.tracks[0].after
                assert first is not None
                self.assertEqual(str(first.status), status)
