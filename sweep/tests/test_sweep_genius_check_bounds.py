"""``sweep --mode genius-check`` at its edges: hostile tracks, an early end, the library.

A track whose tags raise anything is that track's, unsure and unasked, and the check
goes on. A check that ends early anyway still writes its three lists before the end is
raised. A listed path outside ``--library``, links resolved, is passed over unread and
unasked, said and counted. A Ctrl-C from the searches' end on, the breaker's notice
included, is held until the lists are all written; one before the hold is in force is
raised, never read as a failed write. Every title, artist and key here is invented.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - puts album and the libraries on sys.path
import genius_check_fixtures as gcf  # noqa: E402 - import follows the sys.path bootstrap above
import genius_search_fakes as gsf  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes as gf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_genius_check as gc  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_genius_lists as gl  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_model import TrackTags  # noqa: E402 - follows the sys.path bootstrap

_UNSURE_HEADER = "track\treason\tclosest genius url\n"


def _far_replies(count: int) -> gsf.FakeSearchClient:
    """*count* clear replies naming no hit close to any invented track here."""
    reply = gsf.search_reply(gsf.song_result("Paper Boats", "Someone Else"))
    return gsf.FakeSearchClient([reply] * count)


def _failing_on(
    target: Path, error: BaseException, real: Callable[[Path], TrackTags]
) -> Callable[[Path], TrackTags]:
    """A tag reader that raises *error* for *target* and reads every other track."""

    def read(path: Path) -> TrackTags:
        if path == target:
            raise error
        return real(path)

    return read


class HostileTrackTest(gcf.CheckCase):
    """Anything a track's tags raise is that track's, and the check goes on."""

    def setUp(self) -> None:
        """Two tagged tracks in one invented album."""
        super().setUp()
        album = self.library / "Invented Artist" / "Lanterns"
        self.first = gcf.tagged_track(album, "01.mp3", "Invented Artist", "Keeper")
        self.second = gcf.tagged_track(album, "02.mp3", "Invented Artist", "Ferry")
        self.write_list(self.first, self.second)

    def test_a_value_error_is_that_tracks_alone(self) -> None:
        """A malformed frame's `ValueError` is `track unreadable`, named by type."""
        self.read_tags = _failing_on(
            self.first, ValueError("bad frame"), self.read_tags
        )
        client = _far_replies(1)
        result = self.run_check(client)
        self.assertEqual(result.code, gc.EXIT_OK)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            self.read_out(gl.UNSURE_NAME),
            _UNSURE_HEADER + f"{self.first}\ttrack unreadable\t\n",
        )
        self.assertEqual(self.read_out(gl.NOT_ON_GENIUS_NAME), f"{self.second}\n")
        self.assertIn(f"could not read {self.first}: ValueError", result.stderr)

    def test_an_end_past_the_track_still_writes_the_lists(self) -> None:
        """A `SystemExit` mid-check is raised only once the three lists are written."""
        self.read_tags = _failing_on(self.second, SystemExit(9), self.read_tags)
        with self.assertRaises(SystemExit):
            self.run_check(_far_replies(1))
        self.assertEqual(self.read_out(gl.NOT_ON_GENIUS_NAME), f"{self.first}\n")
        self.assertEqual(
            self.read_out(gl.UNSURE_NAME),
            _UNSURE_HEADER + f"{self.second}\tnot asked: check stopped\t\n",
        )
        self.assertTrue((self.out / gl.HAS_IT_NAME).is_file())


class OutsideLibraryTest(gcf.CheckCase):
    """A listed path outside the library, links resolved, is passed over and counted."""

    def setUp(self) -> None:
        """One tagged track inside the library and one in a folder beside it."""
        super().setUp()
        album = self.library / "Invented Artist" / "Lanterns"
        self.inside = gcf.tagged_track(album, "01.mp3", "Invented Artist", "Keeper")
        elsewhere = self.tmp / "elsewhere" / "Invented Artist" / "Driftwood"
        self.outside = gcf.tagged_track(elsewhere, "01.mp3", "Invented Artist", "Ferry")

    def _assert_passed_over(self, track: Path, result: gcf.Result) -> None:
        """*track* is listed unsure as outside the library, said and counted."""
        self.assertEqual(result.code, gc.EXIT_OK)
        self.assertEqual(
            self.read_out(gl.UNSURE_NAME),
            _UNSURE_HEADER + f"{track}\toutside the library\t\n",
        )
        self.assertIn(f"skipped {track}: outside the library", result.stderr)
        self.assertIn("outside the library: 1", result.stdout)

    def test_a_track_outside_is_never_read_or_asked(self) -> None:
        """The track beside the library is neither read nor searched."""
        self.write_list(self.outside, self.inside)
        read: list[Path] = []
        real = self.read_tags

        def reading(path: Path) -> TrackTags:
            read.append(path)
            return real(path)

        self.read_tags = reading
        client = _far_replies(1)
        result = self.run_check(client)
        self._assert_passed_over(self.outside, result)
        self.assertEqual(read, [self.inside])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.read_out(gl.NOT_ON_GENIUS_NAME), f"{self.inside}\n")

    def test_a_link_inside_to_a_track_outside_is_outside(self) -> None:
        """A link in the library is judged by where it leads."""
        link = self.inside.parent / "02.mp3"
        os.symlink(self.outside, link)
        self.write_list(link)
        client = _far_replies(1)
        self._assert_passed_over(link, self.run_check(client))
        self.assertEqual(client.calls, [])

    def test_a_link_outside_to_a_track_inside_is_checked(self) -> None:
        """A path reaching the library through a link is the library's track."""
        link = self.tmp / "shortcut.mp3"
        os.symlink(self.inside, link)
        self.write_list(link)
        result = self.run_check(_far_replies(1))
        self.assertEqual(result.code, gc.EXIT_OK)
        self.assertEqual(self.read_out(gl.NOT_ON_GENIUS_NAME), f"{link}\n")
        self.assertIn("outside the library: 0", result.stdout)


class WriteInterruptTest(gcf.CheckCase):
    """A Ctrl-C while the lists are written is held until all three are written."""

    def test_the_three_lists_are_finished_then_the_exit_is_interrupted(self) -> None:
        """The first list's write takes a real SIGINT; the other two still land."""
        album = self.library / "Invented Artist" / "Lanterns"
        track = gcf.tagged_track(album, "01.mp3", "Invented Artist", "Keeper")
        self.write_list(track)
        real = gl.write_state_file
        calls: list[Path] = []

        def interrupted_write(path: Path, data: bytes) -> None:
            calls.append(path)
            if len(calls) == 1:
                signal.raise_signal(signal.SIGINT)
            real(path, data)

        with mock.patch.object(gl, "write_state_file", interrupted_write):
            result = self.run_check(_far_replies(1))
        self.assertEqual(result.code, gc.EXIT_INTERRUPTED)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.read_out(gl.NOT_ON_GENIUS_NAME), f"{track}\n")
        self.assertEqual(self.read_out(gl.UNSURE_NAME), _UNSURE_HEADER)
        self.assertIn("interrupted while the lists were written", result.stderr)

    def test_a_write_failure_under_a_held_ctrl_c_is_raised_as_itself(self) -> None:
        """An unexpected write error is never hidden behind the held Ctrl-C."""
        album = self.library / "Invented Artist" / "Lanterns"
        self.write_list(gcf.tagged_track(album, "01.mp3", "Invented Artist", "Keeper"))

        def failing_write(_path: Path, _data: bytes) -> None:
            signal.raise_signal(signal.SIGINT)
            raise RuntimeError("invented write failure")

        with (
            mock.patch.object(gl, "write_state_file", failing_write),
            self.assertRaisesRegex(RuntimeError, "invented write failure"),
        ):
            self.run_check(_far_replies(1))


class HeldBeforeWriteTest(gcf.CheckCase):
    """A Ctrl-C after the searches and before the write never loses the searches."""

    def _one_track(self) -> Path:
        """One listed track, with the breaker's file made unwritable."""
        album = self.library / "Invented Artist" / "Lanterns"
        track = gcf.tagged_track(album, "01.mp3", "Invented Artist", "Keeper")
        self.write_list(track)
        self.state.mkdir(parents=True)
        (self.state / "lyricsmith").write_bytes(b"not a folder")
        return track

    def test_a_ctrl_c_at_the_breaker_notice_still_writes_the_lists(self) -> None:
        """A real SIGINT from the breaker notice is held: the lists land, exit 130."""
        self._one_track()
        real = gc._notice

        def notice(text: str) -> None:
            real(text)
            if "could not write the breaker file" in text:
                signal.raise_signal(signal.SIGINT)

        with mock.patch.object(gc, "_notice", notice):
            result = self.run_check(gsf.FakeSearchClient([gf.challenge(403)] * 4))
        self.assertEqual(result.code, gc.EXIT_INTERRUPTED, result.stderr)
        self.assertIn("unsure: 1", result.stdout)
        self.assertIn("refused", self.read_out(gl.UNSURE_NAME))
        self.assertIn("interrupted while the lists were written", result.stderr)

    def test_a_ctrl_c_before_the_hold_is_in_force_is_raised(self) -> None:
        """One landing before the hold is installed ends interrupted, never a usage exit."""
        self._one_track()

        @contextlib.contextmanager
        def interrupted_on_entry() -> Iterator[None]:
            raise KeyboardInterrupt
            yield  # pragma: no cover - never reached: the entry is what is interrupted

        with mock.patch.object(gc, "deferred_interrupt", interrupted_on_entry):
            result = self.run_check(gsf.FakeSearchClient([gf.challenge(403)] * 4))
        self.assertEqual(result.code, gc.EXIT_INTERRUPTED, result.stderr)
        self.assertIn("interrupted", result.stderr)
