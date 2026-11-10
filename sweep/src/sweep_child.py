"""The album tool as a child process: its command line, its bound, and the run it made.

**Why not bounded_run.** The project's bounded child-process helper captures the
child's output and starts it in a session of its own, and it kills the child's whole
tree on Ctrl-C. A sweep needs the opposite on all three counts: the album tool's progress
belongs on the terminal as it happens, over the many minutes an album can take; Ctrl-C
must reach the album tool itself, which stops at once, cutting the track in hand short and
recording it as interrupted, after letting any write in hand finish; and the album tool
starts no process of its own, so there is no
grandchild to hold a pipe open. So the child shares this process group and this
terminal, its stdout goes to this process's stderr (the sweep's own report owns
stdout), and its stdin is closed.

**The bound.** `run_child` waits at most *timeout_s* for the child. Past it, the child is
sent Ctrl-C's own signal, so it stops the way an operator would stop it; a child still
running `STOP_GRACE_S` later is killed, and a second Ctrl-C during that grace kills it at
once. Windows' `Popen.send_signal` cannot send that signal, so there
(`CAN_FORWARD_SIGNALS` false) the child is sent SIGTERM instead, which `Popen` carries out
as `terminate()` on every host (TerminateProcess on Windows), ending it at once with no
chance to record the track in hand, and the result carries the album tool's interrupted
code, as a child stopped by the signal reports it, rather than the terminated child's
own exit 1. Every wait is bounded, the one after the kill too: a child still not gone then (one
stuck in uninterruptible I/O, say) is left, never reaped, and the result says so with
its pid rather than raising, so the album is still recorded.

**The run it made.** The album tool names a run by its UTC start time, to the second, with
``-2``, ``-3`` and so on appended on a collision, and lists it once its manifest exists.
The sweep takes the run ids present before the child starts (`run_ids`, the album tool's
own listing, re-exported here), and once it ends the ids that were not there before
(`new_run_ids`): the caller reads the one new run, and
takes none when there are several, since another process ran on the album alongside and
which run is whose cannot be told. Comparing sets, rather than predicting the id from a
clock or taking the newest, holds however the clock moved and whatever suffix a
collision took. The manifest is read by the album tool's own reader (`read_run`). Nothing
here prints.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_bandcamp import BANDCAMP_STEP_DEADLINE_S
from lyrics_exit import EXIT_INTERRUPTED
from lyrics_fetch import (
    CHAIN,
    PROVIDER_DEADLINE_S,
    AnswersEveryKind,
)
from lyrics_genius import genius_step_deadline_s
from lyrics_manifest import read_manifest
from lyrics_model import LyricsError, ProviderKey, Source
from lyrics_only import SEPARATOR, option_name
from lyrics_options import MODE_RUN, ONLY_FLAG
from lyrics_records import Manifest, run_folder
from lyrics_run_folders import run_ids as run_ids
from lyrics_silence import SILENCE_TIMEOUT_S
from lyrics_table import build_provider_table
from lyrics_tekstowo import TEKSTOWO_STEP_DEADLINE_S
from lyrics_youtube_provider import YOUTUBE_MUSIC_STEP_DEADLINE_S

#: The lookups the album tool may make for one track: its own title, then the retry with
#: the title stripped.
_PASSES_PER_TRACK = 2
#: The whole-call bound of each step that sets its own, needing no key or network to read.
_OWN_BOUNDS: Mapping[Source, float] = {
    Source.GENIUS: genius_step_deadline_s(PROVIDER_DEADLINE_S),
    Source.BANDCAMP: BANDCAMP_STEP_DEADLINE_S,
    Source.YOUTUBE_MUSIC: YOUTUBE_MUSIC_STEP_DEADLINE_S,
    Source.TEKSTOWO: TEKSTOWO_STEP_DEADLINE_S,
}


def _pass_worst_s() -> float:
    """One pass's worst case over the album tool's chain, each call at its own bound.

    The Genius, Bandcamp, YouTube Music and Tekstowo steps retry or pace inside one
    call, so each is taken at its whole-call bound (`_OWN_BOUNDS`); every other step at
    the chain's per-call bound. An LRC provider, like YouTube Music, answers both kinds
    in one call (`AnswersEveryKind`), and its other step re-reads that answer, so it is
    counted once per pass. The LRC steps are read from the album tool's real table, which
    sends nothing when built.
    """
    lrc = build_provider_table(None)
    calls: set[Source | ProviderKey] = {
        key.source if isinstance(provider, AnswersEveryKind) else key
        for key, provider in lrc.items()
    }
    sources = dict.fromkeys(key.source for key in CHAIN)
    own = sum(_OWN_BOUNDS[source] for source in sources if source in _OWN_BOUNDS)
    return len(calls) * PROVIDER_DEADLINE_S + own


#: One track's worst case: one pass at its worst (`_pass_worst_s`) for the title's
#: lookup, and another for the stripped-title retry, which asks every provider again,
#: plus one silence measurement (a track is measured at most once).
TRACK_WORST_S = _pass_worst_s() * _PASSES_PER_TRACK + SILENCE_TIMEOUT_S
#: The tracks of a long album, which the per-album bound covers at that worst.
_LONG_ALBUM_TRACKS = 36
#: The longest one album's run may take before it is stopped.
ALBUM_TIMEOUT_S = TRACK_WORST_S * _LONG_ALBUM_TRACKS
#: How long a child sent Ctrl-C's signal may take to stop. The album tool stops at once,
#: except that it lets a write in hand finish and then records the track and prints its
#: summary; the grace is generous because that write may be to a synced cloud folder
#: that stalls, and killing the album tool mid-write is what the grace exists to avoid.
STOP_GRACE_S = 6 * 60.0
#: How long to wait for a killed child to be gone.
KILL_WAIT_S = 30.0
#: Whether the bound's stop can be sent as Ctrl-C's own signal. Windows' `Popen` sends
#: only SIGTERM (as TerminateProcess) and the console events, so SIGINT raised
#: ValueError there; the child is sent SIGTERM instead. Read at call time, so tests can
#: flip it.
CAN_FORWARD_SIGNALS = sys.platform != "win32"
#: Where the child's stdout goes: this process's own stderr, by descriptor, so the
#: sweep's report alone reaches stdout.
_STDERR_FD = 2


@dataclass(frozen=True)
class ChildResult:
    """How the child ended: its exit code, and whether the bound or Ctrl-C ended it.

    *interrupted* is True when Ctrl-C reached the sweep while it waited for the child,
    or, set by the sweep, after the child ended, while its run was read.
    *exit_code* is None, and *pid* names the child, when it was never reaped: it would
    not stop even when killed, and may still be running.
    """

    exit_code: int | None
    timed_out: bool = False
    interrupted: bool = False
    pid: int | None = None


@dataclass(frozen=True)
class _Settled:
    """How a child sent toward its end ended: its exit code, None if never reaped.

    *interrupted* is True when Ctrl-C reached this process while it waited.
    """

    exit_code: int | None
    interrupted: bool


#: A child's environment: None for this process's own.
ChildEnv = Mapping[str, str] | None


class ChildRunner(Protocol):
    """What runs the album tool: `run_child`, or a test's stand-in.

    An argv and a bound in seconds in, its `ChildResult` out. *env* is the child's
    environment, this process's own when None: the paste walk passes one without the
    Genius key, while a run's child keeps it.
    """

    def __call__(
        self,
        argv: Sequence[str],
        timeout_s: float,
        /,
        *,
        env: ChildEnv = None,
    ) -> ChildResult:
        """Run *argv* on *env*, bounded by *timeout_s*; how it ended."""


class _Process(Protocol):
    """The part of `subprocess.Popen` the runner uses."""

    pid: int

    def wait(self, timeout: float | None = None) -> int:
        """Wait at most *timeout* seconds for the process; its exit code."""

    def send_signal(self, sig: int) -> None:
        """Send *sig* to the process."""

    def kill(self) -> None:
        """Kill the process."""


#: The refetch pass's option, as the album tool takes it and the sweep repeats it.
REFETCH_OPTION = "--refetch-untimed"
#: The option asking again each track serving implausible lyrics: no pass of its own,
#: so the sweep hands it on in whichever pass it runs.
IMPLAUSIBLE_OPTION = "--refetch-implausible"
#: The option the sweep and the album tool each take for the list of the MP3s saved.
CHANGED_LIST_OPTION = "--changed-list"
#: The option the sweep and the album tool's fix each take for the held tracks' list.
HOLD_OPTION = "--hold-for-genius"
#: The option the sweep and the album tool's fix each take for the transcript store.
HEARD_DIR_OPTION = "--heard-dir"
#: The option the sweep and the album tool each take to leave NetEase's steps unasked.
NO_NETEASE_OPTION = "--no-netease"
#: The options the sweep and the album tool each take to leave a source unasked, as
#: (the parsed attribute, the option), in the order the chain first asks each source,
#: which is the order a child's command line ends with them.
SKIP_OPTIONS: tuple[tuple[str, str], ...] = (
    ("no_netease", NO_NETEASE_OPTION),
    ("no_bandcamp", "--no-bandcamp"),
    ("no_youtube_music", "--no-youtube-music"),
    ("no_genius", "--no-genius"),
    ("no_tekstowo", "--no-tekstowo"),
)

#: How a process is started: argv, then `subprocess.Popen`'s keyword arguments.
Spawn = Callable[..., _Process]


def album_argv(
    entry: Path,
    album: Path,
    *,
    lang: str | None,
    offline: bool,
    refetch_untimed: bool = False,
    refetch_implausible: bool = False,
    changed_list: Path | None = None,
    interpreter: str = sys.executable,
    only: Sequence[Source] = (),
    skip: Sequence[str] = (),
    mode: str = MODE_RUN,
    heard_dir: Path | None = None,
    hold_list: Path | None = None,
) -> list[str]:
    """The command running the album tool at *entry* on *album*, never ``--fresh``.

    *mode* is the album tool's mode: a run by default, or the fix pass's
    ``fix-text``, which *heard_dir* names the transcript store of
    (``--heard-dir``), and *hold_list* the file the fix lists the tracks it held
    for Genius in (``--hold-for-genius``).

    *refetch_untimed* adds ``--refetch-untimed``, the refetch pass's option,
    *refetch_implausible* ``--refetch-implausible``, and *changed_list*
    ``--changed-list``, the file the child lists the MP3s it saved in; *only*, when
    not empty, ``--only-providers`` naming those providers by the album tool's own
    option names (`lyrics_only.option_name`), the revisit of an owed album; and
    *skip*, the `SKIP_OPTIONS` asked for, in their order, ends it.
    *interpreter* defaults to the one running the sweep, so the album tool gets the
    same installed packages. The album tool keeps its flags in its parser, so the
    sweep's suite parses this command, every option on, with that real parser.
    """
    argv = [interpreter, str(entry), "--album", str(album), "--mode", mode]
    if heard_dir is not None:
        argv += [HEARD_DIR_OPTION, str(heard_dir)]
    if lang is not None:
        argv += ["--lang", lang]
    if offline:
        argv.append("--offline")
    if refetch_untimed:
        argv.append(REFETCH_OPTION)
    if refetch_implausible:
        argv.append(IMPLAUSIBLE_OPTION)
    if changed_list is not None:
        argv += [CHANGED_LIST_OPTION, str(changed_list)]
    if hold_list is not None:
        argv += [HOLD_OPTION, str(hold_list)]
    if only:
        argv += [ONLY_FLAG, SEPARATOR.join(option_name(source) for source in only)]
    argv += skip
    return argv


def _settle(process: _Process, grace_s: float, kill_wait_s: float) -> _Settled:
    """Wait *grace_s* for *process* to stop by itself, else kill it and wait again.

    A Ctrl-C during the grace kills the child at once: it already had Ctrl-C's signal.
    A child not gone *kill_wait_s* after the kill, or a Ctrl-C during that last wait,
    leaves it unreaped: its exit code is None.
    """
    interrupted = False
    try:
        return _Settled(process.wait(timeout=grace_s), interrupted)
    except subprocess.TimeoutExpired:
        pass
    except KeyboardInterrupt:
        interrupted = True
    process.kill()
    try:
        return _Settled(process.wait(timeout=kill_wait_s), interrupted)
    except subprocess.TimeoutExpired:
        return _Settled(None, interrupted)
    except KeyboardInterrupt:
        return _Settled(None, True)


def _ended(
    process: _Process, settled: _Settled, *, timed_out: bool, interrupted: bool
) -> ChildResult:
    """The result of a child *settled* after the bound or a Ctrl-C; its pid if unreaped."""
    return ChildResult(
        settled.exit_code,
        timed_out=timed_out,
        interrupted=interrupted or settled.interrupted,
        pid=process.pid if settled.exit_code is None else None,
    )


def _stop(process: _Process) -> bool:
    """Stop *process*, past its bound, as Ctrl-C would; True if it was terminated instead.

    Ctrl-C's own signal where it can be sent; elsewhere (`CAN_FORWARD_SIGNALS` false)
    SIGTERM, the one signal Windows' `Popen` sends, as `terminate()`, which the caller
    reports as the stop the signal would have made. Sent through `send_signal` on both
    paths, so a runner's process need offer nothing more.
    """
    if CAN_FORWARD_SIGNALS:
        process.send_signal(signal.SIGINT)
        return False
    process.send_signal(signal.SIGTERM)
    return True


def _as_stopped(settled: _Settled) -> _Settled:
    """*settled* for a child this process terminated: reaped, it reads as interrupted.

    A terminated Windows child exits 1, which the album tool would mean as a failure;
    the signal's stop exits with the album tool's interrupted code, so that is recorded.
    An unreaped child keeps its None.
    """
    if settled.exit_code is None:
        return settled
    return _Settled(EXIT_INTERRUPTED, settled.interrupted)


def run_child(
    argv: Sequence[str],
    timeout_s: float,
    *,
    popen: Spawn = subprocess.Popen,
    stop_grace_s: float = STOP_GRACE_S,
    kill_wait_s: float = KILL_WAIT_S,
    env: ChildEnv = None,
) -> ChildResult:
    """Run *argv* to its end, bounded by *timeout_s*; how it ended.

    The child runs on *env*, this process's own environment when None: the paste walk
    hands its child one without the Genius key, while a run's child keeps it.

    Past the bound the child is sent SIGINT, or terminated where that cannot be sent
    (`_stop`), and given *stop_grace_s* to stop. When
    Ctrl-C interrupts this process, the child, in the same process group, got it too:
    it is given the same grace, and the result says it was interrupted. Never raises
    for a child that will not stop: the result says it was left, with its pid.
    """
    process = popen(list(argv), stdin=subprocess.DEVNULL, stdout=_STDERR_FD, env=env)
    try:
        return ChildResult(process.wait(timeout=timeout_s))
    except subprocess.TimeoutExpired:
        terminated = _stop(process)
        settled = _settle(process, stop_grace_s, kill_wait_s)
        if terminated:
            settled = _as_stopped(settled)
        return _ended(process, settled, timed_out=True, interrupted=False)
    except KeyboardInterrupt:
        settled = _settle(process, stop_grace_s, kill_wait_s)
        return _ended(process, settled, timed_out=False, interrupted=True)


def new_run_ids(album: Path, before: frozenset[str]) -> frozenset[str]:
    """The ids of *album*'s runs that were not among *before*."""
    return run_ids(album) - before


def read_run(album: Path, run_id: str) -> Manifest | None:
    """The manifest of *album*'s run *run_id*, or None when it cannot be read."""
    try:
        return read_manifest(run_folder(album, run_id))
    except (LyricsError, OSError):
        return None
