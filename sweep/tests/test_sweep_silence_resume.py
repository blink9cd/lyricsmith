"""--mode silence's --resume and Ctrl-C, and the options each mode refuses.

A resumed pass appends to the list it is given, skipping the albums it marks done, after
cutting off an unfinished album's lines. Ctrl-C keeps every finished album and prints
the command that resumes. Silence takes only --library, --limit and --resume, and every
other mode refuses --resume. ffmpeg is `silence_fixtures.FakeFfmpeg`, so nothing here
starts a process. Every album and title here is invented.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import silence_fixtures as sfx  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence_list as sil  # noqa: E402 - import follows the sys.path bootstrap above

_HEADER = sil.header_line()
_FIRST, _SECOND = "Artist/First", "Artist/Second"


class ResumeCase(sfx.SilenceCase):
    """Two albums of one silent track each, and a list path outside the state folder."""

    def setUp(self) -> None:
        """Build ``Artist/First`` and ``Artist/Second``."""
        super().setUp()
        sfx.add_track(self.library / "Artist" / "First", "01 One.mp3", title="")
        sfx.add_track(self.library / "Artist" / "Second", "01 Two.mp3", title="")
        self.ffmpeg.silent = frozenset({"01 One.mp3", "01 Two.mp3"})
        self.path = self.tmp / "kept.tsv"

    def write_list(self, text: str) -> None:
        """Write *text* as the list to resume."""
        self.path.write_bytes(text.encode("utf-8"))

    def markers(self) -> list[str]:
        """The albums the list's markers name, in order."""
        return [
            line.split("\t")[1]
            for line in self.list_lines(self.path)
            if line.startswith(sil.DONE_MARKER)
        ]


class ResumeTest(ResumeCase):
    """A resume appends to its list, skipping what it marks done."""

    def test_a_resume_measures_only_what_is_not_done(self) -> None:
        self.write_list(_HEADER + sil.marker_line(_FIRST))
        result = self.main("--resume", str(self.path))
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.ffmpeg.calls, ["01 Two.mp3"])
        self.assertIn("[1/1] Artist/Second", result.stderr)
        self.assertEqual(self.markers(), [_FIRST, _SECOND])
        self.assertTrue(result.stdout.startswith("already done: 1\n"))
        self.assertEqual(self.lists(), [])

    def test_an_unfinished_album_is_cut_off_and_measured_again(self) -> None:
        unfinished = "unsure\tr\t-91.0\t\t\t\tArtist/Second/01 Two.mp3\n#album-do"
        self.write_list(_HEADER + sil.marker_line(_FIRST) + unfinished)
        result = self.main("--resume", str(self.path))
        self.assertIn("dropped 2 lines of an unfinished album", result.stderr)
        lines = self.list_lines(self.path)
        self.assertEqual(len(lines), len(set(lines)))
        self.assertEqual(self.markers(), [_FIRST, _SECOND])
        sil.parse_resume(self.path.read_bytes())

    def test_a_header_with_no_newline_is_written_again(self) -> None:
        self.write_list(_HEADER.rstrip("\n"))
        result = self.main("--resume", str(self.path))
        self.assertIn(
            f"the list's header had no line ending; it was written again: "
            f"{self.path}\n",
            result.stderr,
        )
        self.assertNotIn("dropped", result.stderr)
        self.assertTrue(self.path.read_text("utf-8").startswith(_HEADER))
        self.assertEqual(self.markers(), [_FIRST, _SECOND])

    def test_a_malformed_list_exits_two_naming_it_and_is_left_alone(self) -> None:
        self.write_list(_HEADER + "garbage\n")
        result = self.main("--resume", str(self.path))
        self.assertEqual(result.code, 2)
        self.assertIn(str(self.path), result.stderr)
        self.assertEqual(self.path.read_text("utf-8"), _HEADER + "garbage\n")
        self.assertEqual(self.ffmpeg.calls, [])

    def test_a_list_of_another_library_exits_two_and_is_left_alone(self) -> None:
        text = _HEADER + sil.marker_line("Other/Album")
        self.write_list(text)
        result = self.main("--resume", str(self.path))
        self.assertEqual(result.code, 2)
        self.assertIn(
            f"{self.path} marks 1 album done, and none of them is an album of "
            f"{self.library}",
            result.stderr,
        )
        self.assertEqual(self.path.read_text("utf-8"), text)
        self.assertEqual(self.ffmpeg.calls, [])
        self.assertEqual(result.stdout, "")

    def test_a_list_marking_some_albums_of_this_library_is_resumed(self) -> None:
        self.write_list(
            _HEADER + sil.marker_line("Gone/Album") + sil.marker_line(_FIRST)
        )
        result = self.main("--resume", str(self.path))
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.ffmpeg.calls, ["01 Two.mp3"])

    def test_a_missing_list_exits_two_naming_it(self) -> None:
        result = self.main("--resume", str(self.path))
        self.assertEqual(result.code, 2)
        self.assertIn(str(self.path), result.stderr)
        self.assertFalse(self.path.exists())


class InterruptTest(ResumeCase):
    """Ctrl-C keeps the finished albums and prints the command that resumes."""

    def test_ctrl_c_keeps_the_finished_albums_and_prints_the_resume(self) -> None:
        self.ffmpeg.interrupt = "01 Two.mp3"
        result = self.main()
        self.assertEqual(result.code, 130)
        self.assertIn("interrupted", result.stderr)
        listed = self.the_list()
        self.assertEqual(
            self.list_lines(),
            [
                "unsure\tplaceholder title, 30.0 s\t-91.0"
                "\t30.0\t\t\tArtist/First/01 One.mp3",
                f"#album-done\t{_FIRST}",
            ],
        )
        self.assertIn("interrupted; albums left: 1\n", result.stdout)
        resume = result.stdout.splitlines()[-1]
        self.assertEqual(
            resume,
            f"resume: sweep.py --mode silence --library {shlex.quote(str(self.library))} "
            f"--resume {shlex.quote(str(listed))}",
        )
        # The printed command, run, finishes the pass in the same list.
        self.ffmpeg.interrupt = None
        again = self.main(*shlex.split(resume)[2:])
        self.assertEqual(again.code, 0, again.stderr)
        self.assertEqual(self.lists(), [listed])
        self.assertEqual(
            [line for line in self.list_lines() if line.startswith("#")],
            [f"#album-done\t{_FIRST}", f"#album-done\t{_SECOND}"],
        )


#: The options silence takes, as the parser names their destinations.
_SILENCE_OWN = frozenset({"help", "library", "mode", "limit", "resume"})
#: A value each option taking one accepts, where ``1`` would not do.
_VALUES = {"lang": "eng", "changed_list": "changed.txt"}


def _other_options() -> list[list[str]]:
    """Every option the sweep's parser declares but silence's own, each with a value."""
    return [
        [action.option_strings[0]]
        if action.nargs == 0
        else [action.option_strings[0], _VALUES.get(action.dest, "1")]
        for action in sweep.build_parser()._actions
        if action.dest not in _SILENCE_OWN
    ]


class OptionTest(sfx.SilenceCase):
    """Silence takes only --library, --limit and --resume; no other mode takes --resume."""

    def test_silence_refuses_every_other_option(self) -> None:
        options = _other_options()
        # The parser's own options, read from it: a new one is refused or this fails.
        self.assertIn(["--no-tekstowo"], options)
        self.assertIn(["--stop-after", "1"], options)
        for option in options:
            with self.subTest(option=option):
                result = self.main(*option)
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"{option[0]} cannot be used with --mode silence", result.stderr
                )

    def test_every_other_mode_refuses_resume(self) -> None:
        for mode in ("survey", "run", "accept"):
            with self.subTest(mode=mode):
                result = self.main("--resume", "kept.tsv", mode=mode)
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"--resume cannot be used with --mode {mode}", result.stderr
                )
