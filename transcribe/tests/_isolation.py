"""Host isolation for every transcribe suite but the live one.

IsolatedCase points HOME and the per-user data variables into a temporary folder,
replaces PATH with a folder holding only this test's fakes, and makes every network
entry point raise, so a test that reaches the host or the network fails loudly instead
of touching it. write_fake_whisper writes a stand-in whisper-cli there.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

#: Every network entry point a stray call could take.
NETWORK_ENTRY_POINTS = (
    "socket.socket.connect",
    "socket.create_connection",
    "urllib.request.urlopen",
)

#: The stand-in's behavior, steered per test through the environment:
#: FAKE_WHISPER_LOG    a file each call appends its argv to, one JSON list per line
#: FAKE_WHISPER_FAIL   a substring of the -f path that makes that call exit 3
#: FAKE_WHISPER_UNTIMED  a substring of the -f path whose .lrc gets no timed line
#: FAKE_WHISPER_VERSION  the --version text on stdout, as 1.9.5 prints it
#:                       (default "whisper.cpp version: 1.9.5")
FAKE_WHISPER_BODY = r"""
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_WHISPER_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
if args == ["--version"]:
    sys.stdout.write(os.environ.get("FAKE_WHISPER_VERSION", "whisper.cpp version: 1.9.5") + "\n")
    sys.exit(0)
audio = args[args.index("-f") + 1]
stem = args[args.index("-of") + 1]
if os.environ.get("FAKE_WHISPER_FAIL", "\0") in audio:
    sys.stderr.write("loading model\n\nerror: failed to read audio\n")
    sys.exit(3)
timed = os.environ.get("FAKE_WHISPER_UNTIMED", "\0") not in audio
with open(stem + ".lrc", "w", encoding="utf-8") as lrc:
    lrc.write("[by:whisper.cpp]\n\n")
    lrc.write("[00:00.26] An invented example line.\n" if timed else "An untimed line.\n")
with open(stem + ".txt", "w", encoding="utf-8") as txt:
    txt.write(" An invented example line.\n")
"""


def write_fake_whisper(bin_dir: Path) -> Path:
    """Write the stand-in whisper-cli into bin_dir and return its path."""
    path = bin_dir / "whisper-cli"
    path.write_text(
        f"#!{sys.executable}\n{FAKE_WHISPER_BODY}", encoding="utf-8", newline="\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class IsolatedCase(unittest.TestCase):
    """A test case that cannot reach the host's home, PATH or network."""

    def setUp(self) -> None:
        """Redirect the environment and arm the network tripwire."""
        super().setUp()
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        self.log = self.root / "whisper-calls.jsonl"
        environment = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_DATA_HOME": str(self.home / "xdg"),
            "LOCALAPPDATA": str(self.home / "local"),
            "PATH": str(self.bin),
            "FAKE_WHISPER_LOG": str(self.log),
        }
        patcher = mock.patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        for target in NETWORK_ENTRY_POINTS:
            tripwire = mock.patch(
                target,
                side_effect=AssertionError(f"a test reached the network: {target}"),
            )
            tripwire.start()
            self.addCleanup(tripwire.stop)
