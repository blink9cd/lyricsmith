"""Shared fixtures for the Genius check's suites: tagged tracks, a list, and a run.

`CheckCase` builds an invented library of tagged MP3s and the plain track list naming
them, and runs ``--mode genius-check`` over it with a scripted search client
(`genius_search_fakes.FakeSearchClient`) on a fake clock, so the pacing and retries take
no time and no request leaves the process. Every title, artist and key is invented.
`CtrlCClient` cuts a search short with a real Ctrl-C, handed to the main thread under
`interrupt_taken`'s handler, so the interrupt lands at the same point on every run.

Not discovered as a suite (no ``test_`` prefix).
"""

from __future__ import annotations

import contextlib
import io
import os
import signal
import sys
import threading
import types
import unittest
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from mutagen.id3 import ID3, TIT2, TPE1

import sweep
import sweep_fixtures as sf

sys.path.append(str(sweep.ALBUM_ENTRY.parent / "tests"))

import genius_fakes as gf  # noqa: E402 - import follows the sys.path insert above
import genius_search_fakes as gsf  # noqa: E402 - import follows the sys.path insert above
import lyrics_changed  # noqa: E402 - import follows the sys.path insert above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path insert above
import sweep_genius_seams as gs  # noqa: E402 - import follows the sys.path insert above
from lyrics_config import GENIUS_TOKEN_ENV  # noqa: E402 - follows the sys.path insert
from lyrics_model import TrackTags  # noqa: E402 - follows the sys.path insert

#: The invented key a check runs with: an obvious fake, never sent anywhere.
TOKEN = gf.FAKE_TOKEN


def tagged_track(album: Path, name: str, artist: str, title: str) -> Path:
    """An empty MP3 named *name* under *album*, tagged with *artist* and *title*."""
    mp3 = sf.add_track(album, name)
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.add(TIT2(encoding=3, text=[title]))
    tags.save(mp3)
    return mp3


def _no_child(*_args: object, **_kwargs: object) -> NoReturn:
    """The child runner a check is wired with: it runs no child, so any call fails."""
    raise AssertionError("genius-check runs no child")


@dataclass(frozen=True)
class Result:
    """What one check returned and printed."""

    code: int
    stdout: str
    stderr: str


class CheckCase(sf.IsolatedCase):
    """A library, a track list naming its tracks, and a way to run the check over it."""

    def setUp(self) -> None:
        """Make the out folder's path, the list's path and the fake clock."""
        super().setUp()
        self.out = self.tmp / "checked"
        self.list_path = self.tmp / "held.txt"
        self.clock = gf.FakeClock()
        self.built: list[lr.Secret] = []
        #: How the check reads a track's tags: the real reader unless a test swaps it.
        self.read_tags: Callable[[Path], TrackTags] = gs.real_seams().read_tags
        os.environ[GENIUS_TOKEN_ENV] = TOKEN

    def write_list(self, *tracks: Path) -> None:
        """Write the track list naming *tracks*, in order."""
        data, _ = lyrics_changed.format_changed_list(str(track) for track in tracks)
        self.list_path.write_bytes(data)

    def seams(self, client: gsf.FakeSearchClient) -> gs.CheckSeams:
        """The check's seams: *client* for any key, the fake clock, *read_tags*."""
        client.clock = self.clock

        def build(secret: lr.Secret) -> gsf.FakeSearchClient:
            self.built.append(secret)
            return client

        return gs.CheckSeams(
            environ=os.environ,
            clock=self.clock,
            sleeper=self.clock.sleep,
            now=gs.real_seams().now,
            build_client=build,
            read_tags=self.read_tags,
        )

    def argv(self, *extra: str) -> list[str]:
        """The command's options: the mode, the list, the out folder and *extra*."""
        return [
            "--library",
            str(self.library),
            "--mode",
            "genius-check",
            "--tracks-from",
            str(self.list_path),
            "--out",
            str(self.out),
            *extra,
        ]

    def run_check(self, client: gsf.FakeSearchClient, *extra: str) -> Result:
        """Run the check on *client* through the entry point; what it gave."""
        out, err = io.StringIO(), io.StringIO()
        wiring = sweep.Wiring(
            _no_child, sf.FakeSleep(), sf.fixed_now, genius=self.seams(client)
        )
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sweep.main(self.argv(*extra), wiring=wiring)
        return Result(code, out.getvalue(), err.getvalue())

    def read_out(self, name: str) -> str:
        """The out folder's file *name*, as text."""
        return (self.out / name).read_text(encoding="utf-8")


#: How long the scripted Ctrl-C waits for the main thread to take it. A bound on a
#: broken handoff only: the wait ends the moment the handler runs.
_TAKEN_LIMIT_S = 10.0
#: Whether a thread here can send Ctrl-C's signal to the main one: Windows has no
#: `signal.pthread_kill`, and a join blocked there does not wake for a signal.
HOST_SIGNALS_THREADS = sys.platform != "win32"
NO_THREAD_SIGNAL = "a thread cannot send SIGINT to the main one here (Windows)"


def interrupt_taken(case: unittest.TestCase) -> threading.Event:
    """Python's own Ctrl-C handler for *case*'s run, telling the event once it ran.

    Installed whatever SIGINT was on entry, so a run whose shell ignores SIGINT (a
    background job) still turns it into `KeyboardInterrupt`, and restored after.
    """
    # A handler setting an event can hang on a lock its own thread holds, when the main
    # thread is inside that event's wait: the handler runs on the main thread, between
    # bytecodes of `Event.wait`, which may hold the event's own non-reentrant lock. Only
    # the search's thread ever waits on this one, so the handler never meets its own lock.
    taken = threading.Event()

    def take(signum: int, frame: types.FrameType | None) -> None:
        taken.set()
        signal.default_int_handler(signum, frame)

    case.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, take))
    return taken


class CtrlCClient(gsf.FakeSearchClient):
    """A scripted client whose second search is cut short by a real Ctrl-C."""

    def __init__(self, script: list[gsf.Step], *, taken: threading.Event) -> None:
        """Play *script*; *taken* is set once the main thread has handled SIGINT."""
        super().__init__(script)
        self.taken = taken
        # Made on the test's own thread, the main one, where Python handles signals.
        self.main_thread = threading.get_ident()

    def search_songs(
        self, search_term: str, per_page: int | None = None, page: int | None = None
    ) -> Mapping[str, object]:
        """Play the next step; on the second, Ctrl-C the main thread before answering.

        The search runs on the watchdog's thread. The signal is sent to the main one,
        so its wait on this search is cut short there, as a real Ctrl-C's is, and this
        search answers only once the handler has run: the interrupt always lands while
        the check waits, never after it holds an answer.
        """
        reply = super().search_songs(search_term, per_page, page)
        if len(self.calls) == 2:
            if sys.platform == "win32":
                # Only Windows reaches this: the case is skipped there, so the line
                # runs only if the skip is lost, and then fails loudly, never hangs.
                raise AssertionError(NO_THREAD_SIGNAL)  # pragma: only-on win32
            signal.pthread_kill(self.main_thread, signal.SIGINT)
            if not self.taken.wait(_TAKEN_LIMIT_S):
                raise AssertionError("the main thread never handled the Ctrl-C")
        return reply
