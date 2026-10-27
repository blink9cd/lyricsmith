"""Two options' --help says what the companion doc says, and names no tool the folder lacks.

`--fresh` is refused with `--offline` and with `--refetch-untimed`, and its help says
both, as `--refetch-untimed`'s does. `--changed-list` describes what its file is for, a
library app or script that rereads those files' tags, rather than naming a reader that
does not ship with this tool, in the help and in the companion doc alike.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above

#: The companion doc beside the entry point.
COMPANION = _HERE.parent / "album.md"
#: A tag reader the folder does not carry, which no help or doc may name.
UNSHIPPED_READER = "tag_refresh"


def option_help(flag: str) -> str:
    """*flag*'s help text, as the parser holds it."""
    for action in album.build_parser()._actions:
        if flag in action.option_strings:
            return str(action.help)
    raise AssertionError(f"no option {flag}")


class FreshHelpTest(fixtures.IsolatedCase):
    """`--fresh`'s help names every option it is refused with."""

    def test_it_names_every_refusal(self) -> None:
        self.assertIn(
            "Refused with --offline, --refetch-untimed and --refetch-implausible.",
            option_help("--fresh"),
        )

    def test_the_parser_refuses_each(self) -> None:
        for other in ("--offline", "--refetch-untimed", "--refetch-implausible"):
            refused = self.assertRaises(SystemExit)
            quiet = contextlib.redirect_stderr(io.StringIO())
            with self.subTest(other=other), refused, quiet:
                parser = album.build_parser()
                args = parser.parse_args(["--album", "a", "--fresh", other])
                album.lyrics_options.refuse_misplaced(parser, args)


class ChangedListHelpTest(fixtures.IsolatedCase):
    """`--changed-list` says what its list is for, and the format, naming no reader."""

    def test_the_help_describes_the_use(self) -> None:
        text = option_help("--changed-list")
        self.assertNotIn(UNSHIPPED_READER, text)
        self.assertIn("a library app or script that rereads those files' tags", text)
        self.assertIn("one per line", text)

    def test_the_doc_names_no_unshipped_reader(self) -> None:
        lines = COMPANION.read_text(encoding="utf-8").splitlines()
        naming = [n for n, line in enumerate(lines, 1) if UNSHIPPED_READER in line]
        self.assertEqual(naming, [])
