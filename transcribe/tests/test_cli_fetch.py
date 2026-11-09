"""The fetch-models mode through main(), with an injected opener and manifest."""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import sys
from dataclasses import replace

import transcribe
import at_models
from at_types import EXIT_FILE_FAILED, EXIT_OK
from tests._isolation import IsolatedCase
from tests.test_at_models_fetch import FakeOpener

DATA = b"an invented model file\n" * 1000
TINY = at_models.ModelFile(
    "tiny.bin",
    "https://example.invalid/tiny.bin",
    len(DATA),
    hashlib.sha256(DATA).hexdigest(),
)


#: The pinned names, each an invented file, as the real manifest orders them.
PINNED = tuple(
    replace(TINY, name=name, url=f"https://example.invalid/{name}")
    for name in (
        at_models.WHISPER_MODEL_NAME,
        at_models.VAD_MODEL_NAME,
        at_models.LARGE_MODEL_NAME,
    )
)


class FetchModeTest(IsolatedCase):
    def run_main(
        self,
        opener: FakeOpener,
        *argv: str,
        manifest: tuple[at_models.ModelFile, ...] = (TINY,),
    ) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = transcribe.main(
                [
                    "--mode",
                    "fetch-models",
                    "--model-dir",
                    str(self.root / "models"),
                    *argv,
                ],
                opener=opener,
                manifest=manifest,
            )
        return code, out.getvalue()

    def test_large_v3_is_fetched_only_when_named(self) -> None:
        turbo, vad, large = (model.name for model in PINNED)
        for argv, expected in (
            ((), [turbo, vad]),
            (("--whisper-model", large), [vad, large]),
        ):
            with self.subTest(argv=argv):
                code, out = self.run_main(FakeOpener(data=DATA), *argv, manifest=PINNED)
                self.assertEqual(code, EXIT_OK)
                self.assertEqual(
                    [line.split()[1] for line in out.splitlines()], expected
                )

    def test_download_then_present(self) -> None:
        self.assertEqual(
            self.run_main(FakeOpener(data=DATA)), (EXIT_OK, "downloaded  tiny.bin\n")
        )
        self.assertEqual(
            self.run_main(FakeOpener(data=DATA)), (EXIT_OK, "present     tiny.bin\n")
        )

    def test_a_bad_digest_is_exit_1(self) -> None:
        code, out = self.run_main(FakeOpener(data=b"wrong"))
        self.assertEqual(code, EXIT_FILE_FAILED)
        self.assertIn("failed      tiny.bin", out)

    def test_the_default_folder_is_the_per_user_one(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = transcribe.main(
                ["--mode", "fetch-models"],
                opener=FakeOpener(data=DATA),
                manifest=(TINY,),
            )
        self.assertEqual(code, EXIT_OK)
        expected = at_models.default_model_dir(sys.platform, os.environ, self.home)
        self.assertTrue((expected / "tiny.bin").is_file())
