"""Shared constants and the line helpers."""

from __future__ import annotations

from pathlib import Path

import at_types
from tests._isolation import IsolatedCase


class TypesTest(IsolatedCase):
    def test_action_and_outcome_labels_are_the_printed_rows(self) -> None:
        self.assertEqual(
            [action.value for action in at_types.Action],
            [
                "transcribe",
                "overwrite (ours)",
                "skipped (has sidecar)",
                "refused (not ours)",
            ],
        )
        self.assertEqual(at_types.Outcome.FAILED.value, "failed")

    def test_split_lines_handles_crlf_and_a_final_newline(self) -> None:
        self.assertEqual(at_types.split_lines("a\r\nb\n"), ["a", "b"])
        self.assertEqual(at_types.split_lines("a\nb"), ["a", "b"])
        self.assertEqual(at_types.split_lines(""), [])

    def test_split_lines_keeps_other_separators_inside_a_line(self) -> None:
        self.assertEqual(at_types.split_lines("a\u2028b\n"), ["a\u2028b"])

    def test_has_timestamp(self) -> None:
        self.assertTrue(at_types.has_timestamp("[by:x]\n[00:00.26] line\n"))
        self.assertTrue(at_types.has_timestamp("[1:02] line"))
        self.assertFalse(at_types.has_timestamp("[by:x]\nuntimed\n"))
        self.assertFalse(at_types.has_timestamp("[ar:someone]\n"))

    def test_spell_unsafe_spells_every_c0_character_as_hex(self) -> None:
        # The album tool spells a tab, a line feed and a carriage return as \x09,
        # \x0a and \x0d; one file name must print one way from either tool.
        self.assertEqual(at_types.spell_unsafe("\t"), "\\x09")
        self.assertEqual(at_types.spell_unsafe("a\nb\rc"), "a\\x0ab\\x0dc")
        self.assertEqual(at_types.spell_unsafe("\x1b\x7f\x85"), "\\x1b\\x7f\\x85")
        self.assertEqual(at_types.spell_unsafe("\u202e\u2028"), "\\u202e\\u2028")
        self.assertEqual(at_types.spell_unsafe("café \\"), "café \\")

    def test_shown_path_is_relative_under_the_root_and_whole_outside_it(self) -> None:
        root = Path("/music")
        inside = at_types.shown_path(root / "a" / "b.mp3", root)
        self.assertEqual(inside, Path("a/b.mp3"))
        outside = at_types.shown_path(Path("/else/c.mp3"), root)
        self.assertEqual(outside, Path("/else/c.mp3"))
