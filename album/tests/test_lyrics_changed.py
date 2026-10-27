"""lyrics_changed: the --changed-list file, written only in lines its reader accepts.

The format is a list-file contract shared with the tool that reads the file; these
tests hold the writing side of it alone: what is written, what is left out and why, the
strict read-back the sweep uses, which tracks of a run or an undo are listed, and the
atomic save. The reader's own acceptance of what is written is tested beside the reader,
not here. Every path is under the test's own directory or invented.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above

#: Any text, lone surrogates included: a path decoded from bytes that are not UTF-8
#: holds them, through the file system's surrogate escape.
_ANY_TEXT = st.text(alphabet=st.characters(exclude_categories=()))
#: Paths: any text, and the same text made absolute, so both kinds are common.
_PATHS = st.lists(st.one_of(_ANY_TEXT, _ANY_TEXT.map(lambda text: "/" + text)))


def _track(path: str, after: lr.TrackOutcome | None) -> lr.ManifestTrack:
    """A manifest track at album-relative *path* with outcome *after*."""
    return lr.ManifestTrack(path, (2, 4), lr.TrackBefore((), ()), after)


def _error(stage: lr.ErrorStage) -> lr.TrackOutcome:
    """A track whose run errored at *stage*, its frames not recorded as written."""
    error = lr.StageError(stage, "ReadBackError", "read back differs")
    return lr.TrackOutcome(lr.OutcomeStatus.ERROR, error=error)


class FormatTest(fixtures.IsolatedCase):
    """What a list file holds: each absolute path once, normalized, linefeed ended."""

    def test_each_path_is_one_linefeed_ended_line(self) -> None:
        data, left = lc.format_changed_list(["/m/a b.mp3", "/m/Ü/c.mp3"])
        self.assertEqual(data, "/m/a b.mp3\n/m/Ü/c.mp3\n".encode())
        self.assertEqual(left, ())

    def test_nothing_changed_is_an_empty_file(self) -> None:
        self.assertEqual(lc.format_changed_list([]), (b"", ()))

    def test_a_path_named_twice_is_written_once_where_first_named(self) -> None:
        data, _ = lc.format_changed_list(["/m/b.mp3", "/m/a.mp3", "/m/x/../b.mp3"])
        self.assertEqual(data, b"/m/b.mp3\n/m/a.mp3\n")

    def test_a_path_is_normalized_as_the_reader_normalizes_it(self) -> None:
        data, _ = lc.format_changed_list(["/m/./a//b.mp3"])
        self.assertEqual(data, b"/m/a/b.mp3\n")

    def test_each_path_the_reader_would_refuse_is_left_out_with_its_reason(
        self,
    ) -> None:
        paths = ["/m/a\nb.mp3", "/m/a.mp3\r", "/m/a\x00.mp3", "/m/\udcff.mp3", "a.mp3"]
        data, left = lc.format_changed_list(["/m/ok.mp3", *paths])
        self.assertEqual(data, b"/m/ok.mp3\n")
        self.assertEqual(
            left,
            (
                lc.LeftOut("/m/a\nb.mp3", lc.REASON_LINEFEED),
                lc.LeftOut("/m/a.mp3\r", lc.REASON_CR),
                lc.LeftOut("/m/a\x00.mp3", lc.REASON_NUL),
                lc.LeftOut("/m/\udcff.mp3", lc.REASON_NOT_UTF8),
                lc.LeftOut("a.mp3", lc.REASON_RELATIVE),
            ),
        )

    def test_a_carriage_return_inside_a_name_is_written(self) -> None:
        data, left = lc.format_changed_list(["/m/a\rb.mp3"])
        self.assertEqual((data, left), (b"/m/a\rb.mp3\n", ()))

    def test_a_left_out_path_is_shown_escaped(self) -> None:
        notice = lc.left_out_notice(lc.LeftOut("/m/a\nb\udcff.mp3", lc.REASON_NOT_UTF8))
        self.assertEqual(
            notice,
            "--changed-list: left out '/m/a\\nb\\udcff.mp3': " + lc.REASON_NOT_UTF8,
        )

    @given(_PATHS)
    def test_what_is_written_reads_back_exactly_and_the_rest_is_left_out(
        self, paths: list[str]
    ) -> None:
        data, left = lc.format_changed_list(paths)
        read = lc.parse_changed_list(data)
        self.assertIsNotNone(read)
        written = data.decode("utf-8").split("\n")[:-1] if data else []
        self.assertEqual(list(read or ()), written)
        self.assertEqual(len(set(written)), len(written))
        for path in paths:
            refused = lc.refusal(os.path.normpath(path))
            self.assertEqual(any(item.path == path for item in left), bool(refused))


class ParseTest(fixtures.IsolatedCase):
    """The strict read-back: every line as the contract has it, or nothing at all."""

    def test_each_line_is_a_normalized_absolute_path(self) -> None:
        self.assertEqual(
            lc.parse_changed_list(b"/m/a.mp3\n\n/m/./b.mp3\n"), ("/m/a.mp3", "/m/b.mp3")
        )

    def test_an_empty_file_lists_nothing(self) -> None:
        self.assertEqual(lc.parse_changed_list(b""), ())

    def test_any_line_the_contract_refuses_makes_the_list_unreadable(self) -> None:
        for data in (
            b"/m/a.mp3",
            b"/m/a.mp3\r\n",
            b"/m/a\x00.mp3\n",
            b"/m/\xff.mp3\n",
            b"a.mp3\n",
        ):
            with self.subTest(data=data):
                self.assertIsNone(lc.parse_changed_list(b"/m/ok.mp3\n" + data))

    @given(st.binary())
    def test_fuzz_parse_changed_list(self, data: bytes) -> None:
        read = lc.parse_changed_list(data)
        if read is not None:
            for path in read:
                self.assertTrue(Path(path).is_absolute())
                self.assertIsNone(lc.refusal(path))


class ChangedTracksTest(fixtures.IsolatedCase):
    """Which tracks a run, a strip or an undo lists: every one whose MP3 it saved."""

    def test_a_run_lists_each_track_whose_frames_it_saved(self) -> None:
        saved = lr.TrackOutcome(lr.OutcomeStatus.WRITTEN, frames_written=True)
        sidecar_only = lr.TrackOutcome(lr.OutcomeStatus.WRITTEN)
        tracks = [
            _track("01 a.mp3", saved),
            _track("CD2/02 b.mp3", sidecar_only),
            _track("CD2/03 c.mp3", None),
            _track("CD2/04 d.mp3", saved),
        ]
        album = self.tmp / "album"
        self.assertEqual(
            lc.run_changed(album, tracks),
            (str(album / "01 a.mp3"), str(album / "CD2" / "04 d.mp3")),
        )

    def test_a_track_whose_embed_or_strip_failed_is_listed_as_maybe_saved(
        self,
    ) -> None:
        tracks = [
            _track("embed.mp3", _error(lr.ErrorStage.EMBED)),
            _track("strip.mp3", _error(lr.ErrorStage.STRIP)),
            _track("fetch.mp3", _error(lr.ErrorStage.FETCH)),
            _track("check.mp3", _error(lr.ErrorStage.CHECK)),
        ]
        album = self.tmp / "album"
        self.assertEqual(
            lc.run_changed(album, tracks),
            (str(album / "embed.mp3"), str(album / "strip.mp3")),
        )

    def test_an_undo_lists_each_track_it_saved(self) -> None:
        results = [
            lr.UndoTrackResult("a.mp3", lr.UndoStatus.UNDONE, saved=True),
            lr.UndoTrackResult("b.mp3", lr.UndoStatus.UNDONE),
            lr.UndoTrackResult("c.mp3", lr.UndoStatus.ERROR, saved=True),
        ]
        album = self.tmp / "album"
        self.assertEqual(
            lc.undo_changed(album, results),
            (str(album / "a.mp3"), str(album / "c.mp3")),
        )

    def test_a_listed_path_keeps_the_album_path_it_was_given(self) -> None:
        # Never resolved: the reader matches files by device and inode.
        album = self.tmp / "link" / ".." / "album"
        saved = lr.TrackOutcome(lr.OutcomeStatus.WRITTEN, frames_written=True)
        self.assertEqual(
            lc.run_changed(album, [_track("a.mp3", saved)]),
            (os.path.abspath(album / "a.mp3"),),
        )


class SaveTest(fixtures.IsolatedCase):
    """The save: atomic, each left-out path said once, a failure handed back."""

    def setUp(self) -> None:
        """A list of notices."""
        super().setUp()
        self.notices: list[str] = []

    def test_no_file_named_writes_nothing(self) -> None:
        self.assertIsNone(lc.save_changed_list(None, ["/m/a.mp3"], self.notices.append))
        self.assertEqual(list(self.tmp.iterdir()), [self.home])

    def test_the_list_replaces_the_file_whole(self) -> None:
        target = self.tmp / "changed.txt"
        target.write_bytes(b"/old/list.mp3\n")
        error = lc.save_changed_list(target, ["/m/a.mp3"], self.notices.append)
        self.assertIsNone(error)
        self.assertEqual(target.read_bytes(), b"/m/a.mp3\n")
        # No temporary file is left beside it.
        self.assertEqual(
            sorted(p.name for p in self.tmp.iterdir()),
            sorted(["changed.txt", self.home.name]),
        )

    def test_each_left_out_path_is_one_notice(self) -> None:
        target = self.tmp / "changed.txt"
        lc.save_changed_list(target, ["/m/a\nb.mp3", "/m/c.mp3"], self.notices.append)
        self.assertEqual(target.read_bytes(), b"/m/c.mp3\n")
        self.assertEqual(
            self.notices,
            [lc.left_out_notice(lc.LeftOut("/m/a\nb.mp3", lc.REASON_LINEFEED))],
        )

    def test_a_file_that_cannot_be_written_is_handed_back_not_raised(self) -> None:
        blocker = self.tmp / "a file"
        blocker.write_bytes(b"")
        target = blocker / "changed.txt"
        error = lc.save_changed_list(target, ["/m/a.mp3"], self.notices.append)
        assert error is not None
        self.assertTrue(error.startswith(f"could not write --changed-list {target}: "))


class WriteChangedListTest(fixtures.IsolatedCase):
    """write_changed_list writes once, and runs once more when Ctrl-C lands mid-write."""

    def test_a_list_is_written_with_no_interrupt(self) -> None:
        path = self.tmp / "changed.txt"
        got = lc.write_changed_list(path, lambda: ["/m/01 A.mp3"], lambda text: None)
        self.assertEqual(got, (None, False))
        self.assertEqual(path.read_text(encoding="utf-8"), "/m/01 A.mp3\n")

    def test_a_ctrl_c_while_listing_writes_the_list_on_the_second_pass(self) -> None:
        path = self.tmp / "changed.txt"
        calls: list[int] = []

        def listed() -> list[str]:
            calls.append(1)
            if len(calls) == 1:
                raise KeyboardInterrupt
            return ["/m/01 A.mp3"]

        got = lc.write_changed_list(path, listed, lambda text: None)
        self.assertEqual(got, (None, True))
        self.assertEqual(len(calls), 2)
        self.assertEqual(path.read_text(encoding="utf-8"), "/m/01 A.mp3\n")
