"""The pinned model manifest and the per-user model folder."""

from __future__ import annotations

from pathlib import Path

import at_models
from tests._isolation import IsolatedCase

HOME = Path("example-home")


class ManifestTest(IsolatedCase):
    def test_manifest_pins_each_files_url_size_and_digest(self) -> None:
        self.assertEqual(
            at_models.MANIFEST,
            (
                at_models.ModelFile(
                    "ggml-large-v3-turbo.bin",
                    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
                    "ggml-large-v3-turbo.bin",
                    1624555275,
                    "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69",
                ),
                at_models.ModelFile(
                    "ggml-silero-v6.2.0.bin",
                    "https://huggingface.co/ggml-org/whisper-vad/resolve/main/"
                    "ggml-silero-v6.2.0.bin",
                    885098,
                    "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987",
                ),
            ),
        )

    def test_folder_per_platform(self) -> None:
        cases = [
            (
                "darwin",
                {},
                HOME / "Library" / "Application Support" / "lyricsmith" / "models",
            ),
            ("linux", {}, HOME / ".local" / "share" / "lyricsmith" / "models"),
            (
                "linux",
                {"XDG_DATA_HOME": ""},
                HOME / ".local" / "share" / "lyricsmith" / "models",
            ),
            (
                "linux",
                {"XDG_DATA_HOME": "/data"},
                Path("/data") / "lyricsmith" / "models",
            ),
            (
                "freebsd14",
                {},
                HOME / ".local" / "share" / "lyricsmith" / "models",
            ),
            (
                "win32",
                {"LOCALAPPDATA": "/local"},
                Path("/local") / "lyricsmith" / "models",
            ),
            ("win32", {}, HOME / "AppData" / "Local" / "lyricsmith" / "models"),
        ]
        for platform, env, expected in cases:
            with self.subTest(platform=platform, env=env):
                self.assertEqual(
                    at_models.default_model_dir(platform, env, HOME), expected
                )
