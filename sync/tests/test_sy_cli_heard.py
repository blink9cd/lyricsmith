"""The sung check, black-box: its setup check, its request, and a text not sung."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_modes  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
import sync  # noqa: E402 - import follows the sys.path bootstrap above
from sy_cases import (  # noqa: E402 - follows the bootstrap above
    DATA,
    fake_child,
    fake_opener,
    not_sung_reply,
    reply_for,
)
from test_sy_cli import (  # noqa: E402 - the black-box suite's own stand-ins
    DISPATCHED,
    LINES,
    TINY,
    VERSION,
    RecordingAlbum,
)

fixtures = sy_cases.fixtures
#: Where the stand-in PATH lookup finds every program.
BIN = Path("/usr/bin")


class CliHeardTest(sy_cases.IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(mock.patch.object(sy_models, "MANIFEST", TINY))
        self.models = sy_cases.model_folder(self.tmp / "models", TINY)
        self.whisper = sy_cases.invented_whisper_models(self, self.tmp / "whisper")
        self.album = self.tmp / "Album"
        track = fixtures.TrackSpec("01 A.mp3", seconds=1.0, txt="\n".join(LINES))
        (self.audio,) = fixtures.build_album(self.album, (track,))
        self.requests: list[rq.Request] = []

    def seams(
        self, reply: str, found: Sequence[str] = ("ffmpeg", "whisper-cli")
    ) -> sy_modes.Seams:
        """Every seam faked: a child answering *reply*, the programs *found* on PATH."""
        child = fake_child(reply)

        def run(
            argv: Sequence[str], *, timeout: float
        ) -> subprocess.CompletedProcess[str]:
            text = Path(argv[argv.index("--request") + 1]).read_text(encoding="utf-8")
            self.requests.append(rq.load_request(text))
            return child(argv, timeout=timeout)

        return sy_modes.Seams(
            run=run,
            embed=RecordingAlbum(),
            album_entry=self.tmp / "album.py",
            find_spec=lambda name: object(),
            which=lambda name: str(BIN / name) if name in found else None,
            version=lambda name: VERSION,
        )

    def main(
        self,
        *argv: str,
        seams: sy_modes.Seams | None = None,
        prog: str | None = DISPATCHED,
    ) -> tuple[int, str, str]:
        """main() on *argv* and both model folders; its code and output."""
        out, err = io.StringIO(), io.StringIO()
        full = [*argv, "--model-dir", str(self.models)]
        full += ["--whisper-model-dir", str(self.whisper)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sync.main(
                full,
                seams=seams or self.seams(reply_for(LINES)),
                opener=fake_opener(DATA),
                prog=prog,
            )
        return code, out.getvalue(), err.getvalue()

    def test_the_child_hears_with_whisper_cli_from_path_and_its_models(self) -> None:
        code, _, _ = self.main("--path", str(self.album))
        self.assertEqual(code, 0)
        named = [(r.whisper_cli, r.whisper_model_dir) for r in self.requests]
        self.assertEqual(named, [(BIN / "whisper-cli", self.whisper)])

    def test_whisper_cli_off_path_is_a_setup_problem(self) -> None:
        seams = self.seams(reply_for(LINES), found=("ffmpeg",))
        code, _, err = self.main("--path", str(self.album), seams=seams)
        self.assertEqual((code, self.requests), (2, []))
        self.assertIn("whisper-cli is not on PATH: brew install whisper.cpp", err)
        code, out, _ = self.main("--path", str(self.album), "--dry-run", seams=seams)
        self.assertEqual(code, 2)
        self.assertIn("setup: whisper-cli is not on PATH", out)

    def test_a_relative_whisper_cli_on_path_is_made_absolute(self) -> None:
        seams = dataclasses.replace(
            self.seams(reply_for(LINES)), which=lambda name: f"bin/{name}"
        )
        self.main("--path", str(self.album), seams=seams)
        self.assertEqual(
            [r.whisper_cli for r in self.requests], [Path.cwd() / "bin" / "whisper-cli"]
        )

    def test_a_missing_whisper_model_names_transcribes_fetch(self) -> None:
        (self.whisper / "vad.bin").unlink()
        (self.whisper / "whisper.bin").write_bytes(b"x")
        folder = shlex.quote(str(self.whisper))
        for prog, words in (
            (DISPATCHED, "lyricsmith transcribe"),
            (None, "transcribe.py"),
        ):
            with self.subTest(prog=prog):
                code, _, err = self.main("--path", str(self.album), prog=prog)
                self.assertEqual((code, self.requests), (2, []))
                self.assertIn("missing model vad.bin", err)
                self.assertIn("model whisper.bin does not match its pinned digest", err)
                fetch = f"fetch them: {words} --mode fetch-models --model-dir {folder}"
                self.assertIn(fetch, err)

    def test_the_whisper_model_folder_defaults_to_transcribes(self) -> None:
        default = at_models.default_model_dir(sys.platform, os.environ, self.home)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            sync.main(["--help"], prog=DISPATCHED)
        unwrapped = "".join(out.getvalue().split())
        self.assertIn("--whisper-model-dir", out.getvalue())
        self.assertIn("".join(f"(default: {default})".split()), unwrapped)

    def test_a_text_not_sung_gets_no_lrc_and_exits_0(self) -> None:
        seams = self.seams(not_sung_reply())
        code, out, _ = self.main("--path", str(self.album), seams=seams)
        self.assertEqual(code, 0)
        self.assertFalse(self.audio.with_suffix(".lrc").exists())
        self.assertRegex(out, r"not sung: left untimed +01 A\.mp3")
        self.assertIn("0.04 of the 30 word pairs heard are the text's", out)
        self.assertIn("not sung: left untimed 1", out)
        self.assertIn(sy_modes.sy_summary.NOT_SUNG, out)
        self.assertNotIn("embed them:", out)
