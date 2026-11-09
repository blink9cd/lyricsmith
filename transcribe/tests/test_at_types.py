"""Shared constants and the line helpers."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath

import at_label
import at_modes
import at_types
import stdio_utf8
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

    def test_names_and_labels_are_escaped_by_the_shared_helper(self) -> None:
        # The album tool escapes with the same function, so one file name prints one
        # way from either tool; the escape's own cases belong to the shared library.
        for module in (at_label, at_modes):
            with self.subTest(module=module.__name__):
                self.assertIs(module.spell_unsafe, stdio_utf8.spell_unsafe)
        for copy in ("spell_unsafe", "UNSAFE", "LATIN1_END", "_escape"):
            with self.subTest(copy=copy):
                self.assertFalse(hasattr(at_types, copy))

    def test_shown_path_is_relative_under_the_root_and_whole_outside_it(self) -> None:
        root = Path("/music")
        inside = at_types.shown_path(root / "a" / "b.mp3", root)
        self.assertEqual(inside, "a/b.mp3")
        outside = at_types.shown_path(Path("/else/c.mp3"), root)
        self.assertEqual(outside, str(Path("/else/c.mp3")))

    def test_shown_path_reads_with_slashes_on_every_host(self) -> None:
        # A Windows path, as a run there names it: the row reads a/b.mp3, as the
        # album tool's manifests name a track, while a path outside stays the host's.
        root = PureWindowsPath("C:/music")
        inside = at_types.shown_path(root / "a" / "b.mp3", root)
        self.assertEqual(inside, "a/b.mp3")
        outside = at_types.shown_path(PureWindowsPath("D:/else/c.mp3"), root)
        self.assertEqual(outside, "D:\\else\\c.mp3")
