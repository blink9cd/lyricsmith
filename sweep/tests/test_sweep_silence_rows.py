"""The silence list read back for its page: every finished album's rows, with its album.

The page is built from the whole list, so its rows are read back by a reader with the
resume reader's rules: the same header, the same line checks, and the same cut tail
(rows after the last marker, a last line with no line feed) ignored. Each row's album
is the one its marker names, its key undone; every other field comes back as written.
Every album and title here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence_list as sil  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_silence_verdict import Verdict  # noqa: E402 - follows the sys.path bootstrap

# Its own profile name, as the list suite does. deadline=None: a per-example deadline
# measures machine load, not correctness.
settings.register_profile(
    "sweep_silence_rows_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("sweep_silence_rows_properties")

#: Text that may hold lone surrogates, as a file name that is not UTF-8 decodes to.
_ANY_TEXT = st.text(st.characters(exclude_categories=()), max_size=20)
_ROW = sil.ListRow(
    verdict=Verdict.LIKELY_MEANT,
    reason="gap track: placeholder title, 4.0 s",
    peak_dbfs=-91.25,
    length_s=4.0,
    track_no="3/12",
    title="Untitled",
    path="Artist/Album/03 Untitled.mp3",
)
_UNMEASURED = sil.ListRow(
    verdict=Verdict.NOT_MEASURED,
    reason="ffmpeg exit 1",
    peak_dbfs=None,
    length_s=None,
    track_no=None,
    title=None,
    path="Artist/Album/04 Lost.mp3",
)


def _list(*blocks: str) -> bytes:
    """A list: its header, then each block, as bytes."""
    return (sil.header_line() + "".join(blocks)).encode("utf-8")


class ParseRowsTest(sf.IsolatedCase):
    """Each finished album's rows, in list order, with the album their marker names."""

    def test_each_row_comes_back_as_written_with_its_album(self) -> None:
        data = _list(sil.album_block([_ROW, _UNMEASURED], "Artist/Album"))
        self.assertEqual(
            sil.parse_rows(data),
            (
                sil.ReadRow(
                    "Artist/Album",
                    Verdict.LIKELY_MEANT,
                    "gap track: placeholder title, 4.0 s",
                    "-91.2",
                    "4.0",
                    "3/12",
                    "Untitled",
                    "Artist/Album/03 Untitled.mp3",
                ),
                sil.ReadRow(
                    "Artist/Album",
                    Verdict.NOT_MEASURED,
                    "ffmpeg exit 1",
                    "",
                    "",
                    "",
                    "",
                    "Artist/Album/04 Lost.mp3",
                ),
            ),
        )

    def test_an_album_with_no_row_adds_none(self) -> None:
        data = _list(sil.album_block([], "A/Quiet"), sil.album_block([_ROW], "A/B"))
        self.assertEqual([row.album for row in sil.parse_rows(data)], ["A/B"])

    def test_a_header_alone_holds_no_rows(self) -> None:
        self.assertEqual(sil.parse_rows(_list()), ())
        self.assertEqual(sil.parse_rows(sil.header_line().rstrip("\n").encode()), ())

    def test_rows_after_the_last_marker_are_a_cut_tail_and_ignored(self) -> None:
        unfinished = sil.format_row(_UNMEASURED) + "\n"
        data = _list(sil.album_block([_ROW], "A/B"), unfinished, "#album-do")
        self.assertEqual([row.path for row in sil.parse_rows(data)], [_ROW.path])

    def test_a_tail_with_no_line_feed_is_never_a_marker(self) -> None:
        data = _list(sil.format_row(_ROW) + "\n" + "#album-done\tA/B")
        self.assertEqual(sil.parse_rows(data), ())

    def test_a_bad_line_raises_naming_it_as_the_resume_reader_does(self) -> None:
        for data, line in (
            (b"not a header\n", 1),
            (_list("garbage\n"), 2),
            (_list("#album-done\n"), 2),
            (_list("x\t" * 6 + "p\n"), 2),
            (_list("ok\r\n"), 2),
            (sil.header_line().encode() + b"\xff\n", 2),
        ):
            with self.subTest(data=data), self.assertRaises(sil.SilenceListError) as e:
                sil.parse_rows(data)
            self.assertIn(f"line {line}", str(e.exception))

    def test_read_rows_names_the_file_it_cannot_read(self) -> None:
        path = self.tmp / "absent.tsv"
        with self.assertRaises(sil.SilenceListError) as caught:
            sil.read_rows(path)
        self.assertIn(str(path), str(caught.exception))
        path.write_bytes(_list("garbage\n"))
        with self.assertRaises(sil.SilenceListError) as caught:
            sil.read_rows(path)
        self.assertIn(str(path), str(caught.exception))
        path.write_bytes(_list(sil.album_block([_ROW], "A/B")))
        self.assertEqual(len(sil.read_rows(path)), 1)

    @given(
        st.binary(max_size=300)
        | st.lists(
            st.sampled_from(
                ["#album-done\tA/B", "#album-done\t", "unsure\t" + "x\t" * 5 + "p", ""]
            )
            | st.text(max_size=30),
            max_size=8,
        ).map(lambda lines: (sil.header_line() + "\n".join(lines)).encode("utf-8"))
    )
    def test_fuzz_parse_rows(self, data: bytes) -> None:
        # Any bytes at all, and texts behind a real header: the reader raises only its
        # own error, and accepts exactly the lists the resume reader accepts.
        try:
            rows = sil.parse_rows(data)
        except sil.SilenceListError:
            with self.assertRaises(sil.SilenceListError):
                sil.parse_resume(data)
            return
        point = sil.parse_resume(data)
        albums = {sil.album_of_key(key) for key in point.done}
        self.assertLessEqual({row.album for row in rows}, albums)

    @given(st.lists(st.tuples(_ANY_TEXT.filter(bool), st.integers(0, 2)), max_size=5))
    def test_every_written_album_reads_back_its_rows(
        self, albums: list[tuple[str, int]]
    ) -> None:
        data = _list(*(sil.album_block([_ROW] * n, album) for album, n in albums))
        self.assertEqual(
            [row.album for row in sil.parse_rows(data)],
            [album for album, n in albums for _ in range(n)],
        )


class AlbumOfKeyTest(sf.IsolatedCase):
    """A marker's key undone: the album it was written from, where that is reversible."""

    def test_each_escape_is_undone(self) -> None:
        self.assertEqual(sil.album_of_key("a\\tb\\nc\\rd\\\\e"), "a\tb\nc\rd\\e")

    def test_a_surrogate_escape_is_undone(self) -> None:
        self.assertEqual(sil.album_of_key("caf\\udce9/Album"), "caf\udce9/Album")

    def test_an_escape_the_writer_never_makes_is_left_as_written(self) -> None:
        for key in ("a\\x41", "a\\u0041", "end\\", "a\\q"):
            with self.subTest(key=key):
                self.assertEqual(sil.album_of_key(key), key)

    @given(_ANY_TEXT)
    def test_a_key_undoes_to_its_album(self, album: str) -> None:
        self.assertEqual(sil.album_of_key(sil.done_key(album)), album)
