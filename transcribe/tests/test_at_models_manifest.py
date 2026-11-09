"""The pinned model manifest and the per-user model folder."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

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
                # Full large-v3, from the turbo's own repository: its size and SHA-256
                # are that repository's published LFS pointer for the file.
                at_models.ModelFile(
                    "ggml-large-v3.bin",
                    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
                    "ggml-large-v3.bin",
                    3095033483,
                    "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2",
                ),
            ),
        )

    def test_the_whisper_models_are_the_pinned_two_the_turbo_first(self) -> None:
        self.assertEqual(
            at_models.WHISPER_MODEL_NAMES,
            ("ggml-large-v3-turbo.bin", "ggml-large-v3.bin"),
        )
        pinned = {model.name for model in at_models.MANIFEST}
        self.assertLessEqual(set(at_models.WHISPER_MODEL_NAMES), pinned)

    def test_a_run_reads_its_one_whisper_model_and_the_voice_model(self) -> None:
        for name in at_models.WHISPER_MODEL_NAMES:
            with self.subTest(name=name):
                self.assertCountEqual(
                    [model.name for model in at_models.run_models(name)],
                    [name, at_models.VAD_MODEL_NAME],
                )

    def test_a_stand_in_manifest_is_read_whole_when_it_names_no_whisper_model(
        self,
    ) -> None:
        tiny = (
            at_models.ModelFile("a.bin", "https://example.invalid/a", 1, "0" * 64),
            at_models.ModelFile("b.bin", "https://example.invalid/b", 1, "0" * 64),
        )
        with mock.patch.object(at_models, "MANIFEST", tiny):
            self.assertEqual(at_models.run_models(at_models.LARGE_MODEL_NAME), tiny)

    def test_a_name_that_is_no_pinned_whisper_model_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a pinned whisper model"):
            at_models.run_models("ggml-tiny.bin")

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
