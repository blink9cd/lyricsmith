"""The strata file: a seed header, then one path, group and album row per drawn track.

The report reads it back to weigh a sample's groups, so the reader is a parser of a
file a person may edit: any line it does not expect refuses the whole file, and what it
accepts writes back byte for byte. Every path and album here is invented.
"""

from __future__ import annotations

import os
import stat
import sys
import unittest
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_strata as ss  # noqa: E402 - import follows the sys.path bootstrap above

# Its own profile name, as the ledger suite does. deadline=None: a per-example deadline
# measures machine load, not correctness.
settings.register_profile(
    "sweep_strata_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("sweep_strata_properties")

#: This host's root, so an invented path is absolute on every platform the suite runs on.
_ROOT = os.path.abspath(os.sep)
_GROUP = ss.StrataGroup


def _track(*parts: str) -> str:
    """An invented absolute track path under this host's root."""
    return os.path.join(_ROOT, "library", *parts)


_ROWS = (
    ss.StrataRow(
        _track("Some Artist", "An Album", "01 A.mp3"), _GROUP.NETEASE, "An Album"
    ),
    ss.StrataRow(
        _track("Other Band", "Second", "02 B.mp3"), _GROUP.WRONG_TEXT_ALBUMS, "Second"
    ),
)

#: Text that may hold lone surrogates and every separator, as an edited file may.
_ANY_TEXT = st.text(st.characters(exclude_categories=()), max_size=12)
_FIELD = st.one_of(_ANY_TEXT, st.sampled_from([*ss.StrataGroup, "bogus", ""]))
_LINE = st.one_of(
    st.lists(_FIELD, min_size=1, max_size=4).map("\t".join),
    st.tuples(
        _ANY_TEXT.map(lambda text: os.path.join(_ROOT, text)),
        st.sampled_from(list(ss.StrataGroup)),
        _ANY_TEXT,
    ).map("\t".join),
)
_HEADER = st.one_of(
    st.integers().map(lambda seed: f"#seed\t{seed}"),
    _ANY_TEXT.map(lambda text: f"#seed\t{text}"),
    _ANY_TEXT,
)
#: A field the format allows: non-empty UTF-8 text with no separator, CR or NUL.
_FIELD_TEXT = st.text(
    st.characters(codec="utf-8", exclude_characters="\t\n\r\x00"),
    min_size=1,
    max_size=12,
)
#: A path name with no drive or separator in it, so joined to the root it stays
#: absolute on every platform, Windows included.
_NAME = st.text(
    st.characters(codec="utf-8", exclude_characters="\t\n\r\x00:/\\"),
    min_size=1,
    max_size=12,
)
_ROW = st.builds(
    ss.StrataRow,
    path=_NAME.map(lambda name: os.path.join(_ROOT, name)),
    group=st.sampled_from(list(ss.StrataGroup)),
    album=_FIELD_TEXT,
)
#: Every strata file the writer accepts: a seed and rows with no path twice.
_STRATA = st.builds(
    ss.Strata,
    seed=st.integers(),
    rows=st.lists(_ROW, max_size=4, unique_by=lambda row: row.path).map(tuple),
)


def _mutated(data: bytes, change: tuple[int, int] | None) -> bytes:
    """*data* with the byte at *change*'s index (wrapped) made its value; none: as is."""
    if change is None:
        return data
    index, value = change[0] % len(data), change[1]
    return data[:index] + bytes([value]) + data[index + 1 :]


#: Whole files: near the format, written files whole or with one byte changed, so the
#: accepting branch is reached on every run as well as the refusing one, and any bytes.
_FILES = st.one_of(
    st.tuples(_HEADER, st.lists(_LINE, max_size=4), st.sampled_from(["\n", ""])).map(
        lambda parts: "\n".join([parts[0], *parts[1]]) + parts[2]
    ),
    st.builds(
        _mutated,
        _STRATA.map(lambda strata: ss.format_strata(strata.seed, strata.rows)),
        st.none() | st.tuples(st.integers(min_value=0), st.integers(0, 255)),
    ),
    st.binary(),
)


class FormatTest(sf.IsolatedCase):
    def test_the_header_names_the_seed_and_each_row_is_three_tab_fields(self) -> None:
        data = ss.format_strata(42, _ROWS)
        lines = data.decode("utf-8").split("\n")
        self.assertEqual(lines[0], "#seed\t42")
        self.assertEqual(lines[1], f"{_ROWS[0].path}\tnetease\tAn Album")
        self.assertEqual(lines[2], f"{_ROWS[1].path}\twrong-text-albums\tSecond")
        self.assertEqual(lines[3], "")

    def test_the_groups_are_exactly_the_six(self) -> None:
        self.assertEqual(
            [str(group) for group in ss.StrataGroup],
            [
                "wrong-text-albums",
                "netease",
                "youtube-music",
                "lrclib",
                "older-sidecars",
                "withheld",
            ],
        )

    def test_no_rows_is_a_header_alone(self) -> None:
        self.assertEqual(ss.format_strata(-3, ()), b"#seed\t-3\n")

    def test_a_row_the_reader_would_refuse_is_refused_at_write(self) -> None:
        good = _ROWS[0]
        refused = {
            "a relative path": ss.StrataRow("library/a.mp3", _GROUP.LRCLIB, "A"),
            "an empty album": ss.StrataRow(good.path, _GROUP.LRCLIB, ""),
            "a tab in the album": ss.StrataRow(good.path, _GROUP.LRCLIB, "A\tB"),
            "a linefeed in the path": ss.StrataRow(
                good.path + "\n", _GROUP.LRCLIB, "A"
            ),
            "a carriage return": ss.StrataRow(good.path, _GROUP.LRCLIB, "A\r"),
            "a NUL": ss.StrataRow(good.path + "\x00", _GROUP.LRCLIB, "A"),
            "a path not UTF-8": ss.StrataRow(
                os.path.join(_ROOT, "lib", "\udcff.mp3"), _GROUP.LRCLIB, "A"
            ),
            "an album not UTF-8": ss.StrataRow(good.path, _GROUP.LRCLIB, "\udcff"),
            "a group as text": ss.StrataRow(good.path, "lrclib", "A"),  # type: ignore[arg-type]
        }
        for name, row in refused.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ss.StrataError, "line 2"):
                    ss.format_strata(1, [row])

    def test_a_path_listed_twice_is_refused(self) -> None:
        twice = ss.StrataRow(_ROWS[0].path, _GROUP.LRCLIB, "Elsewhere")
        with self.assertRaisesRegex(ss.StrataError, "line 3: a path listed twice"):
            ss.format_strata(1, [_ROWS[0], twice])

    def test_a_seed_that_is_not_an_integer_is_refused(self) -> None:
        for seed in (True, 1.0, "1"):
            with self.subTest(seed=seed):
                with self.assertRaises(ss.StrataError):
                    ss.format_strata(seed, _ROWS)  # type: ignore[arg-type]

    def test_a_row_not_ascii_is_written_in_utf8(self) -> None:
        row = ss.StrataRow(
            _track("Ünïcode", "Café", "01 É.mp3"), _GROUP.WITHHELD, "Café"
        )
        data = ss.format_strata(7, [row])
        self.assertIn("Café".encode(), data)
        self.assertEqual(ss.parse_strata(data), ss.Strata(7, (row,)))


class ParseTest(sf.IsolatedCase):
    def test_a_written_file_parses_to_its_seed_and_rows(self) -> None:
        data = ss.format_strata(42, _ROWS)
        self.assertEqual(ss.parse_strata(data), ss.Strata(42, _ROWS))

    def test_each_group_reads_back_as_its_enum(self) -> None:
        rows = [
            ss.StrataRow(_track(f"{group}.mp3"), group, "An Album")
            for group in ss.StrataGroup
        ]
        read = ss.parse_strata(ss.format_strata(0, rows))
        self.assertEqual(read.rows, tuple(rows))
        self.assertTrue(all(type(row.group) is ss.StrataGroup for row in read.rows))

    def test_a_malformed_line_refuses_the_whole_file(self) -> None:
        good = ss.format_strata(42, _ROWS)
        row = f"{_track('c.mp3')}\tlrclib\tAlbum"
        malformed = {
            "a group not of the six": f"{_track('c.mp3')}\tsidecar\tAlbum\n",
            "two fields": f"{_track('c.mp3')}\tlrclib\n",
            "four fields": f"{row}\textra\n",
            "a blank line": "\n",
            "a relative path": "c.mp3\tlrclib\tAlbum\n",
            "an empty album": f"{_track('c.mp3')}\tlrclib\t\n",
            "a CRLF ending": f"{row}\r\n",
            "a NUL": f"{row}\x00\n",
            "no final linefeed": row,
            "a repeated path": f"{_ROWS[0].path}\tlrclib\tAlbum\n",
        }
        for name, extra in malformed.items():
            with self.subTest(name):
                with self.assertRaises(ss.StrataError):
                    ss.parse_strata(good + extra.encode("utf-8"))

    def test_a_refusal_names_the_line_never_its_content(self) -> None:
        data = ss.format_strata(42, _ROWS) + b"/secret/words\tbogus\tsecret\n"
        with self.assertRaisesRegex(ss.StrataError, "line 4") as caught:
            ss.parse_strata(data)
        self.assertNotIn("secret", str(caught.exception))

    def test_a_header_that_is_not_the_seed_refuses_the_file(self) -> None:
        for header in (
            b"",
            b"\n",
            b"#seed\n",
            b"#seed\t\n",
            b"#seed\tx\n",
            b"#seed\t007\n",
            b"#seed\t-0\n",
            b"#seed\t+1\n",
            b"#seed\t1\t2\n",
            b"#Seed\t1\n",
            b"seed\t1\n",
            b"#seed\t" + b"9" * 5000 + b"\n",
            b"#seed\t1",
        ):
            with self.subTest(header=header[:20]):
                with self.assertRaises(ss.StrataError):
                    ss.parse_strata(header)

    def test_bytes_that_are_not_utf8_refuse_the_file(self) -> None:
        with self.assertRaisesRegex(ss.StrataError, "UTF-8"):
            ss.parse_strata(b"#seed\t1\n/\xff\tlrclib\tA\n")

    @given(_STRATA)
    def test_any_file_the_writer_writes_reads_back_as_its_rows(
        self, strata: ss.Strata
    ) -> None:
        data = ss.format_strata(strata.seed, strata.rows)
        self.assertEqual(ss.parse_strata(data), strata)

    @given(_FILES)
    def test_fuzz_parse_strata(self, data: str | bytes) -> None:
        raw = data if isinstance(data, bytes) else data.encode("utf-8", "surrogatepass")
        try:
            strata = ss.parse_strata(raw)
        except ss.StrataError:
            return
        self.assertEqual(ss.format_strata(strata.seed, strata.rows), raw)


class FileTest(sf.IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.target = self.state / "entry" / "strata.tsv"

    def test_a_written_file_reads_back_as_written(self) -> None:
        ss.write_strata(self.target, 42, _ROWS)
        self.assertEqual(ss.read_strata(self.target), ss.Strata(42, _ROWS))
        self.assertEqual(self.target.read_bytes(), ss.format_strata(42, _ROWS))

    @unittest.skipIf(os.name == "nt", "POSIX file modes do not exist on Windows")
    def test_the_file_and_its_folder_are_private(self) -> None:
        ss.write_strata(self.target, 42, _ROWS)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.target.parent.stat().st_mode), 0o700)

    def test_a_rerun_replaces_the_file(self) -> None:
        ss.write_strata(self.target, 42, _ROWS)
        ss.write_strata(self.target, 43, _ROWS[:1])
        self.assertEqual(ss.read_strata(self.target), ss.Strata(43, _ROWS[:1]))

    def test_a_refused_row_writes_nothing(self) -> None:
        bad = ss.StrataRow("relative.mp3", _GROUP.LRCLIB, "A")
        with self.assertRaises(ss.StrataError):
            ss.write_strata(self.target, 1, [bad])
        self.assertFalse(self.target.exists())

    def test_a_malformed_file_is_refused_naming_it(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(ss.format_strata(1, _ROWS) + b"no tabs here\n")
        with self.assertRaisesRegex(ss.StrataError, "strata.tsv: line 4"):
            ss.read_strata(self.target)

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaisesRegex(ss.StrataError, "cannot be read"):
            ss.read_strata(self.target)

    def test_a_file_past_the_cap_is_refused(self) -> None:
        self.target.parent.mkdir(parents=True)
        with self.target.open("wb") as handle:
            handle.truncate(ss.MAX_STRATA_BYTES + 1)
        with self.assertRaisesRegex(ss.StrataError, "more than"):
            ss.read_strata(self.target)

    def test_a_link_is_never_followed(self) -> None:
        elsewhere = self.tmp / "elsewhere.tsv"
        elsewhere.write_bytes(ss.format_strata(1, _ROWS))
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(elsewhere)
        with self.assertRaisesRegex(ss.StrataError, "cannot be read"):
            ss.read_strata(self.target)
