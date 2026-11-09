"""What a track held before the run, recorded on its outcome as ``after.held``.

Decided by the inventory's one rule for synced, untimed and none, over real lyrics only:
a placeholder counts as untimed, never synced, and a ``.lrc`` with no stamp reads as
untimed. The run records it before anything changes; the manifest keeps it; the
categories prefer it over reading the backup's record. Every name and lyric is invented.
"""

from __future__ import annotations

import abc
import dataclasses
import errno
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_categories as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_held as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes as lo  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh import FreshCliCase  # noqa: E402 - follows the sys.path bootstrap

H = lr.HeldBefore
W = lc.WrittenCategory
S = lm.LyricsKind.SYNCED
#: A real untimed frame, a real millisecond SYLT, and a SYLT of time stamps alone.
USLT = tag_builders.uslt(fixtures.ORCHARD_PLAIN)
SYLT = tag_builders.sylt((1000, "an invented line"), (4000, "and another"))
STAMPS = tag_builders.sylt((1000, ""), (4000, ""))
#: A SYLT timed in MPEG frames: words, but no millisecond timing.
MPEG = tag_builders.sylt((1000, "an invented line"), time_format=1)
#: A synced sidecar, an untimed one, and LRCLIB's instrumental stand-in.
TIMED = lm.SyncedLyrics((lm.SyncedLine(1000, "an invented line"),))
PLAIN = lm.PlainLyrics(fixtures.ORCHARD_PLAIN)
STAND_IN = lm.PlainLyrics("[au:instrumental]\n")


class HeldLyricsTest(fixtures.IsolatedCase):
    """The rule over a track's planned sidecar and its frames."""

    def test_each_shape(self) -> None:
        cases = [
            ("nothing", None, (), H.NOTHING),
            ("a real USLT", None, (USLT,), H.UNTIMED),
            ("a real SYLT", None, (SYLT,), H.SYNCED),
            ("an MPEG-timed SYLT", None, (MPEG,), H.UNTIMED),
            ("a placeholder SYLT beside a USLT", None, (STAMPS, USLT), H.UNTIMED),
            ("a placeholder SYLT alone", None, (STAMPS,), H.UNTIMED),
            ("an untimed sidecar", PLAIN, (), H.UNTIMED),
            ("a timed sidecar", TIMED, (USLT,), H.SYNCED),
            ("a placeholder sidecar", STAND_IN, (), H.UNTIMED),
            ("a blank sidecar", lm.PlainLyrics("  \n"), (), H.NOTHING),
        ]
        for name, sidecar, frames, expected in cases:
            with self.subTest(name):
                self.assertIs(lh.held_lyrics(sidecar, frames, "eng"), expected)


class CategoryTest(fixtures.IsolatedCase):
    """The categories prefer the recorded ``held`` over the backup's record."""

    @staticmethod
    def written(held: lr.HeldBefore | None) -> lr.ManifestTrack:
        """A written synced track whose backup records a SYLT, holding *held*."""
        sylt = lr.FrameExport(lm.FrameKind.SYLT, "eng", "", 3, 2, 1, "f/01.lrc")
        after = lr.TrackOutcome(
            lr.OutcomeStatus.WRITTEN, lm.Source.LRCLIB, S, held=held
        )
        return lr.ManifestTrack("01.mp3", (2, 4), lr.TrackBefore((sylt,), ()), after)

    def test_held_wins_over_the_backup(self) -> None:
        self.assertIs(lc.written_category(self.written(H.UNTIMED)), W.UPGRADED)
        self.assertIs(lc.written_category(self.written(H.NOTHING)), W.NEW_SYNCED)
        self.assertIs(lc.written_category(self.written(H.SYNCED)), W.SYNCED_REPLACED)

    def test_without_held_the_backup_is_read(self) -> None:
        self.assertIs(lc.written_category(self.written(None)), W.SYNCED_REPLACED)

    def test_held_needs_no_before(self) -> None:
        after = lr.TrackOutcome(
            lr.OutcomeStatus.WRITTEN, lm.Source.LRCLIB, S, held=H.UNTIMED
        )
        track = lr.ManifestTrack("01.mp3", (2, 4), None, after)
        self.assertIs(lc.written_category(track), W.UPGRADED)


class ManifestTest(fixtures.IsolatedCase):
    """The manifest keeps ``held``; an older one reads it as absent."""

    def test_it_round_trips(self) -> None:
        for held in H:
            with self.subTest(held=held):
                after = dataclasses.replace(mb.FULL_AFTER, held=held)
                track = dataclasses.replace(mb.FULL.tracks[0], after=after)
                manifest = dataclasses.replace(mb.FULL, tracks=(track,))
                parsed = lmf.parse_manifest(lmf.manifest_bytes(manifest))
                self.assertEqual(parsed, manifest)
                written = json.loads(lmf.manifest_bytes(manifest))
                after_doc = mb.first_track(written)["after"]
                assert isinstance(after_doc, dict)
                self.assertEqual(after_doc["held"], held.value)

    def test_an_older_manifest_reads_it_as_absent(self) -> None:
        document = mb.document()
        after = mb.first_track(document)["after"]
        assert isinstance(after, dict)
        after.pop("held", None)
        parsed = lmf.parse_manifest(mb.encode(document)).tracks[0].after
        assert parsed is not None
        self.assertIsNone(parsed.held)

    def test_an_unknown_value_is_refused(self) -> None:
        for value in ("none", "timed", 1, True):
            with self.subTest(value=value):
                document = mb.document()
                after = mb.first_track(document)["after"]
                assert isinstance(after, dict)
                after["held"] = value
                with self.assertRaisesRegex(lm.ManifestSchemaError, "held"):
                    lmf.parse_manifest(mb.encode(document))


class StateTest(fixtures.IsolatedCase):
    """Every outcome built from a track's state carries what it held."""

    def test_an_error_carries_it(self) -> None:
        state = lo.TrackState(held_kind=H.UNTIMED)
        self.assertIs(state.failed(OSError("an invented failure")).held, H.UNTIMED)
        self.assertIs(state.outcome(lr.OutcomeStatus.NOT_FOUND).held, H.UNTIMED)


class RunTest(FreshCliCase):
    """A real run records ``held`` before anything changes, and categorizes by it."""

    def held_after(self) -> lr.TrackOutcome:
        """The only track's outcome in the run's manifest."""
        (after,) = self.afters()
        assert after is not None
        return after

    def run_answered(self, track: TrackSpec, *frames: lm.FrameRecord) -> list[str]:
        """Run an album of *track*, holding *frames*, that LRCLIB answers synced."""
        (path,) = fixtures.build_album(self.album, (track,))
        if frames:
            tag_builders.plant_frames(path, frames)
        title = track.title or ""
        self.providers = run_fakes.table(synced={title: fixtures.LIGHTHOUSE_LRC})
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        return result.stdout.splitlines()

    def test_a_placeholder_sylt_beside_a_real_uslt_is_upgraded(self) -> None:
        lines = self.run_answered(
            TrackSpec("01 Mill.mp3", title="The Mill"), STAMPS, USLT
        )
        self.assertIs(self.held_after().held, H.UNTIMED)
        self.assertIn("written: upgraded 1", lines)

    def test_a_lrc_with_no_stamp_is_upgraded(self) -> None:
        unstamped = TrackSpec(
            "01 Mill.mp3", title="The Mill", lrc=fixtures.ORCHARD_PLAIN
        )
        lines = self.run_answered(unstamped)
        self.assertIs(self.held_after().held, H.UNTIMED)
        self.assertIn("written: upgraded 1", lines)

    def test_a_track_holding_nothing_is_new_synced(self) -> None:
        lines = self.run_answered(TrackSpec("01 Mill.mp3", title="The Mill"))
        self.assertIs(self.held_after().held, H.NOTHING)
        self.assertIn("written: new synced 1", lines)

    def test_a_fresh_run_over_real_synced_lyrics_is_synced_replaced(self) -> None:
        timed = TrackSpec("01 Mill.mp3", title="The Mill", lrc=fixtures.LIGHTHOUSE_LRC)
        fixtures.build_album(self.album, (timed,))
        self.providers = run_fakes.table(synced={"The Mill": fixtures.LIGHTHOUSE_LRC})
        result = self.run_album("--fresh")
        self.assertIs(self.held_after().held, H.SYNCED)
        self.assertIn("written: synced replaced 1", result.stdout.splitlines())

    def test_an_embed_error_carries_it(self) -> None:
        def write_lyrics(path: Path, lyrics: lm.Lyrics, lang: str) -> bool:
            raise OSError("an invented failure")

        self.overrides["write_lyrics"] = write_lyrics
        (path,) = fixtures.build_album(
            self.album, (TrackSpec("01 Mill.mp3", title="The Mill"),)
        )
        tag_builders.plant_frames(path, (USLT,))
        self.providers = run_fakes.table(synced={"The Mill": fixtures.LIGHTHOUSE_LRC})
        self.run_album()
        after = self.held_after()
        self.assertIs(after.status, lr.OutcomeStatus.ERROR)
        self.assertIs(after.held, H.UNTIMED)

    def test_an_unreadable_sidecar_leaves_it_unknown(self) -> None:
        (path,) = fixtures.build_album(
            self.album, (TrackSpec("01 Mill.mp3", title="The Mill"),)
        )
        path.with_suffix(".txt").write_bytes(b"\xff\xfe not UTF-8")
        self.run_album()
        self.assertIsNone(self.held_after().held)

    def test_a_strip_records_it(self) -> None:
        (path,) = fixtures.build_album(
            self.album, (TrackSpec("01 Mill.mp3", title="The Mill"),)
        )
        tag_builders.plant_frames(path, (SYLT,))
        result = self.main("--album", str(self.album), "--mode", "strip")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertIs(self.held_after().held, H.SYNCED)

    def test_a_strip_of_an_over_cap_frame_leaves_it_unknown(self) -> None:
        (path,) = fixtures.build_album(
            self.album, (TrackSpec("01 Mill.mp3", title="The Mill"),)
        )
        tag_builders.plant_frames(
            path, (tag_builders.uslt("w " * (lr.MAX_LYRICS_CHARS // 2 + 1)),)
        )
        result = self.main("--album", str(self.album), "--mode", "strip")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        after = self.held_after()
        self.assertIs(after.status, lr.OutcomeStatus.STRIPPED)
        self.assertIsNone(after.held)


def _mode_000_stops_a_read() -> bool:
    """True when ``chmod(0)`` stops this process reading a file.

    Root reads a mode-000 file, and on Windows ``chmod`` sets only the read-only
    attribute, which stops no read. Probed rather than inferred from the platform, so
    the real-permission tests run wherever they can; their twin raises the refusal
    through the open itself, which runs everywhere.
    """
    with TemporaryDirectory() as scratch:
        probe = Path(scratch) / "probe"
        probe.write_bytes(b"")
        probe.chmod(0)
        try:
            probe.read_bytes()
        except PermissionError:
            return True
        finally:
            probe.chmod(0o600)
    return False


#: Whether ``chmod(0)`` stops a read here; probed once, at import.
_MODE_000_LOCKS = _mode_000_stops_a_read()


class UnreadableLinkCase(FreshCliCase, abc.ABC):
    """A sidecar link inside the album whose target cannot be read leaves ``held`` unknown."""

    def setUp(self) -> None:
        """A track whose ``.txt`` links to an in-album file this process cannot read.

        The file is built as another track's sidecar, then moved off any MP3's name, so
        it belongs to no track and only the link reaches it.
        """
        super().setUp()
        self.path, words = fixtures.build_album(
            self.album,
            (
                TrackSpec("01 Mill.mp3", title="The Mill"),
                TrackSpec("02 Words.mp3", title="Words", txt=fixtures.ORCHARD_PLAIN),
            ),
        )
        target = words.with_suffix(".txt").rename(self.album / "notes.txt")
        self.path.with_suffix(".txt").symlink_to(target.name)
        self.lock(target)

    @abc.abstractmethod
    def lock(self, target: Path) -> None:
        """Make *target* unreadable to this process for the rest of the test."""

    def assert_a_strip_goes_on(self) -> None:
        """A strip over the track succeeds, its ``held`` unknown, the link gone."""
        result = self.main("--album", str(self.album), "--mode", "strip")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        after, _words = self.afters()
        assert after is not None
        self.assertIs(after.status, lr.OutcomeStatus.STRIPPED)
        self.assertIsNone(after.held)
        self.assertFalse(self.path.with_suffix(".txt").is_symlink())

    def assert_a_run_errors_at_the_sidecar_stage(self) -> None:
        """A run records the track's sidecar error, ``PermissionError``, ``held`` unknown."""
        self.run_album()
        after, _words = self.afters()
        assert after is not None and after.error is not None
        self.assertIs(after.error.stage, lr.ErrorStage.SIDECAR)
        self.assertEqual(after.error.exc_type, "PermissionError")
        self.assertIsNone(after.held)


@unittest.skipUnless(
    _MODE_000_LOCKS, "chmod(0) does not stop a read here (root, or Windows)"
)
class UnreadableLinkTest(UnreadableLinkCase):
    """The target is made unreadable by its mode, where a mode stops a read."""

    def lock(self, target: Path) -> None:
        """Take every permission off *target*; give them back at the cleanup."""
        target.chmod(0)
        self.addCleanup(target.chmod, 0o644)

    @tag_builders.needs_binding_modes
    def test_a_strip_goes_on(self) -> None:
        self.assert_a_strip_goes_on()

    @tag_builders.needs_binding_modes
    def test_a_run_errors_at_the_sidecar_stage_as_before(self) -> None:
        self.assert_a_run_errors_at_the_sidecar_stage()


class RefusedOpenLinkTest(UnreadableLinkCase):
    """The portable twin: each open of the target refuses it, as a mode-000 file's does."""

    def lock(self, target: Path) -> None:
        """Make every open of *target* raise ``PermissionError``, as a locked file's."""
        real = os.open
        locked = os.path.realpath(target)

        def refusing(name: str, flags: int, *args: int) -> int:
            if os.path.realpath(name) == locked:
                raise PermissionError(errno.EACCES, "Permission denied", str(name))
            return real(name, flags, *args)

        self.enterContext(mock.patch.object(os, "open", refusing))

    def test_a_strip_goes_on(self) -> None:
        self.assert_a_strip_goes_on()

    def test_a_run_errors_at_the_sidecar_stage_as_before(self) -> None:
        self.assert_a_run_errors_at_the_sidecar_stage()
