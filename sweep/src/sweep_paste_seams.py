"""The paste walker's reach outside the process: the terminal, the clipboard, the browser.

`PasteSeams` holds the four things ``sweep --mode paste`` (`sweep_paste`) does that a
test must never do for real, and `real_seams` binds the real ones, macOS's own tools:

- `terminal_ready` says why the walk cannot prompt: stdin or stdout not a terminal, or
  ``pbpaste`` or ``open`` not found on ``PATH``; None when it can.
- `ask_on_terminal` writes a prompt to stderr, so the walk's summary alone reaches
  stdout, and reads one line from stdin, stripped; the end of input is `EOFError`.
- `read_clipboard` runs ``pbpaste`` for the clipboard's plain text and decodes it as
  strict UTF-8 under a UTF-8 locale (``LANG``, ``LC_ALL`` and ``LC_CTYPE`` all set,
  since the last two override the first), as ``pbpaste`` encodes by the locale. Its output
  is read by `read_bounded`: no more than one byte past the paste's own bound
  (`lyrics_paste_mode.MAX_PASTE_BYTES`), so a huge clipboard is never held whole, and
  under a watchdog that kills ``pbpaste`` at `TOOL_TIMEOUT_S`. Empty, failed,
  undecodable, too large or too slow, it is `PasteInputError`.
- `open_in_browser` hands the URL to ``open``, bounded the same way; a failure is
  `PasteInputError`, which the walker says, so the page can still be opened by hand.

**The tools are found once.** `found_tools` resolves ``pbpaste`` and ``open`` on
``PATH`` when the seams are made (`real_seams`), each made absolute, and every spawn
runs the path found, never the bare name: a same-named tool earlier on ``PATH`` later
is never run. Each runs on `keyless_env`, this process's environment without the
Genius key, which neither needs; the walker hands its album tool child the same.

Nothing here makes a request: the browser is the person's, and lyricsmith asks no host.
Each process seam takes the runner it calls (*run*, or *spawn* for the clipboard), so
the suites drive it with a fake.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from typing import Final, Protocol, cast

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_config import GENIUS_TOKEN_ENV
from lyrics_paste_mode import MAX_PASTE_BYTES
from stdio_utf8 import ENCODING

#: The longest ``pbpaste`` or ``open`` may take: each answers at once on a working desktop.
TOOL_TIMEOUT_S: Final = 30.0
#: The clipboard's tool, and its options for the plain text.
PBPASTE_NAME: Final = "pbpaste"
PBPASTE_ARGS: Final = ("-Prefer", "txt")
#: The longest a killed ``pbpaste`` is waited for: a killed process ends at once.
REAP_TIMEOUT_S: Final = 5.0
#: The tool that opens a URL in the person's default browser.
OPEN_NAME: Final = "open"
#: The locale ``pbpaste`` runs under, so it writes UTF-8.
UTF8_LOCALE: Final = "en_US.UTF-8"
#: The locale variables set to it: ``LC_ALL`` and ``LC_CTYPE`` each override ``LANG``.
_LOCALE_VARIABLES: Final = ("LANG", "LC_ALL", "LC_CTYPE")


class PasteInputError(Exception):
    """A clipboard or a browser that did not do what was asked; its message says why."""


@dataclass(frozen=True)
class PasteSeams:
    """What the walker reaches outside itself, each replaceable by a test."""

    ready: Callable[[], str | None]
    ask: Callable[[str], str]
    clipboard: Callable[[], str]
    open_url: Callable[[str], None]


@dataclass(frozen=True)
class Tools:
    """Where ``pbpaste`` and ``open`` were found, each absolute; None when not found."""

    pbpaste: str | None
    opener: str | None


#: The part of `subprocess.run` the seams call: *argv* run to its end, output captured.
#: A callable alias, not a Protocol, so the overloaded `subprocess.run` is one.
Run = Callable[..., "subprocess.CompletedProcess[bytes]"]


class Pipe(Protocol):
    """The part of a process's output pipe `read_bounded` reads."""

    def read(self, size: int, /) -> bytes:
        """At most *size* bytes, fewer only at the pipe's end."""

    def close(self) -> None:
        """Close the pipe."""


class Process(Protocol):
    """The part of `subprocess.Popen` `read_bounded` uses."""

    @property
    def stdout(self) -> Pipe | None:
        """The process's output pipe, None when it was not piped."""

    def kill(self) -> None:
        """Stop the process at once."""

    def poll(self) -> int | None:
        """The process's exit, None while it runs."""

    def wait(self, timeout: float | None = None) -> int:
        """The process's exit, waited for no longer than *timeout*."""


#: How the clipboard's read starts a process: *argv*, then `subprocess.Popen`'s keywords.
Spawn = Callable[..., Process]


class Timer(Protocol):
    """The part of `threading.Timer` `read_bounded`'s watchdog uses."""

    def start(self) -> None:
        """Start counting down."""

    def cancel(self) -> None:
        """Stop it firing, if it has not started to."""


#: How the watchdog is made: its interval in seconds and what it calls when due.
MakeTimer = Callable[[float, Callable[[], None]], Timer]


def _absolute(path: str | None) -> str | None:
    """*path* made absolute, so a relative ``PATH`` entry is pinned to where it was."""
    return None if path is None else os.path.abspath(path)


def found_tools(which: Callable[[str], str | None] = shutil.which) -> Tools:
    """Where *which* finds ``pbpaste`` and ``open``, each made absolute."""
    return Tools(_absolute(which(PBPASTE_NAME)), _absolute(which(OPEN_NAME)))


def _not_found(name: str) -> str:
    """Why a tool not found stops the walk."""
    return f"{name} is not on PATH: the walk needs macOS's {name}"


def terminal_ready(tools: Tools) -> str | None:
    """Why the walk cannot prompt here, None when it can; *tools* as found."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return "it needs a terminal: stdin and stdout must both be one"
    for name, path in ((PBPASTE_NAME, tools.pbpaste), (OPEN_NAME, tools.opener)):
        if path is None:
            return _not_found(name)
    return None


def ask_on_terminal(prompt: str) -> str:
    """One line answered to *prompt*, stripped; `EOFError` at the end of input."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.strip()


def keyless_env(environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """*environ* without the Genius key, for a child that has no use for it."""
    return {name: value for name, value in environ.items() if name != GENIUS_TOKEN_ENV}


def _tool_env() -> dict[str, str]:
    """The keyless environment under a UTF-8 locale, for ``pbpaste`` and ``open``."""
    return {**keyless_env(), **dict.fromkeys(_LOCALE_VARIABLES, UTF8_LOCALE)}


def _ran(run: Run, argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """*argv* run to its end, bounded, its output captured; `PasteInputError` if not."""
    try:
        return run(
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=TOOL_TIMEOUT_S,
            check=False,
            env=_tool_env(),
        )
    except subprocess.TimeoutExpired as error:
        raise PasteInputError(f"{argv[0]} did not answer in time") from error
    except OSError as error:
        raise PasteInputError(f"{argv[0]} could not run: {error}") from error


def _started(spawn: Spawn, argv: Sequence[str]) -> Process:
    """*argv* started, its output piped; `PasteInputError` when it cannot start."""
    try:
        return spawn(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_tool_env(),
        )
    except OSError as error:
        raise PasteInputError(f"{argv[0]} could not run: {error}") from error


def read_bounded(
    argv: Sequence[str],
    limit: int,
    spawn: Spawn = subprocess.Popen,
    *,
    timer: MakeTimer = threading.Timer,
) -> tuple[int, bytes]:
    """*argv*'s exit and its output, no more than *limit* + 1 bytes of it.

    Output past *limit* stops the process there, so the caller sees it is too large
    without it ever being held whole. A watchdog kills the process `TOOL_TIMEOUT_S`
    after it starts, since a pipe's read takes no timeout; that, or a process not gone
    a bound after its output ends, is `PasteInputError`. Any way out that leaves the
    process running, that or an error from the read itself (a Ctrl-C, say), kills it
    and waits for it once, `REAP_TIMEOUT_S` at most, before the error goes on.

    A watchdog that comes due as the process ends cannot be stopped by its cancel
    once it has begun, so the two share a lock: a watchdog that finds the read's end
    recorded, or the process already exited, does nothing, so it never turns a read
    that finished into a timeout. *timer* makes the watchdog.
    """
    process = _started(spawn, argv)
    ended = False
    expired = threading.Event()
    settled = threading.Lock()

    def expire() -> None:
        with settled:
            if ended or process.poll() is not None:
                return
            expired.set()
            process.kill()

    watchdog = timer(TOOL_TIMEOUT_S, expire)
    watchdog.start()
    # The pipe `_started` asked for, so never None.
    pipe = cast("Pipe", process.stdout)
    try:
        data = pipe.read(limit + 1)
        if len(data) > limit:
            process.kill()
        code = process.wait(timeout=TOOL_TIMEOUT_S)
        with settled:
            ended = True
    except subprocess.TimeoutExpired as error:
        raise PasteInputError(f"{argv[0]} did not stop in time") from error
    finally:
        watchdog.cancel()
        # Closed first, so a second Ctrl-C during the reap's wait still closes it.
        pipe.close()
        if not ended:
            _reap(process)
    if expired.is_set():
        raise PasteInputError(f"{argv[0]} did not answer in time")
    return code, data


def _reap(process: Process) -> None:
    """Kill *process* and wait for it once, `REAP_TIMEOUT_S` at most.

    Called only on the way out with an error already on its way, so a process that
    outlives even its kill is left to the system: waiting longer would hold the walk,
    and raising here would hide the error that brought it.
    """
    process.kill()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=REAP_TIMEOUT_S)


def read_clipboard(pbpaste: str | None, spawn: Spawn = subprocess.Popen) -> str:
    """The clipboard's plain text, read by the ``pbpaste`` at *pbpaste*.

    `PasteInputError` when it is unusable, or when *pbpaste* is None, never found.
    """
    if pbpaste is None:
        raise PasteInputError(_not_found(PBPASTE_NAME))
    code, data = read_bounded((pbpaste, *PBPASTE_ARGS), MAX_PASTE_BYTES, spawn)
    # An oversize clipboard is said first: its read was cut short by a kill.
    if len(data) > MAX_PASTE_BYTES:
        raise PasteInputError(f"the clipboard holds more than {MAX_PASTE_BYTES} bytes")
    if code != 0:
        raise PasteInputError(f"pbpaste failed (exit {code})")
    if not data.strip():
        raise PasteInputError("the clipboard is empty: copy the lyrics first")
    try:
        return data.decode(ENCODING)
    except UnicodeDecodeError as error:
        raise PasteInputError("the clipboard is not UTF-8 text") from error


def open_in_browser(opener: str | None, url: str, run: Run = subprocess.run) -> None:
    """Open *url* in the default browser by the ``open`` at *opener*.

    `PasteInputError` when it failed, or when *opener* is None, never found.
    """
    if opener is None:
        raise PasteInputError(_not_found(OPEN_NAME))
    done = _ran(run, (opener, url))
    if done.returncode != 0:
        raise PasteInputError(f"open failed (exit {done.returncode})")


def real_seams(which: Callable[[str], str | None] = shutil.which) -> PasteSeams:
    """The seams a real walk runs on, its tools found now by *which*, once."""
    tools = found_tools(which)
    return PasteSeams(
        partial(terminal_ready, tools),
        ask_on_terminal,
        partial(read_clipboard, tools.pbpaste),
        partial(open_in_browser, tools.opener),
    )
