"""lyrics_changed: the --changed-list bytes for one fixed corpus, pinned as literals.

The list file is a contract with a separate reader, and a consumer's test suite pins
the same bytes: it holds a copy of this writer and asserts this very corpus gives this
very output. Both literals must change together, in the same change, or the copy drifts
from the writer unseen. The corpus covers every rule the writer holds: a plain absolute
path, a duplicate, a lexically unnormalized path, a non-ASCII name, a carriage return
inside a name (written: only a trailing one is refused), one path for each reason a
path is left out, paths breaking two rules (the first rule checked names the reason),
and a path that ends in a carriage return only once normalized (judged as normalized,
named as given). Every path is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import host_shapes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed as lc  # noqa: E402 - import follows the sys.path bootstrap above

#: The golden corpus, in the order it is handed to the writer. A consumer's test suite
#: holds a twin of this tuple and of the two literals below: change all three together.
GOLDEN_PATHS = (
    "/music/Artist/Album/01 Track.mp3",
    "/music/Artist/Album/./CD2/../02 Track.mp3",
    "/music/Artist/Album/01 Track.mp3",
    "/music/Artist/Café/03 Track.mp3",
    "/music/Artist/Album/04\nTrack.mp3",
    "/music/Artist/Album/05 Track.mp3\r",
    "/music/Artist/Album/06\x00Track.mp3",
    "/music/Artist/Album/07 Track\udcff.mp3",
    "Album/./08 Track.mp3",
    "Album/./08 Track.mp3",
    "/music/Artist/Album/09\rTrack.mp3",
    "/music/Artist/Album/10 Track\r/.",
    "Album/11\x00Track.mp3",
    "/music/Artist/Album/12\n\x00Track.mp3",
    "/music/Artist/Album/13\x00Track.mp3\r",
    "/music/Artist/Album/14 Track\udcff.mp3\r",
    "Album/15 Track\udcff.mp3",
)

#: The exact file bytes the writer makes of `GOLDEN_PATHS`.
GOLDEN_BYTES = (
    b"/music/Artist/Album/01 Track.mp3\n"
    b"/music/Artist/Album/02 Track.mp3\n"
    b"/music/Artist/Caf\xc3\xa9/03 Track.mp3\n"
    b"/music/Artist/Album/09\rTrack.mp3\n"
)

#: Each path of `GOLDEN_PATHS` left out, as it was given, with the exact reason text.
GOLDEN_LEFT_OUT = (
    (
        "/music/Artist/Album/04\nTrack.mp3",
        "holds a linefeed, which would split it into two lines",
    ),
    (
        "/music/Artist/Album/05 Track.mp3\r",
        "ends in a carriage return, which reads as a CRLF line ending",
    ),
    ("/music/Artist/Album/06\x00Track.mp3", "holds a NUL"),
    ("/music/Artist/Album/07 Track\udcff.mp3", "is not UTF-8"),
    ("Album/./08 Track.mp3", "is not an absolute path"),
    (
        "/music/Artist/Album/10 Track\r/.",
        "ends in a carriage return, which reads as a CRLF line ending",
    ),
    ("Album/11\x00Track.mp3", "holds a NUL"),
    (
        "/music/Artist/Album/12\n\x00Track.mp3",
        "holds a linefeed, which would split it into two lines",
    ),
    ("/music/Artist/Album/13\x00Track.mp3\r", "holds a NUL"),
    (
        "/music/Artist/Album/14 Track\udcff.mp3\r",
        "ends in a carriage return, which reads as a CRLF line ending",
    ),
    ("Album/15 Track\udcff.mp3", "is not UTF-8"),
)


def _on_host(paths: tuple[str, ...]) -> tuple[str, ...]:
    """*paths*, each absolute POSIX one made absolute on this host: *paths* off Windows."""
    return tuple(host_shapes.on_host(path) for path in paths)


def host_golden_bytes() -> bytes:
    """`GOLDEN_BYTES` as this host writes them: the literal itself off Windows.

    On Windows each line is put on the host's drive with its separators, as the
    writer writes the corpus `_on_host` makes; the literal stays the contract.
    """
    lines = GOLDEN_BYTES.decode("utf-8").split("\n")[:-1]
    return "".join(f"{host_shapes.as_written(line)}\n" for line in lines).encode()


class GoldenFormatTest(fixtures.IsolatedCase):
    """The writer turns the golden corpus into the golden bytes and left-out paths."""

    def setUp(self) -> None:
        """Format the golden corpus once per test, inside the isolation."""
        super().setUp()
        self.data, self.left = lc.format_changed_list(_on_host(GOLDEN_PATHS))

    def test_the_bytes_written_are_the_golden_bytes(self) -> None:
        self.assertEqual(self.data, host_golden_bytes())

    def test_off_windows_the_golden_bytes_are_the_literal_itself(self) -> None:
        # The literal is the contract a consumer pins: on POSIX it is what is written.
        same = host_golden_bytes() == GOLDEN_BYTES
        self.assertEqual(same, not host_shapes.HOST_DRIVE)

    def test_the_paths_left_out_and_their_reasons_are_the_golden_ones(self) -> None:
        got = tuple((item.path, item.reason) for item in self.left)
        expected = tuple(
            (host_shapes.on_host(path), reason) for path, reason in GOLDEN_LEFT_OUT
        )
        self.assertEqual(got, expected)

    def test_no_paths_make_an_empty_file(self) -> None:
        self.assertEqual(lc.format_changed_list(()), (b"", ()))
