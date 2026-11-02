"""An on/off option left off: no default in --help, and False in the resolved options.

Each flag parses as None when omitted, so the shared formatter shows no default beside
it, as it shows none beside an option whose default is None; the flag's help already
says what giving it does. Resolving the options makes each a bool, so a run reads
False, never None.
"""

from __future__ import annotations

import transcribe
from tests._isolation import IsolatedCase

#: Every on/off option transcribe takes, by its parsed name.
FLAGS = ("overwrite", "dry_run")


class FlagDefaultTest(IsolatedCase):
    """Each flag is None when omitted, hidden in --help, and False once resolved."""

    def setUp(self) -> None:
        super().setUp()
        self.audio = self.root / "a.mp3"
        self.audio.write_bytes(b"")
        self.parser = transcribe.build_parser(self.root / "models")

    def test_the_help_shows_no_false_default(self) -> None:
        self.assertNotIn("(default: False)", self.parser.format_help())

    def test_each_omitted_flag_parses_as_none(self) -> None:
        args = self.parser.parse_args(["--path", str(self.audio)])
        for flag in FLAGS:
            with self.subTest(flag=flag):
                self.assertIsNone(getattr(args, flag))

    def test_each_given_flag_parses_as_true(self) -> None:
        for flag in FLAGS:
            option = "--" + flag.replace("_", "-")
            with self.subTest(flag=flag):
                args = self.parser.parse_args(["--path", str(self.audio), option])
                self.assertIs(getattr(args, flag), True)

    def test_the_options_hold_false_for_an_omitted_flag(self) -> None:
        args = self.parser.parse_args(["--path", str(self.audio)])
        options = transcribe.resolve_options(args)
        for flag in FLAGS:
            with self.subTest(flag=flag):
                self.assertIs(getattr(options, flag), False)
