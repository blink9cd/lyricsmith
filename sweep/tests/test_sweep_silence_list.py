"""The silence pass's list: where it lives, its rows and markers, and its resume reader.

The list is a TSV file under the state directory: a header, a row per silent or
unmeasured track, and after each album's rows a marker naming the album as done. A
resumed pass reads the markers back to skip those albums, so the reader is a parser of
untrusted input: anything it does not expect raises `SilenceListError` and nothing else.
Every album and title here is INVENTED.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence_list as sil  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_silence_verdict import Verdict  # noqa: E402 - follows the sys.path bootstrap

# Its own profile name, as the ledger suite does. deadline=None: a per-example deadline
# measures machine load, not correctness.
settings.register_profile(
    "sweep_silence_list_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("sweep_silence_list_properties")

#: Text that may hold lone surrogates, as a file name that is not UTF-8 decodes to.
_ANY_TEXT = st.text(st.characters(exclude_categories=()), max_size=20)
_HEADER = "verdict\treason\tpeak_dbfs\tlength_s\ttrack_no\ttitle\tpath"
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


class WhereTest(sf.IsolatedCase):
    """The list folder follows the ledger's state directory, under its own name."""

    def test_an_absolute_state_home_holds_the_folder(self) -> None:
        folder = sil.list_dir({"XDG_STATE_HOME": str(self.state)})
        self.assertEqual(folder, self.state / "lyricsmith" / "silence")

    def test_a_relative_or_missing_state_home_falls_back_under_home(self) -> None:
        fallback = self.home / ".local" / "state" / "lyricsmith" / "silence"
        for environ in ({"XDG_STATE_HOME": "relative/state"}, {}):
            with self.subTest(environ=environ):
                self.assertEqual(sil.list_dir(environ), fallback)

    def test_the_ledger_and_the_list_share_one_state_root(self) -> None:
        environ = {"XDG_STATE_HOME": str(self.state)}
        self.assertEqual(sl.state_root(environ), self.state)
        self.assertEqual(sl.state_root({}), self.home / ".local" / "state")
        self.assertEqual(
            sl.ledger_path(self.library, environ).parent.parent.parent, self.state
        )

    def test_the_list_is_named_by_its_utc_start(self) -> None:
        started = datetime(2026, 10, 26, 9, 5, 7, tzinfo=timezone(timedelta(hours=2)))
        path = sil.list_path({"XDG_STATE_HOME": str(self.state)}, started)
        self.assertEqual(
            path, self.state / "lyricsmith" / "silence" / "silent-20261026T070507Z.tsv"
        )

    def test_a_utc_start_is_named_as_it_is(self) -> None:
        started = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        self.assertEqual(sil.list_path({}, started).name, "silent-20260102T030405Z.tsv")


class FormatTest(sf.IsolatedCase):
    """The header, a row, a not-measured row, an album's block and its marker."""

    def test_the_header_names_the_columns_in_order(self) -> None:
        self.assertEqual(sil.header_line(), _HEADER + "\n")

    def test_a_silent_track_row(self) -> None:
        self.assertEqual(
            sil.format_row(_ROW),
            "likely meant\tgap track: placeholder title, 4.0 s\t-91.2\t4.0\t3/12"
            "\tUntitled\tArtist/Album/03 Untitled.mp3",
        )

    def test_a_not_measured_row_leaves_the_unknowns_empty(self) -> None:
        self.assertEqual(
            sil.format_row(_UNMEASURED),
            "not measured\tffmpeg exit 1\t\t\t\t\tArtist/Album/04 Lost.mp3",
        )

    def test_a_non_finite_peak_or_length_is_an_empty_field(self) -> None:
        # An unknown value is an empty field, as sweep.md promises; never nan or inf.
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                row = sil.ListRow(
                    verdict=Verdict.UNSURE,
                    reason="placeholder title, length unknown",
                    peak_dbfs=value,
                    length_s=value,
                    track_no=None,
                    title=None,
                    path="Artist/Album/05.mp3",
                )
                self.assertEqual(
                    sil.format_row(row),
                    "unsure\tplaceholder title, length unknown\t\t\t\t\t"
                    "Artist/Album/05.mp3",
                )

    def test_tabs_and_line_breaks_in_a_field_become_spaces(self) -> None:
        row = sil.ListRow(
            verdict=Verdict.UNSURE,
            reason="odd\treason",
            peak_dbfs=-120.0,
            length_s=12.5,
            track_no="1\n",
            title="Line\r\nBreak\tand Tab",
            path="Art\tist/Al\nbum/01.mp3",
        )
        line = sil.format_row(row)
        self.assertEqual(line.count("\t"), len(sil.COLUMNS) - 1)
        self.assertNotRegex(line, "[\r\n]")
        self.assertIn("\tLine  Break and Tab\t", line)

    def test_an_albums_block_ends_with_its_marker(self) -> None:
        block = sil.album_block([_ROW, _UNMEASURED], "Artist/Album")
        self.assertEqual(
            block,
            sil.format_row(_ROW)
            + "\n"
            + sil.format_row(_UNMEASURED)
            + "\n#album-done\tArtist/Album\n",
        )

    def test_an_album_with_no_silent_track_is_its_marker_alone(self) -> None:
        self.assertEqual(
            sil.album_block([], "Artist/Quiet"), "#album-done\tArtist/Quiet\n"
        )


def _rows_of(album: str) -> list[sil.ListRow]:
    """One invented row for *album*, its path naming the album, so no two albums share one."""
    return [
        sil.ListRow(
            Verdict.UNSURE, "r", -90.0, 2.0, "1", "T", f"{sil.done_key(album)}/01.mp3"
        )
    ]


def _written(albums: list[str]) -> bytes:
    """A list as the pass writes it: the header, then each album's block."""
    text = sil.header_line() + "".join(
        sil.album_block(_rows_of(album), album) for album in albums
    )
    return text.encode("utf-8")


class ResumeTest(sf.IsolatedCase):
    """A list's done markers, read back; anything unexpected is a named error."""

    def _list(self, *blocks: str) -> bytes:
        return (sil.header_line() + "".join(blocks)).encode("utf-8")

    def test_the_markers_name_the_done_albums(self) -> None:
        data = self._list(
            sil.album_block([_ROW], "Artist/Album"),
            sil.album_block([], "Other/Quiet"),
        )
        point = sil.parse_resume(data)
        self.assertEqual(point.done, frozenset({"Artist/Album", "Other/Quiet"}))
        self.assertEqual((point.keep, point.dropped), (len(data), 0))

    def test_a_header_alone_is_nothing_done(self) -> None:
        header = sil.header_line().encode()
        self.assertEqual(
            sil.parse_resume(header), sil.ResumePoint(frozenset(), len(header), 0)
        )

    def test_a_header_without_its_newline_is_a_tail(self) -> None:
        # Nothing complete is kept: the resume writes the header again.
        self.assertEqual(
            sil.parse_resume(_HEADER.encode()), sil.ResumePoint(frozenset(), 0, 1)
        )

    def test_rows_after_the_last_marker_are_dropped(self) -> None:
        kept = self._list(sil.album_block([_ROW], "Artist/Album"))
        data = kept + (sil.format_row(_UNMEASURED) + "\n").encode() * 2
        point = sil.parse_resume(data)
        self.assertEqual(
            point, sil.ResumePoint(frozenset({"Artist/Album"}), len(kept), 2)
        )

    def test_an_unterminated_marker_is_not_a_marker(self) -> None:
        kept = self._list(sil.album_block([], "Artist/Album"))
        data = kept + b"#album-done\tArtist/Alb"
        point = sil.parse_resume(data)
        self.assertEqual(
            point, sil.ResumePoint(frozenset({"Artist/Album"}), len(kept), 1)
        )

    def test_an_unterminated_tail_may_hold_anything(self) -> None:
        kept = self._list()
        for tail in (b"garbage", b"\xe9\xff", b"#comment\r", "Café".encode()[:-1]):
            with self.subTest(tail=tail):
                point = sil.parse_resume(kept + tail)
                self.assertEqual(point, sil.ResumePoint(frozenset(), len(kept), 1))

    def test_a_marked_album_with_odd_characters_is_found_by_its_key(self) -> None:
        data = self._list(sil.album_block([], "Odd\tAlbum"))
        self.assertIn(sil.done_key("Odd\tAlbum"), sil.parse_resume(data).done)

    def test_malformed_lists_are_refused_by_line(self) -> None:
        tail = sil.album_block([], "Artist/Album")
        for text, line in (
            (b"", 1),
            (b"verdict\treason", 1),
            (b"not\ta\theader\n", 1),
            (_HEADER.encode() + b"\n\n" + tail.encode(), 2),
            (_HEADER.encode() + b"\nlikely meant\ttoo\tfew\n", 2),
            (_HEADER.encode() + b"\nmaybe\tr\t\t\t\t\tA/B/c.mp3\n", 2),
            (_HEADER.encode() + b"\n#album-done\t\n", 2),
            (_HEADER.encode() + b"\n#album-done\n", 2),
            (_HEADER.encode() + b"\n#album-done\tA\tB\n", 2),
            (_HEADER.encode() + b"\n#comment\n", 2),
            (_HEADER.encode() + b"\n#album-done\tcaf\xe9\n", 2),
            ((_HEADER + "\n" + sil.format_row(_ROW) + "\r\n").encode(), 2),
        ):
            with self.subTest(text=text):
                with self.assertRaises(sil.SilenceListError) as caught:
                    sil.parse_resume(text)
                self.assertIn(f"line {line}", str(caught.exception))

    def test_reading_a_list_file(self) -> None:
        path = self.tmp / "silent.tsv"
        path.write_bytes(self._list(sil.album_block([_ROW], "Artist/Album")))
        self.assertEqual(sil.read_resume(path).done, frozenset({"Artist/Album"}))

    def test_an_unreadable_list_names_itself(self) -> None:
        missing = self.tmp / "missing.tsv"
        with self.assertRaises(sil.SilenceListError) as caught:
            sil.read_resume(missing)
        self.assertIn(str(missing), str(caught.exception))

    def test_a_malformed_list_file_names_itself_and_the_line(self) -> None:
        path = self.tmp / "bad.tsv"
        path.write_bytes(_HEADER.encode() + b"\ngarbage\n")
        with self.assertRaises(sil.SilenceListError) as caught:
            sil.read_resume(path)
        self.assertIn(str(path), str(caught.exception))
        self.assertIn("line 2", str(caught.exception))

    @given(
        st.binary(max_size=300)
        | st.lists(
            st.sampled_from(["#album-done\tA/B", "#album-done\t", "x\t" * 6 + "p", ""])
            | st.text(max_size=30),
            max_size=8,
        ).map(lambda lines: (_HEADER + "\n" + "\n".join(lines)).encode("utf-8"))
    )
    def test_fuzz_parse_resume(self, data: bytes) -> None:
        # Any bytes at all, and texts behind a real header, so the line checks are
        # reached as well as the header check.
        try:
            point = sil.parse_resume(data)
        except sil.SilenceListError:
            return
        self.assertLessEqual(point.keep, len(data))

    @given(
        st.lists(st.tuples(st.text(min_size=1, max_size=20), st.booleans()), max_size=6)
    )
    def test_every_written_list_reads_back_its_albums(
        self, albums: list[tuple[str, bool]]
    ) -> None:
        data = sil.header_line() + "".join(
            sil.album_block([_ROW] if has_row else [], album)
            for album, has_row in albums
        )
        self.assertEqual(
            sil.parse_resume(data.encode("utf-8")).done,
            frozenset(sil.done_key(album) for album, _ in albums),
        )

    @given(
        st.lists(st.text(min_size=1, max_size=12), unique=True, min_size=1, max_size=5),
        st.data(),
    )
    def test_any_cut_resumes_cleanly(
        self, albums: list[str], data: st.DataObject
    ) -> None:
        # A list cut anywhere, as a crash mid-write leaves it, is truncated to its last
        # complete block and one more block appended: it reads back with no row twice.
        *before, last = albums
        written = _written(before)
        cut = written[: data.draw(st.integers(0, len(written)), label="cut")]
        header = sil.header_line().encode()
        if len(cut) < len(header) - 1:
            with self.assertRaises(sil.SilenceListError):
                sil.parse_resume(cut)
            return
        point = sil.parse_resume(cut)
        kept = cut[: point.keep] or header
        resumed = kept + sil.album_block(_rows_of(last), last).encode("utf-8")
        again = sil.parse_resume(resumed)
        self.assertEqual(again.done, point.done | {sil.done_key(last)})
        self.assertEqual((again.keep, again.dropped), (len(resumed), 0))
        lines = resumed.split(b"\n")[1:-1]
        self.assertEqual(len(lines), len(set(lines)))


class WritableTest(sf.IsolatedCase):
    """An album's key is injective, and a name that is not UTF-8 is still writable."""

    def test_a_tab_and_a_space_no_longer_collide(self) -> None:
        self.assertNotEqual(sil.done_key("A\tB"), sil.done_key("A B"))

    def test_escapes_are_themselves_escaped(self) -> None:
        self.assertEqual(sil.done_key("A\\tB"), "A\\\\tB")
        self.assertNotEqual(sil.done_key("A\\tB"), sil.done_key("A\tB"))
        self.assertEqual(sil.done_key("a\tb\nc\rd"), "a\\tb\\nc\\rd")

    def test_a_marker_uses_the_key(self) -> None:
        self.assertEqual(
            sil.marker_line("Odd\tAlbum\n"), "#album-done\tOdd\\tAlbum\\n\n"
        )

    def test_an_undecodable_name_round_trips_through_its_key(self) -> None:
        album = (
            "caf\udce9/Album"  # a surrogate-escaped byte, as os gives a non-UTF-8 name
        )
        data = (sil.header_line() + sil.album_block([], album)).encode("utf-8")
        self.assertEqual(sil.parse_resume(data).done, frozenset({sil.done_key(album)}))
        self.assertNotEqual(sil.done_key(album), sil.done_key("caf\\udce9/Album"))

    def test_a_row_with_an_undecodable_name_is_writable(self) -> None:
        row = sil.ListRow(
            Verdict.UNSURE, "r", None, None, None, "t\udcff", "a\udce9/b.mp3"
        )
        line = sil.format_row(row)
        line.encode("utf-8")  # raises UnicodeEncodeError when a surrogate remains
        self.assertIn("a\\udce9/b.mp3", line)

    @given(_ANY_TEXT, _ANY_TEXT)
    def test_done_key_is_one_to_one(self, first: str, second: str) -> None:
        if first != second:
            self.assertNotEqual(sil.done_key(first), sil.done_key(second))
        sil.done_key(first).encode("utf-8")
