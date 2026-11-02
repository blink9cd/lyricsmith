"""Shared test fixtures for the album tool: isolation, silent MP3s, invented lyrics, fakes.

Every album test case derives from `IsolatedCase`, and the tool folder's test
aggregator fails the run when one does not (`isolation_offenders`). Its `setUp` points
HOME and the XDG directories at a fresh temporary directory, unsets the Genius key
variable, and replaces the socket layer's connect and name lookup with a tripwire. So no
test can reach a real music library, the network, or a real config file, even by mistake.
Before the isolation is left, each test waits for the threads it started to end, and
fails if one is still running or one reached the tripwire (`unsettled`): a provider call
abandoned at its deadline must finish while the fakes it was given still stand.

Albums are built from silent MP3s at test time: no audio binary is committed. Every
lyric here is invented for these tests; no real song's words appear anywhere.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import unittest
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NoReturn
from unittest import mock

from mutagen.id3 import ID3, TCON, TIT2, TPE1

import lyrics_fetch
import lyrics_model as lm
import lyrics_silence

#: The Genius key variable, unset inside every test.
GENIUS_TOKEN_ENV = "GENIUS_ACCESS_TOKEN"
#: The XDG base directories redirected under the temporary HOME, by subdirectory.
_XDG_DIRS = {
    "XDG_CONFIG_HOME": ".config",
    "XDG_CACHE_HOME": ".cache",
    "XDG_DATA_HOME": ".local/share",
    "XDG_STATE_HOME": ".local/state",
}

# --- Isolation ---------------------------------------------------------------------


class NetworkTouched(AssertionError):
    """A test reached for the network; an AssertionError, so it fails the test."""


@dataclass
class Tripwire:
    """Stands in for a network call: fails the call that made it, naming the target.

    A hit on a thread other than ``home_thread``, the one that set it, is remembered in
    ``stray``: such a thread is usually a provider call the watchdog abandoned, whose
    error the watchdog swallows, so the hit would otherwise go unseen.
    """

    home_thread: threading.Thread = field(default_factory=threading.current_thread)
    stray: list[str] = field(default_factory=list)

    def __call__(self, *args: object, **kwargs: object) -> NoReturn:
        """Raise `NetworkTouched`, remembering a hit off the home thread."""
        message = f"test reached the network: {args!r} {kwargs!r}"
        if threading.current_thread() is not self.home_thread:
            self.stray.append(message)
        raise NetworkTouched(message)


@dataclass(frozen=True)
class IsolatedPaths:
    """Where an isolation pointed HOME and each XDG base directory, by variable name.

    ``tripwire`` is the stand-in the isolation put in front of the socket layer.
    """

    home: Path
    xdg: Mapping[str, Path]
    tripwire: Tripwire = field(default_factory=Tripwire)


@contextmanager
def isolated_environment(root: Path) -> Iterator[IsolatedPaths]:
    """Redirect HOME and XDG under *root*, unset the Genius key, trip the network, hide ffmpeg.

    Yields the paths it set, the very values written into the environment, so a test
    can assert on them without reading the environment back by name. Everything is
    restored on exit, the socket layer included, so the redirect cannot leak into a
    later test.
    """
    paths = IsolatedPaths(
        root / "home",
        {name: root / "home" / sub for name, sub in _XDG_DIRS.items()},
    )
    paths.home.mkdir()
    values = {"HOME": str(paths.home)}
    values.update({name: str(path) for name, path in paths.xdg.items()})
    with ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, values))
        os.environ.pop(GENIUS_TOKEN_ENV, None)
        for name in ("connect", "connect_ex"):
            stack.enter_context(mock.patch.object(socket.socket, name, paths.tripwire))
        stack.enter_context(mock.patch.object(socket, "getaddrinfo", paths.tripwire))
        # Every fixture MP3 is silent: a real ffmpeg would change outcomes, so no test
        # reaches one except the live suite, which asks shutil.which itself.
        stack.enter_context(
            mock.patch.object(lyrics_silence, "find_ffmpeg", return_value=None)
        )
        yield paths


def unsettled(
    before: frozenset[threading.Thread], tripwire: Tripwire, limit_s: float
) -> list[str]:
    """What a finished test left behind: threads still running, and stray network hits.

    Waits up to *limit_s* in all for every thread not in *before* to end, so a call
    abandoned at its deadline finishes while the caller's fakes and tripwire still
    stand, then names each thread still running and each hit off the test's thread.
    """
    deadline = time.monotonic() + limit_s
    current = threading.current_thread()
    started = [t for t in threading.enumerate() if t not in before | {current}]
    for thread in started:
        thread.join(max(0.0, deadline - time.monotonic()))
    running = [f"thread still running: {t.name}" for t in started if t.is_alive()]
    return running + [f"stray network call: {hit}" for hit in tripwire.stray]


class IsolatedCase(unittest.TestCase):
    """The base of every album test case: each test runs isolated.

    ``self.tmp`` is the test's own temporary directory, removed when it ends,
    ``self.home`` the redirected HOME inside it, and ``self.xdg`` each redirected XDG
    base directory by variable name.
    """

    tmp: Path
    home: Path
    xdg: Mapping[str, Path]

    def setUp(self) -> None:
        """Create the temporary directory and enter the isolation for this test."""
        super().setUp()
        self.tmp = Path(self.enterContext(TemporaryDirectory())).resolve()
        paths = self.enterContext(isolated_environment(self.tmp))
        self.home, self.xdg = paths.home, paths.xdg
        # Registered after the isolation, so it runs before the isolation is left, and
        # after any cleanup the test adds, such as releasing a blocked fake.
        before = frozenset(threading.enumerate())
        self.addCleanup(self._assert_settled, before, paths.tripwire)

    def _assert_settled(
        self, before: frozenset[threading.Thread], tripwire: Tripwire
    ) -> None:
        """Fail the test when it left a thread running or a stray network hit."""
        problems = unsettled(before, tripwire, HANG_LIMIT_S)
        if problems:
            self.fail("; ".join(problems))


def tests_in(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    """Every test case in *suite*, nested suites flattened."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from tests_in(item)
        else:
            yield item


def isolation_offenders(suite: unittest.TestSuite) -> list[str]:
    """The ids of tests in *suite* that do not derive from `IsolatedCase`.

    A suite that failed to import is skipped: unittest already reports it as a failing
    test of its own, with the real error, which a second complaint here would bury.
    """
    return [
        test.id()
        for test in tests_in(suite)
        if not isinstance(test, IsolatedCase)
        and type(test).__module__ != unittest.loader.__name__
    ]


# --- Silent MP3s and albums --------------------------------------------------------

#: MPEG-1 Layer III, 128 kbps, 44.1 kHz, mono, no CRC: the four header bytes, with the
#: padding bit (0x02 in the third) clear. Every frame carries 1152 samples.
_HEADER = (0xFF, 0xFB, 0x90, 0xC0)
_PADDING_BIT = 0x02
_SAMPLES_PER_FRAME = 1152
_SAMPLE_RATE = 44_100
_BITRATE = 128_000
#: A Layer III frame is 144 * bitrate / sample_rate bytes; the fraction is paid in
#: padded frames, so the average frame size, and the duration read from it, is exact.
_FRAME_NUMERATOR = 144 * _BITRATE


def _frame_padded(index: int) -> bool:
    """Whether frame *index* takes the padding byte, spreading the fraction evenly."""
    before = index * _FRAME_NUMERATOR % _SAMPLE_RATE
    return before + _FRAME_NUMERATOR % _SAMPLE_RATE >= _SAMPLE_RATE


def _silent_frames(seconds: float) -> bytes:
    """Enough zero-payload MPEG frames to play for *seconds*."""
    count = round(seconds * _SAMPLE_RATE / _SAMPLES_PER_FRAME)
    base = _FRAME_NUMERATOR // _SAMPLE_RATE
    frames = bytearray()
    for index in range(count):
        padded = _frame_padded(index)
        header = bytearray(_HEADER)
        header[2] |= _PADDING_BIT if padded else 0
        frames += header + bytes(base + padded - len(header))
    return bytes(frames)


def build_silent_mp3(
    path: Path,
    seconds: float,
    artist: str | None = "Invented Artist",
    title: str | None = "Invented Title",
    *,
    id3_version: tuple[int, int] | None = (2, 4),
    genres: tuple[str, ...] = (),
) -> Path:
    """Write a silent MP3 of *seconds* at *path*, tagged through mutagen; return *path*.

    A None *artist* or *title* leaves that frame out; a None *id3_version* writes no
    ID3 header at all. *genres* are written as one TCON frame when given.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_silent_frames(seconds))
    if id3_version is not None:
        tags = ID3()
        if artist is not None:
            tags.add(TPE1(encoding=3, text=[artist]))
        if title is not None:
            tags.add(TIT2(encoding=3, text=[title]))
        if genres:
            tags.add(TCON(encoding=3, text=list(genres)))
        tags.save(path, v2_version=id3_version[1])
    return path


@dataclass(frozen=True)
class TrackSpec:
    """One track of a built album: where it goes, how long, its tags and sidecars."""

    relpath: str
    seconds: float = 30.0
    artist: str | None = "Invented Artist"
    title: str | None = "Invented Title"
    lrc: str | None = None
    txt: str | None = None
    id3_version: tuple[int, int] | None = (2, 4)
    genres: tuple[str, ...] = ()


def build_album(album: Path, tracks: tuple[TrackSpec, ...]) -> list[Path]:
    """Build *tracks* under *album*, sidecars beside them; return the MP3s sorted."""
    paths: list[Path] = []
    for track in tracks:
        mp3 = album / track.relpath
        build_silent_mp3(
            mp3,
            track.seconds,
            track.artist,
            track.title,
            id3_version=track.id3_version,
            genres=track.genres,
        )
        for suffix, text in ((".lrc", track.lrc), (".txt", track.txt)):
            if text is not None:
                mp3.with_suffix(suffix).write_text(text, encoding="utf-8", newline="")
        paths.append(mp3)
    return sorted(paths)


# --- Invented lyrics -----------------------------------------------------------------

#: Synced, with a tag line and a stamp-only break, so a parser has something to skip.
LIGHTHOUSE_LRC = """[ti:The Keeper Counts]
[00:01.00]The keeper climbs at half past four
[00:05.50]and counts the ships along the shore,
[00:10.00]one with a red sail, two with none,
[00:14.25]three running home before the sun.
[00:19.00]
[00:21.00]He writes them down in pencil gray,
[00:25.75]then rubs the harbor out by day.
"""

#: An invented transcript as the transcribe tool writes one: its two header lines, then
#: timed lines. The version numbers are invented too.
MACHINE_LRC = (
    "[re:lyricsmith transcribe 0.0.0; whisper.cpp 0.0.0; ggml-invented]\n"
    "[by:machine transcript]\n"
    "[00:01.000]A gull on the railing\n"
    "[00:04.000]counts the boats coming in\n"
)

#: A .lrc the sync tool would write, labeled `[by:machine timed]`, with invented lines.
MACHINE_TIMED_LRC = (
    "[re:lyricsmith sync 0.0.0; demucs 0.0.0 htdemucs; "
    "torchaudio 0.0.0 WAV2VEC2_ASR_BASE_960H]\n"
    "[by:machine timed]\n"
    "[00:01.000]A gull on the railing\n"
    "[00:04.000]counts the boats coming in\n"
)

#: LIGHTHOUSE_LRC's words with the stamps and the tag line gone.
LIGHTHOUSE_PLAIN = """The keeper climbs at half past four
and counts the ships along the shore,
one with a red sail, two with none,
three running home before the sun.

He writes them down in pencil gray,
then rubs the harbor out by day."""

#: LIGHTHOUSE_PLAIN as a second transcription might give it: same words, other
#: punctuation and capitals. The similarity check must call the two alike.
LIGHTHOUSE_EDITED = """the Keeper climbs at half past four...
And counts the ships along the shore
One with a red sail - two with none
Three running home before the sun!

He writes them down in pencil gray;
Then rubs the harbor out by day"""

#: A different invented song. The similarity check must call it unlike the other.
ORCHARD_PLAIN = """Under the orchard wall we lay
and named the clouds for towns away,
a kettle, a bridge, a sleeping hound,
till the bell from the mill came round."""


# --- Fake providers ------------------------------------------------------------------

#: How long a hanging fake waits before giving up on its own, so a forgotten release
#: cannot leave a thread alive past the test run.
HANG_LIMIT_S = 10.0


@dataclass
class FakeProvider:
    """A provider stand-in: records each query, then answers, raises, or hangs."""

    answer: str | None = None
    error: Exception | None = None
    release: threading.Event | None = None
    limit_s: float = HANG_LIMIT_S
    calls: list[lm.TrackQuery] = field(default_factory=list)

    def __call__(self, query: lm.TrackQuery) -> str | None:
        """Record *query*, then behave as configured."""
        self.calls.append(query)
        if self.release is not None:
            self.release.wait(timeout=self.limit_s)
        if self.error is not None:
            raise self.error
        return self.answer


def answering(text: str) -> FakeProvider:
    """A provider that answers *text*."""
    return FakeProvider(answer=text)


def empty() -> FakeProvider:
    """A provider that finds nothing."""
    return FakeProvider()


def raising(error: Exception) -> FakeProvider:
    """A provider that raises *error*."""
    return FakeProvider(error=error)


def hanging(release: threading.Event, limit_s: float = HANG_LIMIT_S) -> FakeProvider:
    """A provider that blocks until *release* is set, or *limit_s* passes, then finds nothing."""
    return FakeProvider(release=release, limit_s=limit_s)


def chain_table(
    overrides: Mapping[lm.ProviderKey, FakeProvider] | None = None,
) -> dict[lm.ProviderKey, FakeProvider]:
    """Every chain step finding nothing, except the steps *overrides* names."""
    table = {key: empty() for key in lyrics_fetch.CHAIN}
    table.update(overrides or {})
    return table


def synced_lines(*pairs: tuple[int, str]) -> lm.SyncedLyrics:
    """Synced lyrics of ``(ms, text)`` *pairs*, in the order given."""
    return lm.SyncedLyrics(tuple(lm.SyncedLine(ms, text) for ms, text in pairs))


def synced_lrc(text: str) -> lm.SyncedLyrics:
    """*text* parsed as LRC, narrowed to the synced lyrics a caller gives it."""
    lyrics = lm.parse_lrc(text)
    assert isinstance(lyrics, lm.SyncedLyrics)  # every caller passes timed text
    return lyrics
