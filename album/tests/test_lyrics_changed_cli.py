"""--changed-list through the entry point: what a run, a strip and an undo list, and when.

Every stage is the real one and only the providers are faked, so the list names the
MP3s the real writers saved. The album is built from silent MP3s under the test's own
directory; every lyric is invented.
"""

from __future__ import annotations

import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_undo  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: One track found synced, one served by its own plain sidecar (so its frames are
#: written from it), and one nobody has words for, which the run never saves.
TRACKS = (
    TrackSpec("01 Keeper.mp3", title="The Keeper"),
    TrackSpec("CD2/02 Orchard.mp3", title="The Orchard", txt=fixtures.ORCHARD_PLAIN),
    TrackSpec("CD2/03 Mill.mp3", title="The Mill", id3_version=(2, 3)),
)


class ChangedListCase(cli_fakes.RealWiringCliCase):
    """The built album, its MP3s, and the list file's path."""

    def setUp(self) -> None:
        """Build the album; the providers answer the first track only."""
        super().setUp()
        self.paths = fixtures.build_album(self.album, TRACKS)
        self.keeper, self.orchard, self.mill = (self.album / t.relpath for t in TRACKS)
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        self.listed = self.tmp / "changed.txt"

    def changed(self) -> list[Path]:
        """The paths the list file holds, in order."""
        read = lyrics_changed.parse_changed_list(self.listed.read_bytes())
        assert read is not None
        return [Path(path) for path in read]

    def with_list(self, *extra: str) -> cli_fakes.Result:
        """Run the album with *extra* options and --changed-list."""
        return self.run_album(*extra, "--changed-list", str(self.listed))


class RunListTest(ChangedListCase):
    """A run lists each MP3 it saved, and only those."""

    def test_a_run_lists_each_track_whose_file_it_saved(self) -> None:
        result = self.with_list()
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertEqual(self.changed(), [self.keeper, self.orchard])

    def test_a_run_that_changed_nothing_writes_an_empty_list(self) -> None:
        self.run_album()
        result = self.with_list()
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertEqual(self.listed.read_bytes(), b"")

    def test_no_list_is_written_unless_one_is_named(self) -> None:
        self.run_album()
        self.assertFalse(self.listed.exists())

    def test_a_save_that_did_not_read_back_is_listed(self) -> None:
        # The file was saved and its confirmation then failed: the track errors, its
        # frames are not recorded as written, and its file still changed.
        def write_lyrics(path: Path, lyrics: lm.Lyrics, lang: str) -> bool:
            lyrics_tags.write_lyrics(path, lyrics, lang)
            raise lm.ReadBackError("the lyrics frames read back differ")

        self.overrides["write_lyrics"] = write_lyrics
        result = self.with_list()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertEqual(self.changed(), [self.keeper, self.orchard])

    def test_an_interrupted_run_lists_each_track_saved_before_it(self) -> None:
        def fetch(
            query: lm.TrackQuery,
            sidecar: lm.SidecarState,
            providers: lm.ProviderTable,
            *,
            deadline_s: float,
            embedded: lyrics_inventory.EmbeddedLyrics,
            duration_s: float | None,
        ) -> lm.FetchResult:
            if query.title == "The Orchard":
                raise KeyboardInterrupt
            return lyrics_fetch.fetch_lyrics(
                query,
                sidecar,
                providers,
                deadline_s=deadline_s,
                embedded=embedded,
                duration_s=duration_s,
            )

        self.overrides["fetch"] = fetch
        result = self.with_list()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertEqual(self.changed(), [self.keeper])

    def test_a_ctrl_c_during_the_list_write_lets_it_finish(self) -> None:
        real = lyrics_files.replace_atomically
        raised_once: list[Path] = []

        def replace(target: Path, data: bytes, **mode: int) -> None:
            # Every other write (the manifest's) goes through as it is.
            if target == self.listed and not raised_once:
                raised_once.append(target)
                signal.raise_signal(signal.SIGINT)
            real(target, data, **mode)

        with mock.patch.object(lyrics_files, "replace_atomically", replace):
            result = self.with_list()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertEqual(self.changed(), [self.keeper, self.orchard])
        # The report still prints after the held Ctrl-C.
        self.assertIn("album run", result.stdout)

    def test_a_write_failing_behind_a_held_ctrl_c_is_still_reported(self) -> None:
        real = lyrics_files.replace_atomically
        raised_once: list[Path] = []

        def replace(target: Path, data: bytes, **mode: int) -> None:
            if target != self.listed:
                real(target, data, **mode)
                return
            if not raised_once:
                raised_once.append(target)
                signal.raise_signal(signal.SIGINT)
            raise OSError("list disk full")

        with mock.patch.object(lyrics_files, "replace_atomically", replace):
            result = self.with_list()
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertIn("album run", result.stdout)
        self.assertIn("could not write --changed-list", result.stderr)
        self.assertIn("list disk full", result.stderr)

    def test_a_ctrl_c_while_the_list_is_chosen_still_writes_it(self) -> None:
        real = lyrics_changed.run_changed
        calls: list[bool] = []

        def run_changed(
            album: Path, tracks: Sequence[lr.ManifestTrack]
        ) -> tuple[str, ...]:
            calls.append(True)
            if len(calls) == 1:
                raise KeyboardInterrupt
            return real(album, tracks)

        with mock.patch.object(lyrics_changed, "run_changed", run_changed):
            result = self.with_list()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertEqual(self.changed(), [self.keeper, self.orchard])
        self.assertIn("album run", result.stdout)
        self.assertEqual(result.stderr.splitlines()[-1], "album.py: interrupted")

    def test_a_list_that_cannot_be_written_is_exit_3_after_the_summary(self) -> None:
        blocker = self.tmp / "a file"
        blocker.write_bytes(b"")
        self.listed = blocker / "changed.txt"
        result = self.with_list()
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertIn("album run", result.stdout)
        last = result.stderr.splitlines()[-1]
        self.assertTrue(
            last.startswith(f"album.py: could not write --changed-list {self.listed}")
        )


class LeftOutTest(cli_fakes.RealWiringCliCase):
    """A path the list cannot hold is left out and said, and the exit is unchanged."""

    def test_a_track_whose_name_holds_a_linefeed_is_left_out(self) -> None:
        track = TrackSpec("01 Line\nBreak.mp3", title="The Keeper")
        (mp3,) = fixtures.build_album(self.album, (track,))
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        listed = self.tmp / "changed.txt"
        result = self.run_album("--changed-list", str(listed))
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertEqual(listed.read_bytes(), b"")
        left = lyrics_changed.LeftOut(str(mp3), lyrics_changed.REASON_LINEFEED)
        self.assertIn(lyrics_changed.left_out_notice(left), result.stderr)

    def test_a_retried_write_says_each_left_out_path_once(self) -> None:
        track = TrackSpec("01 Line\nBreak.mp3", title="The Keeper")
        (mp3,) = fixtures.build_album(self.album, (track,))
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        listed = self.tmp / "changed.txt"
        real = lyrics_files.replace_atomically
        raised_once: list[Path] = []

        def replace(target: Path, data: bytes, **mode: int) -> None:
            # A Ctrl-C held through the first list write makes the tool write it again.
            if target == listed and not raised_once:
                raised_once.append(target)
                signal.raise_signal(signal.SIGINT)
            real(target, data, **mode)

        with mock.patch.object(lyrics_files, "replace_atomically", replace):
            result = self.run_album("--changed-list", str(listed))
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        left = lyrics_changed.LeftOut(str(mp3), lyrics_changed.REASON_LINEFEED)
        self.assertEqual(result.stderr.count(lyrics_changed.left_out_notice(left)), 1)


class StripAndUndoListTest(ChangedListCase):
    """A strip lists each MP3 whose frames it removed; an undo each MP3 it restored."""

    def test_a_strip_lists_only_the_tracks_whose_frames_it_removed(self) -> None:
        tag_builders.plant_frames(self.keeper, [tag_builders.uslt("An older verse\n")])
        result = self.with_list("--mode", "strip")
        self.assertEqual(result.code, album.EXIT_OK)
        # The second track's sidecar went, but its MP3 was never saved.
        self.assertEqual(self.changed(), [self.keeper])

    def test_an_undo_lists_each_track_it_saved(self) -> None:
        self.run_album()
        result = self.with_list("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertEqual(self.changed(), [self.keeper, self.orchard])

    def test_an_undo_whose_restore_did_not_read_back_lists_the_track(self) -> None:
        self.run_album()
        real = lyrics_undo.restore_frames

        def restore(
            path: Path, frames: tuple[lm.FrameRecord, ...], version: tuple[int, int]
        ) -> None:
            real(path, frames, version)
            raise lm.ReadBackError("the lyrics frames read back differ")

        with mock.patch.object(lyrics_undo, "restore_frames", restore):
            result = self.with_list("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertEqual(self.changed(), [self.keeper, self.orchard])


class RefusalTest(ChangedListCase):
    """A mode that changes nothing refuses the option: its list would always be empty."""

    def test_check_and_a_dry_run_refuse_it(self) -> None:
        for extra, flag in (
            (("--mode", "check"), "--mode check"),
            (("--dry-run",), "--dry-run"),
        ):
            with self.subTest(flag=flag):
                result = self.with_list(*extra)
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn(
                    f"--changed-list cannot be used with {flag}", result.stderr
                )
                self.assertFalse(self.listed.exists())
