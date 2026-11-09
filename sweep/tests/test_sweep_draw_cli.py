"""``sweep --mode draw`` end to end: an invented library in, two files out, nothing else.

`main` runs over a small invented library holding every group and every left-out
reason, with its manifests and a ledger in the redirected state folder, its output
captured. It writes ``tracks.txt`` (the changed-list format) and ``strata.tsv`` into
``--out``, replaces both on a rerun, writes nothing in the library or the ledger, and
refuses every option it does not take. Every name and lyric here is invented.
"""

from __future__ import annotations

import contextlib
import io
import os
import stat
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import draw_fixtures as df  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_strata as ss  # noqa: E402 - import follows the sys.path bootstrap above

_GROUP = ss.StrataGroup


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


class DrawCase(sf.IsolatedCase):
    """An invented library of every group and reason, and a way to run the draw."""

    def setUp(self) -> None:
        """Build the library, its manifests and its ledger."""
        super().setUp()
        self.out = self.tmp / "drawn"
        artist = self.library / "Invented Artist"
        self._wrong_text_album(artist / "Wrong")
        self._fixed_album(artist / "Fixed")
        self._provenance_album(artist / "Kept")
        self._spoken_album(artist / "Spoken")

    def _wrong_text_album(self, album: Path) -> None:
        df.provider_timed(album, "01.mp3")
        df.untimed(album, "02.mp3")
        df.machine_timed(album, "03.mp3")
        df.write_manifest(
            album, df.OLD_RUN, (df.entry("02.mp3", df.wrong_text_removed()),)
        )

    def _fixed_album(self, album: Path) -> None:
        df.provider_timed(album, "01.mp3")
        df.write_manifest(
            album, df.OLD_RUN, (df.entry("01.mp3", df.synced(lm.Source.NETEASE)),)
        )
        df.write_ledger(
            self.library,
            df.ledger_entry("Invented Artist/Fixed", sl.SweepPass.FIX_TEXT),
        )

    def _provenance_album(self, album: Path) -> None:
        sources = {
            "01.mp3": lm.Source.NETEASE,
            "02.mp3": lm.Source.YOUTUBE_MUSIC,
            "03.mp3": lm.Source.LRCLIB,
            "04.mp3": lm.Source.SIDECAR,
            "05.mp3": lm.Source.FRAMES,
        }
        for name in (*sources, "06.mp3"):
            df.provider_timed(album, name)
        df.frames_only(album, "07.mp3")
        entries = [df.entry(n, df.synced(s)) for n, s in sources.items()]
        df.write_manifest(album, df.OLD_RUN, entries)

    def _spoken_album(self, album: Path) -> None:
        df.untimed(album, "01.mp3")
        df.provider_timed(album, "02.mp3")
        df.untimed(album, "03.mp3")
        df.write_manifest(
            album,
            df.OLD_RUN,
            (
                df.entry("01.mp3", df.withheld_removal()),
                df.entry("02.mp3", df.withheld_removal()),
                df.entry("03.mp3", df.withheld_removal(), undone=True),
            ),
        )

    def main(self, *args: str) -> Result:
        """Run `main` with *args* and ``--library``; return the code and the output."""
        argv = [*args, "--library", str(self.library)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = sweep.main(argv)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def draw(self, *args: str) -> Result:
        """Run the draw into this test's out folder, seed 11 unless *args* give one."""
        seed = () if "--seed" in args else ("--seed", "11")
        return self.main("--mode", "draw", "--out", str(self.out), *seed, *args)

    def strata(self) -> dict[str, ss.StrataGroup]:
        """The written strata file's groups, by library-relative path."""
        rows = ss.read_strata(self.out / "strata.tsv").rows
        return {
            Path(row.path).relative_to(self.library).as_posix(): row.group
            for row in rows
        }


class DrawModeTest(DrawCase):
    """The draw's files, its summary, and that it writes nothing else."""

    def test_every_group_is_drawn_and_every_reason_left_out(self) -> None:
        result = self.draw()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(
            self.strata(),
            {
                "Invented Artist/Fixed/01.mp3": _GROUP.WRONG_TEXT_ALBUMS,
                "Invented Artist/Kept/01.mp3": _GROUP.NETEASE,
                "Invented Artist/Kept/02.mp3": _GROUP.YOUTUBE_MUSIC,
                "Invented Artist/Kept/03.mp3": _GROUP.LRCLIB,
                "Invented Artist/Kept/04.mp3": _GROUP.OLDER_SIDECARS,
                "Invented Artist/Spoken/02.mp3": _GROUP.WRONG_TEXT_ALBUMS,
                "Invented Artist/Wrong/01.mp3": _GROUP.WRONG_TEXT_ALBUMS,
            },
        )
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "seed: 11")
        for reason in (
            "machine-timed",
            "frames-only",
            "no manifest record",
            "another source",
        ):
            with self.subTest(reason=reason):
                self.assertIn(f"left out, {reason}: under 10", lines)

    def test_the_track_list_holds_the_strata_paths_in_the_list_format(self) -> None:
        self.draw()
        listed = lyrics_changed.parse_changed_list(
            (self.out / "tracks.txt").read_bytes()
        )
        rows = ss.read_strata(self.out / "strata.tsv").rows
        self.assertEqual(listed, tuple(row.path for row in rows))
        self.assertEqual(ss.read_strata(self.out / "strata.tsv").seed, 11)
        wrote = self.draw().stdout.splitlines()
        self.assertIn(f"wrote: {self.out / 'tracks.txt'}", wrote)
        self.assertIn(f"wrote: {self.out / 'strata.tsv'}", wrote)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not meaningful on NTFS")
    def test_both_drawn_files_are_readable_by_their_owner_alone(self) -> None:
        self.assertEqual(self.draw().code, 0)
        for name in ("tracks.txt", "strata.tsv"):
            with self.subTest(name=name):
                mode = stat.S_IMODE((self.out / name).stat().st_mode)
                self.assertEqual(mode, 0o600)

    def test_the_same_seed_writes_the_same_files_and_a_rerun_replaces_them(
        self,
    ) -> None:
        self.draw()
        first = df.tree_bytes(self.out)
        self.draw()
        self.assertEqual(df.tree_bytes(self.out), first)
        self.draw("--seed", "12")
        self.assertEqual(ss.read_strata(self.out / "strata.tsv").seed, 12)
        self.assertEqual(sorted(df.tree_bytes(self.out)), ["strata.tsv", "tracks.txt"])

    def test_the_library_and_the_ledger_are_left_as_they_were(self) -> None:
        library = df.tree_bytes(self.library)
        ledger = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        before = ledger.read_bytes()
        self.assertEqual(self.draw().code, 0)
        self.assertEqual(df.tree_bytes(self.library), library)
        self.assertEqual(ledger.read_bytes(), before)

    def test_withheld_lists_the_marked_tracks_still_untimed(self) -> None:
        result = self.draw("--withheld")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(
            self.strata(), {"Invented Artist/Spoken/01.mp3": _GROUP.WITHHELD}
        )
        self.assertIn("left out, no longer untimed: under 10", result.stdout)


class DrawFailureTest(DrawCase):
    """What stops the draw with exit 2 before or instead of writing."""

    def test_an_out_folder_inside_the_library_is_refused(self) -> None:
        self.out = self.library / "drawn"
        result = self.draw()
        self.assertEqual(result.code, 2)
        self.assertIn("inside the library", result.stderr)
        self.assertFalse(self.out.exists())

    def test_an_out_that_cannot_be_written_is_exit_2(self) -> None:
        self.out.write_bytes(b"a file, not a folder\n")
        result = self.draw()
        self.assertEqual(result.code, 2)
        self.assertIn("cannot be written", result.stderr)

    def test_a_ledger_that_cannot_be_read_is_exit_2(self) -> None:
        with mock.patch.object(sl, "read_ledger", side_effect=OSError("refused")):
            result = self.draw()
        self.assertEqual(result.code, 2)
        self.assertIn("the ledger cannot be read", result.stderr)
        self.assertFalse(self.out.exists())

    def test_unreadable_ledger_lines_and_linked_albums_are_said(self) -> None:
        ledger = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        sf.append_line(ledger, "not json")
        (self.library / "Linked Artist").symlink_to(self.library / "Invented Artist")
        result = self.draw()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("skipped", result.stderr)
        self.assertIn("ledger line", result.stderr)


class DrawOptionsTest(DrawCase):
    """The draw takes --library, --seed, --out and --withheld; nothing else takes those."""

    def test_draw_needs_a_seed_and_an_out(self) -> None:
        for missing, argv in (
            ("--seed", ("--mode", "draw", "--out", str(self.out))),
            ("--out", ("--mode", "draw", "--seed", "1")),
        ):
            with self.subTest(missing=missing):
                result = self.main(*argv)
                self.assertEqual(result.code, 2)
                self.assertIn(f"{missing} is required with --mode draw", result.stderr)

    def test_every_other_option_is_refused_by_the_draw(self) -> None:
        for argv in (
            ("--lang", "fra"),
            ("--offline",),
            ("--limit", "2"),
            ("--pause-seconds", "1"),
            ("--changed-list", str(self.tmp / "c.txt")),
            ("--heard-dir", str(self.tmp)),
            ("--apply",),
            ("--resume", str(self.tmp / "r.tsv")),
            ("--no-genius",),
        ):
            with self.subTest(option=argv[0]):
                result = self.draw(*argv)
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"{argv[0]} cannot be used with --mode draw", result.stderr
                )

    def test_the_draw_options_are_refused_by_every_other_mode(self) -> None:
        for argv in (
            ("--seed", "1"),
            ("--out", str(self.out)),
            ("--withheld",),
        ):
            with self.subTest(option=argv[0]):
                result = self.main("--mode", "survey", *argv)
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"{argv[0]} cannot be used with --mode survey", result.stderr
                )

    def test_a_zero_seed_is_refused_by_another_mode(self) -> None:
        # Zero equals False, so a check against (None, False) would let it through.
        result = self.main("--mode", "run", "--seed", "0")
        self.assertEqual(result.code, 2)
        self.assertIn("--seed cannot be used with --mode run", result.stderr)

    def test_the_help_names_the_mode_and_its_options(self) -> None:
        result = self.main("--help")
        self.assertEqual(result.code, 0)
        for text in ("draw:", "--seed", "--out", "--withheld", "Draw "):
            with self.subTest(text=text):
                self.assertIn(text, result.stdout)
