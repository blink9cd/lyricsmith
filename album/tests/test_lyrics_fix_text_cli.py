"""`--mode fix-text` end to end: a wrong untimed text replaced by a sung one, or removed.

A track sync found not sung has its transcript kept in the store `--heard-dir` names.
The fix tries the album's other stored untimed texts first, then the providers' untimed
answers, judges each against the transcript, and writes the first that is sung, backed
up for undo; with none it removes the wrong text, backed up, as ``wrong text removed``.
It never removes when every provider failed, or when another track's text could not be
read, and it leaves alone a track whose text changed since the transcript was judged.
Each test drives the entry point with its real wiring; only the clock, the provider
table and the store's folder are the test's, and every title and lyric is invented.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    FOUNDRY,
    HARBOR,
    MEADOW,
    QUARRY,
    TITLES,
    FixCase,
    song_track,
    sung_neighbor,
    words,
)
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above

#: How a removed track's row reads, and how the summary counts it.
WRONG_TEXT = "wrong text removed"
REMOVED_LINE = "removed: wrong text 1"


class ShiftedAlbumTest(FixCase):
    """Each track's right text is another track's: fixed with no provider asked."""

    def test_a_rotated_album_takes_each_text_from_its_sibling(self) -> None:
        # Each track holds the next song's text, the last one the first's.
        songs = (HARBOR, MEADOW, FOUNDRY)
        paths = self.build(
            *(
                song_track(i + 1, song, txt=songs[(i + 1) % 3])
                for i, song in enumerate(songs)
            )
        )
        for path, song in zip(paths, songs, strict=True):
            self.hear(path, song)
        records = [self.record_of(path) for path in paths]
        # Every provider would answer each title rightly, but none is asked.
        self.answer({TITLES[song]: song for song in songs})
        changed = self.tmp / "changed.txt"
        before = (rb.snapshots(paths), raw(paths))
        result = self.fix("--changed-list", str(changed))
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        # Each track's text is the one its predecessor held, the first's the last's.
        origins = [paths[i - 1].name for i in range(len(paths))]
        for number, (path, song, record, origin) in enumerate(
            zip(paths, songs, records, origins, strict=True), start=1
        ):
            self.assertEqual(self.text_of(path), words(song))
            # Kept, unchanged, for sync's rerun to judge the new text and time it.
            self.assertIsNotNone(record)
            self.assertEqual(self.record_of(path), record)
            outcome = self.outcome(path.name)
            self.assertIs(outcome.status, lr.OutcomeStatus.WRITTEN)
            # No run recorded where the sibling's text came from: it is named so.
            self.assertIs(outcome.source, lm.Source.ALBUM_TEXT)
            self.assertEqual(outcome.origin, origin)
            self.assertEqual(
                cli_fakes.row_cells(result.stdout, f"{number}  {path.name}")[2],
                f"another track's text from {origin}",
            )
        self.assertEqual(self.asked(), [])
        listed = changed.read_text("utf-8").splitlines()
        self.assertEqual(sorted(listed), sorted(str(p) for p in paths))
        undone = self.run_album("--mode", "undo")
        self.assertEqual(undone.code, album.EXIT_OK, undone.stdout + undone.stderr)
        self.assertEqual((rb.snapshots(paths), raw(paths)), before)

    def test_a_sibling_text_keeps_the_provider_a_run_recorded(self) -> None:
        # A run fetched each text from LRCLIB, one track off, as a shifted page is.
        paths = self.build(
            song_track(1, HARBOR, txt=None), song_track(2, MEADOW, txt=None)
        )
        self.answer({TITLES[HARBOR]: MEADOW, TITLES[MEADOW]: HARBOR})
        first = self.run_album()
        self.assertEqual(first.code, album.EXIT_OK, first.stdout + first.stderr)
        self.hear(paths[0], HARBOR)
        self.hear(paths[1], MEADOW)
        self.answer({})
        self.later(5)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        for path, song in zip(paths, (HARBOR, MEADOW), strict=True):
            self.assertEqual(self.text_of(path), words(song))
            self.assertIs(self.outcome(path.name).source, lm.Source.LRCLIB)
        self.assertEqual(self.asked(), [])

    def test_a_sibling_holding_the_refused_words_is_not_a_candidate(self) -> None:
        # Both hold the meadow song; only a provider has the harbor song.
        paths = self.build(
            song_track(1, HARBOR, txt=MEADOW), song_track(2, MEADOW, txt=MEADOW)
        )
        self.hear(paths[0], HARBOR)
        self.answer({TITLES[HARBOR]: HARBOR})
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertEqual(self.text_of(paths[0]), words(HARBOR))
        self.assertIs(self.outcome(paths[0].name).source, lm.Source.LRCLIB)
        # Only the track with a record was backed up and run.
        self.assertEqual([t.path for t in self.newest()], [paths[0].name])


class ProviderPassTest(FixCase):
    """No sibling is sung, so the providers' untimed answers are asked in turn."""

    def test_a_provider_s_sung_answer_is_written_and_undone(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY), song_track(2, MEADOW, txt=MEADOW)
        )
        self.hear(paths[0], HARBOR)
        before = (rb.snapshots(paths), raw(paths))
        record = self.record_of(paths[0])
        self.answer({TITLES[HARBOR]: HARBOR})
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertEqual(self.text_of(paths[0]), words(HARBOR))
        outcome = self.outcome(paths[0].name)
        self.assertEqual(
            (outcome.status, outcome.source, outcome.kind),
            (lr.OutcomeStatus.WRITTEN, lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED),
        )
        self.assertIsNotNone(record)
        self.assertEqual(self.record_of(paths[0]), record)
        self.assertEqual(self.asked(), [TITLES[HARBOR]])
        undone = self.run_album("--mode", "undo")
        self.assertEqual(undone.code, album.EXIT_OK, undone.stdout + undone.stderr)
        self.assertEqual((rb.snapshots(paths), raw(paths)), before)

    def test_an_answer_of_the_refused_words_is_skipped(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY), sung_neighbor())
        self.hear(paths[0], HARBOR)
        self.answer({TITLES[HARBOR]: QUARRY.upper()})
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.REMOVED)

    def test_an_unsung_answer_is_not_written(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY), sung_neighbor())
        self.hear(paths[0], HARBOR)
        self.answer({TITLES[HARBOR]: FOUNDRY})
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.REMOVED)
        self.assertFalse(paths[0].with_suffix(".txt").exists())


class RemovalTest(FixCase):
    """Nothing sung found: the wrong text goes, flagged, and undo brings it back."""

    def test_no_pass_removes_the_frames_and_the_sidecar(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY), song_track(2, MEADOW, txt=MEADOW)
        )
        tag_builders.plant_frames(paths[0], (tag_builders.uslt(QUARRY),))
        self.hear(paths[0], HARBOR)
        record = self.record_of(paths[0])
        before = (rb.snapshots(paths), raw(paths))
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertEqual(
            cli_fakes.row_cells(result.stdout, paths[0].name)[-1], WRONG_TEXT
        )
        self.assertIn(REMOVED_LINE, result.stdout)
        self.assertIn(f"{paths[0].name}: {WRONG_TEXT}", result.stderr)
        self.assertFalse(paths[0].with_suffix(".txt").exists())
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        outcome = self.outcome(paths[0].name)
        self.assertIs(outcome.status, lr.OutcomeStatus.REMOVED)
        self.assertEqual(outcome.implausible, WRONG_TEXT)
        # Kept: the track now holds no untimed text, so nothing takes it but undo.
        self.assertIsNotNone(record)
        self.assertEqual(self.record_of(paths[0]), record)
        undone = self.run_album("--mode", "undo")
        self.assertEqual(undone.code, album.EXIT_OK, undone.stdout + undone.stderr)
        self.assertEqual((rb.snapshots(paths), raw(paths)), before)

    def test_every_provider_failing_removes_nothing(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY))
        self.hear(paths[0], HARBOR)
        self.providers = run_fakes.failing_table()
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        self.assertIn("every provider failed", result.stdout)
        self.assertEqual(raw(paths), before)
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.ERROR)
        self.assertIsNotNone(self.record_of(paths[0]))

    def test_no_provider_asked_removes_nothing(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY))
        self.hear(paths[0], HARBOR)
        self.providers = {}
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        self.assertIn("no provider answered", result.stdout)
        self.assertEqual(raw(paths), before)
        self.assertIsNotNone(self.record_of(paths[0]))

    def test_an_unreadable_sibling_removes_nothing(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY), song_track(2, MEADOW, txt=None)
        )
        paths[1].with_suffix(".txt").write_bytes(b"caf\xe9 by the quay\n")
        self.hear(paths[0], HARBOR)
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        self.assertIn(
            "02 Meadow.mp3", cli_fakes.row_cells(result.stdout, "01 Harbor")[-1]
        )
        self.assertEqual(raw(paths), before)
        self.assertIsNotNone(self.record_of(paths[0]))

    def test_an_unreadable_sibling_does_not_stop_a_pass(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY), song_track(2, MEADOW, txt=None)
        )
        paths[1].with_suffix(".txt").write_bytes(b"caf\xe9 by the quay\n")
        self.hear(paths[0], HARBOR)
        self.answer({TITLES[HARBOR]: HARBOR})
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertEqual(self.text_of(paths[0]), words(HARBOR))


class SkipTest(FixCase):
    """A track the fix leaves alone, its record kept, and nothing backed up."""

    def assert_skipped(self, paths: list[Path], reason: str) -> None:
        """The fix ran, skipped the first track for *reason*, and changed nothing."""
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIn(f"skipped {paths[0].name}: {reason}", result.stderr)
        self.assertIn(reason, cli_fakes.row_cells(result.stdout, paths[0].name)[-1])
        self.assertIn("nothing to fix", result.stdout)
        self.assertEqual(raw(paths), before)
        self.assertIsNotNone(self.record_of(paths[0]))
        self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())
        self.assertEqual(self.asked(), [])

    def test_a_changed_text_is_skipped(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY))
        self.hear(paths[0], HARBOR, held=FOUNDRY)
        self.answer({TITLES[HARBOR]: HARBOR})
        self.assert_skipped(paths, "its text changed since it was heard")

    def test_a_timed_track_is_skipped(self) -> None:
        lrc = "[00:01.00]Up at the quarry on a chalky shelf\n"
        paths = self.build(song_track(1, HARBOR, txt=QUARRY, lrc=lrc))
        self.hear(paths[0], HARBOR, held=QUARRY)
        self.assert_skipped(paths, "its lyrics are not untimed only")

    def test_a_sung_text_is_skipped(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=HARBOR))
        self.hear(paths[0], HARBOR)
        self.assert_skipped(paths, "the transcript sings its text")

    def test_a_little_heard_track_is_skipped(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY))
        self.hear(paths[0], "the rope man whistles\n")
        self.assert_skipped(paths, "too little heard to judge")

    def test_an_album_with_no_record_has_nothing_to_fix(self) -> None:
        self.build(song_track(1, HARBOR, txt=QUARRY))
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIn("nothing to fix", result.stdout)
        self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())

    def test_a_store_problem_is_reported(self) -> None:
        self.build(song_track(1, HARBOR, txt=QUARRY))
        self.heard.mkdir()
        (self.heard / f"{'0' * 64}.json").write_text(
            "not json", encoding="utf-8", newline="\n"
        )
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIn("transcript record ignored", result.stderr)


class TrackOptionTest(FixCase):
    """--track picks one track to fix; the others are still its candidates."""

    def test_only_the_named_track_is_fixed(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=MEADOW), song_track(2, MEADOW, txt=HARBOR)
        )
        self.hear(paths[0], HARBOR)
        self.hear(paths[1], MEADOW)
        result = self.fix("--track", str(paths[0]))
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertEqual(self.text_of(paths[0]), words(HARBOR))
        self.assertEqual(self.text_of(paths[1]), words(HARBOR))
        self.assertIsNotNone(self.record_of(paths[1]))
        self.assertEqual([t.path for t in self.newest()], [paths[0].name])


class DryRunTest(FixCase):
    """A dry run plans each track and writes nothing, asking no provider."""

    def test_the_plan(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=MEADOW),
            song_track(2, MEADOW, txt=QUARRY),
            song_track(3, FOUNDRY, txt=FOUNDRY),
        )
        self.hear(paths[0], HARBOR, held=FOUNDRY)
        self.hear(paths[1], HARBOR)
        self.hear(paths[2], MEADOW)
        self.answer({TITLES[MEADOW]: HARBOR})
        before = raw(paths)
        result = self.fix("--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        # Its name opens its own row, and is named in the third's plan too.
        cells = cli_fakes.row_cells(result.stdout, f"1  {paths[0].name}")
        self.assertIn("its text changed since it was heard", cells[-1])
        self.assertIn(
            "ask the providers", cli_fakes.row_cells(result.stdout, "02 ")[-1]
        )
        cells = cli_fakes.row_cells(result.stdout, paths[2].name)
        self.assertIn("take 01 Harbor.mp3's text", cells[-1])
        self.assertIn("dry run: nothing was written", result.stdout)
        self.assertEqual(raw(paths), before)
        self.assertEqual(self.asked(), [])
        self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())
        for path in paths:
            self.assertIsNotNone(self.record_of(path))


class OptionsTest(FixCase):
    """The options the mode takes, needs and refuses."""

    def test_the_heard_dir_is_required(self) -> None:
        self.build(song_track(1, HARBOR, txt=QUARRY))
        result = self.run_album("--mode", "fix-text")
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertIn("--heard-dir", result.stderr)

    def test_every_other_mode_refuses_the_heard_dir(self) -> None:
        for mode in ("run", "check", "undo", "strip", "compare", "prune", "accept"):
            with self.subTest(mode=mode):
                result = self.run_album("--mode", mode, "--heard-dir", str(self.heard))
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn("--heard-dir cannot be used", result.stderr)

    def test_the_mode_refuses_what_it_has_no_use_for(self) -> None:
        for extra in (
            ("--offline",),
            ("--fresh",),
            ("--refetch-untimed",),
            ("--refetch-implausible",),
            ("--run-id", "x"),
            ("--apply",),
        ):
            with self.subTest(extra=extra):
                result = self.fix(*extra)
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn("cannot be used with --mode fix-text", result.stderr)


class StoreTest(FixCase):
    """The store's own failures: a folder that is none, a record that cannot go."""

    def test_a_heard_dir_that_is_a_file_cannot_run(self) -> None:
        self.build(song_track(1, HARBOR, txt=QUARRY))
        self.heard.write_text("not a folder\n", encoding="utf-8", newline="\n")
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN, result.stderr)
        self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())

    @tag_builders.needs_binding_modes
    def test_a_store_no_file_can_leave_does_not_stop_a_removal(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY), sung_neighbor())
        self.hear(paths[0], HARBOR)
        # No provider answers, so the wrong text is removed. The fix only reads the
        # store, so one in which no file can be removed or written is no problem.
        self.heard.chmod(0o500)
        self.addCleanup(self.heard.chmod, 0o700)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertNotIn("transcript record", result.stderr)
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.REMOVED)
        self.assertIsNotNone(self.record_of(paths[0]))


#: Bytes no sidecar reader takes: not UTF-8.
UNDECODABLE = b"caf\xe9 by the quay\n"


class ReadFailureTest(FixCase):
    """A track or a run that cannot be read is said so, and nothing is changed."""

    def unreadable_track(self) -> list[Path]:
        """An album whose one track has a record but a ``.txt`` that cannot be read."""
        paths = self.build(song_track(1, HARBOR, txt=None))
        paths[0].with_suffix(".txt").write_bytes(UNDECODABLE)
        self.hear(paths[0], HARBOR, held=QUARRY)
        return paths

    def test_a_dry_run_shows_the_track_it_cannot_read(self) -> None:
        paths = self.unreadable_track()
        result = self.fix("--dry-run")
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        cell = cli_fakes.row_cells(result.stdout, paths[0].name)[-1]
        self.assertTrue(cell.startswith("ERROR sidecar: "), cell)

    def test_a_run_reports_the_track_it_cannot_read(self) -> None:
        paths = self.unreadable_track()
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.ERROR)
        self.assertEqual(raw(paths), before)
        self.assertIsNotNone(self.record_of(paths[0]))

    def test_a_text_changed_after_the_plan_is_left_alone(self) -> None:
        paths = self.build(song_track(1, HARBOR, txt=QUARRY))
        self.hear(paths[0], HARBOR)
        self.answer({TITLES[HARBOR]: HARBOR})
        real = album.real_wiring().stages.back_up

        def back_up_then_edit(*args: object, **kwargs: object) -> object:
            # Another hand edits the text once the plan is made, before the step.
            manifest = real(*args, **kwargs)  # type: ignore[arg-type] # passed through
            paths[0].with_suffix(".txt").write_text(
                FOUNDRY, encoding="utf-8", newline="\n"
            )
            return manifest

        self.overrides["back_up"] = back_up_then_edit
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        cell = cli_fakes.row_cells(result.stdout, paths[0].name)[-1]
        self.assertIn("its text changed since it was heard", cell)
        self.assertEqual(self.text_of(paths[0]), words(FOUNDRY))
        self.assertIsNotNone(self.record_of(paths[0]))
        self.assertEqual(self.asked(), [])

    def test_a_run_whose_manifest_cannot_be_read_is_passed_over(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=None), song_track(2, MEADOW, txt=None)
        )
        self.answer({TITLES[HARBOR]: MEADOW, TITLES[MEADOW]: HARBOR})
        first = self.run_album()
        self.assertEqual(first.code, album.EXIT_OK, first.stdout + first.stderr)
        run_dir = lr.run_folder(self.album, run_fakes.RUN_ID)
        (run_dir / lr.MANIFEST_NAME).write_bytes(b"not a manifest")
        self.hear(paths[0], HARBOR)
        self.answer({})
        self.later(5)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        shown = run_dir.relative_to(self.album).as_posix()
        self.assertIn(f"passed over the run {shown}", result.stderr)
        # Its record unread, the sibling's text is unrecorded.
        self.assertIs(self.outcome(paths[0].name).source, lm.Source.ALBUM_TEXT)
