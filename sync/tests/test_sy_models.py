"""The pinned models: the manifest, the sync folder, and the check a run makes first."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_models  # noqa: E402 - import follows the sys.path bootstrap above

#: The pinned Hugging Face commit every demucs address names.
DEMUCS_COMMIT = "/resolve/cbc8a9b1a87023b7fd74e7b3412e6321c0eab003/"


class ModelsTest(sy_cases.IsolatedCase):
    def test_the_folder_is_lyricsmiths_model_folder_plus_sync(self) -> None:
        base = at_models.default_model_dir("darwin", {}, self.home)
        self.assertEqual(
            sy_models.default_model_dir("darwin", {}, self.home), base / "sync"
        )

    def test_every_pin_is_https_with_a_size_and_a_sha256(self) -> None:
        for pinned in sy_models.MANIFEST:
            self.assertTrue(pinned.url.startswith("https://"), pinned.name)
            self.assertEqual(len(pinned.sha256), 64)
            self.assertGreater(pinned.size, 0)

    def test_the_demucs_pins_name_a_commit_never_a_branch(self) -> None:
        for pinned in sy_models.MANIFEST[1:]:
            self.assertIn(DEMUCS_COMMIT, pinned.url)

    def test_a_missing_and_a_mismatched_model_are_each_named(self) -> None:
        data = b"weights"
        digest = hashlib.sha256(data).hexdigest()
        good = at_models.ModelFile("good.bin", "https://example.invalid/g", 7, digest)
        bad = at_models.ModelFile("bad.bin", "https://example.invalid/b", 3, "0" * 64)
        short = at_models.ModelFile("short.bin", "https://example.invalid/s", 9, digest)
        gone = at_models.ModelFile("gone.bin", "https://example.invalid/x", 1, "0" * 64)
        (self.tmp / "good.bin").write_bytes(data)
        (self.tmp / "bad.bin").write_bytes(b"abc")
        (self.tmp / "short.bin").write_bytes(data)
        problems = sy_models.model_problems(self.tmp, (good, bad, short, gone))
        self.assertEqual(
            problems,
            [
                "model bad.bin does not match its pinned digest",
                "model short.bin does not match its pinned digest",
                "missing model gone.bin",
            ],
        )
