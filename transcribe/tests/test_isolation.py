"""The isolation base class really isolates."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import unittest
import urllib.request
from pathlib import Path

from tests._isolation import IsolatedCase, write_fake_whisper


class IsolationTest(IsolatedCase):
    def test_home_and_path_point_into_the_scratch_folder(self) -> None:
        self.assertEqual(Path.home(), self.home)
        self.assertEqual(os.environ["PATH"], str(self.bin))

    def test_no_real_whisper_cli_is_reachable(self) -> None:
        self.assertIsNone(shutil.which("whisper-cli"))

    def test_network_entry_points_raise(self) -> None:
        with self.assertRaises(AssertionError):
            urllib.request.urlopen("https://example.invalid/", timeout=5)
        with self.assertRaises(AssertionError):
            socket.create_connection(("192.0.2.1", 443), timeout=5)
        with socket.socket() as raw, self.assertRaises(AssertionError):
            raw.connect(("192.0.2.1", 443))

    @unittest.skipIf(
        sys.platform == "win32", "the fake whisper-cli is a shebang script"
    )
    def test_fake_whisper_answers_version_and_logs(self) -> None:
        fake = write_fake_whisper(self.bin)
        self.assertEqual(shutil.which("whisper-cli"), str(fake))
        # reliability: ignore[RL005] the fake starts no child, so no grandchild holds a pipe
        done = subprocess.run(
            [str(fake), "--version"],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(done.returncode, 0)
        self.assertIn("whisper.cpp version: 1.9.5", done.stdout)
        self.assertEqual(
            json.loads(self.log.read_text(encoding="utf-8")), ["--version"]
        )
