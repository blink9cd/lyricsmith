"""`--track` on `--mode strip` and `--mode run`: one track cleared, fetched again, undone.

Each test drives the entry point with its real wiring, so the backup, the strip, the
run, the manifest and the undo are the ones an operator would get; only the providers
are faked and the clock fixed. The other two tracks of the album must come through
every one-track mode byte for byte. The album is built from silent MP3s under the
test's own directory, and every title and lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: Three tracks, each with its own plain sidecar of invented words.
TRACKS = (
    TrackSpec("01 A.mp3", title="The Keeper", txt="The lamp is lit\nthe tide is in\n"),
    TrackSpec(
        "02 B.mp3", title="The Mill", txt="The wheel turns slow\nthe grain runs\n"
    ),
    TrackSpec(
        "03 C.mp3", title="The Ford", txt="Stones under water\nwe cross at dawn\n"
    ),
)
#: The track every test here names with --track, by its album path.
CHOSEN = "02 B.mp3"


class OneTrackCase(cli_fakes.RealWiringCliCase):
    """The three-track album on the real wiring, with a fixed clock."""

    def setUp(self) -> None:
        """Build the album."""
        super().setUp()
        self.paths = fixtures.build_album(self.album, TRACKS)
        self.first, self.chosen, self.third = self.paths

    def mode(self, mode: str, *extra: str) -> cli_fakes.Result:
        """Run this test's album in *mode*, with *extra* options."""
        return self.main("--album", str(self.album), "--mode", mode, *extra)

    def one(self, mode: str, *extra: str) -> cli_fakes.Result:
        """Run *mode* on the chosen track alone, with *extra* options."""
        return self.mode(mode, "--track", str(self.chosen), *extra)

    def files(self, mp3: Path) -> dict[str, bytes]:
        """The bytes of *mp3* and of each file beside it sharing its stem."""
        return {
            path.name: path.read_bytes()
            for path in mp3.parent.iterdir()
            if path.is_file() and path.stem == mp3.stem
        }

    def album_bytes(self) -> dict[str, bytes]:
        """Every file under the album, run folder included, by album path."""
        return {
            path.relative_to(self.album).as_posix(): path.read_bytes()
            for path in sorted(self.album.rglob("*"))
            if path.is_file()
        }

    def newest(self) -> lr.Manifest:
        """The newest run's manifest as it stands on disk."""
        run_dir = lyrics_backup.select_run(self.album, None)
        return lyrics_manifest.read_manifest(run_dir)

    def assert_others_unchanged(self, before: dict[Path, dict[str, bytes]]) -> None:
        """The first and third tracks' files hold exactly what *before* recorded."""
        for mp3, files in before.items():
            self.assertEqual(self.files(mp3), files, mp3.name)

    def others(self) -> dict[Path, dict[str, bytes]]:
        """The first and third tracks' files, for `assert_others_unchanged`."""
        return {mp3: self.files(mp3) for mp3 in (self.first, self.third)}


class OneTrackStripTest(OneTrackCase):
    """A strip of one track clears that track alone, and its undo puts it back."""

    def test_only_the_named_track_is_stripped_and_recorded(self) -> None:
        others = self.others()
        result = self.one("strip")
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertFalse(self.chosen.with_suffix(".txt").exists())
        self.assert_others_unchanged(others)
        manifest = self.newest()
        self.assertEqual([t.path for t in manifest.tracks], [CHOSEN])
        (track,) = manifest.tracks
        assert track.after is not None
        self.assertIs(track.after.status, lr.OutcomeStatus.STRIPPED)
        self.assertIn("stripped 1 · errors 0", result.stdout)

    def test_an_undo_after_it_puts_the_track_back_with_no_warning(self) -> None:
        before = self.files(self.chosen)
        self.one("strip")
        result = self.mode("undo")
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertEqual(self.files(self.chosen), before)
        self.assertNotIn("warning:", result.stderr)

    def test_the_changed_list_names_the_track_alone(self) -> None:
        # Frames on every track, so a strip of any of them would save its MP3.
        for path in self.paths:
            tag_builders.plant_frames(path, (tag_builders.uslt("A planted verse\n"),))
        listed = self.tmp / "changed.txt"
        result = self.one("strip", "--changed-list", str(listed))
        self.assertEqual(result.code, album.EXIT_OK)
        read = lyrics_changed.parse_changed_list(listed.read_bytes())
        self.assertEqual(read, (str(self.chosen),))


class OneTrackRunTest(OneTrackCase):
    """A run of one track asks for that track alone and records it alone."""

    def test_a_run_after_a_strip_asks_for_the_track_alone(self) -> None:
        self.one("strip")
        others = self.others()
        self.providers = run_fakes.table({"The Mill": fixtures.LIGHTHOUSE_LRC})
        result = self.one("run")
        self.assertEqual(result.code, album.EXIT_OK)
        asked = [
            query.title
            for provider in self.providers.values()
            for query in provider.calls  # type: ignore[attr-defined] # the fake's record
        ]
        self.assertEqual(asked, ["The Mill"])
        self.assertEqual([t.path for t in self.newest().tracks], [CHOSEN])
        self.assert_others_unchanged(others)

    def test_a_dry_run_plans_the_track_alone_and_writes_nothing(self) -> None:
        before = self.album_bytes()
        result = self.one("run", "--dry-run")
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertIn("1 track under", result.stdout)
        self.assertIn(CHOSEN, result.stdout)
        for other in ("01 A.mp3", "03 C.mp3"):
            self.assertNotIn(other, result.stdout)
        self.assertEqual(self.album_bytes(), before)

    def test_every_run_option_is_accepted_with_it(self) -> None:
        for option in ("--fresh", "--refetch-untimed", "--offline"):
            with self.subTest(option=option):
                result = self.one("run", option)
                self.assertNotEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertNotIn("error:", result.stderr)


class OneTrackRefusalTest(OneTrackCase):
    """A --track that is not one of the album's tracks is refused before anything runs."""

    def test_a_file_outside_the_album_exits_2_for_run_and_strip(self) -> None:
        outside = fixtures.build_silent_mp3(self.tmp / "elsewhere" / "02 B.mp3", 30.0)
        before = self.album_bytes()
        for mode in ("run", "strip"):
            with self.subTest(mode=mode):
                result = self.mode(mode, "--track", str(outside))
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn("is not under", result.stderr)
                self.assertEqual(self.album_bytes(), before)
                self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())

    def test_a_file_in_the_album_that_is_no_track_exits_2_naming_it(self) -> None:
        before = self.album_bytes()
        sidecar = self.first.with_suffix(".txt")
        for mode in ("run", "strip"):
            with self.subTest(mode=mode):
                result = self.mode(mode, "--track", str(sidecar))
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn("--track 01 A.txt is not a track of", result.stderr)
                self.assertEqual(self.album_bytes(), before)
