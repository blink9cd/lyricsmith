"""What a walked name is, and where a run's paths point, black-box through main().

A walked FIFO, device or link named like a track is judged as the transcribe tool
judges it before any sidecar is read, so no child ever decodes one. A relative --path
is made absolute, so ffmpeg is never handed a name it reads as a protocol. A link at a
track's .lrc name is never replaced by a first-time write. A test that could block on
a FIFO runs under album's `fifo_fakes.within_deadline`.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_modes  # noqa: E402 - import follows the sys.path bootstrap above
import sync  # noqa: E402 - import follows the sys.path bootstrap above
from at_modes import NOT_REGULAR_DETAIL  # noqa: E402 - follows the bootstrap above
from fifo_fakes import make_pipe, within_deadline  # noqa: E402 - album's test helpers
from sweep_child import ChildResult  # noqa: E402 - follows the bootstrap above
from sy_cases import (  # noqa: E402 - follows the bootstrap above
    DATA,
    fake_child,
    fake_opener,
    invented_models,
    reply_for,
)

fixtures = sy_cases.fixtures
#: One invented model standing in for the pinned ones.
TINY = invented_models("one.bin")
LINES = ("la la", "lo lo")


class EntriesTest(sy_cases.IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(mock.patch.object(sy_models, "MANIFEST", TINY))
        self.models = self.tmp / "models"
        self.models.mkdir()
        (self.models / TINY[0].name).write_bytes(DATA)
        self.whisper = sy_cases.invented_whisper_models(self, self.tmp / "whisper")
        self.album = self.tmp / "Album"
        track = fixtures.TrackSpec("01 A.mp3", seconds=1.0, txt="\n".join(LINES))
        (self.audio,) = fixtures.build_album(self.album, (track,))
        self.children: list[list[str]] = []
        self.embeds: list[list[str]] = []

    def seams(self) -> sy_modes.Seams:
        """Every seam faked; each child run and album run is recorded."""
        child = fake_child(reply_for(LINES))

        def run(
            argv: Sequence[str], *, timeout: float
        ) -> subprocess.CompletedProcess[str]:
            self.children.append(list(argv))
            return child(argv, timeout=timeout)

        def embed(argv: Sequence[str], timeout: float) -> ChildResult:
            self.embeds.append(list(argv))
            return ChildResult(0)

        return sy_modes.Seams(
            run=run,
            embed=embed,
            album_entry=self.tmp / "album.py",
            find_spec=lambda name: object(),
            which=lambda name: f"/usr/bin/{name}",
            version=lambda name: "9.9.9",
        )

    def main(self, *argv: str) -> tuple[int, str]:
        """main() on *argv* plus the model folder; its code and standard output."""
        out = io.StringIO()
        full = [*argv, "--model-dir", str(self.models)]
        full += ["--whisper-model-dir", str(self.whisper)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = sync.main(
                full,
                seams=self.seams(),
                opener=fake_opener(DATA),
                prog="lyricsmith sync",
            )
        return code, out.getvalue()

    def outside_track(self) -> Path:
        """A real track with words, in a folder outside the album."""
        track = fixtures.TrackSpec("09 Z.mp3", seconds=1.0, txt="\n".join(LINES))
        (audio,) = fixtures.build_album(self.tmp / "Elsewhere", (track,))
        return audio

    def test_a_walked_fifo_is_refused_before_anything_reads_it(self) -> None:
        pipe = self.album / "02 B.mp3"
        make_pipe(pipe)
        pipe.with_suffix(".txt").write_text(
            "\n".join(LINES), encoding="utf-8", newline="\n"
        )
        code, out = within_deadline(
            self, pipe, lambda: self.main("--path", str(self.album), "--dry-run")
        )
        self.assertEqual(code, 1)
        self.assertRegex(out, r"ERROR audio +02 B\.mp3")
        self.assertIn(NOT_REGULAR_DETAIL, out)
        self.assertRegex(out, r"time +01 A\.mp3")

    def test_a_walked_fifo_never_reaches_a_child(self) -> None:
        pipe = self.album / "02 B.mp3"
        make_pipe(pipe)
        pipe.with_suffix(".txt").write_text(
            "\n".join(LINES), encoding="utf-8", newline="\n"
        )
        code, out = within_deadline(
            self, pipe, lambda: self.main("--path", str(self.album))
        )
        self.assertEqual(code, 1)
        self.assertRegex(out, r"ERROR audio +02 B\.mp3")
        self.assertEqual(len(self.children), 1)

    def test_a_walked_link_to_a_track_is_skipped(self) -> None:
        link = self.album / "02 B.mp3"
        link.symlink_to(self.outside_track())
        link.with_suffix(".txt").write_text(
            "\n".join(LINES), encoding="utf-8", newline="\n"
        )
        code, out = self.main("--path", str(self.album))
        self.assertEqual(code, 0)
        self.assertRegex(out, r"skip: linked +02 B\.mp3")
        self.assertFalse(link.with_suffix(".lrc").exists())
        self.assertEqual(len(self.children), 1)

    def test_a_linked_track_claims_no_lrc(self) -> None:
        link = self.album / "01 A.flac"
        link.symlink_to(self.outside_track())
        code, out = self.main(
            "--path", str(self.album), "--extensions", "flac,mp3", "--dry-run"
        )
        self.assertEqual(code, 0)
        self.assertRegex(out, r"skip: linked +01 A\.flac")
        self.assertRegex(out, r"time +01 A\.mp3")
        self.assertNotIn("already claims", out)

    def test_a_path_naming_a_link_is_followed(self) -> None:
        link = self.tmp / "named.mp3"
        link.symlink_to(self.audio)
        link.with_suffix(".txt").write_text(
            "\n".join(LINES), encoding="utf-8", newline="\n"
        )
        code, out = self.main("--path", str(link), "--dry-run")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"time +named\.mp3")

    def test_a_walked_name_that_cannot_be_read_is_a_failed_row(self) -> None:
        with mock.patch.object(
            sy_modes.at_discover, "classify", side_effect=OSError("refused")
        ):
            code, out = self.main("--path", str(self.album), "--dry-run")
        self.assertEqual(code, 1)
        self.assertRegex(out, r"ERROR audio +01 A\.mp3")
        self.assertIn("could not read it: refused", out)

    def test_the_path_and_the_model_folders_are_made_absolute(self) -> None:
        parser = sync.build_parser(self.models, self.whisper)
        args = ["--path", "http:x.mp3", "--model-dir", "pipe:0"]
        options = sync.resolve_options(
            parser.parse_args([*args, "--whisper-model-dir", "w"])
        )
        self.assertEqual(options.path, Path.cwd() / "http:x.mp3")
        self.assertEqual(options.model_dir, Path.cwd() / "pipe:0")
        self.assertEqual(options.whisper_model_dir, Path.cwd() / "w")

    def test_a_first_time_write_never_replaces_a_link_at_the_lrc_name(self) -> None:
        lrc = self.audio.with_suffix(".lrc")
        lrc.symlink_to("gone.lrc")
        code, out = self.main("--path", str(self.album))
        self.assertEqual(code, 1)
        self.assertRegex(out, r"ERROR text +01 A\.mp3")
        self.assertIn("01 A.lrc is a link", out)
        self.assertTrue(lrc.is_symlink())
        self.assertEqual(self.children, [])

    def deep_name(self) -> Path:
        """A track's name three folders below the album, deeper than any library's."""
        deep = self.album / "Disc" / "Side" / "09 Z.mp3"
        deep.parent.mkdir(parents=True)
        return deep

    def test_a_deep_walked_link_leaves_the_album_embedded_whole(self) -> None:
        self.deep_name().symlink_to(self.outside_track())
        code, out = self.main("--path", str(self.album), "--embed")
        self.assertEqual(code, 0)
        self.assertEqual([call[3] for call in self.embeds], [str(self.album)])
        self.assertIn(f"embedded: {self.album}", out)
        self.assertNotIn("embed not done", out)

    def test_a_deep_walked_fifo_leaves_the_album_embedded_whole(self) -> None:
        pipe = self.deep_name()
        make_pipe(pipe)
        code, out = within_deadline(
            self, pipe, lambda: self.main("--path", str(self.album), "--embed")
        )
        self.assertEqual(code, 1)
        self.assertRegex(out, r"ERROR audio +Disc/Side/09 Z\.mp3")
        self.assertEqual([call[3] for call in self.embeds], [str(self.album)])
        self.assertNotIn("embed not done", out)
