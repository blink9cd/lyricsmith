"""The warning an undo of an older run gives: only for a newer run sharing a track.

The end-to-end cases build real runs, one-track strips among them, through the entry
point's real wiring with a fixed clock, so the runs take the ids ``<id>``, ``<id>-2``
and ``<id>-3`` in order. Then `newer_warnings` and `newer_runs` are tested alone. The
album is built from silent MP3s under the test's own directory; every title and lyric
is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_undo_mode  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

TRACKS = (
    TrackSpec("01 A.mp3", title="The Keeper", txt="The lamp is lit\nthe tide is in\n"),
    TrackSpec(
        "02 B.mp3", title="The Mill", txt="The wheel turns slow\nthe grain runs\n"
    ),
    TrackSpec(
        "03 C.mp3", title="The Ford", txt="Stones under water\nwe cross at dawn\n"
    ),
)
FIRST, SECOND, THIRD = (
    run_fakes.RUN_ID,
    f"{run_fakes.RUN_ID}-2",
    f"{run_fakes.RUN_ID}-3",
)
#: The warning given when a newer run's manifest cannot be read.
GENERAL = (
    f"warning: run {FIRST} is not the newest; a newer run's changes to the same "
    "tracks were overwritten"
)


def shared(newer: str, paths: str) -> str:
    """The warning naming *newer* run's changes to *paths* after the first run."""
    return (
        f"warning: run {newer} changed {paths} after run {FIRST}; "
        "this undo overwrote those changes"
    )


class UndoNewerCase(cli_fakes.RealWiringCliCase):
    """The three-track album on the real wiring, with a fixed clock."""

    def setUp(self) -> None:
        """Build the album."""
        super().setUp()
        self.paths = fixtures.build_album(self.album, TRACKS)

    def strip(self, *track: Path) -> None:
        """Strip the album, or the one track given, as a new run."""
        named = ("--track", str(track[0])) if track else ()
        result = self.main("--album", str(self.album), "--mode", "strip", *named)
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)

    def undo_first(self) -> list[str]:
        """Undo the first run by its id; return the warning lines on stderr."""
        result = self.main(
            "--album", str(self.album), "--mode", "undo", "--run-id", FIRST
        )
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        lines = result.stderr.splitlines()
        return [line for line in lines if line.startswith("warning:")]


class UndoNewerWarningTest(UndoNewerCase):
    """Each newer run sharing a track an undo restored is named, with those tracks."""

    def test_a_newer_run_of_another_track_gives_no_warning(self) -> None:
        self.strip(self.paths[0])
        self.strip(self.paths[1])
        self.assertEqual(self.undo_first(), [])

    def test_a_newer_run_of_the_same_track_is_named(self) -> None:
        self.strip(self.paths[0])
        self.paths[0].with_suffix(".txt").write_bytes(b"A new verse\n")
        self.strip(self.paths[0])
        self.assertEqual(self.undo_first(), [shared(SECOND, "01 A.mp3")])

    def test_each_newer_run_sharing_a_track_is_named_oldest_first(self) -> None:
        self.strip()
        self.strip(self.paths[0])
        self.strip()
        self.assertEqual(
            self.undo_first(),
            [
                shared(SECOND, "01 A.mp3"),
                shared(THIRD, "01 A.mp3, 02 B.mp3, 03 C.mp3"),
            ],
        )

    def test_a_shared_track_this_undo_did_not_restore_is_not_named(self) -> None:
        # The first run's first track is undone alone before the newer run strips it,
        # so undoing the whole first run finds it already undone and restores only
        # the other two, which the newer run never touched.
        self.strip()
        result = self.main(
            "--album", str(self.album), "--mode", "undo", "--track", str(self.paths[0])
        )
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.strip(self.paths[0])
        self.assertEqual(self.undo_first(), [])

    def test_an_unreadable_newer_manifest_gives_the_general_warning(self) -> None:
        self.strip(self.paths[0])
        self.strip(self.paths[0])
        newer = lr.run_folder(self.album, SECOND) / lr.MANIFEST_NAME
        newer.write_bytes(b"{")
        self.assertEqual(self.undo_first(), [GENERAL])


class UndoNewerEdgeTest(UndoNewerCase):
    """A track begun but not finished, a newer run already undone, a failed listing."""

    def errored_but_saved(self) -> lr.UndoReport:
        """The first run's undo of its one track: an ERROR after the MP3 was saved."""
        error = lr.StageError(lr.ErrorStage.UNDO, "OSError", "sidecar refused")
        track = lr.UndoTrackResult("01 A.mp3", lr.UndoStatus.ERROR, error, saved=True)
        return lr.UndoReport(FIRST, False, (track,))

    def test_a_track_that_errored_after_saving_is_still_warned_of(self) -> None:
        self.strip(self.paths[0])
        self.paths[0].with_suffix(".txt").write_bytes(b"A new verse\n")
        self.strip(self.paths[0])
        report = self.errored_but_saved()
        self.overrides["undo"] = lambda *_args, **_kwargs: report
        result = self.main(
            "--album", str(self.album), "--mode", "undo", "--run-id", FIRST
        )
        warnings = [ln for ln in result.stderr.splitlines() if ln.startswith("warning")]
        self.assertEqual(warnings, [shared(SECOND, "01 A.mp3")])

    def test_a_newer_run_already_undone_gives_no_warning(self) -> None:
        self.strip(self.paths[0])
        self.providers = run_fakes.table(
            plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN}
        )
        run = self.main("--album", str(self.album), "--track", str(self.paths[0]))
        self.assertEqual(run.code, album.EXIT_OK, run.stderr)
        undo = self.main(
            "--album", str(self.album), "--mode", "undo", "--run-id", SECOND
        )
        self.assertEqual(undo.code, album.EXIT_OK, undo.stderr)
        self.assertEqual(self.undo_first(), [])

    def test_a_newer_run_not_undone_still_warns(self) -> None:
        self.strip(self.paths[0])
        self.providers = run_fakes.table(
            plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN}
        )
        run = self.main("--album", str(self.album), "--track", str(self.paths[0]))
        self.assertEqual(run.code, album.EXIT_OK, run.stderr)
        self.assertEqual(self.undo_first(), [shared(SECOND, "01 A.mp3")])

    def test_a_failed_listing_of_newer_runs_keeps_the_report(self) -> None:
        self.strip(self.paths[0])
        self.strip(self.paths[1])
        refused = mock.patch.object(
            lyrics_undo_mode, "newer_runs", side_effect=OSError("listing refused")
        )
        with refused:
            result = self.main(
                "--album", str(self.album), "--mode", "undo", "--run-id", FIRST
            )
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertIn("undone 1", result.stdout)
        self.assertIn(GENERAL, result.stderr.splitlines())


class NewerWarningsTest(fixtures.IsolatedCase):
    """The warning lines alone, from an overlap built by hand."""

    def test_several_paths_are_joined_by_a_comma(self) -> None:
        overlap = lyrics_undo_mode.NewerOverlap(
            ((SECOND, ("01 A.mp3", "CD2/02 B.mp3")),)
        )
        self.assertEqual(
            lyrics_undo_mode.newer_warnings(overlap, FIRST),
            (shared(SECOND, "01 A.mp3, CD2/02 B.mp3"),),
        )

    def test_an_unreadable_overlap_gives_the_general_line(self) -> None:
        overlap = lyrics_undo_mode.NewerOverlap((), unreadable=True)
        self.assertEqual(lyrics_undo_mode.newer_warnings(overlap, FIRST), (GENERAL,))

    def test_no_shared_track_gives_no_line(self) -> None:
        overlap = lyrics_undo_mode.NewerOverlap(())
        self.assertEqual(lyrics_undo_mode.newer_warnings(overlap, FIRST), ())


class NewerRunsTest(fixtures.IsolatedCase):
    """The runs after a given one, sorted as `select_run` sorts them."""

    def setUp(self) -> None:
        """An album holding three runs, each with an empty manifest."""
        super().setUp()
        self.album = self.tmp / "album"
        for run_id in (FIRST, SECOND, f"{FIRST}-10"):
            run_dir = lr.run_folder(self.album, run_id)
            run_dir.mkdir(parents=True)
            empty = lr.Manifest(
                run_id, "eng", lr.RunState.COMPLETE, lr.GeniusState.ABSENT, ()
            )
            lyrics_manifest.write_manifest(run_dir, empty)

    def test_the_runs_after_one_are_oldest_first_by_suffix_as_a_number(self) -> None:
        self.assertEqual(lyrics_backup.newer_runs(self.album, SECOND), (f"{FIRST}-10",))
        self.assertEqual(
            lyrics_backup.newer_runs(self.album, FIRST), (SECOND, f"{FIRST}-10")
        )

    def test_an_unknown_run_has_none_after_it(self) -> None:
        self.assertEqual(lyrics_backup.newer_runs(self.album, "20991231T000000Z"), ())
