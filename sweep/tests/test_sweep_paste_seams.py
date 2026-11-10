"""The paste walker's tools: run by the paths found, keyless, UTF-8, timed out honestly.

``pbpaste`` and ``open`` are spawned
by the absolute paths `found_tools` resolved, never by a bare name through ``PATH``;
neither they nor the paste child receive the Genius key; ``pbpaste`` runs under a
UTF-8 locale that ``LC_ALL`` cannot override; and a watchdog that fires after the read
ended cannot turn a finished read into a timeout. Every process here is a fake, and
every path invented.
"""

from __future__ import annotations

import functools
import io
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - puts album and the libraries on sys.path
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_paste_seams as ss  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_config import GENIUS_TOKEN_ENV  # noqa: E402 - follows the bootstrap above

#: Where the fakes say each tool is.
_PBPASTE = "/invented/bin/pbpaste"
_OPEN = "/invented/bin/open"
_FOUND = {"pbpaste": _PBPASTE, "open": _OPEN}
_INVENTED_KEY = "invented-genius-key"


class _Pipe:
    """A pipe that hands *data* back at once."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self, size: int) -> bytes:
        return self.data[:size]

    def close(self) -> None:
        """Nothing to close."""


class _Process:
    """A started tool that wrote *data* and exited 0."""

    def __init__(
        self, data: bytes = b"lyrics\n", on_exit: Callable[[], None] | None = None
    ) -> None:
        self.stdout = _Pipe(data)
        self.kills = 0
        self.on_exit = on_exit
        self.exited = False

    def kill(self) -> None:
        self.kills += 1

    def poll(self) -> int | None:
        return 0 if self.exited else None

    def wait(self, timeout: float | None = None) -> int:
        # The process has exited; *on_exit* runs before the wait hands that back.
        self.exited = True
        if self.on_exit is not None:
            self.on_exit()
        return 0


class _Spawn:
    """A stand-in for `subprocess.Popen`, recording each argv and its keywords."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv: Sequence[str], **kwargs: object) -> _Process:
        self.calls.append((list(argv), kwargs))
        return _Process()


def _ran(calls: list[tuple[list[str], dict[str, object]]]) -> ss.Run:
    """A stand-in for `subprocess.run` recording into *calls*, each run exiting 0."""

    def run(
        argv: Sequence[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    return run


class _LateTimer:
    """A watchdog that fires only as it is canceled, a read and its watchdog's race.

    `threading.Timer.cancel` cannot stop a function already running, so a timer that
    came due just as the read ended runs its function after the read's own end.
    """

    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        self.interval, self.function = interval, function

    def start(self) -> None:
        """Never fires on its own."""

    def cancel(self) -> None:
        self.function()


class _Idle:
    """A watchdog that never fires on its own and is canceled quietly."""

    def start(self) -> None:
        """Never fires."""

    def cancel(self) -> None:
        """Nothing to stop."""


class FoundToolsTest(sf.IsolatedCase):
    """Each tool is found once, made absolute, and run by that path alone."""

    def test_each_tool_is_resolved_to_an_absolute_path(self) -> None:
        relative: dict[str, str] = {"pbpaste": "bin/pbpaste", "open": _OPEN}
        tools = ss.found_tools(which=relative.get)
        self.assertEqual(tools.pbpaste, os.path.abspath("bin/pbpaste"))
        self.assertEqual(tools.opener, _OPEN)
        none: dict[str, str] = {}
        self.assertEqual(ss.found_tools(which=none.get), ss.Tools(None, None))

    def test_the_clipboard_is_read_by_the_path_found(self) -> None:
        spawn = _Spawn()
        self.assertEqual(ss.read_clipboard(_PBPASTE, spawn=spawn), "lyrics\n")
        ((argv, _),) = spawn.calls
        self.assertEqual(argv, [_PBPASTE, *ss.PBPASTE_ARGS])

    def test_the_page_is_opened_by_the_path_found(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        ss.open_in_browser(_OPEN, "https://genius.com/x", run=_ran(calls))
        ((argv, _),) = calls
        self.assertEqual(argv, [_OPEN, "https://genius.com/x"])

    def test_a_tool_not_found_is_never_run(self) -> None:
        spawn = _Spawn()
        calls: list[tuple[list[str], dict[str, object]]] = []
        with self.assertRaisesRegex(ss.PasteInputError, "pbpaste is not on PATH"):
            ss.read_clipboard(None, spawn=spawn)
        with self.assertRaisesRegex(ss.PasteInputError, "open is not on PATH"):
            ss.open_in_browser(None, "https://genius.com/x", run=_ran(calls))
        self.assertEqual((spawn.calls, calls), ([], []))

    def test_the_real_seams_run_the_paths_found_at_their_making(self) -> None:
        seams = ss.real_seams(which=_FOUND.get)
        bound = {
            seams.clipboard: (ss.read_clipboard, (_PBPASTE,)),
            seams.open_url: (ss.open_in_browser, (_OPEN,)),
        }
        for seam, (function, args) in bound.items():
            assert isinstance(seam, functools.partial)
            self.assertEqual((seam.func, seam.args), (function, args))
        self.assertIs(seams.ask, ss.ask_on_terminal)

    def test_ready_names_a_missing_tool_after_the_terminal(self) -> None:
        class Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        with (
            mock.patch.object(sys, "stdin", Tty()),
            mock.patch.object(sys, "stdout", Tty()),
        ):
            self.assertIsNone(ss.terminal_ready(ss.Tools(_PBPASTE, _OPEN)))
            self.assertIn("pbpaste", ss.terminal_ready(ss.Tools(None, _OPEN)) or "")
            self.assertIn("open", ss.terminal_ready(ss.Tools(_PBPASTE, None)) or "")
            self.assertIsNone(ss.real_seams(which=_FOUND.get).ready())
        with mock.patch.object(sys, "stdin", io.StringIO()):
            ready = ss.terminal_ready(ss.Tools(_PBPASTE, _OPEN))
            self.assertIn("terminal", ready or "")


class KeylessEnvTest(sf.IsolatedCase):
    """No paste-side child is handed the Genius key; ``pbpaste`` writes UTF-8."""

    def test_the_key_is_left_out_and_the_rest_kept(self) -> None:
        environ = {GENIUS_TOKEN_ENV: _INVENTED_KEY, "PATH": "/invented/bin"}
        self.assertEqual(ss.keyless_env(environ), {"PATH": "/invented/bin"})

    def test_the_tools_get_no_key_and_a_utf8_locale_lc_all_cannot_undo(self) -> None:
        spawn = _Spawn()
        calls: list[tuple[list[str], dict[str, object]]] = []
        outer = {GENIUS_TOKEN_ENV: _INVENTED_KEY, "LC_ALL": "C", "LC_CTYPE": "C"}
        with mock.patch.dict(os.environ, outer):
            ss.read_clipboard(_PBPASTE, spawn=spawn)
            ss.open_in_browser(_OPEN, "https://genius.com/x", run=_ran(calls))
        for env in (spawn.calls[0][1]["env"], calls[0][1]["env"]):
            assert isinstance(env, dict)
            # Membership alone, so a failure never prints the environment.
            self.assertFalse(GENIUS_TOKEN_ENV in env, "a tool was handed the key")
            for name in ("LANG", "LC_ALL", "LC_CTYPE"):
                self.assertEqual(env[name], ss.UTF8_LOCALE, name)


class WatchdogRaceTest(sf.IsolatedCase):
    """A watchdog firing after the read ended is too late to fail it."""

    def test_a_watchdog_due_as_the_read_ends_leaves_the_read_good(self) -> None:
        process = _Process(b"lyrics\n")
        got = ss.read_bounded(
            ("tool",), 100, lambda *_a, **_k: process, timer=_LateTimer
        )
        self.assertEqual(got, (0, b"lyrics\n"))
        self.assertEqual(process.kills, 0)

    def test_a_watchdog_due_as_the_wait_returns_leaves_the_read_good(self) -> None:
        fired: list[Callable[[], None]] = []

        def timer(interval: float, function: Callable[[], None]) -> _Idle:
            fired.append(function)
            return _Idle()

        # Fires once the process has exited, before its exit is recorded as the end.
        process = _Process(b"lyrics\n", on_exit=lambda: fired[0]())
        got = ss.read_bounded(("tool",), 100, lambda *_a, **_k: process, timer=timer)
        self.assertEqual(got, (0, b"lyrics\n"))
        self.assertEqual(process.kills, 0)
