"""`--mode fix-text --hold-for-genius`: a track only Genius might have is kept and listed.

Genius challenges every script, so a fix given the option never asks it, as
``--no-genius`` would not. A track whose wrong text no source sings, every source asked
answering emptily, is held instead of removed: it keeps its text, ends ``kept existing``
with its removal withheld as ``held for Genius``, and its absolute path is written to
the option's file, one per line, as ``--changed-list`` writes. A source that failed
still keeps the text as the track's error, not held; a later source that sings still
replaces it. Without the option the text is removed, as before. Each test drives the
entry point with its real wiring; every title and lyric is invented.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_categories as lcat  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    FOUNDRY,
    HARBOR,
    QUARRY,
    TITLES,
    FixCase,
    song_track,
    sung_neighbor,
    words,
)
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_tekstowo_wiring import StoppingStep  # noqa: E402 - import follows the sys.path bootstrap above

#: The option, as the parser declares it.
HOLD = "--hold-for-genius"
#: How a held track's row reads, and how the summary counts the held tracks.
HELD_ROW = "kept existing: removal withheld, held for Genius"
HELD_LINE = "removal withheld: held for Genius"
NETEASE_KEY = lm.ProviderKey(lm.Source.NETEASE, lm.LyricsKind.UNSYNCED)
TEKSTOWO_KEY = lm.ProviderKey(lm.Source.TEKSTOWO, lm.LyricsKind.UNSYNCED)
#: An invented Genius key, so a run that looked for one would find it.
INVENTED_KEY = "invented-genius-key-for-tests"
#: How the refusal of one file named for both lists reads.
SAME_FILE = f"{HOLD} cannot name the same file as --changed-list"


class _HoldCase(FixCase):
    """`FixCase`, with the hold list's file and a reader of it."""

    def setUp(self) -> None:
        """No list written yet."""
        super().setUp()
        self.held = self.tmp / "held.txt"

    def hold(self, *extra: str) -> cli_fakes.Result:
        """Run the fix with --hold-for-genius naming this test's file."""
        return self.fix(HOLD, str(self.held), *extra)

    def listed(self) -> list[str]:
        """The hold list's paths, in order, read back strictly."""
        paths = lyrics_changed.parse_changed_list(self.held.read_bytes())
        assert paths is not None
        return list(paths)

    def blocked(self, name: str) -> Path:
        """A list path named *name* that cannot be written: its folder is a file."""
        blocker = self.tmp / f"{name}.blocker"
        blocker.write_bytes(b"")
        return blocker / name

    def wrong_track(self) -> list[Path]:
        """A track holding the wrong text, heard singing its own, beside a sung one."""
        paths = self.build(song_track(1, HARBOR, txt=QUARRY), sung_neighbor())
        self.hear(paths[0], HARBOR)
        self.answer({TITLES[HARBOR]: FOUNDRY})
        return paths


class HeldTest(_HoldCase):
    """Every source asked answered, none sings: the track is kept and listed."""

    def test_a_track_no_source_sings_is_held_and_listed(self) -> None:
        paths = self.wrong_track()
        before = raw(paths)
        result = self.hold()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        row = cli_fakes.row_cells(result.stdout, paths[0].name)[-1]
        self.assertEqual(row, HELD_ROW)
        self.assertIn(f"{HELD_LINE} 1", result.stdout)
        self.assertNotIn("spoken word", result.stdout)
        self.assertNotIn("removed:", result.stdout)
        self.assertEqual(raw(paths), before)
        self.assertEqual(self.text_of(paths[0]), words(QUARRY))
        outcome = self.outcome(paths[0].name)
        self.assertIs(outcome.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertEqual(outcome.withheld, lyrics_outcomes.HELD_FOR_GENIUS)
        self.assertIsNone(outcome.error)
        self.assertEqual(self.listed(), [os.path.abspath(paths[0])])
        self.assertTrue(self.held.read_bytes().endswith(b"\n"))
        self.assertIsNotNone(self.record_of(paths[0]))

    def test_without_the_option_the_track_is_removed(self) -> None:
        paths = self.wrong_track()
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.REMOVED)
        self.assertFalse(paths[0].with_suffix(".txt").exists())
        self.assertNotIn(HELD_LINE, result.stdout)

    def test_a_held_track_on_a_spoken_album_is_held_and_listed(self) -> None:
        # Two of two tracks heard not sung: the album looks spoken. The hold is
        # asked first, so the track is still listed for the Genius pass.
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY), song_track(2, FOUNDRY, txt=QUARRY)
        )
        self.hear(paths[0], HARBOR)
        self.hear(paths[1], FOUNDRY)
        result = self.hold()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        for path in paths:
            outcome = self.outcome(path.name)
            self.assertEqual(outcome.withheld, lyrics_outcomes.HELD_FOR_GENIUS)
        self.assertEqual(self.listed(), [os.path.abspath(path) for path in paths])
        self.assertIn(f"{HELD_LINE} 2", result.stdout)
        # The same album fixed again without the option withholds as spoken word,
        # so the hold above was asked ahead of the spoken-word rule.
        self.later(5)
        plain = self.fix()
        self.assertEqual(plain.code, album.EXIT_OK, plain.stdout + plain.stderr)
        for path in paths:
            withheld = self.outcome(path.name).withheld
            assert withheld is not None
            self.assertTrue(withheld.startswith("spoken word"), withheld)


class NotHeldTest(_HoldCase):
    """A failing source keeps the error; a later source that sings still replaces."""

    def test_a_source_that_failed_keeps_the_error_and_lists_nothing(self) -> None:
        paths = self.wrong_track()
        self.providers = {
            **self.titles,
            NETEASE_KEY: fixtures.raising(OSError("refused")),
        }
        before = raw(paths)
        result = self.hold()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        row = cli_fakes.row_cells(result.stdout, paths[0].name)[-1]
        self.assertIn("NetEase failed", row)
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.ERROR)
        self.assertIsNone(self.outcome(paths[0].name).withheld)
        self.assertEqual(raw(paths), before)
        self.assertEqual(self.held.read_bytes(), b"")

    def assert_kept_unheld(
        self, result: cli_fakes.Result, name: str, cell: str
    ) -> None:
        """*name* ended as an error naming *cell*, not held, and nothing is listed."""
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        row = cli_fakes.row_cells(result.stdout, name)[-1]
        self.assertIn(cell, row)
        self.assertNotIn("held for Genius", row)
        outcome = self.outcome(name)
        self.assertIs(outcome.status, lr.OutcomeStatus.ERROR)
        self.assertIsNone(outcome.withheld)
        self.assertEqual(self.held.read_bytes(), b"")

    def test_a_provider_cooling_down_keeps_the_error_and_lists_nothing(self) -> None:
        paths = self.wrong_track()
        state = lyc.cooldown_path(self.wiring().environ, lm.Source.TEKSTOWO)
        lyc.write_cooldown(state, self.started - timedelta(hours=1), "HTTP 429")
        before = raw(paths)
        result = self.hold()
        self.assert_kept_unheld(
            result, paths[0].name, "Tekstowo not asked (cooling down): nothing removed"
        )
        self.assertEqual(raw(paths), before)

    def test_a_provider_stopped_part_way_keeps_the_error_and_lists_nothing(
        self,
    ) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY), song_track(2, FOUNDRY, txt=QUARRY)
        )
        self.hear(paths[0], HARBOR)
        self.hear(paths[1], FOUNDRY)
        self.answer({TITLES[HARBOR]: QUARRY, TITLES[FOUNDRY]: QUARRY})
        # Tekstowo refuses the first track and is not asked for the second.
        self.providers = {**self.titles, TEKSTOWO_KEY: StoppingStep()}
        result = self.hold()
        self.assert_kept_unheld(result, paths[0].name, "Tekstowo failed")
        self.assert_kept_unheld(
            result, paths[1].name, "Tekstowo not asked (cooling down): nothing removed"
        )

    def test_only_genius_asked_is_no_provider_answered_not_held(self) -> None:
        paths = self.wrong_track()
        result = self.hold("--only-providers", "genius")
        self.assert_kept_unheld(
            result, paths[0].name, "no provider answered: nothing removed"
        )

    def test_a_later_source_that_sings_still_replaces(self) -> None:
        paths = self.wrong_track()
        self.providers = {
            **self.titles,
            TEKSTOWO_KEY: run_fakes.TitleProvider({TITLES[HARBOR]: HARBOR}),
        }
        result = self.hold()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.WRITTEN)
        self.assertEqual(self.text_of(paths[0]), words(HARBOR))
        self.assertEqual(self.held.read_bytes(), b"")


class GeniusTest(_HoldCase):
    """The option leaves Genius out as --no-genius does: no key is even looked for."""

    def test_genius_is_never_asked_with_the_option(self) -> None:
        self.wrong_track()
        key = {fixtures.GENIUS_TOKEN_ENV: INVENTED_KEY}
        with mock.patch.dict(os.environ, key):
            held = self.hold()
            asked = self.provider_calls[-1][0]
            self.later(5)
            plain = self.fix("--dry-run")
        self.assertEqual(held.code, album.EXIT_OK, held.stdout + held.stderr)
        self.assertIsNone(asked)
        self.assertIn("genius: skipped", held.stdout)
        # The same environment hands a fix without the option the key.
        self.assertNotIn("genius: skipped", plain.stdout)

    def test_a_dry_run_skips_genius_and_writes_no_list(self) -> None:
        paths = self.wrong_track()
        before = raw(paths)
        result = self.hold("--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIn("genius: skipped", result.stdout)
        self.assertFalse(self.held.exists())
        self.assertEqual(raw(paths), before)


class ListTest(_HoldCase):
    """The list is written as --changed-list is: its failure is exit 3, said last."""

    def test_a_list_that_cannot_be_written_is_exit_3(self) -> None:
        self.wrong_track()
        self.held = self.blocked("held.txt")
        result = self.hold()
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN, result.stderr)
        self.assertIn(f"could not write {HOLD} {self.held}", result.stderr)

    def test_both_lists_failing_are_both_said(self) -> None:
        self.wrong_track()
        self.held = self.blocked("held.txt")
        changed = self.blocked("changed.txt")
        result = self.hold("--changed-list", str(changed))
        self.assertEqual(result.code, album.EXIT_RECORD_NOT_WRITTEN, result.stderr)
        self.assertIn(f"could not write --changed-list {changed}", result.stderr)
        self.assertIn(f"could not write {HOLD} {self.held}", result.stderr)

    def test_a_left_out_path_names_the_option_it_was_left_out_of(self) -> None:
        left = lyrics_changed.LeftOut("relative.mp3", lyrics_changed.REASON_RELATIVE)
        self.assertTrue(
            lyrics_changed.left_out_notice(left, flag=HOLD).startswith(f"{HOLD}: ")
        )
        self.assertTrue(
            lyrics_changed.left_out_notice(left).startswith("--changed-list: ")
        )

    def test_only_tracks_held_for_genius_are_listed(self) -> None:
        held = lr.TrackOutcome(
            lr.OutcomeStatus.KEPT_EXISTING, withheld=lyrics_outcomes.HELD_FOR_GENIUS
        )
        spoken = lr.TrackOutcome(
            lr.OutcomeStatus.KEPT_EXISTING, withheld="spoken word (genre Comedy)"
        )
        tracks = [
            lr.ManifestTrack(name, (1, 2), lr.TrackBefore((), ()), after)
            for name, after in (("a.mp3", held), ("b.mp3", spoken), ("c.mp3", None))
        ]
        listed = lyrics_changed.held_changed(self.album, tracks)
        self.assertEqual(listed, (os.path.abspath(self.album / "a.mp3"),))


class SummaryLineTest(_HoldCase):
    """The ``removal withheld:`` line counts the spoken and the held apart."""

    def test_the_line_names_each_reason_it_counts(self) -> None:
        cases = (
            (
                lcat.OutcomeCategories(withheld=3, held=1),
                "spoken word 2, held for Genius 1",
            ),
            (lcat.OutcomeCategories(withheld=2), "spoken word 2"),
            (lcat.OutcomeCategories(withheld=2, held=2), "held for Genius 2"),
        )
        for counts, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    lcat.format_withheld(counts), f"removal withheld: {expected}"
                )
        self.assertIsNone(lcat.format_withheld(lcat.OutcomeCategories()))


class RefusedTest(_HoldCase):
    """The option is the fix's alone, and never beside --no-genius."""

    def test_every_other_mode_refuses_the_option(self) -> None:
        for mode in ("run", "check", "undo", "strip", "compare", "prune", "accept"):
            with self.subTest(mode=mode):
                result = self.run_album("--mode", mode, HOLD, str(self.held))
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"{HOLD} cannot be used with --mode {mode}", result.stderr
                )

    def test_the_option_beside_no_genius_is_refused(self) -> None:
        result = self.hold("--no-genius")
        self.assertEqual(result.code, 2)
        self.assertIn(f"{HOLD} cannot be used with --no-genius", result.stderr)
        self.assertFalse(self.held.exists())

    def test_one_file_named_for_both_lists_is_refused(self) -> None:
        paths = self.wrong_track()
        before = raw(paths)
        self.held.write_bytes(b"")
        linked = self.tmp / "linked.txt"
        linked.symlink_to(self.held)
        hard = self.tmp / "hard.txt"
        os.link(self.held, hard)
        dangling = self.tmp / "dangling.txt"
        dangling.symlink_to(self.tmp / "not-yet.txt")
        cases = {
            "the same path": (self.held, self.held),
            "one spelled another way": (self.tmp / "." / "held.txt", self.held),
            "a link to the other": (linked, self.held),
            "a hard link": (hard, self.held),
            "a link to a file not yet written": (dangling, self.tmp / "not-yet.txt"),
        }
        for case, (changed, held) in cases.items():
            with self.subTest(case=case):
                result = self.fix("--changed-list", str(changed), HOLD, str(held))
                self.assertEqual(result.code, 2, result.stdout + result.stderr)
                self.assertIn(SAME_FILE, result.stderr)
        self.assertEqual(self.held.read_bytes(), b"")
        self.assertFalse((self.tmp / "not-yet.txt").exists())
        self.assertEqual(raw(paths), before)

    def test_two_files_for_the_two_lists_are_taken(self) -> None:
        self.wrong_track()
        changed = self.tmp / "changed.txt"
        result = self.hold("--changed-list", str(changed))
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertNotIn(SAME_FILE, result.stderr)
        self.assertTrue(changed.exists())
        self.assertEqual(len(self.listed()), 1)
