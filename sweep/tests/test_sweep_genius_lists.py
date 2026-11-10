"""The Genius check's three lists: their rows, made safe, and only Genius URLs in them.

Pure formatting, plus the CLI's own refusals around the mode. Each row is built from a
hit in the keyed search's shape (`genius_search_fakes`). Every title and artist is
invented.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - puts album and the libraries on sys.path

sys.path.append(str(_HERE.parents[1] / "album" / "tests"))

import genius_search_fakes as gsf  # noqa: E402 - import follows the sys.path insert above
import lyrics_genius_search as lgs  # noqa: E402 - import follows the sys.path insert above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path insert above
import sweep_genius_check as gc  # noqa: E402 - import follows the sys.path insert above
import sweep_genius_judge as gj  # noqa: E402 - import follows the sys.path insert above
import sweep_genius_lists as gl  # noqa: E402 - import follows the sys.path insert above

_TRACK = "/library/Invented Artist/Lanterns/01.mp3"


def _hit(**fields: object) -> lgs.SearchHit:
    """One hit read from a song record with *fields* over an invented one's."""
    record = gsf.song_result("Keeper", "Invented Artist")
    record.update(fields)
    reply = {"hits": [{"type": "song", "result": record}]}
    (hit,) = lgs.parse_search_reply(reply).hits
    return hit


def _has(hit: lgs.SearchHit, path: str = _TRACK) -> gl.CheckedTrack:
    """*path* found on Genius as *hit*."""
    return gl.CheckedTrack(path, gj.Judged(gj.Finding.HAS_IT, None, hit))


class RowsTest(sf.IsolatedCase):
    """Each list's rows, as written."""

    def test_has_it_names_the_hits_credits(self) -> None:
        """A joint credit is written as its artists joined."""
        hit = _hit(primary_artists=[{"name": "Invented Artist"}, {"name": "Guest"}])
        text = gl.format_has_it([_has(hit)]).decode()
        self.assertEqual(
            text.splitlines()[1],
            f"{_TRACK}\t{gsf.SONG_URL}\tKeeper\tInvented Artist & Guest",
        )

    def test_fields_are_made_tab_and_line_safe(self) -> None:
        """A tab or line break in a Genius name becomes a space."""
        hit = _hit(title="Keep\ter\nCounts", primary_artist={"name": "In\rvented"})
        text = gl.format_has_it([_has(hit, "/a b.mp3")]).decode()
        self.assertEqual(
            text.splitlines()[1], f"/a b.mp3\t{gsf.SONG_URL}\tKeep er Counts\tIn vented"
        )

    def test_a_path_a_tsv_cannot_hold_is_listed_unsure_not_had(self) -> None:
        """A track whose path a has-it row would change is said in unsure.tsv instead.

        The walker would read the changed path back and pass the row over as
        unreadable, silently.
        """
        tracks = [_has(_hit(), "/a\tb.mp3"), _has(_hit(), "/a\rb.mp3")]
        self.assertEqual(len(gl.format_has_it(tracks).splitlines()), 1)
        rows = gl.format_unsure(tracks).decode().splitlines()[1:]
        self.assertEqual(
            rows,
            [f"/a b.mp3\t{gl.UNLISTABLE_PATH}\t{gsf.SONG_URL}"] * 2,
        )

    def test_the_summary_counts_an_unlistable_track_as_its_list_does(self) -> None:
        """A had track listed unsure for its path is counted unsure, not had."""
        tracks = [_has(_hit(), "/a\tb.mp3"), _has(_hit(), "/x/1.mp3")]
        summary = gc.format_summary(tracks, ())
        has_it = len(gl.format_has_it(tracks).splitlines()) - 1
        unsure = len(gl.format_unsure(tracks).splitlines()) - 1
        self.assertEqual((has_it, unsure), (1, 1))
        for label, count in (("has it", has_it), ("unsure", unsure)):
            with self.subTest(label):
                self.assertIn(f"  {label}: {count}\n", summary)
        self.assertIn(f"    {gc.OUTSIDE_LIBRARY}: 0\n", summary)

    def test_a_has_it_finding_with_no_hit_raises(self) -> None:
        """The check never makes one; a has-it row must not be written without it."""
        lone = gl.CheckedTrack(_TRACK, gj.Judged(gj.Finding.HAS_IT, None, None))
        with self.assertRaisesRegex(ValueError, "names no hit"):
            gl.format_has_it([lone])

    def test_a_long_genius_name_is_cut(self) -> None:
        """A Genius title past the cap is written cut to it."""
        hit = _hit(title="k" * (gl.MAX_NAME_CHARS + 50))
        row = gl.format_has_it([_has(hit)]).decode().splitlines()[1]
        self.assertEqual(row.split("\t")[2], "k" * gl.MAX_NAME_CHARS)

    def test_a_foreign_url_is_never_written(self) -> None:
        """A hit whose URL is on no Genius host is written with an empty URL."""
        hit = _hit(url=gsf.FOREIGN_URL)
        unsure = gl.CheckedTrack(_TRACK, gj.Judged(gj.Finding.UNSURE, "near miss", hit))
        written = gl.format_has_it([_has(hit)]) + gl.format_unsure([unsure])
        self.assertNotIn(b"elsewhere", written)
        self.assertEqual(
            gl.format_unsure([unsure]).decode().splitlines()[1],
            f"{_TRACK}\tnear miss\t",
        )

    def test_each_list_takes_only_its_own_finding(self) -> None:
        """A track is written only to the list of its finding."""
        lacks = gl.CheckedTrack(_TRACK, gj.Judged(gj.Finding.NOT_ON_GENIUS, None, None))
        tracks = [_has(_hit(), "/x/1.mp3"), lacks]
        self.assertEqual(gl.format_not_on_genius(tracks), f"{_TRACK}\n".encode())
        self.assertEqual(len(gl.format_has_it(tracks).splitlines()), 2)
        self.assertEqual(len(gl.format_unsure(tracks).splitlines()), 1)


class CliTest(sf.IsolatedCase):
    """The mode's options, refused where they do not belong."""

    def _exit(self, *argv: str) -> tuple[int, str]:
        """The exit code and stderr of parsing *argv*."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            sweep.main(["--library", str(self.library), *argv])
        code = raised.exception.code
        assert isinstance(code, int)
        return code, err.getvalue()

    def test_tracks_from_and_out_are_required(self) -> None:
        """The check needs both its list and its folder."""
        out = str(self.tmp / "out")
        code, err = self._exit("--mode", "genius-check", "--out", out)
        self.assertEqual((code, "--tracks-from is required" in err), (2, True))
        code, err = self._exit("--mode", "genius-check", "--tracks-from", out)
        self.assertEqual((code, "--out is required" in err), (2, True))

    def test_tracks_from_is_refused_by_every_other_mode(self) -> None:
        """--tracks-from is the check's alone."""
        code, err = self._exit("--mode", "survey", "--tracks-from", str(self.tmp))
        self.assertEqual(code, 2)
        self.assertIn("--tracks-from cannot be used with --mode survey", err)

    def test_the_draws_options_are_refused_by_the_check(self) -> None:
        """--seed is the draw's, refused by the check."""
        code, err = self._exit(
            "--mode",
            "genius-check",
            "--tracks-from",
            str(self.tmp / "list.txt"),
            "--out",
            str(self.tmp / "out"),
            "--seed",
            "3",
        )
        self.assertEqual(code, 2)
        self.assertIn("--seed cannot be used with --mode genius-check", err)

    def test_the_help_names_the_mode(self) -> None:
        """--help lists the mode and its paragraph."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            sweep.main(["--help"])
        self.assertIn("genius-check", out.getvalue())
        self.assertIn("--tracks-from", out.getvalue())
