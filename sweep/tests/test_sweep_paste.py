"""``--mode paste``: walk has-it.tsv, open each page, paste each copied text in.

Each test runs the walker through the entry point over an invented library, its
terminal, clipboard and browser scripted (`paste_fixtures.FakeTerminal`) and its album
tool child run in this process on the real ``--mode paste``. Every title, URL and lyric
is invented.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import album  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import paste_fixtures as pf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_paste as sp  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import FOUNDRY, HARBOR, MEADOW, QUARRY, words  # noqa: E402 - follows the bootstrap

#: The answer that pastes: Enter alone.
ENTER = ""


class ReplacedTest(pf.PasteCase):
    """Enter pastes the clipboard; a sung text is written and the walk moves on."""

    def test_each_row_is_opened_pasted_and_written(self) -> None:
        self.terminal.answers = [ENTER, ENTER]
        self.terminal.clips = [HARBOR, MEADOW]
        changed = self.tmp / "changed.txt"
        result = self.walk("--changed-list", str(changed))
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.terminal.opened, [pf.URLS[HARBOR], pf.URLS[MEADOW]])
        self.assertEqual(self.words_of(self.tracks[0]), words(HARBOR))
        self.assertEqual(self.words_of(self.tracks[1]), words(MEADOW))
        self.assertIn(
            "paste: 2 replaced, 0 skipped, 0 refused, 0 given up", result.stdout
        )
        # Each child's own list, merged in visit order.
        self.assertEqual(
            changed.read_text("utf-8").splitlines(), [str(t) for t in self.tracks]
        )
        for argv in self.child.calls:
            self.assertEqual(sf.option_value(argv, "--mode"), "paste")
            self.assertEqual(sf.option_value(argv, "--heard-dir"), str(self.heard))
        self.assertEqual(self.terminal.prompts[0], sp.PROMPT)

    def test_the_text_file_is_private_and_removed_after_each_attempt(self) -> None:
        self.terminal.answers = [ENTER, ENTER]
        self.terminal.clips = [HARBOR, MEADOW]
        self.walk()
        self.assertEqual(self.child.texts, [HARBOR, MEADOW])
        self.assertEqual(self.child.modes, [0o600, 0o600])
        for text_file in self.child.files:
            self.assertFalse(text_file.exists())
            self.assertFalse(text_file.parent.exists())

    def test_an_unsung_paste_is_asked_again_until_one_is_sung(self) -> None:
        self.terminal.answers = [ENTER, ENTER, "q"]
        self.terminal.clips = [FOUNDRY, HARBOR]
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.words_of(self.tracks[0]), words(HARBOR))
        self.assertEqual(len(self.child.calls), 2)
        self.assertIn("not sung", result.stderr)
        self.assertIn(sp.NOT_SUNG_AGAIN, result.stderr)

    def test_an_unknown_answer_asks_again(self) -> None:
        self.terminal.answers = ["maybe", ENTER, "q"]
        self.terminal.clips = [HARBOR]
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.words_of(self.tracks[0]), words(HARBOR))
        self.assertIn(sp.UNKNOWN_ANSWER, result.stderr)


class SkipAndStopTest(pf.PasteCase):
    """``s`` skips a row, ``q`` and the end of input stop the walk cleanly."""

    def test_s_skips_and_q_stops(self) -> None:
        self.terminal.answers = ["s", "q"]
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.child.calls, [])
        self.assertEqual(self.words_of(self.tracks[0]), words(QUARRY))
        self.assertEqual(len(self.terminal.opened), 2)
        self.assertIn(
            "paste: 0 replaced, 1 skipped, 0 refused, 0 given up", result.stdout
        )
        self.assertIn(f"skipped {self.tracks[0]}: {sp.SKIPPED_BY_YOU}", result.stdout)
        self.assertIn(sp.STOPPED, result.stdout)

    def test_s_after_an_unsung_paste_is_given_up(self) -> None:
        self.terminal.answers = [ENTER, "s", "q"]
        self.terminal.clips = [FOUNDRY]
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn(
            "paste: 0 replaced, 0 skipped, 0 refused, 1 given up", result.stdout
        )
        self.assertIn(f"given up {self.tracks[0]}: not sung", result.stdout)

    def test_the_end_of_input_stops_as_q_does(self) -> None:
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(len(self.terminal.opened), 1)
        self.assertIn(sp.STOPPED, result.stdout)


class ResumeTest(pf.PasteCase):
    """A row whose track no longer holds the heard text, or has no record, is passed."""

    def test_a_track_already_pasted_is_skipped_unopened(self) -> None:
        self.terminal.answers = [ENTER]
        self.terminal.clips = [HARBOR]
        first = self.walk()
        self.assertEqual(first.code, 0, first.stderr)
        self.terminal.answers = [ENTER]
        self.terminal.clips = [MEADOW]
        self.terminal.opened.clear()
        again = self.walk()
        self.assertEqual(again.code, 0, again.stderr)
        self.assertEqual(self.terminal.opened, [pf.URLS[MEADOW]])
        self.assertIn(
            f"skipped {self.tracks[0]}: its text changed since it was heard",
            again.stdout,
        )
        self.assertEqual(self.words_of(self.tracks[1]), words(MEADOW))

    def test_a_track_with_no_record_is_skipped(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        self.heard = empty
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.terminal.opened, [])
        self.assertIn("paste: 0 replaced, 2 skipped", result.stdout)
        self.assertIn("no transcript record", result.stdout)

    def test_a_row_outside_the_library_or_not_genius_is_never_opened(self) -> None:
        outside = self.tmp / "elsewhere" / "Artist" / "Album" / "01 Out.mp3"
        self.write_has_it(
            [
                (outside, pf.URLS[HARBOR], "Harbor Song"),
                (self.tracks[0], "https://lyrics.example/harbor", "Harbor Song"),
                (self.tracks[1], "http://genius.com/plain-http-lyrics", "Meadow"),
            ]
        )
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.terminal.opened, [])
        self.assertEqual(self.child.calls, [])
        self.assertIn(f"skipped {outside}: {sp.OUTSIDE_LIBRARY}", result.stdout)
        self.assertIn(f"skipped {self.tracks[0]}: {sp.NOT_GENIUS}", result.stdout)
        self.assertIn(f"skipped {self.tracks[1]}: {sp.NOT_GENIUS}", result.stdout)


class ChildOutcomeTest(pf.PasteCase):
    """A refusal moves on; an unexpected child exit is counted and exits 1."""

    def test_a_refused_track_is_noted_and_the_walk_moves_on(self) -> None:
        self.child.codes = [sp.EXIT_REFUSED, 0]
        self.terminal.answers = [ENTER, ENTER]
        self.terminal.clips = [HARBOR, MEADOW]
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(len(self.child.calls), 2)
        self.assertIn("1 refused", result.stdout)

    def test_an_unexpected_exit_is_a_failure(self) -> None:
        self.child.codes = [2, 5]
        self.terminal.answers = [ENTER, ENTER]
        self.terminal.clips = [HARBOR, MEADOW]
        result = self.walk()
        self.assertEqual(result.code, 1, result.stderr)
        self.assertIn("2 failed", result.stdout)
        self.assertIn(
            f"failed {self.tracks[0]}: the album tool exited 2", result.stdout
        )

    def test_a_clipboard_that_cannot_be_read_asks_again(self) -> None:
        self.terminal.answers = [ENTER, ENTER, "q"]
        self.terminal.clips = [sp.PasteInputError("the clipboard is empty"), HARBOR]
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("the clipboard is empty", result.stderr)
        self.assertEqual(len(self.child.calls), 1)


class InterruptTest(pf.PasteCase):
    """Ctrl-C at a prompt or in the child ends the walk with 130, the list merged."""

    def test_ctrl_c_at_a_prompt_keeps_what_was_pasted(self) -> None:
        self.terminal.answers = [ENTER, KeyboardInterrupt()]
        self.terminal.clips = [HARBOR]
        changed = self.tmp / "changed.txt"
        result = self.walk("--changed-list", str(changed))
        self.assertEqual(result.code, 130, result.stderr)
        self.assertEqual(changed.read_text("utf-8"), f"{self.tracks[0]}\n")
        self.assertIn("paste: 1 replaced", result.stdout)
        self.assertIn("interrupted", result.stderr)

    def test_ctrl_c_in_the_child_ends_the_walk(self) -> None:
        self.child.interrupted = True
        self.terminal.answers = [ENTER, ENTER]
        self.terminal.clips = [HARBOR, MEADOW]
        result = self.walk()
        self.assertEqual(result.code, 130, result.stderr)
        self.assertEqual(len(self.child.calls), 1)
        self.assertFalse(self.child.files[0].exists())


class SetupTest(pf.PasteCase):
    """No terminal, an unusable list or store, or a misplaced option: exit 2 at once."""

    def test_no_terminal_exits_2_before_anything(self) -> None:
        self.terminal.why_not = "stdin is not a terminal"
        result = self.walk()
        self.assertEqual(result.code, 2)
        self.assertIn("stdin is not a terminal", result.stderr)
        self.assertEqual(self.terminal.opened, [])

    def test_an_unusable_list_exits_2(self) -> None:
        for name, data in (
            ("missing", None),
            ("wrong header", b"track\turl\n"),
            ("short row", "\t".join(sp.HAS_IT_COLUMNS).encode() + b"\n/a.mp3\tx\n"),
        ):
            with self.subTest(name):
                self.has_it.unlink(missing_ok=True)
                if data is not None:
                    self.has_it.write_bytes(data)
                result = self.walk()
                self.assertEqual(result.code, 2)
                self.assertIn("--from", result.stderr)
                self.assertEqual(self.terminal.opened, [])

    def test_a_heard_dir_that_is_a_file_exits_2(self) -> None:
        self.heard = self.has_it
        result = self.walk()
        self.assertEqual(result.code, 2)
        self.assertIn("--heard-dir", result.stderr)

    def test_paste_needs_its_list_and_store_and_refuses_the_rest(self) -> None:
        for extra, message in (
            (("--restart",), "--restart cannot be used with --mode paste"),
            (("--limit", "2"), "--limit cannot be used with --mode paste"),
        ):
            with self.subTest(extra):
                result = self.walk(*extra)
                self.assertEqual(result.code, 2)
                self.assertIn(message, result.stderr)

    def test_from_is_refused_by_other_modes(self) -> None:
        result = self.walk_raw("--mode", "survey", "--from", str(self.has_it))
        self.assertEqual(result.code, 2)
        self.assertIn("--from cannot be used with --mode survey", result.stderr)

    def walk_raw(self, *argv: str) -> pf.Result:
        """Run the entry point with *argv* and ``--library`` alone."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = sweep.main(["--library", str(self.library), *argv])
            except SystemExit as stop:
                code = int(stop.code or 0)
        return pf.Result(code, out.getvalue(), err.getvalue())


class NonGeniusTest(pf.PasteCase):
    """A URL that is not Genius's is skipped even when it looks like one."""

    def test_a_lookalike_host_is_never_opened(self) -> None:
        self.write_has_it(
            [(self.tracks[0], "https://genius.com.lyrics.example/song", "Harbor")]
        )
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.terminal.opened, [])
        self.assertIn(sp.NOT_GENIUS, result.stdout)


class StoredRowTest(pf.PasteCase):
    """The tracks' paths in the list name the library's own files."""

    def test_a_path_spelled_with_dots_still_finds_its_album(self) -> None:
        spelled = Path(
            str(self.tracks[0].parent), "..", "Lanterns", self.tracks[0].name
        )
        self.write_has_it([(spelled, pf.URLS[HARBOR], "Harbor Song")])
        self.terminal.answers = [ENTER]
        self.terminal.clips = [HARBOR]
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.words_of(self.tracks[0]), words(HARBOR))

    def test_a_track_whose_files_cannot_be_read_is_passed_over(self) -> None:
        self.tracks[0].with_suffix(".txt").write_bytes(b"caf\xe9 not utf-8\n")
        self.terminal.answers = ["q"]
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn(f"skipped {self.tracks[0]}: it cannot be read", result.stdout)

    def test_a_folder_directly_under_the_library_is_no_album(self) -> None:
        loose = self.library / "Invented Artist" / "01 Loose.mp3"
        self.write_has_it([(loose, pf.URLS[HARBOR], "Harbor Song")])
        result = self.walk()
        self.assertIn(f"skipped {loose}: {sp.OUTSIDE_LIBRARY}", result.stdout)


class SeamFailureTest(pf.PasteCase):
    """A page that will not open, a child past its bound, a list that cannot be used."""

    def test_a_page_that_will_not_open_is_said_and_still_pasted(self) -> None:
        def refuse(url: str) -> None:
            raise sp.PasteInputError("open failed (exit 1)")

        self.terminal.open_url = refuse  # type: ignore[method-assign] # a failing browser
        self.terminal.answers = ["S", "Q"]
        result = self.walk()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("could not open the page (open failed (exit 1))", result.stderr)
        self.assertIn(pf.URLS[HARBOR], result.stderr)

    def test_a_child_past_its_bound_or_left_running_is_a_failure(self) -> None:
        results = [
            sweep_child.ChildResult(130, timed_out=True),
            sweep_child.ChildResult(None, timed_out=False, pid=4242),
        ]
        self.child.results = results
        self.terminal.answers = [ENTER, ENTER]
        self.terminal.clips = [HARBOR, MEADOW]
        result = self.walk()
        self.assertEqual(result.code, 1, result.stderr)
        self.assertIn("ran past its bound", result.stdout)
        self.assertIn("would not stop", result.stdout)

    def test_an_existing_changed_list_that_is_no_list_exits_2(self) -> None:
        changed = self.tmp / "changed.txt"
        changed.write_bytes(b"not a list")
        result = self.walk("--changed-list", str(changed))
        self.assertEqual(result.code, 2)
        self.assertIn("--changed-list", result.stderr)
        self.assertEqual(self.terminal.opened, [])
        self.assertEqual(changed.read_bytes(), b"not a list")

    def test_the_child_s_command_parses_with_the_album_tool_s_parser(self) -> None:
        argv = sp.paste_argv(
            self.tracks[0].parent,
            self.tracks[0],
            self.heard,
            self.tmp / "t.txt",
            self.tmp / "c.list",
            lang="fra",
        )
        self.assertEqual(argv[1], str(sp.ALBUM_ENTRY))
        args = album.build_parser().parse_args(argv[2:])
        self.assertEqual(
            (
                args.mode,
                args.track,
                args.heard_dir,
                args.text_from,
                args.changed_list,
                args.lang,
            ),
            (
                "paste",
                self.tracks[0],
                self.heard,
                self.tmp / "t.txt",
                self.tmp / "c.list",
                "fra",
            ),
        )
        bare = sp.paste_argv(*argv_parts(self), None, lang=sp.DEFAULT_LANG)
        self.assertNotIn("--changed-list", bare)


def argv_parts(case: pf.PasteCase) -> tuple[Path, Path, Path, Path]:
    """A paste command's album, track, store and text file, as a test builds them."""
    return (case.tracks[0].parent, case.tracks[0], case.heard, case.tmp / "t.txt")
