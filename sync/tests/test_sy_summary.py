"""The rows, the count line, the footer's commands and notes, and the exit code."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path, PureWindowsPath
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_summary as ss  # noqa: E402 - import follows the sys.path bootstrap above
from sy_types import (  # noqa: E402 - import follows the sys.path bootstrap above
    EXIT_FAILED,
    EXIT_OK,
    Action,
    EmbedResult,
    EmbedStop,
    Outcome,
    Planned,
    TrackResult,
)

#: The walk's root, absolute on the host as the walk names it.
ROOT = sy_cases.host_absolute("/music")
#: The root as a printed command names it: quoted as a POSIX shell reads it, on every
#: host, so a Windows drive's backslashes are kept whole.
SHOWN_ROOT = shlex.quote(str(ROOT))
A = ROOT / "One" / "01 A.mp3"
B = ROOT / "Two" / "01 B.mp3"
#: The album tool's words when sync is run through the dispatcher.
DISPATCHED = ("lyricsmith", "album")


class SummaryTest(sy_cases.IsolatedCase):
    def test_a_plan_row_names_the_action_and_the_relative_path(self) -> None:
        (row,) = ss.plan_lines(Planned(A, Action.TIME), ROOT)
        self.assertEqual(row.split(), ["time", "One/01", "A.mp3"])

    def test_a_relative_path_reads_with_slashes_on_every_host(self) -> None:
        # A Windows path, as a run there names it: the row still reads One/01 A.mp3,
        # as the album tool's manifests name a track. Cast, since a run hands a Path.
        root = cast(Path, PureWindowsPath("C:/music"))
        audio = cast(Path, PureWindowsPath("C:/music/One/01 A.mp3"))
        (row,) = ss.plan_lines(Planned(audio, Action.TIME), root)
        self.assertEqual(row.split(), ["time", "One/01", "A.mp3"])

    def test_a_track_given_as_the_root_or_outside_it_shows_its_name(self) -> None:
        for root in (A, ROOT / "Two"):
            (row,) = ss.plan_lines(Planned(A, Action.TIME), root)
            self.assertEqual(row.split(), ["time", "01", "A.mp3"])

    def test_a_refused_plan_row_carries_its_detail(self) -> None:
        refused = Planned(A, Action.ERROR_TEXT, detail="01 A.txt is not UTF-8")
        lines = ss.plan_lines(refused, ROOT)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1], f"{ss.DETAIL_INDENT}01 A.txt is not UTF-8")

    def test_a_flagged_first_line_names_the_file_and_stamp(self) -> None:
        flagged = TrackResult(A, Outcome.TIMED, first_line="[00:09.100]")
        lines = ss.result_lines(flagged, ROOT)
        self.assertEqual(lines[1].strip(), "check the first line: 01 A.lrc [00:09.100]")

    def test_a_skipped_track_is_its_row_alone(self) -> None:
        (row,) = ss.result_lines(TrackResult(A, Outcome.SKIP_TIMED), ROOT)
        self.assertEqual(row.split()[-2:], ["One/01", "A.mp3"])

    def test_unaligned_lines_are_counted(self) -> None:
        lines = ss.result_lines(TrackResult(A, Outcome.TIMED, unaligned=2), ROOT)
        self.assertIn("2 line(s) with nothing to align", lines[1])

    def test_a_track_too_little_heard_says_its_text_was_not_checked(self) -> None:
        lines = ss.result_lines(TrackResult(A, Outcome.TIMED, little_heard=12), ROOT)
        self.assertEqual(
            lines[1].strip(),
            "only 12 word pairs heard, under 20: its text was not checked against"
            " the vocals",
        )

    def test_a_text_not_sung_prints_its_note_once(self) -> None:
        results = [
            TrackResult(A, Outcome.NOT_SUNG, ("0.04 of the 30 word pairs",)),
            TrackResult(B, Outcome.NOT_SUNG),
        ]
        lines = ss.result_lines(results[0], ROOT)
        self.assertEqual(lines[0].split()[:4], ["not", "sung:", "left", "untimed"])
        self.assertEqual(lines[1].strip(), "0.04 of the 30 word pairs")
        footer = ss.footer_lines(results, album=DISPATCHED)
        self.assertEqual(footer.count(ss.NOT_SUNG), 1)
        self.assertNotIn(ss.BY_HAND, footer)
        self.assertNotIn(ss.NOT_SUNG, ss.footer_lines(results[:0], album=DISPATCHED))

    def test_the_count_line_names_each_outcome_seen(self) -> None:
        results = [
            TrackResult(A, Outcome.TIMED),
            TrackResult(B, Outcome.TIMED),
            TrackResult(B, Outcome.LOW_CONFIDENCE),
        ]
        self.assertEqual(
            ss.count_line(results), "timed 2, low confidence: time by hand 1"
        )
        self.assertEqual(ss.count_line([]), "nothing to time")

    def test_without_embed_the_footer_prints_each_folders_album_command(self) -> None:
        timed = [TrackResult(A, Outcome.TIMED)]
        footer = ss.footer_lines(timed, album=DISPATCHED, units=(ROOT,), loose=(B,))
        command = f"embed them: lyricsmith album --offline --album {SHOWN_ROOT}"
        track = shlex.quote(str(B))
        folder = shlex.quote(str(B.parent))
        loose = f"embed it: lyricsmith album --offline --album {folder} --track {track}"
        self.assertEqual(footer[1:], [command, loose])
        embedded = ss.footer_lines(timed, album=DISPATCHED)
        self.assertFalse(any("embed them" in line for line in embedded))

    def test_a_track_of_another_format_is_named_as_staying_a_sidecar(self) -> None:
        flac = ROOT / "One" / "01 F.flac"
        footer = ss.footer_lines(
            [TrackResult(flac, Outcome.TIMED)], album=DISPATCHED, sidecars=(flac,)
        )
        self.assertIn(f"stays a sidecar: {flac.with_suffix('.lrc')}", footer[1])
        self.assertIn("embeds MP3s only", footer[1])

    def test_an_interrupted_run_says_how_many_tracks_are_left(self) -> None:
        line = ss.interrupted_line(3)
        self.assertIn("interrupted", line)
        self.assertIn("3 track(s) left", line)

    def test_the_written_tracks_are_those_timed_or_re_timed(self) -> None:
        results = [
            TrackResult(A, Outcome.TIMED),
            TrackResult(B, Outcome.LOW_CONFIDENCE),
            TrackResult(B, Outcome.RETIMED),
        ]
        self.assertEqual(ss.written_tracks(results), (A, B))

    def test_the_fix_names_the_album_folder_or_the_one_track(self) -> None:
        fix = ss.first_line_fix(DISPATCHED)
        self.assertNotIn("<its folder>", fix)
        self.assertIn("lyricsmith album --offline --album <the album folder>", fix)
        self.assertIn("the library's Artist/Album folder", fix)
        self.assertIn("--track <the track>", fix)
        self.assertIn(" album.py --offline --album ", ss.first_line_fix(("album.py",)))

    def test_the_fix_and_the_by_hand_note_print_once_when_needed(self) -> None:
        results = [
            TrackResult(A, Outcome.TIMED, first_line="[00:01.000]"),
            TrackResult(B, Outcome.TIMED, first_line="[00:02.000]"),
            TrackResult(B, Outcome.LOW_CONFIDENCE),
        ]
        footer = ss.footer_lines(results, album=DISPATCHED)
        self.assertEqual(footer.count(ss.first_line_fix(DISPATCHED)), 1)
        self.assertEqual(footer.count(ss.BY_HAND), 1)

    def test_an_embed_not_done_prints_the_command_to_run(self) -> None:
        embeds = [
            EmbedResult(ROOT / "One", 0),
            EmbedResult(ROOT / "Two", 2),
            EmbedResult(ROOT / "Two", EmbedStop.TIMED_OUT),
            EmbedResult(ROOT, EmbedStop.LOOSE, track=ROOT / "01 L.mp3"),
        ]
        lines = ss.embed_lines(embeds, DISPATCHED)
        self.assertEqual(lines[0], f"embedded: {ROOT / 'One'}")
        self.assertIn("album exit 2", lines[1])
        self.assertIn("embed not done (timed out)", lines[2])
        self.assertIn("run: lyricsmith album --offline --album", lines[2])
        loose_command = (
            f"Artist/Album folder): {ROOT / '01 L.mp3'};"
            f" run: lyricsmith album --offline --album {SHOWN_ROOT}"
            f" --track {shlex.quote(str(ROOT / '01 L.mp3'))}"
        )
        self.assertIn(loose_command, lines[3])

    def test_each_command_quotes_absolute_paths(self) -> None:
        album = Path("My Album")
        command = ss.album_command(DISPATCHED, album, album / "01 A.mp3")
        here = Path.cwd()
        expected = (
            f"lyricsmith album --offline --album '{here / 'My Album'}'"
            f" --track '{here / 'My Album' / '01 A.mp3'}'"
        )
        self.assertEqual(command, expected)

    def test_the_album_command_names_the_album_tool_as_given(self) -> None:
        alone = ss.album_command(("album.py",), ROOT)
        self.assertEqual(alone, f"album.py --offline --album {SHOWN_ROOT}")
        spaced = ss.album_command(("my album.py",), ROOT)
        self.assertEqual(spaced, f"'my album.py' --offline --album {SHOWN_ROOT}")

    def test_the_exit_code_is_1_for_a_track_error_or_an_embed_not_done(self) -> None:
        low = [TrackResult(A, Outcome.LOW_CONFIDENCE), TrackResult(B, Outcome.NOT_SUNG)]
        self.assertEqual(ss.exit_code(low, []), EXIT_OK)
        failed = [TrackResult(A, Outcome.ERROR_ALIGN)]
        self.assertEqual(ss.exit_code(failed, []), EXIT_FAILED)
        timed = [TrackResult(A, Outcome.TIMED)]
        self.assertEqual(ss.exit_code(timed, [EmbedResult(ROOT, 1)]), EXIT_FAILED)
