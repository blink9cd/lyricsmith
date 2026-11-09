"""The folder walk and the output locations."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import at_discover
from tests._isolation import IsolatedCase

#: Why a FIFO case skips on Windows.
_NO_FIFO = "Windows has no named pipes in its file system"


class DiscoverTest(IsolatedCase):
    def make(self, relative: str) -> Path:
        path = self.root / "lib" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path

    def test_a_named_file_is_taken_whatever_its_extension(self) -> None:
        path = self.make("one.flac")
        self.assertEqual(at_discover.find_audio(path, frozenset({"mp3"})), [path])

    def test_walk_is_recursive_sorted_and_case_insensitive(self) -> None:
        two = self.make("b/Two.MP3")
        one = self.make("a/one.mp3")
        self.make("a/skip.flac")
        found = at_discover.find_audio(self.root / "lib", frozenset({"mp3"}))
        self.assertEqual(found, [one, two])

    def test_dot_files_and_dot_folders_are_skipped(self) -> None:
        self.make("._one.mp3")
        self.make(".hidden.mp3")
        self.make(".lyricsmith-abc123/out.mp3")
        kept = self.make("kept.mp3")
        self.assertEqual(
            at_discover.find_audio(self.root / "lib", frozenset({"mp3"})), [kept]
        )

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_symlinked_folders_are_not_followed_but_symlinked_files_are_listed(
        self,
    ) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "far.mp3").write_bytes(b"")
        lib = self.root / "lib"
        lib.mkdir()
        os.symlink(outside, lib / "linked-folder")
        os.symlink(outside / "far.mp3", lib / "linked.mp3")
        self.assertEqual(
            at_discover.find_audio(lib, frozenset({"mp3"})), [lib / "linked.mp3"]
        )

    @unittest.skipIf(sys.platform == "win32", "folder permissions differ on Windows")
    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0, "root reads any folder"
    )
    def test_an_unreadable_folder_is_collected_not_dropped(self) -> None:
        kept = self.make("a/one.mp3")
        self.make("locked/far.mp3")
        locked = self.root / "lib" / "locked"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        errors: list[OSError] = []
        found = at_discover.find_audio(self.root / "lib", frozenset({"mp3"}), errors)
        self.assertEqual(found, [kept])
        self.assertEqual([error.filename for error in errors], [str(locked)])
        # Without a list, the walk still runs; the caller just chose not to hear.
        self.assertEqual(
            at_discover.find_audio(self.root / "lib", frozenset({"mp3"})), [kept]
        )

    def test_walk_root(self) -> None:
        path = self.make("a/one.mp3")
        self.assertEqual(at_discover.walk_root(path), path.parent)
        self.assertEqual(at_discover.walk_root(self.root / "lib"), self.root / "lib")

    def test_output_paths_beside_and_mirrored(self) -> None:
        root = self.root / "lib"
        audio = root / "a" / "one.live.mp3"
        self.assertEqual(
            at_discover.output_paths(audio, root, None),
            (root / "a" / "one.live.lrc", root / "a" / "one.live.txt"),
        )
        out = self.root / "out"
        self.assertEqual(
            at_discover.output_paths(audio, root, out),
            (out / "a" / "one.live.lrc", out / "a" / "one.live.txt"),
        )


@unittest.skipIf(sys.platform == "win32", "FIFOs and symlinks are POSIX here")
class ClassifyTest(IsolatedCase):
    """One classifier, named outcomes: audio, linked, or not a regular file."""

    # Declared here: a win32 type check sees setUp end at its skip, before these are set.
    link: Path
    fifo_link: Path

    def setUp(self) -> None:
        super().setUp()
        self.regular = self.root / "one.mp3"
        self.regular.write_bytes(b"")
        self.fifo = self.root / "pipe.mp3"
        # Skipped by platform rather than by probing for os.mkfifo, so a win32 type
        # check of the rest is narrowed too; the class decorator skips there first.
        if sys.platform == "win32":
            self.skipTest(_NO_FIFO)  # pragma: only-on win32
        os.mkfifo(self.fifo)
        self.link = self.root / "link.mp3"
        os.symlink(self.regular, self.link)
        self.fifo_link = self.root / "pipe-link.mp3"
        os.symlink(self.fifo, self.fifo_link)

    def test_a_walked_entry(self) -> None:
        cases = {
            self.regular: at_discover.Entry.AUDIO,
            self.fifo: at_discover.Entry.NOT_REGULAR,
            self.link: at_discover.Entry.LINKED,
            self.fifo_link: at_discover.Entry.LINKED,
        }
        for path, expected in cases.items():
            with self.subTest(path=path.name):
                self.assertIs(at_discover.classify(path, named=False), expected)

    def test_a_named_entry_follows_its_link(self) -> None:
        cases = {
            self.regular: at_discover.Entry.AUDIO,
            self.link: at_discover.Entry.AUDIO,
            self.fifo: at_discover.Entry.NOT_REGULAR,
            self.fifo_link: at_discover.Entry.NOT_REGULAR,
        }
        for path, expected in cases.items():
            with self.subTest(path=path.name):
                self.assertIs(at_discover.classify(path, named=True), expected)

    def test_an_entry_that_cannot_be_read_raises(self) -> None:
        with self.assertRaises(OSError):
            at_discover.classify(self.root / "gone.mp3", named=True)


class ClassifySeamTest(IsolatedCase):
    """ClassifyTest's link outcomes, reached on every host through Path.is_symlink.

    Windows needs a privilege to make a symlink, so there the real links are skipped.
    """

    def setUp(self) -> None:
        super().setUp()
        self.regular = self.root / "one.mp3"
        self.regular.write_bytes(b"")

    def test_a_walked_link_is_linked_and_a_named_one_is_followed(self) -> None:
        with mock.patch.object(Path, "is_symlink", return_value=True):
            self.assertIs(
                at_discover.classify(self.regular, named=False),
                at_discover.Entry.LINKED,
            )
            self.assertIs(
                at_discover.classify(self.regular, named=True),
                at_discover.Entry.AUDIO,
            )
