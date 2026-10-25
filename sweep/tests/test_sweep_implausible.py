"""`--refetch-implausible`: handed to each album child, riding the sweep's own pass.

The option adds no pass: a sweep with it keeps the normal pass's done set, or the
refetch pass's beside ``--refetch-untimed``, and hands the album tool the option. The
survey's restart hint and the printed resume line carry it, and ``--offline`` is
refused beside it, as the album tool refuses the pair. Under the option, and only
then, the survey visits an album whose every track is synced when a track's serving
lyrics are implausible, by the album tool's own rule, and names why. The album tool is
played by `sweep_fixtures.FakeAlbumTool`, so nothing here reaches a provider.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import album  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_survey  # noqa: E402 - import follows the sys.path bootstrap above
from test_sweep_pass import PassCliCase  # noqa: E402 - import follows the sys.path bootstrap above

OPTION = "--refetch-implausible"
UNTIMED = "--refetch-untimed"

#: MPEG-1 Layer III, 128 kbps, 44.1 kHz, mono: a frame header, and the frame's whole
#: length, its payload silent. Each frame plays 1152 samples.
_FRAME_HEADER = bytes((0xFF, 0xFB, 0x90, 0xC0))
_FRAME_BYTES = 417
_FRAMES_A_SECOND = 44_100 / 1152
#: The interlude's length, and timed lines a whole song long on it: the latest starts
#: three minutes in, far past its end.
INTERLUDE_S = 10
WRONG_SONG_LRC = (
    "[00:01.00]The tram bell rings for nobody\n[03:00.00]and the rails hum on\n"
)
#: The interlude's own lines, inside its length.
RIGHT_LRC = "[00:01.00]The tram bell rings for nobody\n[00:04.50]and the rails hum on\n"


def audible_track(album: Path, name: str, lrc: str) -> Path:
    """A silent MP3 of `INTERLUDE_S` seconds named *name* under *album*, *lrc* beside it.

    Unlike `sweep_fixtures.add_track`'s empty file, it holds audio, so its duration
    can be read and its lyrics judged against it.
    """
    mp3 = album / name
    mp3.parent.mkdir(parents=True, exist_ok=True)
    frame = _FRAME_HEADER + bytes(_FRAME_BYTES - len(_FRAME_HEADER))
    mp3.write_bytes(frame * round(INTERLUDE_S * _FRAMES_A_SECOND))
    mp3.with_suffix(".lrc").write_text(lrc, encoding="utf-8", newline="")
    return mp3


class ImplausibleArgvTest(sf.IsolatedCase):
    """The child's command line carries the option only when asked."""

    def argv(self, *, implausible: bool, untimed: bool = False) -> list[str]:
        """The album tool's command for one album."""
        return sweep_child.album_argv(
            self.tmp / "album.py",
            self.library,
            lang=None,
            offline=False,
            refetch_untimed=untimed,
            refetch_implausible=implausible,
        )

    def test_the_option_reaches_the_command_line_only_when_asked(self) -> None:
        self.assertEqual(self.argv(implausible=True)[-1], OPTION)
        self.assertNotIn(OPTION, self.argv(implausible=False))
        self.assertEqual(sweep_child.IMPLAUSIBLE_OPTION, OPTION)

    def test_the_album_tools_parser_reads_both_refetch_options(self) -> None:
        parser = album.build_parser()
        parser.allow_abbrev = False
        args = parser.parse_args(self.argv(implausible=True, untimed=True)[2:])
        self.assertEqual((args.refetch_untimed, args.refetch_implausible), (True, True))


class ImplausibleSweepTest(PassCliCase):
    """A run hands the option on in its own pass; the survey and resume repeat it."""

    def test_a_run_passes_the_option_and_keeps_the_normal_pass(self) -> None:
        self.main("--mode", "run", OPTION, "--limit", "1")
        self.assertIn(OPTION, self.fake.calls[0])
        self.assertNotIn(UNTIMED, self.fake.calls[0])
        self.assertEqual([e.pass_ for e in self.entries()], [sl.SweepPass.NORMAL])

    def test_beside_the_untimed_refetch_it_rides_that_pass(self) -> None:
        self.main("--mode", "run", UNTIMED, OPTION, "--limit", "1")
        self.assertIn(OPTION, self.fake.calls[0])
        self.assertIn(UNTIMED, self.fake.calls[0])
        self.assertEqual(
            [e.pass_ for e in self.entries()], [sl.SweepPass.REFETCH_UNTIMED]
        )

    def test_albums_the_normal_pass_did_are_passed_over(self) -> None:
        self.main("--mode", "run")
        again = self.main("--mode", "run", OPTION)
        self.assertEqual(len(self.fake.calls), 3)
        self.assertIn("already done: 3\n", again.stdout)

    def test_the_resume_line_repeats_the_option(self) -> None:
        result = self.main("--mode", "run", OPTION, "--limit", "1")
        resume = result.stdout.split("resume: ")[1].splitlines()[0]
        self.assertIn(OPTION, resume)

    def test_the_survey_restart_hint_names_the_option(self) -> None:
        self.main("--mode", "run")
        survey = self.main("--mode", "survey", OPTION)
        self.assertEqual(survey.code, 0)
        self.assertIn(f"--mode run {OPTION} --restart", survey.stdout)
        self.main("--mode", "run", UNTIMED)
        both = self.main("--mode", "survey", UNTIMED, OPTION)
        self.assertIn(f"--mode run {UNTIMED} {OPTION} --restart", both.stdout)

    def test_the_option_is_refused_with_offline(self) -> None:
        result = self.main("--mode", "run", OPTION, "--offline")
        self.assertEqual(result.code, 2)
        self.assertIn(f"{OPTION} cannot be used with --offline", result.stderr)
        self.assertEqual(self.fake.calls, [])

    def test_the_entry_point_names_the_option_in_its_help(self) -> None:
        self.assertIn(OPTION, sweep.build_parser().format_help())


class ImplausibleSurveyAlbumTest(sf.IsolatedCase):
    """`survey_album` counts a track serving implausible lyrics only when asked."""

    def test_a_synced_album_serving_another_songs_lines_needs_a_visit(self) -> None:
        album = self.library / "Artist" / "Wrong"
        audible_track(album, "01 Interlude.mp3", WRONG_SONG_LRC)
        asked = sweep_survey.survey_album(album, lang="eng", refetch_implausible=True)
        self.assertTrue(asked.needs_visit)
        self.assertEqual(asked.synced, 1)
        (why,) = asked.implausible
        self.assertTrue(
            why.startswith("01 Interlude.mp3 (past end: last line 180.000 s")
        )
        plain = sweep_survey.survey_album(album, lang="eng")
        self.assertFalse(plain.needs_visit)
        self.assertEqual(plain.implausible, ())

    def test_a_synced_album_serving_its_own_lines_needs_no_visit(self) -> None:
        album = self.library / "Artist" / "Right"
        audible_track(album, "01 Interlude.mp3", RIGHT_LRC)
        survey = sweep_survey.survey_album(album, lang="eng", refetch_implausible=True)
        self.assertFalse(survey.needs_visit)
        self.assertEqual(survey.implausible, ())


class ImplausibleVisitTest(PassCliCase):
    """Beside three needy albums, a synced one serving another song's lines."""

    def setUp(self) -> None:
        """Add the synced album whose one track's lines run far past its end."""
        super().setUp()
        audible_track(self.library / "Artist" / "Wrong", "01.mp3", WRONG_SONG_LRC)

    def test_the_survey_visits_it_only_under_the_option_and_says_why(self) -> None:
        heading = "albums a run would visit"
        plain = self.main("--mode", "survey")
        self.assertNotIn("Artist/Wrong", self.listed(plain.stdout, heading))
        self.assertNotIn("implausible:", plain.stdout)
        asked = self.main("--mode", "survey", OPTION)
        self.assertIn("Artist/Wrong", self.listed(asked.stdout, heading))
        self.assertIn(
            "implausible: Artist/Wrong/01.mp3 (past end: last line 180.000 s",
            asked.stdout,
        )

    def test_a_run_visits_it_only_under_the_option(self) -> None:
        self.main("--mode", "run")
        self.assertNotIn("Wrong", self.fake.albums())
        self.main("--mode", "run", OPTION)
        self.assertEqual(self.fake.albums()[-1:], ["Wrong"])

    def test_its_visit_keeps_the_passs_done_set(self) -> None:
        self.main("--mode", "run", OPTION)
        again = self.main("--mode", "run", OPTION)
        self.assertEqual(len(self.fake.calls), 4)
        self.assertIn("already done: 4\n", again.stdout)
        self.main("--mode", "run", OPTION, "--restart")
        self.assertEqual(len(self.fake.calls), 8)
