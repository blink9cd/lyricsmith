"""The paste walker's reading of each child's end, and what it hands each child.

Only the album tool's not-sung exit (`6`, `lyrics_paste_mode.EXIT_NOT_SUNG`) asks the
person to copy again; a track error (`5`), a crash (`1`), or any other end is that row's
failure, and the walk goes on. ``--lang`` reaches each child and the walker's own refusal
check. The child runs without the Genius key. A row the walker cannot look at is passed
over, never a traceback, and a list holding a NUL is refused whole. Every title, URL, path
and lyric is invented.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - puts album and the libraries on sys.path
import paste_fixtures as pf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_paste as sp  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import HARBOR, MEADOW  # noqa: E402 - follows the bootstrap above
from lyrics_config import GENIUS_TOKEN_ENV  # noqa: E402 - follows the bootstrap above
from lyrics_paste_mode import EXIT_PASTE_ERROR  # noqa: E402 - follows the bootstrap above

#: The answer that pastes: Enter alone.
ENTER = ""
#: The album tool's paste exits, as its contract with the walker fixes them: not sung,
#: a track error (`lyrics_paste_mode.EXIT_PASTE_ERROR`), and a crash's traceback.
NOT_SUNG, TRACK_ERROR, CRASH = 6, 5, 1


class ChildEndTest(pf.PasteCase):
    """Copy again on not sung alone; every other end is the row's, and the walk goes on."""

    def test_only_not_sung_asks_to_copy_again(self) -> None:
        self.assertEqual(sp.EXIT_NOT_SUNG, NOT_SUNG)
        self.child.codes = [NOT_SUNG, 0, 0]
        self.terminal.answers = [ENTER, ENTER, ENTER]
        self.terminal.clips = [HARBOR, HARBOR, MEADOW]
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(result.stderr.count(sp.NOT_SUNG_AGAIN), 1)
        self.assertIn("paste: 2 replaced", result.stdout)

    def _assert_the_row_s_failure(self, code: int) -> None:
        """A child ending *code* fails its row, asks no copy again, and the walk goes on."""
        self.child.codes = [code, 0]
        self.terminal.answers = [ENTER, ENTER]
        self.terminal.clips = [HARBOR, MEADOW]
        result = self.walk()
        self.assertEqual(result.code, 1, result.stderr)
        self.assertNotIn(sp.NOT_SUNG_AGAIN, result.stderr)
        self.assertIn(
            f"failed {self.tracks[0]}: the album tool exited {code}", result.stdout
        )
        self.assertIn("1 replaced", result.stdout)

    def test_a_track_error_is_the_row_s_failure_never_copy_again(self) -> None:
        self.assertEqual(EXIT_PASTE_ERROR, TRACK_ERROR)
        self._assert_the_row_s_failure(TRACK_ERROR)

    def test_a_crash_is_the_row_s_failure_never_copy_again(self) -> None:
        self._assert_the_row_s_failure(CRASH)


class ChildInputTest(pf.PasteCase):
    """Each child is told the language and is never handed the Genius key."""

    def test_lang_reaches_each_child_its_default_when_omitted(self) -> None:
        self.child.codes = [0, 0, 0, 0]
        self.terminal.answers = [ENTER, ENTER, ENTER, ENTER]
        self.terminal.clips = [HARBOR, MEADOW, HARBOR, MEADOW]
        self.walk()
        self.walk("--lang", "fra")
        langs = [sf.option_value(argv, "--lang") for argv in self.child.calls]
        self.assertEqual(langs, [sp.DEFAULT_LANG, sp.DEFAULT_LANG, "fra", "fra"])

    def test_lang_is_what_the_walker_judges_a_refusal_by(self) -> None:
        with mock.patch.object(sp, "paste_refusal", return_value=None) as refusal:
            self.terminal.answers = ["q"]
            self.walk("--lang", "fra")
        self.assertEqual(refusal.call_args.args[-1], "fra")

    def test_the_child_runs_without_the_genius_key(self) -> None:
        self.terminal.answers = [ENTER, "q"]
        self.terminal.clips = [HARBOR]
        with mock.patch.dict(os.environ, {GENIUS_TOKEN_ENV: "invented-genius-key"}):
            self.walk()
        (env,) = self.child.envs
        # Membership alone, so a failure never prints the environment.
        self.assertFalse(GENIUS_TOKEN_ENV in env, "the child was handed the key")
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))


class UnreadableRowTest(pf.PasteCase):
    """A row whose folders cannot be looked at is passed over; a NUL row refused."""

    def test_a_failed_link_check_passes_the_row_over(self) -> None:
        self.terminal.answers = ["q"]
        failing = mock.patch.object(
            sp, "is_linked", side_effect=ValueError("embedded null character")
        )
        with failing:
            result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn(
            f"skipped {self.tracks[0]}: it cannot be read: embedded null character",
            result.stdout,
        )

    def test_a_nul_in_a_listed_track_refuses_the_list(self) -> None:
        nul = f"{self.tracks[0].parent}/01\0Harbor.mp3"
        self.write_has_it([(nul, pf.URLS[HARBOR], "Harbor Song")])
        result = self.walk()
        self.assertEqual(result.code, 2, result.stderr)
        self.assertIn("NUL", result.stderr)
        self.assertEqual(self.terminal.opened, [])


class HelpTest(pf.PasteCase):
    """``--help`` says paste takes what it takes, and refuses the fix's held list."""

    def test_the_help_names_paste_for_its_shared_options(self) -> None:
        helps = {
            action.option_strings[0]: action.help or ""
            for action in sweep.build_parser()._actions
            if action.option_strings
        }
        for flag in ("--heard-dir", "--changed-list"):
            with self.subTest(flag):
                self.assertIn("paste", helps[flag])
        self.assertNotIn("fix-text only", helps["--heard-dir"])

    def test_paste_refuses_the_fix_s_held_list(self) -> None:
        result = self.walk("--hold-for-genius", str(self.tmp / "held.txt"))
        self.assertEqual(result.code, 2)
        self.assertIn("--hold-for-genius cannot be used", result.stderr)
