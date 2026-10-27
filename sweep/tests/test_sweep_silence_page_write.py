"""The silence pass writes its page beside its list after every run, replaced atomically.

Finished, stopped by --limit, interrupted by Ctrl-C, or a --resume with nothing left:
each ends by writing ``silent-<stamp>.html`` beside the list from the whole list, and
the summary names it under the list. A page that cannot be written is named on stderr,
the list kept, and the exit is 1. ffmpeg is `silence_fixtures.FakeFfmpeg`, so nothing
here starts a process. Every album and title here is INVENTED.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]
# album's test helpers, last, so none of them shadows a module of this tool's own.
sys.path.append(str(_HERE.parents[1] / "album" / "tests"))

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import page_fixtures as pf  # noqa: E402 - import follows the sys.path bootstrap above
import silence_fixtures as sfx  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence_list as sil  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence_page as ssp  # noqa: E402 - import follows the sys.path bootstrap above
from fifo_fakes import make_pipe, within_deadline  # noqa: E402 - album's test helpers

_FIRST, _SECOND = "Artist/First", "Artist/Second"


class PageCase(sfx.SilenceCase):
    """Two albums of one silent track each, titled so each row is easy to find."""

    def setUp(self) -> None:
        """Build ``Artist/First`` and ``Artist/Second``."""
        super().setUp()
        sfx.add_track(self.library / "Artist" / "First", "01 One.mp3", title="Ember")
        sfx.add_track(self.library / "Artist" / "Second", "01 Two.mp3", title="Flint")
        self.ffmpeg.silent = frozenset({"01 One.mp3", "01 Two.mp3"})

    def page_of(self, listed: Path) -> Path:
        """The page beside *listed*."""
        return listed.with_suffix(".html")

    def read_page(self, listed: Path) -> str:
        """The page beside *listed*, checked well formed."""
        page = self.page_of(listed).read_text(encoding="utf-8")
        pf.assert_well_formed(self, page)
        return page


class WrittenTest(PageCase):
    """Every way a pass ends writes the page, and the summary names it."""

    def test_a_finished_pass_writes_its_page_and_names_it(self) -> None:
        result = self.main()
        self.assertEqual(result.code, 0, result.stderr)
        listed = self.the_list()
        page = self.read_page(listed)
        self.assertIn("Ember", page)
        self.assertIn("Flint", page)
        self.assertIn(listed.name, page)
        self.assertNotIn(str(listed.parent), page)
        self.assertIn(f"list: {listed}\npage: {self.page_of(listed)}\n", result.stdout)

    def test_a_limited_pass_writes_its_page(self) -> None:
        result = self.main("--limit", "1")
        page = self.read_page(self.the_list())
        self.assertIn("Ember", page)
        self.assertNotIn("Flint", page)
        self.assertIn("\npage: ", result.stdout)
        self.assertTrue(result.stdout.splitlines()[-1].startswith("resume: "))

    def test_ctrl_c_writes_the_page_of_the_finished_albums(self) -> None:
        self.ffmpeg.interrupt = "01 Two.mp3"
        result = self.main()
        self.assertEqual(result.code, 130)
        page = self.read_page(self.the_list())
        self.assertIn("Ember", page)
        self.assertNotIn("Flint", page)
        self.assertIn("\npage: ", result.stdout)

    def test_a_resume_shows_the_earlier_legs_rows_too(self) -> None:
        self.main("--limit", "1")
        listed = self.the_list()
        result = self.main("--resume", str(listed))
        self.assertEqual(result.code, 0, result.stderr)
        page = self.read_page(listed)
        self.assertIn("Ember", page)
        self.assertIn("Flint", page)

    def test_a_resume_with_nothing_left_measures_nothing_and_rewrites_the_page(
        self,
    ) -> None:
        self.main()
        listed = self.the_list()
        self.page_of(listed).write_bytes(f"{ssp.MARKER}stale".encode())
        self.ffmpeg.calls.clear()
        result = self.main("--resume", str(listed))
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.ffmpeg.calls, [])
        self.assertIn("Flint", self.read_page(listed))
        self.assertIn("albums measured: 0\n", result.stdout)

    def test_the_replace_leaves_no_temporary_file(self) -> None:
        self.main()
        listed = self.the_list()
        self.main("--resume", str(listed))
        self.assertEqual(
            sorted(path.name for path in listed.parent.iterdir()),
            sorted([listed.name, self.page_of(listed).name]),
        )


class PageFailureTest(PageCase):
    """A page that cannot be written is named; the list is kept; the exit is 1."""

    def test_a_page_that_cannot_be_written_exits_one_and_keeps_the_list(self) -> None:
        listed = self.tmp / "kept.tsv"
        listed.write_bytes(sil.header_line().encode())
        # A folder where the page goes: the replace cannot put a file there.
        blocker = self.page_of(listed)
        blocker.mkdir()
        result = self.main("--resume", str(listed))
        self.assertEqual(result.code, 1)
        self.assertIn(f"the page cannot be written: {blocker}", result.stderr)
        self.assertNotIn("\npage: ", result.stdout)
        self.assertEqual(
            [line for line in self.list_lines(listed) if line.startswith("#")],
            [f"#album-done\t{_FIRST}", f"#album-done\t{_SECOND}"],
        )
        self.assertEqual(
            sorted(path.name for path in self.tmp.iterdir() if path.name != "home"),
            sorted(["kept.tsv", "kept.html", "library"]),
        )

    def test_ctrl_c_keeps_its_exit_when_the_page_fails_too(self) -> None:
        listed = self.tmp / "kept.tsv"
        listed.write_bytes(sil.header_line().encode())
        self.page_of(listed).mkdir()
        self.ffmpeg.interrupt = "01 Two.mp3"
        result = self.main("--resume", str(listed))
        self.assertEqual(result.code, 130)
        self.assertIn("the page cannot be written", result.stderr)


class PageGuardTest(PageCase):
    """The page replaces only a page this pass wrote, and never the list itself."""

    def test_the_page_carries_the_marker_it_is_known_by(self) -> None:
        self.main()
        self.assertIn(ssp.MARKER, self.read_page(self.the_list()))

    def test_a_file_this_pass_did_not_write_is_refused_and_kept(self) -> None:
        listed = self.tmp / "report.tsv"
        listed.write_bytes(sil.header_line().encode())
        theirs = self.page_of(listed)
        theirs.write_bytes(b"<p>my own notes</p>")
        result = self.main("--resume", str(listed))
        self.assertEqual(result.code, 1)
        self.assertIn(f"the page cannot be written: {theirs}", result.stderr)
        self.assertIn("not a page this pass wrote", result.stderr)
        self.assertEqual(theirs.read_bytes(), b"<p>my own notes</p>")
        self.assertIn(f"#album-done\t{_SECOND}", self.list_lines(listed))

    def test_a_page_that_is_the_list_itself_is_refused(self) -> None:
        listed = self.tmp / "kept.tsv"
        listed.write_bytes(sil.header_line().encode())
        # A second name for the list, as a case-insensitive volume makes of one case.
        os.link(listed, self.page_of(listed))
        with self.assertRaisesRegex(ssp.PageRefusedError, "is the list itself"):
            ssp.write_page(listed, sf.fixed_now())
        self.assertEqual(listed.read_bytes(), sil.header_line().encode())

    def test_a_pipe_where_the_page_goes_is_refused_at_once(self) -> None:
        listed = self.tmp / "piped.tsv"
        listed.write_bytes(sil.header_line().encode())
        pipe = self.page_of(listed)
        make_pipe(pipe)
        with self.assertRaisesRegex(ssp.PageRefusedError, "is not a regular file"):
            within_deadline(self, pipe, lambda: ssp.write_page(listed, sf.fixed_now()))

    def test_a_list_named_html_in_any_case_does_not_become_its_own_page(self) -> None:
        for name in ("odd.HTML", "odd.Html"):
            with self.subTest(name=name):
                self.assertEqual(
                    ssp.page_path(Path("d") / name), Path("d") / f"{name}.html"
                )

    def test_a_list_named_near_the_name_limit_still_gets_its_page(self) -> None:
        listed = self.tmp / f"{'a' * 247}.tsv"
        listed.write_bytes(sil.header_line().encode())
        page = ssp.write_page(listed, sf.fixed_now())
        self.assertEqual(page, self.page_of(listed))
        pf.assert_well_formed(self, page.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(path.name for path in self.tmp.iterdir() if path.name[0] == "a"),
            sorted([listed.name, page.name]),
        )


class PageInterruptedTest(PageCase):
    """Ctrl-C while the page is written: the summary and resume still print; exit 130."""

    def test_ctrl_c_during_the_page_write_prints_the_summary_and_resume(self) -> None:
        with mock.patch.object(
            sweep_silence, "write_page", side_effect=KeyboardInterrupt
        ):
            result = self.main()
        self.assertEqual(result.code, 130)
        listed = self.the_list()
        self.assertIn(
            f"the page was not written: {self.page_of(listed)}", result.stderr
        )
        self.assertIn("interrupted; albums left: 0\n", result.stdout)
        self.assertIn(f"list: {listed}\n", result.stdout)
        self.assertNotIn("\npage: ", result.stdout)
        last = result.stdout.splitlines()[-1]
        self.assertTrue(last.startswith("resume: "), result.stdout)
        self.assertIn(str(listed), last)


class PagePathTest(PageCase):
    """The page is the list's name with ``.html``, and never the list itself."""

    def test_the_page_takes_the_lists_name(self) -> None:
        self.assertEqual(
            ssp.page_path(Path("d") / "silent-x.tsv"), Path("d") / "silent-x.html"
        )
        self.assertEqual(ssp.page_path(Path("d") / "kept"), Path("d") / "kept.html")

    def test_a_list_named_html_does_not_become_its_own_page(self) -> None:
        self.assertEqual(
            ssp.page_path(Path("d") / "odd.html"), Path("d") / "odd.html.html"
        )
