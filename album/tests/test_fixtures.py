"""The shared test fixtures do what every other suite relies on them to do.

Two of these carry the whole folder's safety: the network tripwire must actually fire,
and the environment redirect must actually hide the real home directory and any Genius
key. A fixture that silently stopped doing either would leave every suite passing while
reaching the real network or the real config file, so each is proven here by behavior.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp3 import MP3

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above

# A documentation address (RFC 5737): never routed, and never a real host.
_DOC_ADDRESS = ("192.0.2.1", 9)
# How far mutagen's duration may sit from the one asked for. One MPEG frame is
# 1152 / 44100 s (about 26 ms), so a whole number of frames lands within half of that.
_DURATION_TOLERANCE_S = 0.02
# How long a test waits on a fake's worker thread before calling it stuck.
_JOIN_S = 5.0


def _length(path: Path) -> float:
    """The audio length mutagen reads from *path*, failing when it reads none."""
    info = MP3(path).info
    if info is None:
        raise AssertionError(f"mutagen read no audio stream from {path}")
    return float(info.length)


class TripwireTest(fixtures.IsolatedCase):
    """Any attempt to reach the network fails the test that made it."""

    def test_a_socket_connect_fires_the_tripwire(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with self.assertRaises(fixtures.NetworkTouched):
                sock.connect(_DOC_ADDRESS)
            with self.assertRaises(fixtures.NetworkTouched):
                sock.connect_ex(_DOC_ADDRESS)

    def test_a_name_lookup_fires_the_tripwire(self) -> None:
        with self.assertRaises(fixtures.NetworkTouched):
            socket.getaddrinfo("lyrics.invalid", 443)

    def test_a_high_level_connection_fires_the_tripwire(self) -> None:
        with self.assertRaises(fixtures.NetworkTouched):
            socket.create_connection(_DOC_ADDRESS, timeout=1)

    def test_the_tripwire_is_an_assertion_failure(self) -> None:
        self.assertTrue(issubclass(fixtures.NetworkTouched, AssertionError))


class SettlingTest(fixtures.IsolatedCase):
    """A thread a test leaves behind is waited for while the tripwire still stands.

    A provider call abandoned at its deadline keeps running on its own thread, so a
    test that ends before it does would let it reach the network after the fake
    transport and the tripwire are gone, and a tripwire hit on that thread is swallowed
    by the watchdog that started it. Both are reported instead.
    """

    def _started(self, target: Callable[[], object]) -> threading.Thread:
        """A daemon thread running *target*, already started."""
        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        return worker

    def test_a_network_call_off_the_test_thread_is_remembered(self) -> None:
        with TemporaryDirectory() as raw:
            with fixtures.isolated_environment(Path(raw)) as inner:
                with self.assertRaises(fixtures.NetworkTouched):
                    socket.getaddrinfo("lyrics.invalid", 443)
                self.assertEqual(inner.tripwire.stray, [])
                caught: list[BaseException] = []

                def look_up() -> None:
                    try:
                        socket.getaddrinfo("lyrics.invalid", 443)
                    except fixtures.NetworkTouched as touched:
                        caught.append(touched)

                self._started(look_up).join(_JOIN_S)
        self.assertEqual(len(caught), 1)
        self.assertEqual(len(inner.tripwire.stray), 1)
        self.assertIn("lyrics.invalid", inner.tripwire.stray[0])

    def test_a_thread_still_running_is_reported(self) -> None:
        before = frozenset(threading.enumerate())
        release = threading.Event()
        self.addCleanup(release.set)
        worker = self._started(lambda: release.wait(_JOIN_S))
        problems = fixtures.unsettled(before, fixtures.Tripwire(), limit_s=0.01)
        self.assertEqual(len(problems), 1)
        self.assertIn(worker.name, problems[0])
        release.set()
        worker.join(_JOIN_S)

    def test_a_thread_that_finishes_in_time_is_waited_for(self) -> None:
        before = frozenset(threading.enumerate())
        worker = self._started(lambda: threading.Event().wait(0.05))
        problems = fixtures.unsettled(before, fixtures.Tripwire(), limit_s=_JOIN_S)
        self.assertEqual(problems, [])
        self.assertFalse(worker.is_alive())

    def test_a_stray_network_hit_is_reported(self) -> None:
        tripwire = fixtures.Tripwire()
        tripwire.stray.append("test reached the network: ('lyrics.invalid', 443)")
        before = frozenset(threading.enumerate())
        problems = fixtures.unsettled(before, tripwire, limit_s=0.01)
        self.assertEqual(len(problems), 1)
        self.assertIn("lyrics.invalid", problems[0])


class EnvironmentTest(fixtures.IsolatedCase):
    """HOME and the XDG directories point into the test's own temporary directory."""

    def test_home_and_xdg_point_under_the_temporary_directory(self) -> None:
        self.assertEqual(Path.home(), self.home)
        self.assertTrue(self.home.is_dir())
        self.assertTrue(self.home.is_relative_to(self.tmp))
        self.assertEqual(
            set(self.xdg),
            {"XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"},
        )
        for name, path in self.xdg.items():
            with self.subTest(name=name):
                self.assertTrue(path.is_relative_to(self.home))
                self.assertNotEqual(path, self.home)
        # Containment against the whole environment, so no variable is read back by
        # name: the redirect must have reached os.environ, not only the yielded paths.
        # The difference, not a subset check, so a failure names only what is missing
        # rather than printing every variable the environment holds.
        expected = {"HOME": str(self.home), **{n: str(p) for n, p in self.xdg.items()}}
        self.assertEqual(expected.items() - os.environ.items(), set())

    def test_a_genius_key_set_outside_is_gone_inside(self) -> None:
        self.assertEqual(fixtures.GENIUS_TOKEN_ENV, "GENIUS_ACCESS_TOKEN")
        outer = {fixtures.GENIUS_TOKEN_ENV: "fake-token-for-tests"}
        # Names and containment only, never a read by the variable's name: the project's
        # environment scan must find the key's one read in the code that ships.
        with mock.patch.dict(os.environ, outer), TemporaryDirectory() as raw:
            with fixtures.isolated_environment(Path(raw)):
                self.assertFalse(fixtures.GENIUS_TOKEN_ENV in os.environ.keys())
            self.assertEqual(outer.items() - os.environ.items(), set())

    def test_leaving_restores_the_environment_and_the_socket(self) -> None:
        outer_home = Path.home()
        outer_connect = socket.socket.connect
        with TemporaryDirectory() as raw:
            with fixtures.isolated_environment(Path(raw)) as inner:
                self.assertNotEqual(Path.home(), outer_home)
                self.assertEqual(Path.home(), inner.home)
                self.assertTrue(inner.home.is_relative_to(Path(raw)))
        self.assertEqual(Path.home(), outer_home)
        self.assertIs(socket.socket.connect, outer_connect)


class IsolationEnforcementTest(fixtures.IsolatedCase):
    """isolation_offenders names every test that skipped the isolated base."""

    def test_a_plain_test_case_is_named_and_an_isolated_one_is_not(self) -> None:
        class Plain(unittest.TestCase):
            def test_x(self) -> None:
                """A test outside the isolation."""

        class Isolated(fixtures.IsolatedCase):
            def test_y(self) -> None:
                """A test inside it."""

        loader = unittest.TestLoader()
        suite = unittest.TestSuite(
            [
                loader.loadTestsFromTestCase(Plain),
                unittest.TestSuite([loader.loadTestsFromTestCase(Isolated)]),
            ]
        )
        offenders = fixtures.isolation_offenders(suite)
        self.assertEqual(len(offenders), 1)
        self.assertIn("Plain", offenders[0])

    def test_a_suite_that_failed_to_import_is_left_to_report_itself(self) -> None:
        suite = unittest.TestLoader().loadTestsFromName("no_such_module_here")
        self.assertEqual(fixtures.isolation_offenders(suite), [])


class SilentMp3Test(fixtures.IsolatedCase):
    """build_silent_mp3 writes a file mutagen reads with the asked-for duration."""

    def test_mutagen_reads_the_requested_duration(self) -> None:
        for seconds in (1.0, 30.0, 250.0):
            with self.subTest(seconds=seconds):
                path = fixtures.build_silent_mp3(self.tmp / f"{seconds}.mp3", seconds)
                length = _length(path)
                self.assertAlmostEqual(length, seconds, delta=_DURATION_TOLERANCE_S)

    def test_the_tags_and_version_are_written(self) -> None:
        path = fixtures.build_silent_mp3(
            self.tmp / "a.mp3", 5, "An Artist", "A Title", id3_version=(2, 3)
        )
        tags = ID3(path)
        self.assertEqual(tags.version, (2, 3, 0))
        self.assertEqual(str(tags["TPE1"]), "An Artist")
        self.assertEqual(str(tags["TIT2"]), "A Title")

    def test_a_missing_artist_or_title_leaves_its_frame_out(self) -> None:
        path = fixtures.build_silent_mp3(self.tmp / "a.mp3", 5, None, None)
        tags = ID3(path)
        self.assertNotIn("TPE1", tags)
        self.assertNotIn("TIT2", tags)

    def test_no_version_means_no_id3_header_at_all(self) -> None:
        path = fixtures.build_silent_mp3(self.tmp / "a.mp3", 5, id3_version=None)
        with self.assertRaises(ID3NoHeaderError):
            ID3(path)
        self.assertAlmostEqual(_length(path), 5, delta=_DURATION_TOLERANCE_S)


class BuildAlbumTest(fixtures.IsolatedCase):
    """build_album lays out tracks, nested folders and sidecars."""

    def test_tracks_and_sidecars_land_where_named(self) -> None:
        album = self.tmp / "album"
        paths = fixtures.build_album(
            album,
            (
                fixtures.TrackSpec("CD2/01 B.mp3", lrc=fixtures.LIGHTHOUSE_LRC),
                fixtures.TrackSpec("CD1/01 A.mp3", txt=fixtures.ORCHARD_PLAIN),
            ),
        )
        self.assertEqual(paths, [album / "CD1/01 A.mp3", album / "CD2/01 B.mp3"])
        self.assertEqual(
            (album / "CD2/01 B.lrc").read_text(encoding="utf-8"),
            fixtures.LIGHTHOUSE_LRC,
        )
        self.assertEqual(
            (album / "CD1/01 A.txt").read_text(encoding="utf-8"), fixtures.ORCHARD_PLAIN
        )
        self.assertFalse((album / "CD1/01 A.lrc").exists())
        self.assertEqual(str(ID3(album / "CD1/01 A.mp3")["TIT2"]), "Invented Title")


class InventedLyricsTest(fixtures.IsolatedCase):
    """The invented texts have the relationships the similarity tests depend on."""

    @staticmethod
    def _words(text: str) -> list[str]:
        kept = "".join(c for c in text.casefold() if c.isalnum() or c.isspace())
        return kept.split()

    def test_the_edited_copy_differs_only_in_punctuation_and_case(self) -> None:
        self.assertNotEqual(fixtures.LIGHTHOUSE_EDITED, fixtures.LIGHTHOUSE_PLAIN)
        self.assertEqual(
            self._words(fixtures.LIGHTHOUSE_EDITED),
            self._words(fixtures.LIGHTHOUSE_PLAIN),
        )

    def test_the_other_text_is_a_different_song(self) -> None:
        self.assertNotEqual(
            self._words(fixtures.ORCHARD_PLAIN), self._words(fixtures.LIGHTHOUSE_PLAIN)
        )


class FakeProviderTest(fixtures.IsolatedCase):
    """The four fake providers: answer, empty, raise and hang."""

    query = lm.TrackQuery("Invented Artist", "Invented Title")

    def test_answering_returns_its_text_and_records_the_query(self) -> None:
        fake = fixtures.answering("words")
        self.assertEqual(fake(self.query), "words")
        self.assertEqual(fake.calls, [self.query])

    def test_empty_returns_nothing(self) -> None:
        fake = fixtures.empty()
        self.assertIsNone(fake(self.query))
        self.assertEqual(fake.calls, [self.query])

    def test_raising_raises_the_given_error(self) -> None:
        with self.assertRaisesRegex(ConnectionError, "provider down"):
            fixtures.raising(ConnectionError("provider down"))(self.query)

    def test_hanging_blocks_until_released(self) -> None:
        release = threading.Event()
        fake = fixtures.hanging(release)
        results: list[str | None] = []
        worker = threading.Thread(target=lambda: results.append(fake(self.query)))
        worker.start()
        worker.join(timeout=0.05)
        self.assertTrue(worker.is_alive())
        release.set()
        worker.join(timeout=_JOIN_S)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [None])

    def test_hanging_gives_up_on_its_own_after_its_limit(self) -> None:
        fake = fixtures.hanging(threading.Event(), limit_s=0.01)
        self.assertIsNone(fake(self.query))


if __name__ == "__main__":
    unittest.main()
