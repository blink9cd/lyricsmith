"""An on/off option left off: no default in --help, and False wherever the sweep keeps it.

Each flag parses as None when omitted, so the shared formatter shows no default beside
it, as it shows none beside an option whose default is None; the flag's help already
says what giving it does. Every reader that keeps the value, rather than testing it for
truth, makes it a bool first, so the sweep's options hold False, never None.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_options  # noqa: E402 - import follows the sys.path bootstrap above

#: Every on/off option the sweep takes, by its parsed name.
FLAGS = (
    "apply",
    "refetch_untimed",
    "refetch_implausible",
    "owe_earlier_runs",
    "offline",
    "no_netease",
    "no_bandcamp",
    "no_youtube_music",
    "no_genius",
    "no_tekstowo",
    "restart",
)


class FlagDefaultTest(sf.IsolatedCase):
    """Each flag is None when omitted, hidden in --help, and False once kept."""

    def test_the_help_shows_no_false_default(self) -> None:
        self.assertNotIn("(default: False)", sweep.build_parser().format_help())

    def test_each_omitted_flag_parses_as_none(self) -> None:
        args = sweep.build_parser().parse_args([])
        for flag in FLAGS:
            with self.subTest(flag=flag):
                self.assertIsNone(getattr(args, flag))

    def test_each_given_flag_parses_as_true(self) -> None:
        parser = sweep.build_parser()
        for flag in FLAGS:
            option = "--" + flag.replace("_", "-")
            with self.subTest(flag=flag):
                self.assertIs(getattr(parser.parse_args([option]), flag), True)

    def test_the_options_hold_false_for_an_omitted_flag(self) -> None:
        options = sweep_options.build_options(sweep.build_parser().parse_args([]))
        for name in (
            "offline",
            "restart",
            "refetch_untimed",
            "refetch_implausible",
            "owe_earlier_runs",
        ):
            with self.subTest(option=name):
                self.assertIs(getattr(options, name), False)
        self.assertEqual(options.skip, ())

    def test_the_long_help_texts_cannot_be_changed(self) -> None:
        with self.assertRaises(TypeError):
            sweep_options.FLAG_HELP["--refetch-untimed"] = "changed"  # type: ignore[index]
