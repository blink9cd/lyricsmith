"""The command, black-box: main() with every outside call faked, offline and isolated."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import help_formatter  # noqa: E402 - reachable through the entry point's bootstrap
import sy_embed  # noqa: E402 - import follows the sys.path bootstrap above
import sy_lrc  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_model import Source  # noqa: E402 - follows the bootstrap above
from lyrics_sidecar import machine_label  # noqa: E402 - follows the bootstrap
import sy_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_modes  # noqa: E402 - import follows the sys.path bootstrap above
import sync  # noqa: E402 - import follows the sys.path bootstrap above
from sy_cases import (  # noqa: E402 - follows the bootstrap above
    DATA,
    fake_child,
    fake_opener,
    invented_models,
    reply_for,
)
from sweep_child import ChildResult  # noqa: E402 - follows the bootstrap above
from sy_types import (  # noqa: E402 - follows the bootstrap
    DEFAULT_EXTENSIONS,
    DEMUXERS,
    Action,
    Label,
    Options,
    Outcome,
    Planned,
    SetupError,
    TrackResult,
)

fixtures = sy_cases.fixtures
#: Three invented models standing in for the pinned ones.
TINY = invented_models("one.bin", "two.bin", "three.bin")
LINES = ("la la", "lo lo")
VERSION = "9.9.9"
#: The name the dispatcher gives the tool, which most cases run it under.
DISPATCHED = "lyricsmith sync"


class RecordingAlbum:
    """A stand-in album runner: records each argv and exits *code*."""

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], timeout: float) -> ChildResult:
        self.calls.append(list(argv))
        return ChildResult(self.code)


class CliTest(sy_cases.IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(mock.patch.object(sy_models, "MANIFEST", TINY))
        self.models = self.tmp / "models"
        self.models.mkdir()
        for model in TINY:
            (self.models / model.name).write_bytes(DATA)
        self.album = self.tmp / "Album"
        track = fixtures.TrackSpec("01 A.mp3", seconds=1.0, txt="\n".join(LINES))
        (self.audio,) = fixtures.build_album(self.album, (track,))
        self.embed = RecordingAlbum()

    def seams(self) -> sy_modes.Seams:
        """Every seam faked: a confident child, the album recorder, a full install."""
        return sy_modes.Seams(
            run=fake_child(reply_for(LINES)),
            embed=self.embed,
            album_entry=self.tmp / "album.py",
            find_spec=lambda name: object(),
            which=lambda name: f"/usr/bin/{name}",
            version=lambda name: VERSION,
        )

    def main(
        self,
        *argv: str,
        seams: sy_modes.Seams | None = None,
        prog: str | None = DISPATCHED,
    ) -> tuple[int, str, str]:
        """main() on *argv* plus the model folder, named *prog*; its code and output."""
        out, err = io.StringIO(), io.StringIO()
        full = [*argv, "--model-dir", str(self.models)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sync.main(
                full, seams=seams or self.seams(), opener=fake_opener(DATA), prog=prog
            )
        return code, out.getvalue(), err.getvalue()

    def listing(self) -> list[str]:
        """Every name under the album folder, sorted."""
        return sorted(str(p.relative_to(self.album)) for p in self.album.rglob("*"))

    def test_the_parser_uses_the_shared_formatter(self) -> None:
        self.assertIs(
            sync.build_parser(self.models).formatter_class,
            help_formatter.RawNoneHidingFormatter,
        )

    def test_a_dry_run_prints_the_plan_and_times_nothing(self) -> None:
        before = self.listing()
        code, out, _ = self.main("--path", str(self.album), "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "setup: ok")
        self.assertRegex(out, r"time +01 A\.mp3")
        self.assertEqual(self.listing(), before)

    def test_a_dry_run_with_a_refused_track_exits_1(self) -> None:
        self.audio.with_suffix(".txt").write_bytes(b"\xff\xfe\xfa")
        code, out, _ = self.main("--path", str(self.album), "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("ERROR text", out)

    def test_a_dry_run_prints_a_refusals_reason_on_its_own_line(self) -> None:
        self.audio.with_suffix(".txt").write_bytes(b"\xff\xfe\xfa")
        _, out, _ = self.main("--path", str(self.album), "--dry-run")
        self.assertNotIn("\\x0a", out)
        rows = out.splitlines()
        (row,) = (index for index, line in enumerate(rows) if "ERROR text" in line)
        self.assertTrue(rows[row + 1].startswith("    01 A.txt"), rows[row + 1])

    def test_a_flac_only_album_embeds_nothing_and_keeps_its_sidecar(self) -> None:
        flac_album = self.tmp / "Flac"
        track = fixtures.TrackSpec("01 F.flac", seconds=1.0, txt="\n".join(LINES))
        (flac,) = fixtures.build_album(flac_album, (track,))
        argv = ("--path", str(flac_album), "--extensions", "flac")
        code, out, _ = self.main(*argv, "--embed")
        self.assertEqual(code, 0)
        self.assertEqual(self.embed.calls, [])
        self.assertIn(f"stays a sidecar: {flac.with_suffix('.lrc')}", out)
        self.assertNotIn("embed not done", out)
        code, out, _ = self.main(*argv, "--overwrite")
        self.assertEqual(code, 0)
        self.assertNotIn("embed them:", out)

    def test_ctrl_c_while_timing_prints_the_footer_and_exits_130(self) -> None:
        track = fixtures.TrackSpec("02 B.mp3", seconds=1.0, txt="\n".join(LINES))
        (second,) = fixtures.build_album(self.album, (track,))
        timed = fake_child(reply_for(LINES))

        def child(
            argv: Sequence[str], *, timeout: float
        ) -> subprocess.CompletedProcess[str]:
            """Times the first track; Ctrl-C lands while the second is timed."""
            if self.audio.with_suffix(".lrc").exists():
                raise KeyboardInterrupt
            return timed(argv, timeout=timeout)

        seams = dataclasses.replace(self.seams(), run=child)
        code, out, _ = self.main("--path", str(self.album), "--embed", seams=seams)
        self.assertEqual(code, 130)
        self.assertEqual(self.embed.calls, [])
        self.assertRegex(out, r"timed +01 A\.mp3")
        self.assertIn("interrupted: timing stopped with 1 track(s) left", out)
        self.assertIn(
            f"embed them: lyricsmith album --offline --album {self.album}", out
        )
        self.assertFalse(second.with_suffix(".lrc").exists())

    def test_ctrl_c_while_a_written_tracks_row_prints_keeps_its_album_command(
        self,
    ) -> None:
        printed = sy_modes.say
        rows: list[str] = []

        def say(line: str) -> None:
            """Ctrl-C lands as the first track's timed row is printed, and only then."""
            if not rows:
                rows.append(line)
                raise KeyboardInterrupt
            printed(line)

        self.enterContext(mock.patch.object(sy_modes, "say", say))
        code, out, _ = self.main("--path", str(self.album), "--embed")
        self.assertEqual(code, 130)
        self.assertRegex(rows[0], r"timed +01 A\.mp3")
        self.assertEqual(self.embed.calls, [])
        self.assertIn("interrupted: timing stopped with 0 track(s) left", out)
        self.assertIn(
            f"embed them: lyricsmith album --offline --album {self.album}", out
        )

    def test_ctrl_c_once_a_tracks_lrc_is_in_place_keeps_its_album_command(
        self,
    ) -> None:
        write = sy_lrc.write_lrc

        def write_then_interrupt(audio: Path, text: str) -> None:
            """The .lrc is replaced into place; Ctrl-C lands before the write returns."""
            write(audio, text)
            raise KeyboardInterrupt

        self.enterContext(mock.patch.object(sy_lrc, "write_lrc", write_then_interrupt))
        code, out, _ = self.main("--path", str(self.album), "--embed")
        self.assertEqual(code, 130)
        self.assertTrue(self.audio.with_suffix(".lrc").exists())
        self.assertEqual(self.embed.calls, [])
        self.assertIn("interrupted: timing stopped with 0 track(s) left", out)
        self.assertIn("\ntimed 1\n", out)
        self.assertIn(
            f"embed them: lyricsmith album --offline --album {self.album}", out
        )

    def test_ctrl_c_once_a_retimed_lrc_is_in_place_keeps_its_album_command(
        self,
    ) -> None:
        self.assertEqual(self.main("--path", str(self.album))[0], 0)
        write = sy_lrc.write_lrc

        def write_then_interrupt(audio: Path, text: str) -> None:
            """The new .lrc is replaced into place; Ctrl-C lands before the write returns."""
            write(audio, text)
            raise KeyboardInterrupt

        self.enterContext(mock.patch.object(sy_lrc, "write_lrc", write_then_interrupt))
        argv = ("--path", str(self.album), "--embed", "--overwrite")
        code, out, _ = self.main(*argv)
        self.assertEqual(code, 130)
        self.assertEqual(self.embed.calls, [])
        self.assertIn("interrupted: timing stopped with 0 track(s) left", out)
        self.assertIn("\nre-timed 1\n", out)
        self.assertIn(
            f"embed them: lyricsmith album --offline --album {self.album}", out
        )

    def test_ctrl_c_beside_an_old_stamps_only_lrc_names_no_album_command(
        self,
    ) -> None:
        self.audio.with_suffix(".lrc").write_bytes(b"[00:01.00]\n[00:02.00]\n")

        def child(
            argv: Sequence[str], *, timeout: float
        ) -> subprocess.CompletedProcess[str]:
            """Ctrl-C lands while the track is timed, before any .lrc is written."""
            raise KeyboardInterrupt

        seams = dataclasses.replace(self.seams(), run=child)
        code, out, _ = self.main("--path", str(self.album), "--embed", seams=seams)
        self.assertEqual(code, 130)
        self.assertIn("interrupted: timing stopped with 1 track(s) left", out)
        self.assertIn("\nnothing to time\n", out)
        self.assertNotIn("embed them:", out)

    def test_an_lrc_entry_that_is_no_new_file_is_never_named(self) -> None:
        planned = Planned(self.audio, Action.TIME, lines=LINES)
        lrc = self.audio.with_suffix(".lrc")
        # A regular file outside the album: followed, the link would read as a new .lrc.
        outside = self.tmp / "outside.lrc"
        outside.write_bytes(b"[00:01.00]la la\n")
        cases = (
            ("a folder", lrc.mkdir, lrc.rmdir),
            ("a dangling link", lambda: lrc.symlink_to("gone.lrc"), lrc.unlink),
            ("a link to a file", lambda: lrc.symlink_to(outside), lrc.unlink),
        )
        for name, make, remove in cases:
            with self.subTest(entry=name):
                in_hand = sy_modes.InHand(0, planned, sy_modes.lrc_identity(lrc))
                make()
                try:
                    self.assertEqual(sy_modes._written_in_hand(in_hand, 0), ())
                finally:
                    remove()

    def test_only_a_new_lrc_for_the_track_in_hand_is_named(self) -> None:
        lrc = self.audio.with_suffix(".lrc")
        timed = Planned(self.audio, Action.TIME, lines=LINES)
        skipped = Planned(self.audio, Action.SKIP_NO_TEXT)
        fresh = sy_modes.InHand(0, timed, None)
        lrc.write_bytes(b"[00:01.00]la la\n")
        named = TrackResult(self.audio, Outcome.TIMED)
        self.assertEqual(sy_modes._written_in_hand(fresh, 0), (named,))
        self.assertEqual(sy_modes._written_in_hand(fresh, 1), ())
        self.assertEqual(sy_modes._written_in_hand(None, 0), ())
        unplanned = sy_modes.InHand(0, skipped, None)
        self.assertEqual(sy_modes._written_in_hand(unplanned, 0), ())
        same = sy_modes.InHand(0, timed, sy_modes.lrc_identity(lrc))
        self.assertEqual(sy_modes._written_in_hand(same, 0), ())

    def test_lrc_or_txt_extensions_are_refused(self) -> None:
        for extensions in ("lrc", "mp3,.TXT"):
            with self.subTest(extensions=extensions):
                argv = ("--path", str(self.album), "--extensions", extensions)
                code, _, err = self.main(*argv)
                self.assertEqual(code, 2)
                self.assertIn("not audio", err)

    def test_an_extension_with_no_known_demuxer_is_refused(self) -> None:
        for given, named in (
            ("mp3,xyz", "xyz: no audio demuxer is allowed for it;"),
            ("qqq,mp3,xyz", "qqq, xyz: no audio demuxer is allowed for them;"),
        ):
            with self.subTest(extensions=given):
                argv = ("--path", str(self.album), "--extensions", given)
                code, _, err = self.main(*argv)
                self.assertEqual(code, 2)
                self.assertIn(named, err)
                self.assertIn(", ".join(sorted(DEMUXERS)), err)

    def test_every_extension_with_a_demuxer_is_accepted(self) -> None:
        argv = ("--path", str(self.album), "--extensions", ",".join(DEMUXERS))
        code, out, _ = self.main(*argv, "--dry-run")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"time +01 A\.mp3")

    def test_the_parser_defaults_to_the_shared_extensions(self) -> None:
        default = sync.build_parser(self.models).get_default("extensions")
        self.assertEqual(default, DEFAULT_EXTENSIONS)

    def test_a_dry_run_with_a_setup_problem_exits_2(self) -> None:
        broken = dataclasses.replace(self.seams(), which=lambda name: None)
        code, out, _ = self.main("--path", str(self.album), "--dry-run", seams=broken)
        self.assertEqual(code, 2)
        self.assertIn("setup: ffmpeg is not on PATH", out)

    def test_a_run_times_the_track_and_prints_the_album_command(self) -> None:
        code, out, _ = self.main("--path", str(self.album))
        self.assertEqual(code, 0)
        text = self.audio.with_suffix(".lrc").read_text(encoding="utf-8")
        tool = sync.lyricsmith_project.project_version()
        self.assertEqual(
            text.splitlines()[0], sy_lrc.re_line(Label(tool, VERSION, VERSION))
        )
        self.assertIs(machine_label(text), Source.MACHINE_TIMED)
        self.assertRegex(out, r"timed +01 A\.mp3")
        self.assertIn(
            f"embed them: lyricsmith album --offline --album {self.album}", out
        )

    def test_run_alone_its_commands_name_the_entry_files(self) -> None:
        _, out, _ = self.main("--path", str(self.album), prog=None)
        self.assertIn(f"embed them: album.py --offline --album {self.album}", out)
        (self.models / "two.bin").write_bytes(b"x")
        _, _, err = self.main("--path", str(self.album), prog=None)
        folder = shlex.quote(str(self.models))
        fetch = f"fetch them: sync.py --mode fetch-models --model-dir {folder}"
        self.assertIn(fetch, err)

    def test_dispatched_its_commands_name_the_dispatcher(self) -> None:
        (self.models / "two.bin").write_bytes(b"x")
        _, _, err = self.main("--path", str(self.album))
        self.assertIn("fetch them: lyricsmith sync --mode fetch-models", err)

    def test_embed_runs_album_offline_on_each_written_folder(self) -> None:
        code, out, _ = self.main("--path", str(self.album), "--embed")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.embed.calls), 1)
        self.assertIn("--offline", self.embed.calls[0])
        self.assertIn(f"embedded: {self.album}", out)
        self.assertNotIn("embed them:", out)

    def test_embed_in_a_library_runs_album_on_each_artist_album_folder(self) -> None:
        library = self.tmp / "Library"
        album = library / "Artist" / "Album"
        tracks = (
            fixtures.TrackSpec(f"CD{disc}/0{disc} A.mp3", seconds=1.0, txt=LINES[0])
            for disc in (1, 2)
        )
        fixtures.build_album(album, tuple(tracks))
        loose = fixtures.TrackSpec("01 L.mp3", seconds=1.0, txt=LINES[0])
        fixtures.build_album(library / "Artist", (loose,))
        seams = dataclasses.replace(self.seams(), run=fake_child(reply_for(LINES[:1])))
        code, out, _ = self.main("--path", str(library), "--embed", seams=seams)
        self.assertEqual(code, 1)
        self.assertEqual([call[3] for call in self.embed.calls], [str(album)])
        self.assertIn(f"embedded: {album}", out)
        self.assertIn("embed not done (in a library but not in an Artist", out)
        loose_track = library / "Artist" / "01 L.mp3"
        track = shlex.quote(str(loose_track))
        self.assertIn(f"--album {loose_track.parent} --track {track}", out)

    def test_a_refused_embed_exits_1(self) -> None:
        self.embed.code = 2
        code, out, _ = self.main("--path", str(self.album), "--embed")
        self.assertEqual(code, 1)
        self.assertIn("embed not done (album exit 2)", out)

    def test_each_setup_problem_exits_2_and_touches_no_track(self) -> None:
        missing = dataclasses.replace(self.seams(), find_spec=lambda name: None)
        no_ffmpeg = dataclasses.replace(self.seams(), which=lambda name: None)
        cases = (
            (missing, ("pip install -r", "requirements-sync.txt")),
            (no_ffmpeg, ("ffmpeg is not on PATH",)),
        )
        before = self.listing()
        for seams, expected in cases:
            with self.subTest(expected=expected):
                code, _, err = self.main("--path", str(self.album), seams=seams)
                self.assertEqual(code, 2)
                for words in expected:
                    self.assertIn(words, err)
        self.assertEqual(self.listing(), before)

    def test_a_bad_model_or_a_missing_path_exits_2(self) -> None:
        (self.models / "two.bin").write_bytes(b"x")
        before = self.listing()
        code, _, err = self.main("--path", str(self.album))
        self.assertEqual(code, 2)
        self.assertIn("model two.bin does not match", err)
        self.assertIn("--mode fetch-models", err)
        self.assertEqual(self.listing(), before)
        code, _, err = self.main("--path", str(self.tmp / "gone"))
        self.assertEqual(code, 2)
        self.assertIn("--path does not exist", err)

    def test_fetch_models_writes_each_file_and_a_short_read_exits_1(self) -> None:
        target = self.tmp / "fetched"
        out, err = io.StringIO(), io.StringIO()
        argv = ["--mode", "fetch-models", "--model-dir", str(target)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(sync.main(argv, opener=fake_opener(DATA)), 0)
            for model in TINY:
                (target / model.name).unlink()
            self.assertEqual(sync.main(argv, opener=fake_opener(DATA[:10])), 1)
        self.assertIn("downloaded", out.getvalue())

    def test_path_is_required_and_empty_extensions_are_refused(self) -> None:
        code, _, err = self.main()
        self.assertEqual(code, 2)
        self.assertIn("--path is required", err)
        code, _, err = self.main("--path", str(self.album), "--extensions", " , ")
        self.assertEqual(code, 2)
        self.assertIn("--extensions", err)

    def test_run_mode_refuses_a_missing_path_itself(self) -> None:
        options = Options(
            "run", None, frozenset({"mp3"}), False, False, False, self.models
        )
        with self.assertRaises(SetupError):
            sy_modes.run_mode(
                options, self.seams(), tool_version=VERSION, prog=("sync.py",)
            )

    def test_help_shows_each_default_and_the_epilog(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            sync.main(["--help"], prog="lyricsmith sync")
        text = out.getvalue()
        self.assertIn("(default: mp3)", text)
        default_dir = sy_models.default_model_dir(sys.platform, os.environ, self.home)
        # argparse wraps a long default mid-path, so compare with whitespace removed.
        unwrapped = "".join(text.split())
        self.assertIn("".join(f"(default: {default_dir})".split()), unwrapped)
        for heading in ("Prerequisites:", "Output:", "Exit codes:"):
            self.assertIn(heading, text)

    def test_a_second_track_claiming_the_same_lrc_is_refused(self) -> None:
        (self.album / "01 A.flac").write_bytes(b"invented")
        code, out, _ = self.main(
            "--path", str(self.album), "--extensions", "mp3,flac", "--dry-run"
        )
        self.assertEqual(code, 1)
        self.assertRegex(out, r"time +01 A\.flac")
        self.assertIn("another track already claims 01 A.lrc", out)

    @unittest.skipIf(sy_cases.IS_ROOT, "root reads a mode-000 folder")
    def test_an_unreadable_folder_is_a_failed_row(self) -> None:
        locked = self.album / "Locked"
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        code, out, _ = self.main("--path", str(self.album))
        self.assertEqual(code, 1)
        self.assertRegex(out, r"ERROR text +Locked")
        self.assertIn("could not read the folder", out)
        self.assertRegex(out, r"timed +01 A\.mp3")

    def test_a_dry_run_on_a_missing_path_plans_nothing(self) -> None:
        code, out, _ = self.main("--path", str(self.tmp / "gone"), "--dry-run")
        self.assertEqual(code, 2)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIn("setup: --path does not exist", out)

    def test_the_real_seams_are_the_real_calls(self) -> None:
        seams = sy_modes.real_seams(sync.ALBUM_ENTRY)
        expected = (sync.ALBUM_ENTRY, shutil.which, sy_embed.run_album)
        self.assertEqual((seams.album_entry, seams.which, seams.embed), expected)

    def test_an_unnamed_walk_error_names_the_root(self) -> None:
        row = sy_modes._unreadable_folder(OSError("refused"), self.album)
        expected = (self.album, "could not read the folder: refused")
        self.assertEqual((row.audio, row.detail), expected)
