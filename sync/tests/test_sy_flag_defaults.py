"""An on/off option left off: no default in --help, and False in the resolved options.

Each flag parses as None when omitted, so the shared formatter shows no default beside
it, as it shows none beside an option whose default is None; the flag's help already
says what giving it does. Resolving the options makes each a bool, so a run reads
False, never None.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sync  # noqa: E402 - import follows the sys.path bootstrap above

#: Every on/off option sync takes, by its parsed name.
FLAGS = ("embed", "overwrite", "dry_run")


class FlagDefaultTest(sy_cases.IsolatedCase):
    """Each flag is None when omitted, hidden in --help, and False once resolved."""

    def parser(self) -> argparse.ArgumentParser:
        """sync's parser, its model folders under the test's own folder."""
        return sync.build_parser(self.tmp / "models", self.tmp / "whisper")

    def test_the_help_shows_no_false_default(self) -> None:
        self.assertNotIn("(default: False)", self.parser().format_help())

    def test_each_omitted_flag_parses_as_none(self) -> None:
        args = self.parser().parse_args(["--path", "a.mp3"])
        for flag in FLAGS:
            with self.subTest(flag=flag):
                self.assertIsNone(getattr(args, flag))

    def test_each_given_flag_parses_as_true(self) -> None:
        for flag in FLAGS:
            option = "--" + flag.replace("_", "-")
            with self.subTest(flag=flag):
                args = self.parser().parse_args(["--path", "a.mp3", option])
                self.assertIs(getattr(args, flag), True)

    def test_the_options_hold_false_for_an_omitted_flag(self) -> None:
        args = self.parser().parse_args(["--path", "a.mp3"])
        options = sync.resolve_options(args)
        for flag in FLAGS:
            with self.subTest(flag=flag):
                self.assertIs(getattr(options, flag), False)
