"""`lyrics_pinned.pinned`: every open of a pinned name must be the pinned file itself.

A fix or a paste matches a track to its transcript record by file identity, then writes
it by name. While the step holds the name pinned to that identity, `open_regular` tells
the file it opened by the descriptor's own `fstat`, so a file swapped in under the name
after the step's check is refused at the open that would have written it, its
descriptor closed. A name other than the pinned one opens as it always did, and the pin
ends with its block.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_pinned  # noqa: E402 - import follows the sys.path bootstrap above

#: Why the tests' pin refuses another file.
WHY = "not the file planned"


def identity(path: Path) -> lyrics_pinned.FileIdentity:
    """*path*'s device and file number, links followed."""
    status = path.stat()
    return (status.st_dev, status.st_ino)


class PinnedTest(fixtures.IsolatedCase):
    """A pinned name opens only as the pinned file; nothing else changes."""

    def setUp(self) -> None:
        """A track, and a stand-in file ready to take its name."""
        super().setUp()
        self.track = self.tmp / "track.mp3"
        self.track.write_bytes(b"the planned file")
        self.stand_in = self.tmp / "stand-in.mp3"
        self.stand_in.write_bytes(b"another file")

    def read(self, path: Path) -> bytes:
        """*path*'s bytes, read through `open_regular`."""
        with lyrics_files.open_regular(path, "rb") as stream:
            return stream.read()

    def test_the_pinned_file_opens_as_it_always_did(self) -> None:
        with lyrics_pinned.pinned(self.track, identity(self.track), WHY):
            self.assertEqual(self.read(self.track), b"the planned file")
            with lyrics_files.open_regular(self.track, "r+b") as stream:
                stream.write(b"T")
        self.assertEqual(self.track.read_bytes(), b"The planned file")

    def test_a_file_swapped_in_under_the_name_is_refused_and_closed(self) -> None:
        planned = identity(self.track)
        os.replace(self.stand_in, self.track)
        opened: list[int] = []
        real_open = os.open

        def recording_open(*args: object, **kwargs: object) -> int:
            descriptor = real_open(*args, **kwargs)  # type: ignore[arg-type] # passed through
            opened.append(descriptor)
            return descriptor

        with (
            lyrics_pinned.pinned(self.track, planned, WHY),
            mock.patch.object(lyrics_files.os, "open", recording_open),
            self.assertRaises(lyrics_pinned.PinnedFileError) as caught,
        ):
            lyrics_files.open_regular(self.track, "r+b")
        self.assertEqual(str(caught.exception), WHY)
        self.assertIsInstance(caught.exception, OSError)
        (descriptor,) = opened
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.assertEqual(self.track.read_bytes(), b"another file")

    def test_another_name_is_not_held_to_the_pin(self) -> None:
        with lyrics_pinned.pinned(self.track, identity(self.track), WHY):
            self.assertEqual(self.read(self.stand_in), b"another file")

    def test_the_pin_ends_with_its_block(self) -> None:
        planned = identity(self.track)
        with (
            self.assertRaises(KeyError),
            lyrics_pinned.pinned(self.track, planned, WHY),
        ):
            raise KeyError("the step failed")
        os.replace(self.stand_in, self.track)
        self.assertEqual(self.read(self.track), b"another file")
