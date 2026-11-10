"""has-it.tsv read back, strictly, and the walker's real terminal, clipboard and browser.

The reader takes exactly what the Genius check writes (`sweep_genius_lists`) and
refuses anything else whole. The real seams are driven through their own injected
process runner and streams, so no clipboard, browser or terminal is touched. Every
title, URL and path is invented.
"""

from __future__ import annotations

import io
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - puts album and the libraries on sys.path
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_genius_lists as gl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_paste as sp  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_paste_seams as ss  # noqa: E402 - import follows the sys.path bootstrap above

_HEADER = "\t".join(gl.HAS_IT_COLUMNS) + "\n"
#: The longest a hung fake pipe waits for its kill, so a missed kill fails, never hangs.
_HANG_S = 5.0
#: Where the tests say each tool was found.
_PBPASTE, _OPEN = "/invented/bin/pbpaste", "/invented/bin/open"
_ROW = "/library/Invented Artist/Lanterns/01 Harbor.mp3\thttps://genius.com/x\tHarbor\tInvented Artist\n"


class ReadHasItTest(sf.IsolatedCase):
    """Only the check's own list is read; any other file is refused whole."""

    def read(self, data: bytes) -> tuple[gl.HasItRow, ...]:
        """*data* written to a file and read back as a has-it list."""
        path = self.tmp / "has-it.tsv"
        path.write_bytes(data)
        return gl.read_has_it(path)

    def test_the_check_s_own_list_reads_back(self) -> None:
        (row,) = self.read((_HEADER + _ROW).encode())
        self.assertEqual(
            row,
            gl.HasItRow(
                Path("/library/Invented Artist/Lanterns/01 Harbor.mp3"),
                "https://genius.com/x",
                "Harbor",
                "Invented Artist",
            ),
        )

    def test_a_header_alone_is_an_empty_list(self) -> None:
        self.assertEqual(self.read(_HEADER.encode()), ())

    def test_each_malformed_list_is_refused(self) -> None:
        cases = {
            "empty": b"",
            "wrong header": b"track\turl\n" + _ROW.encode(),
            "no last linefeed": (_HEADER + _ROW).encode()[:-1],
            "three fields": (_HEADER + "/a.mp3\thttps://genius.com/x\tA\n").encode(),
            "relative track": (_HEADER + _ROW.lstrip("/")).encode(),
            "empty track": (_HEADER + "\thttps://genius.com/x\tA\tB\n").encode(),
            "not UTF-8": _HEADER.encode() + b"/caf\xe9.mp3\tu\tt\ta\n",
        }
        for name, data in cases.items():
            with self.subTest(name):
                with self.assertRaises(gl.HasItError):
                    self.read(data)

    def test_a_list_past_the_bound_is_refused_unread(self) -> None:
        with mock.patch.object(gl, "MAX_HAS_IT_BYTES", 10):
            with self.assertRaisesRegex(gl.HasItError, "more than 10 bytes"):
                self.read((_HEADER + _ROW).encode())

    def test_a_link_or_a_missing_file_is_refused(self) -> None:
        target = self.tmp / "real.tsv"
        target.write_text(_HEADER, "utf-8", newline="")
        link = self.tmp / "link.tsv"
        link.symlink_to(target)
        for path in (link, self.tmp / "missing.tsv"):
            with self.subTest(path.name):
                with self.assertRaisesRegex(gl.HasItError, "cannot be read"):
                    gl.read_has_it(path)


class _Ran:
    """A stand-in for `subprocess.run`: records each call, answers as told."""

    def __init__(
        self, stdout: bytes = b"", code: int = 0, error: BaseException | None = None
    ) -> None:
        self.stdout, self.code, self.error = stdout, code, error
        self.calls: list[tuple[Sequence[str], dict[str, object]]] = []

    def __call__(
        self, argv: Sequence[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((argv, kwargs))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(argv, self.code, self.stdout, b"")


class _Pipe:
    """A pipe's read end: each read's size recorded; *hang* blocks it until a kill.

    *raises*, when given, is raised by the read, as a Ctrl-C during it would be.
    """

    def __init__(
        self,
        data: bytes,
        killed: threading.Event,
        *,
        hang: bool,
        raises: BaseException | None = None,
    ) -> None:
        self.data, self.killed, self.hang = data, killed, hang
        self.raises = raises
        self.sizes: list[int] = []
        self.closed = False

    def read(self, size: int) -> bytes:
        self.sizes.append(size)
        if self.raises is not None:
            raise self.raises
        if self.hang:
            self.killed.wait(timeout=_HANG_S)
            return b""
        return self.data[:size]

    def close(self) -> None:
        self.closed = True


class _Spawned:
    """A stand-in for a started ``pbpaste``: its output piped, its exit, its kill."""

    def __init__(
        self,
        data: bytes = b"",
        code: int = 0,
        *,
        hang: bool = False,
        stuck: bool = False,
        raises: BaseException | None = None,
    ) -> None:
        self.killed = threading.Event()
        self.stdout = _Pipe(data, self.killed, hang=hang, raises=raises)
        self.code, self.stuck = code, stuck
        self.waits: list[float | None] = []

    def kill(self) -> None:
        self.killed.set()

    def poll(self) -> int | None:
        """Running until killed, as a pbpaste the watchdog must stop."""
        return -9 if self.killed.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if self.stuck:
            raise subprocess.TimeoutExpired(["pbpaste"], timeout or 0.0)
        return self.code


class _Spawn:
    """A stand-in for `subprocess.Popen`: records each call, starts *spawned*."""

    def __init__(
        self, spawned: _Spawned | None = None, error: BaseException | None = None
    ) -> None:
        self.spawned, self.error = spawned or _Spawned(), error
        self.calls: list[tuple[Sequence[str], dict[str, object]]] = []

    def __call__(self, argv: Sequence[str], **kwargs: object) -> _Spawned:
        self.calls.append((argv, kwargs))
        if self.error is not None:
            raise self.error
        return self.spawned


class ClipboardTest(sf.IsolatedCase):
    """``pbpaste`` read bounded, as UTF-8, its failures each a `PasteInputError`."""

    def test_the_clipboard_is_read_as_utf8_with_a_timeout(self) -> None:
        spawn = _Spawn(_Spawned("café lights\n".encode()))
        self.assertEqual(ss.read_clipboard(_PBPASTE, spawn=spawn), "café lights\n")
        ((argv, kwargs),) = spawn.calls
        self.assertEqual(list(argv), [_PBPASTE, *ss.PBPASTE_ARGS])
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(spawn.spawned.waits, [ss.TOOL_TIMEOUT_S])
        self.assertTrue(spawn.spawned.stdout.closed)
        env = kwargs["env"]
        assert isinstance(env, dict)
        self.assertEqual(env["LANG"], ss.UTF8_LOCALE)

    def test_an_oversize_clipboard_is_read_no_further_than_the_bound(self) -> None:
        spawned = _Spawned(b"a" * (sp.MAX_PASTE_BYTES * 3))
        with self.assertRaisesRegex(sp.PasteInputError, "more than"):
            ss.read_clipboard(_PBPASTE, spawn=_Spawn(spawned))
        self.assertEqual(spawned.stdout.sizes, [sp.MAX_PASTE_BYTES + 1])
        self.assertTrue(spawned.killed.is_set())

    def test_a_pbpaste_that_never_ends_its_output_is_killed_at_the_bound(self) -> None:
        spawned = _Spawned(hang=True)
        with mock.patch.object(ss, "TOOL_TIMEOUT_S", 0.01):
            with self.assertRaisesRegex(sp.PasteInputError, "did not answer in time"):
                ss.read_clipboard(_PBPASTE, spawn=_Spawn(spawned))
        self.assertTrue(spawned.killed.is_set())

    def test_a_pbpaste_that_will_not_stop_is_killed_and_waited_for(self) -> None:
        spawned = _Spawned(b"x", stuck=True)
        with self.assertRaisesRegex(sp.PasteInputError, "did not stop in time"):
            ss.read_clipboard(_PBPASTE, spawn=_Spawn(spawned))
        self.assertTrue(spawned.killed.is_set())
        self.assertEqual(spawned.waits, [ss.TOOL_TIMEOUT_S, ss.REAP_TIMEOUT_S])
        self.assertTrue(spawned.stdout.closed)

    def test_a_ctrl_c_during_the_read_kills_and_waits_for_pbpaste(self) -> None:
        spawned = _Spawned(raises=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            ss.read_clipboard(_PBPASTE, spawn=_Spawn(spawned))
        self.assertTrue(spawned.killed.is_set())
        self.assertEqual(spawned.waits, [ss.REAP_TIMEOUT_S])
        self.assertTrue(spawned.stdout.closed)

    def test_each_unusable_clipboard_is_refused(self) -> None:
        cases = {
            "empty": _Spawn(_Spawned(b"")),
            "failed": _Spawn(_Spawned(b"x", code=1)),
            "not UTF-8": _Spawn(_Spawned(b"caf\xe9")),
            "too large": _Spawn(_Spawned(b"a" * (sp.MAX_PASTE_BYTES + 1))),
            "not stopping": _Spawn(_Spawned(b"x", stuck=True)),
            "not started": _Spawn(error=FileNotFoundError("pbpaste")),
        }
        for name, spawn in cases.items():
            with self.subTest(name):
                with self.assertRaises(sp.PasteInputError):
                    ss.read_clipboard(_PBPASTE, spawn=spawn)

    def test_a_real_process_s_output_is_read_bounded(self) -> None:
        # A real child at the process boundary, never pbpaste: the bound holds there.
        script = "import sys; sys.stdout.write('a' * 5000); sys.stdout.flush()"
        argv = (sys.executable, "-c", script)
        self.assertEqual(ss.read_bounded(argv, 10_000), (0, b"a" * 5000))
        code, data = ss.read_bounded(argv, 100)
        self.assertEqual(len(data), 101)


class OpenTest(sf.IsolatedCase):
    """``open`` is handed the URL alone; a failure is said, never raised."""

    def test_the_url_is_opened_by_open(self) -> None:
        ran = _Ran()
        ss.open_in_browser(_OPEN, "https://genius.com/x", run=ran)
        ((argv, kwargs),) = ran.calls
        self.assertEqual(list(argv), [_OPEN, "https://genius.com/x"])
        self.assertEqual(kwargs["timeout"], ss.TOOL_TIMEOUT_S)

    def test_a_failed_open_is_said(self) -> None:
        timeout = subprocess.TimeoutExpired(["open"], 1.0)
        for ran in (_Ran(code=1), _Ran(error=OSError("no open")), _Ran(error=timeout)):
            with self.subTest(code=ran.code, error=ran.error):
                with self.assertRaises(sp.PasteInputError):
                    ss.open_in_browser(_OPEN, "https://genius.com/x", run=ran)


class TerminalTest(sf.IsolatedCase):
    """The prompt goes to stderr and its answer comes from stdin; both must be ttys."""

    def test_the_answer_is_one_line_its_break_dropped(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", io.StringIO(" s \nlater\n")),
            mock.patch.object(sys, "stderr", err),
        ):
            self.assertEqual(ss.ask_on_terminal("go? "), "s")
        self.assertEqual(err.getvalue(), "go? ")

    def test_the_end_of_input_is_eof(self) -> None:
        with (
            mock.patch.object(sys, "stdin", io.StringIO("")),
            mock.patch.object(sys, "stderr", io.StringIO()),
        ):
            with self.assertRaises(EOFError):
                ss.ask_on_terminal("go? ")
