"""Undo: a run's backup put back, frame for frame and sidecar for sidecar.

Each test backs an album up with the real backup stage, changes it as a run does
through the real tag and sidecar stages (`run_builders.simulate_run`), undoes it, and
compares the album with a snapshot taken before the run: every lyrics frame field by
field, the other frames, the ID3v1 tail and the sidecars' bytes. Every lyric here is
invented.
"""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from unittest import mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages as ls  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags as lt  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_undo as lu  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above

# Bound to its protocol, so the type checker holds it to the orchestrator's call.
_UNDO: ls.UndoRun = lu.undo_run

_V23, _V24 = (2, 3), (2, 4)
_TXT = "A harbor bell, a harbor bell\nrings out the tide we know so well\n"
_LRC = "[00:01.00]Gulls above the ferry\n[00:04.50]count the bridges home\n"


def _mixed(minor: int) -> tuple[lm.FrameRecord, ...]:
    """Two USLT and three SYLT frames a v2.*minor* tag can hold, every field varied."""
    wide = tb.UTF16 if minor == 3 else tb.UTF8
    return (
        tb.uslt("a ferry hums below the hill", encoding=tb.LATIN1),
        tb.uslt("eine Fähre summt", lang="deu", desc="alt", encoding=wide),
        tb.sylt((200, "first"), (100, "second, stored after"), encoding=wide),
        tb.sylt((40, "counted"), lang="fra", desc="frames", time_format=tb.MPEG_FRAMES),
        tb.sylt((1000, "plain timed line"), (2500, "and its second"), desc="clean"),
    )


class FullCycleTest(rb.UndoCase):
    """Backup, run and undo leave every track as it was before the run."""

    def test_v23_and_v24_tracks_come_back_frame_for_frame(self) -> None:
        tracks = [
            self.track("CD1/01 Three.mp3", _mixed(3), minor=3, txt=_TXT),
            self.track("CD2/01 Four.mp3", _mixed(4), lrc=_LRC),
            self.track("02 Both.mp3", _mixed(4), txt=_TXT, lrc=_LRC),
        ]
        tb.append_v1_tail(tracks[0])
        before = rb.snapshots(tracks)
        manifest = rb.simulate_run(self.album, self.back_up(*tracks))
        self.assertNotEqual(rb.snapshots(tracks), before)
        report = self.undo()
        self.assertFalse(report.has_errors)
        self.assertTrue(report.newest)
        self.assertEqual(report.run_id, manifest.run_id)
        self.assert_statuses(report, *[lr.UndoStatus.UNDONE] * 3)
        self.assertEqual(rb.snapshots(tracks), before)
        self.assertIs(self.manifest(manifest.run_id).state, lr.RunState.UNDONE)

    def test_a_track_with_no_lyrics_ends_with_none(self) -> None:
        mp3 = self.track("01.mp3")
        rb.simulate_run(self.album, self.back_up(mp3), rb.RUN_PLAIN)
        self.assertTrue(rb.lyrics_frames(mp3))
        self.undo()
        self.assertEqual(rb.lyrics_frames(mp3), {})
        self.assertEqual(rb.sidecars(mp3), {})

    def test_frames_an_lrc_cannot_carry_come_back_from_their_json_copy(self) -> None:
        frames = (
            tb.sylt((40, "counted"), time_format=tb.MPEG_FRAMES),
            tb.sylt((100, "\nbroken"), desc="newline"),
            tb.sylt((900, "late"), (100, "early"), desc="disorder"),
            tb.sylt((100, " padded "), desc="space"),
        )
        mp3 = self.track("01.mp3", frames)
        before = rb.snapshot(mp3)
        manifest = rb.simulate_run(self.album, self.back_up(mp3))
        track_before = manifest.tracks[0].before
        assert track_before is not None
        self.assertTrue(all(export.exact for export in track_before.frames))
        self.undo()
        self.assertEqual(rb.snapshot(mp3), before)

    def test_an_album_moved_since_the_run_still_undoes(self) -> None:
        mp3 = self.track("CD1/01.mp3", _mixed(4), txt=_TXT)
        before = rb.snapshot(mp3)
        rb.simulate_run(self.album, self.back_up(mp3))
        moved = self.tmp / "renamed album"
        self.album.rename(moved)
        self.album = moved
        self.assertFalse(self.undo().has_errors)
        self.assertEqual(rb.snapshot(moved / "CD1/01.mp3"), before)

    def test_a_run_killed_after_its_backup_still_undoes(self) -> None:
        tracks = [
            self.track("01.mp3", _mixed(4), lrc="no stamps here\n"),
            self.track("02.mp3"),
        ]
        before = rb.snapshots(tracks)
        manifest = rb.simulate_run(self.album, self.back_up(*tracks), record=False)
        self.assertIs(self.manifest(manifest.run_id).state, lr.RunState.BACKED_UP)
        self.assertFalse(self.undo().has_errors)
        self.assertEqual(rb.snapshots(tracks), before)


class SidecarRestoreTest(rb.UndoCase):
    """Sidecars come back by name, whatever kind the run left under that name."""

    def test_a_renamed_lrc_comes_back_and_its_rename_goes(self) -> None:
        mp3 = self.track("01.mp3", lrc="an untimed verse\n")
        rb.simulate_run(self.album, self.back_up(mp3), rb.RUN_PLAIN)
        self.assertEqual(sorted(rb.sidecars(mp3)), ["01.txt"])
        self.undo()
        self.assertEqual(rb.sidecars(mp3), {"01.lrc": b"an untimed verse\n"})

    def test_a_txt_whose_name_survived_gets_its_own_content_back(self) -> None:
        mp3 = self.track("01.mp3", txt=_TXT)
        rb.simulate_run(self.album, self.back_up(mp3), rb.RUN_PLAIN)
        self.assertNotEqual(rb.sidecars(mp3)["01.txt"], _TXT.encode())
        self.undo()
        self.assertEqual(rb.sidecars(mp3), {"01.txt": _TXT.encode()})

    def test_a_sidecar_name_that_is_not_the_tracks_is_an_error(self) -> None:
        mp3 = self.track("01.mp3", txt=_TXT)
        manifest = rb.simulate_run(self.album, self.back_up(mp3))
        track = manifest.tracks[0]
        assert track.before is not None
        stray = lr.SidecarCopy("02.mp3", track.before.sidecars[0].file)
        edited = lr.Manifest(
            manifest.run_id,
            manifest.lang,
            manifest.state,
            manifest.genius,
            (
                lr.ManifestTrack(
                    track.path,
                    track.id3_version,
                    lr.TrackBefore(track.before.frames, (stray,)),
                    track.after,
                ),
            ),
        )
        lmf.write_manifest(lr.run_folder(self.album, manifest.run_id), edited)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ERROR)
        self.assertFalse((self.album / "02.mp3").exists())


class SelectionTest(rb.UndoCase):
    """Undo by run and by track; a re-undo is a no-op; the newest run is reported."""

    def test_one_track_at_a_time_then_the_run_is_undone(self) -> None:
        first, second = self.track("01.mp3"), self.track("02.mp3")
        manifest = rb.simulate_run(self.album, self.back_up(first, second))
        report = self.undo(track="02.mp3")
        self.assertEqual([t.path for t in report.tracks], ["02.mp3"])
        self.assertEqual(rb.lyrics_frames(second), {})
        self.assertTrue(rb.lyrics_frames(first))
        self.assertIs(self.manifest(manifest.run_id).state, lr.RunState.COMPLETE)
        self.undo(track="01.mp3")
        self.assertIs(self.manifest(manifest.run_id).state, lr.RunState.UNDONE)

    def test_a_second_undo_changes_nothing_and_says_so(self) -> None:
        mp3 = self.track("01.mp3", _mixed(4))
        rb.simulate_run(self.album, self.back_up(mp3))
        self.undo()
        after_first = mp3.read_bytes()
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ALREADY_UNDONE)
        self.assertFalse(report.has_errors)
        self.assertEqual(mp3.read_bytes(), after_first)

    def test_an_older_run_is_undone_but_reported_as_not_the_newest(self) -> None:
        mp3 = self.track("01.mp3", txt=_TXT)
        before = rb.snapshot(mp3)
        older = rb.simulate_run(self.album, self.back_up(mp3), rb.RUN_PLAIN)
        later = run_fakes.STARTED + dt.timedelta(minutes=5)
        rb.simulate_run(self.album, self.back_up(mp3, started=later))
        report = self.undo(run_id=older.run_id)
        self.assertFalse(report.newest)
        self.assertFalse(report.has_errors)
        self.assertEqual(rb.snapshot(mp3), before)

    def test_a_track_the_run_does_not_list_is_refused(self) -> None:
        listed = self.track("01.mp3")
        self.track("02.mp3")
        manifest = rb.simulate_run(self.album, self.back_up(listed))
        frames = rb.lyrics_frames(listed)
        with self.assertRaisesRegex(lm.TrackNotInRunError, "02.mp3"):
            self.undo(track="02.mp3")
        self.assertEqual(rb.lyrics_frames(listed), frames)
        self.assertFalse(self.manifest(manifest.run_id).tracks[0].undone)

    def test_no_run_to_undo_is_no_run(self) -> None:
        with self.assertRaises(lm.NoRunError):
            self.undo()

    def test_an_unknown_schema_is_refused_before_anything_changes(self) -> None:
        mp3 = self.track("01.mp3")
        manifest = rb.simulate_run(self.album, self.back_up(mp3))
        path = lr.run_folder(self.album, manifest.run_id) / lr.MANIFEST_NAME
        path.write_bytes(path.read_bytes().replace(b'"schema": 1', b'"schema": 2'))
        state = mp3.read_bytes()
        with self.assertRaises(lm.ManifestSchemaError):
            self.undo()
        self.assertEqual(mp3.read_bytes(), state)


class PerTrackErrorTest(rb.UndoCase):
    """One track's undo error never stops the others."""

    def test_a_track_deleted_since_the_run_fails_alone(self) -> None:
        gone, kept = self.track("01.mp3"), self.track("02.mp3", _mixed(4))
        before = rb.snapshot(kept)
        manifest = rb.simulate_run(self.album, self.back_up(gone, kept))
        gone.unlink()
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ERROR, lr.UndoStatus.UNDONE)
        self.assertEqual(
            rb.undo_error(report.tracks[0]),
            "TrackRefusedError: 01.mp3 is missing from the album",
        )
        self.assertTrue(report.has_errors)
        self.assertEqual(rb.snapshot(kept), before)
        self.assertIs(self.manifest(manifest.run_id).state, lr.RunState.COMPLETE)

    def test_an_os_error_while_checking_a_track_is_that_tracks_error(self) -> None:
        unreadable, kept = self.track("01.mp3"), self.track("02.mp3", _mixed(4))
        before = rb.snapshot(kept)
        rb.simulate_run(self.album, self.back_up(unreadable, kept))
        real = lu._track_refusal

        def refusal(album: Path, mp3: Path, relpath: str) -> str | None:
            if relpath == "01.mp3":
                raise PermissionError("the folder cannot be read")
            return real(album, mp3, relpath)

        with mock.patch.object(lu, "_track_refusal", refusal):
            report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ERROR, lr.UndoStatus.UNDONE)
        self.assertIn("PermissionError", rb.undo_error(report.tracks[0]))
        self.assertEqual(rb.snapshot(kept), before)

    def test_a_track_whose_backup_failed_has_nothing_to_undo(self) -> None:
        failed, kept = self.track("01.mp3"), self.track("02.mp3")
        failed_bytes = failed.read_bytes()
        failed.unlink()
        manifest = self.back_up(failed, kept)
        failed.write_bytes(failed_bytes)
        rb.simulate_run(self.album, manifest)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ALREADY_UNDONE, lr.UndoStatus.UNDONE)
        self.assertEqual(failed.read_bytes(), failed_bytes)
        self.assertIs(self.manifest(manifest.run_id).state, lr.RunState.UNDONE)

    def test_a_tag_rewritten_at_another_version_is_that_tracks_error(self) -> None:
        moved, kept = self.track("01.mp3", _mixed(4)), self.track("02.mp3")
        rb.simulate_run(self.album, self.back_up(moved, kept))
        tb.build_rich_mp3(moved, 3)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ERROR, lr.UndoStatus.UNDONE)
        self.assertIn("TagVersionError", rb.undo_error(report.tracks[0]))

    def test_twin_frames_a_tag_cannot_hold_are_that_tracks_error(self) -> None:
        twin, kept = self.track("01.mp3", (tb.uslt(_TXT),)), self.track("02.mp3")
        manifest = rb.simulate_run(self.album, self.back_up(twin, kept))
        track = manifest.tracks[0]
        assert track.before is not None
        doubled = lr.TrackBefore(track.before.frames * 2, ())
        tracks = (
            lr.ManifestTrack(track.path, track.id3_version, doubled, track.after),
            manifest.tracks[1],
        )
        edited = lr.Manifest(
            manifest.run_id, "eng", manifest.state, manifest.genius, tracks
        )
        lmf.write_manifest(lr.run_folder(self.album, manifest.run_id), edited)
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.ERROR, lr.UndoStatus.UNDONE)
        self.assertIn("ReadBackError", rb.undo_error(report.tracks[0]))

    def test_an_untouched_track_is_not_rewritten(self) -> None:
        v22 = tb.build_v22_mp3(self.album / "01.mp3", lyrics="an old verse")
        sidecar = v22.with_suffix(".txt")
        sidecar.write_text(_TXT, encoding="utf-8", newline="")
        state, inode = v22.read_bytes(), sidecar.stat().st_ino
        manifest = self.back_up(v22)
        self.assertEqual(manifest.tracks[0].id3_version, (2, 2))
        report = self.undo()
        self.assert_statuses(report, lr.UndoStatus.UNDONE)
        self.assertEqual(v22.read_bytes(), state)
        # An atomic replace gives the file a new inode; an untouched sidecar keeps its own.
        self.assertEqual(sidecar.stat().st_ino, inode)


class NoHeaderTest(rb.UndoCase):
    """A track with no ID3v2 header before the run loses the run's lyrics, nothing else."""

    def test_a_tagless_track_keeps_an_empty_tag_and_its_audio(self) -> None:
        mp3 = self.track("01.mp3", minor=None)
        audio = mp3.read_bytes()
        rb.simulate_run(self.album, self.back_up(mp3))
        self.undo()
        tags = lt.read_tags(mp3)
        self.assertEqual(tags.id3_version, _V24)
        self.assertEqual(tags.frames, ())
        self.assertEqual(tb.other_frames(mp3), [])
        self.assertTrue(mp3.read_bytes().endswith(audio))

    def test_a_v1_only_track_keeps_its_tail_and_the_fields_carried_into_v2(
        self,
    ) -> None:
        mp3 = tb.build_v1_only_mp3(self.album / "01.mp3", "Tail Artist")
        tail = mp3.read_bytes()[-tb.V1_TAG_BYTES :]
        rb.simulate_run(self.album, self.back_up(mp3))
        carried = tb.other_frames(mp3)
        self.undo()
        self.assertEqual(rb.lyrics_frames(mp3), {})
        self.assertEqual(tb.other_frames(mp3), carried)
        self.assertTrue(any("Tail Artist" in frame for frame in carried))
        self.assertEqual(mp3.read_bytes()[-tb.V1_TAG_BYTES :], tail)


class TwinFramesOnDiskTest(rb.UndoCase):
    """Of two stored frames sharing a key, the one every reader sees is backed up."""

    def test_the_last_twin_is_recorded_and_restored(self) -> None:
        mp3 = self.track(
            "01.mp3",
            (
                tb.uslt("first twin", desc="a", encoding=tb.LATIN1),
                tb.uslt("second twin", desc="b", encoding=tb.LATIN1),
            ),
        )
        data = mp3.read_bytes()
        marker = b"engb\x00second twin"
        self.assertIn(marker, data)
        mp3.write_bytes(data.replace(marker, b"enga\x00second twin"))
        seen = rb.lyrics_frames(mp3)
        self.assertEqual(len(seen), 1)
        manifest = rb.simulate_run(self.album, self.back_up(mp3))
        before = manifest.tracks[0].before
        assert before is not None
        self.assertEqual(len(before.frames), 1)
        self.undo()
        self.assertEqual(rb.lyrics_frames(mp3), seen)


if __name__ == "__main__":
    unittest.main()
