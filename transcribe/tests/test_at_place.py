"""Moving the finished pair into place (atomic renames)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import at_place
from at_types import TEMP_PREFIX, Action, Planned
from tests._isolation import IsolatedCase


class PlaceTest(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.dest = self.root / "out" / "a"
        self.planned = Planned(
            self.root / "lib" / "a" / "one.mp3",
            self.dest / "one.lrc",
            self.dest / "one.txt",
            Action.TRANSCRIBE,
        )

    def engine_output(self) -> Path:
        workdir = at_place.make_workdir(self.dest)
        (workdir / "out.lrc").write_text(
            "[by:whisper.cpp]\n[00:00.26] x\n", encoding="utf-8", newline="\n"
        )
        (workdir / "out.txt").write_text(" x\n", encoding="utf-8", newline="\n")
        return workdir

    def test_workdir_is_a_dot_folder_inside_a_created_destination(self) -> None:
        workdir = at_place.make_workdir(self.dest)
        self.assertEqual(workdir.parent, self.dest)
        self.assertTrue(workdir.name.startswith(TEMP_PREFIX))

    def test_place_pair_writes_the_labeled_lrc_and_moves_the_txt(self) -> None:
        workdir = self.engine_output()
        at_place.place_pair(workdir, "LABELED\n", self.planned)
        self.assertEqual(self.planned.lrc.read_text(encoding="utf-8"), "LABELED\n")
        self.assertEqual(self.planned.txt.read_text(encoding="utf-8"), " x\n")
        at_place.remove_workdir(workdir)
        self.assertEqual(
            sorted(p.name for p in self.dest.iterdir()), ["one.lrc", "one.txt"]
        )

    def test_place_pair_replaces_existing_files(self) -> None:
        self.dest.mkdir(parents=True)
        self.planned.lrc.write_text("old\n", encoding="utf-8", newline="\n")
        self.planned.txt.write_text("old\n", encoding="utf-8", newline="\n")
        at_place.place_pair(self.engine_output(), "NEW\n", self.planned)
        self.assertEqual(self.planned.lrc.read_text(encoding="utf-8"), "NEW\n")
        self.assertEqual(self.planned.txt.read_text(encoding="utf-8"), " x\n")

    def test_the_lrc_goes_first_so_a_failure_leaves_no_lone_txt(self) -> None:
        workdir = self.engine_output()
        real_replace = os.replace
        calls: list[str] = []

        def replace_then_fail_second(src: str | Path, dst: str | Path) -> None:
            calls.append(Path(dst).name)
            if len(calls) == 2:
                raise OSError("disk full")
            real_replace(src, dst)

        with mock.patch.object(os, "replace", side_effect=replace_then_fail_second):
            with self.assertRaises(OSError):
                at_place.place_pair(workdir, "LABELED\n", self.planned)
        self.assertEqual(calls, ["one.lrc", "one.txt"])
        self.assertTrue(self.planned.lrc.exists())
        self.assertFalse(self.planned.txt.exists())

    def test_remove_workdir_removes_leftovers(self) -> None:
        workdir = self.engine_output()
        at_place.remove_workdir(workdir)
        self.assertFalse(workdir.exists())


class MirrorFolderTest(IsolatedCase):
    def test_missing_folders_lists_only_the_absent_ones_deepest_first(self) -> None:
        deep = self.root / "out" / "a" / "b"
        self.assertEqual(
            at_place.missing_folders(deep),
            (deep, deep.parent, deep.parent.parent),
        )

    def test_missing_folders_can_reach_the_top(self) -> None:
        with mock.patch.object(at_place.os.path, "lexists", return_value=False):
            folders = at_place.missing_folders(Path("a") / "b")
        self.assertEqual(folders, (Path("a") / "b", Path("a"), Path(".")))

    def test_prune_stops_at_the_first_folder_that_holds_something(self) -> None:
        deep = self.root / "out" / "a" / "b"
        created = at_place.missing_folders(deep)
        deep.mkdir(parents=True)
        (deep.parent / "kept.lrc").write_bytes(b"")
        at_place.prune_folders(created)
        self.assertFalse(deep.exists())
        self.assertTrue((deep.parent / "kept.lrc").is_file())
