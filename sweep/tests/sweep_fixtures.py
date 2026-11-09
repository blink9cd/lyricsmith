"""Shared test fixtures for the sweep: isolation, invented libraries, a fake album tool.

Every sweep test case derives from `IsolatedCase`, and the tool folder's test
aggregator fails the run when one does not (`isolation_offenders`). Its `setUp` points
HOME (and USERPROFILE, Windows' home) and every XDG base directory, the state directory
the ledger lives in among them, at a fresh temporary directory, unsets the Genius key
variable, and replaces the socket layer's connect and name lookup with a tripwire. So
no test can reach a real music library, the real ledger, the network or a real config file, even by mistake.

`FakeAlbumTool` stands in for the album tool child process: called with the argv the
sweep built, it writes a run folder and a manifest through the album tool's own records
and writer, so the sweep's reader round-trips what the album tool really writes.
`SweepCase` sweeps five needy albums with it, and `manifest_of` and `status_outcome`
build the manifests the tally reads. Every lyric here is invented for these tests.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``, after importing the entry point, which puts the album tool's
own modules on the path.
"""

from __future__ import annotations

import json
import os
import socket
import unittest
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NoReturn
from unittest import mock

import album
import lyrics_changed
import lyrics_manifest
import lyrics_model as lm
import lyrics_records as lr
import lyrics_refusal as lrf
import sweep_child
import sweep_ledger as sl
import sweep_run as sr
from lyrics_backup import RUN_ID_FORMAT
from lyrics_config import GENIUS_TOKEN_ENV
from lyrics_model import parse_lrc
from lyrics_provider_states import CoolingState
from lyrics_tags import write_lyrics
from sweep_survey import AlbumSurvey

#: The XDG base directories redirected under the temporary HOME, by subdirectory.
XDG_DIRS = {
    "XDG_CONFIG_HOME": ".config",
    "XDG_CACHE_HOME": ".cache",
    "XDG_DATA_HOME": ".local/share",
    "XDG_STATE_HOME": ".local/state",
}
#: An invented timed sidecar: a track beside it classifies as synced.
SYNCED_LRC = (
    "[00:01.00]The tram bell rings for nobody\n[00:04.50]and the rails hum on\n"
)
#: An invented untimed sidecar: a track beside it classifies as untimed.
UNTIMED_TXT = "Paper boats along the gutter\nsail to the drain and on\n"
#: Bytes that are not UTF-8: a sidecar holding them cannot be read.
NOT_UTF8 = b"caf\xe9 at the corner\n"


class NetworkTouched(AssertionError):
    """A test reached for the network; an AssertionError, so it fails the test."""


def _tripwire(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a network call: fail the call that made it, naming the target."""
    raise NetworkTouched(f"test reached the network: {args!r} {kwargs!r}")


class IsolatedCase(unittest.TestCase):
    """The base of every sweep test case: each test runs isolated.

    ``self.tmp`` is the test's own temporary directory, removed when it ends,
    ``self.home`` the redirected HOME inside it, ``self.state`` the redirected
    ``XDG_STATE_HOME``, and ``self.library`` an empty library folder.
    """

    tmp: Path
    home: Path
    state: Path
    library: Path

    def setUp(self) -> None:
        """Create the temporary directory and enter the isolation for this test."""
        super().setUp()
        self.tmp = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        # Both spellings: `Path.home()` reads HOME on POSIX and USERPROFILE on Windows,
        # where HOME alone would leave the real profile folder as every test's home.
        values = {"HOME": str(self.home), "USERPROFILE": str(self.home)}
        values.update({name: str(self.home / sub) for name, sub in XDG_DIRS.items()})
        self.state = Path(values["XDG_STATE_HOME"])
        stack = self.enterContext(ExitStack())
        stack.enter_context(mock.patch.dict(os.environ, values))
        os.environ.pop(GENIUS_TOKEN_ENV, None)
        for name in ("connect", "connect_ex"):
            stack.enter_context(mock.patch.object(socket.socket, name, _tripwire))
        stack.enter_context(mock.patch.object(socket, "getaddrinfo", _tripwire))
        self.library = self.tmp / "library"
        self.library.mkdir()


def env_without_ffmpeg(root: Path) -> dict[str, str]:
    """This environment with PATH one empty folder: a child album tool finds no ffmpeg.

    Children run by absolute sys.executable and the album tool runs no other program, so
    an empty PATH costs nothing and keeps the host's ffmpeg out of every sweep test.
    """
    empty = root / "no-ffmpeg-path"
    empty.mkdir(exist_ok=True)
    return {**os.environ, "PATH": str(empty)}


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


# --- Invented libraries --------------------------------------------------------------


def add_track(
    album: Path, name: str, *, lrc: str | None = None, txt: str | bytes | None = None
) -> Path:
    """Write an empty MP3 named *name* under *album*, with any sidecars; return it.

    An empty file holds no ID3 tag, so its lyrics are whatever its sidecars hold: a
    timed ``.lrc`` makes it synced, a ``.txt`` untimed, neither none, and bytes that
    are not UTF-8 make it unreadable.
    """
    mp3 = album / name
    mp3.parent.mkdir(parents=True, exist_ok=True)
    mp3.write_bytes(b"")
    if lrc is not None:
        mp3.with_suffix(".lrc").write_text(lrc, encoding="utf-8", newline="")
    if isinstance(txt, bytes):
        mp3.with_suffix(".txt").write_bytes(txt)
    elif txt is not None:
        mp3.with_suffix(".txt").write_text(txt, encoding="utf-8", newline="")
    return mp3


def embed_sidecar(mp3: Path) -> Path:
    """Embed *mp3*'s timed ``.lrc`` in its frames, by the album tool's write; return it.

    A timed ``.lrc`` beside frames holding no timed lyrics owes an embed, so a track
    meant as done needs its frames to hold the file, as a run leaves them.
    """
    lrc = mp3.with_suffix(".lrc").read_text(encoding="utf-8")
    write_lyrics(mp3, parse_lrc(lrc), "eng")
    return mp3


def synced_album(library: Path, artist: str, album: str) -> Path:
    """An album whose one track is synced and embedded: a run would not visit it."""
    folder = library / artist / album
    embed_sidecar(add_track(folder, "01 Done.mp3", lrc=SYNCED_LRC))
    return folder


def needy_album(library: Path, artist: str, album: str) -> Path:
    """An album with a synced, an untimed-only and a no-lyrics track: a run visits it."""
    folder = library / artist / album
    add_track(folder, "01 Timed.mp3", lrc=SYNCED_LRC)
    add_track(folder, "02 Plain.mp3", txt=UNTIMED_TXT)
    add_track(folder, "03 Bare.mp3")
    return folder


# --- Outcomes, as the album tool records them ----------------------------------------


def refusal(
    provider: lm.Source = lm.Source.LRCLIB,
    detail: str = "HTTP 429",
    reason: lrf.Refusal = lrf.Refusal.RATE_LIMITED,
) -> lr.ProviderRefusal:
    """One refused provider call, at the synced step, of class *reason*."""
    return lr.ProviderRefusal(provider, lm.LyricsKind.SYNCED, detail, reason)


def breaker_open(
    provider: lm.Source = lm.Source.GENIUS,
    detail: str = "breaker open until 03:47:12 UTC",
) -> lr.ProviderRefusal:
    """A lookup an open breaker kept unsent: blocked, no status, never challenged."""
    return lr.ProviderRefusal(
        provider, lm.LyricsKind.UNSYNCED, detail, lrf.Refusal.BLOCKED, unsent=True
    )


def written(source: lm.Source = lm.Source.LRCLIB) -> lr.TrackOutcome:
    """A track a provider answered, its synced lyrics written."""
    return lr.TrackOutcome(
        lr.OutcomeStatus.WRITTEN, source, lm.LyricsKind.SYNCED, frames_written=True
    )


def not_found(*refusals: lr.ProviderRefusal) -> lr.TrackOutcome:
    """A track no provider found, with the refusals met on the way."""
    return lr.TrackOutcome(lr.OutcomeStatus.NOT_FOUND, refusals=refusals)


def fetch_error(*refusals: lr.ProviderRefusal) -> lr.TrackOutcome:
    """A track whose every provider failed: the album tool's fetch error."""
    error = lr.StageError(lr.ErrorStage.FETCH, "FetchError", "every provider failed")
    return lr.TrackOutcome(lr.OutcomeStatus.ERROR, error=error, refusals=refusals)


#: The outcomes of an album whose providers only refused: every track's every provider
#: failed, which the album tool records as the track's fetch error.
REFUSED = (
    fetch_error(refusal()),
    fetch_error(refusal(lm.Source.NETEASE, "code -460", lrf.Refusal.UNRECOGNIZED)),
)
#: The outcomes of an album whose providers answered.
ANSWERED = (written(), not_found())


# --- A fake album tool ---------------------------------------------------------------


@dataclass(frozen=True)
class Script:
    """What the fake album tool does for one album.

    *outcomes* are its tracks' ``after`` blocks, in order; a manifest is written only
    when *manifest* is True, and *other_runs* more runs beside it, as a second album
    tool run alongside would leave. *exit_code*, *timed_out*, *interrupted* and *pid*
    make the `sweep_child.ChildResult` it returns. *changed* is what it writes to the
    file ``--changed-list`` names, when the sweep passed one: the album-relative names of
    the MP3s it changed, as the album tool writes them, or raw bytes; None writes no
    list, as a child killed or crashed leaves none. *tekstowo* and *youtube_music* are
    the run-level states its manifest records, ``cooling down`` among them.
    """

    outcomes: tuple[lr.TrackOutcome, ...] = ANSWERED
    exit_code: int | None = album.EXIT_OK
    manifest: bool = True
    other_runs: int = 0
    timed_out: bool = False
    interrupted: bool = False
    pid: int | None = None
    changed: tuple[str, ...] | bytes | None = ()
    tekstowo: CoolingState = CoolingState.OFF
    youtube_music: CoolingState = CoolingState.OFF


def option_value(argv: Sequence[str], name: str) -> str:
    """The value following *name* in *argv*."""
    return argv[list(argv).index(name) + 1]


def _write_changed(album: Path, target: Path, changed: tuple[str, ...] | bytes) -> None:
    """Write *changed* to *target*: raw bytes as they are, names as the album tool would."""
    if isinstance(changed, bytes):
        target.write_bytes(changed)
        return
    data, _left = lyrics_changed.format_changed_list(str(album / n) for n in changed)
    target.write_bytes(data)


@dataclass
class FakeAlbumTool:
    """A `sweep_child.ChildRunner` that writes the album tool's manifest itself.

    *scripts* maps an album folder's name to its `Script`; an album with none gets the
    default one. Every argv it is called with is kept in *calls*, every timeout in
    *timeouts*. Each run takes the next second of an invented clock for its run id, so
    two runs of one album never collide.
    """

    scripts: dict[str, Script] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)
    clock: datetime = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)

    def __call__(
        self, argv: Sequence[str], timeout_s: float
    ) -> sweep_child.ChildResult:
        """Record the call, write the scripted manifest, return the scripted result."""
        self.calls.append(list(argv))
        self.timeouts.append(timeout_s)
        album = Path(option_value(argv, "--album"))
        script = self.scripts.get(album.name, Script())
        if script.manifest:
            self._write_manifest(album, script)
        for _ in range(script.other_runs):
            self._write_manifest(album, script)
        listed = sweep_child.CHANGED_LIST_OPTION
        if listed in argv and script.changed is not None:
            _write_changed(album, Path(option_value(argv, listed)), script.changed)
        return sweep_child.ChildResult(
            script.exit_code,
            timed_out=script.timed_out,
            interrupted=script.interrupted,
            pid=script.pid,
        )

    def _write_manifest(self, album: Path, script: Script) -> None:
        """Write *script*'s complete run under *album* through the album tool's writer."""
        self.clock += timedelta(seconds=1)
        run_id = self.clock.strftime(RUN_ID_FORMAT)
        run_dir = lr.run_folder(album, run_id)
        run_dir.mkdir(parents=True)
        tracks = tuple(
            lr.ManifestTrack(f"{n:02d}.mp3", (2, 4), lr.TrackBefore((), ()), after)
            for n, after in enumerate(script.outcomes, start=1)
        )
        manifest = lr.Manifest(
            run_id,
            "eng",
            lr.RunState.COMPLETE,
            lr.GeniusState.ABSENT,
            tracks,
            tekstowo=script.tekstowo,
            youtube_music=script.youtube_music,
        )
        lyrics_manifest.write_manifest(run_dir, manifest)

    def albums(self) -> list[str]:
        """The album folder names it was run on, in order."""
        return [Path(option_value(argv, "--album")).name for argv in self.calls]


@dataclass
class FakeSleep:
    """A sleep that only records each pause it was asked for."""

    pauses: list[float] = field(default_factory=list)

    def __call__(self, seconds: float) -> None:
        """Record *seconds*."""
        self.pauses.append(seconds)


def fixed_now() -> datetime:
    """An invented, fixed moment for the ledger's time stamps."""
    return datetime(2026, 10, 19, 9, 30, 0, tzinfo=UTC)


# --- Manifests and a sweep, built for the suites that share them --------------------


def manifest_of(*outcomes: lr.TrackOutcome | None) -> lr.Manifest:
    """A complete run's manifest whose tracks ended as *outcomes*; None never ran."""
    tracks = tuple(
        lr.ManifestTrack(f"{n:02d}.mp3", (2, 4), lr.TrackBefore((), ()), after)
        for n, after in enumerate(outcomes, start=1)
    )
    return lr.Manifest(
        "20261019T120000Z", "eng", lr.RunState.COMPLETE, lr.GeniusState.ABSENT, tracks
    )


def status_outcome(
    status: lr.OutcomeStatus,
    *,
    source: lm.Source | None = None,
    kind: lm.LyricsKind | None = None,
    check: lr.CheckResult | None = None,
    refusals: tuple[lr.ProviderRefusal, ...] = (),
) -> lr.TrackOutcome:
    """An outcome of *status* with the given fields set."""
    return lr.TrackOutcome(
        status, source=source, kind=kind, check=check, refusals=refusals
    )


#: Invented sweep options: a pause and a bound no test waits for.
OPTIONS = sr.SweepOptions(
    lang=None,
    offline=False,
    pause_s=7.0,
    stop_after=2,
    limit=None,
    restart=False,
    timeout_s=123.0,
)


class SweepCase(IsolatedCase):
    """A library of needy albums, a fake album tool, a recording sleep, a ledger."""

    def setUp(self) -> None:
        """Build five albums a run would visit, A1 to A5."""
        super().setUp()
        self.albums = [
            needy_album(self.library, "Artist", f"A{n}") for n in range(1, 6)
        ]
        self.fake = FakeAlbumTool()
        self.sleep = FakeSleep()
        self.sleep_fn: Callable[[float], None] = self.sleep
        self.run_child: sweep_child.ChildRunner = self.fake
        self.notes: list[str] = []
        self.ledger = self.state / "lyricsmith" / "sweep" / "ledger.jsonl"

    def _sweep(
        self,
        *,
        limit: int | None = None,
        restart: bool = False,
        offline: bool = False,
        lang: str | None = None,
        skip: tuple[str, ...] = (),
    ) -> sr.SweepReport:
        seams = sr.SweepSeams(
            run_child=self.run_child,
            sleep=self.sleep_fn,
            now=fixed_now,
            progress=self.notes.append,
            entry=self.tmp / "album.py",
        )
        options = replace(
            OPTIONS, limit=limit, restart=restart, offline=offline, lang=lang, skip=skip
        )
        # Each album surveyed as one with a track lacking lyrics: one a run visits.
        surveys = [AlbumSurvey(album, none=1) for album in self.albums]
        return sr.sweep(self.library, surveys, self.ledger, options, seams)

    def _ledger(self) -> list[sl.LedgerEntry]:
        records = sl.read_ledger(self.ledger).records
        return [r for r in records if isinstance(r, sl.LedgerEntry)]


# --- Ledger lines and runs from before the owed outcome existed ---------------------


def earlier_line(entry: sl.LedgerEntry) -> str:
    """*entry*'s ledger line as written before ``owed_providers`` was recorded."""
    document = json.loads(sl.encode_record(entry))
    del document["owed_providers"]
    return json.dumps(document, sort_keys=True)


def append_line(ledger: Path, line: str) -> None:
    """Add the raw *line* to the ledger at *ledger*, making its folder if need be."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


def write_run(
    album: Path,
    run_id: str,
    outcomes: Sequence[lr.TrackOutcome | None],
    *,
    tekstowo: CoolingState = CoolingState.ON,
    youtube_music: CoolingState = CoolingState.ON,
) -> None:
    """Write run *run_id*'s complete manifest under *album*, its cooling states given."""
    run_dir = lr.run_folder(album, run_id)
    run_dir.mkdir(parents=True)
    manifest = replace(
        manifest_of(*outcomes),
        run_id=run_id,
        tekstowo=tekstowo,
        youtube_music=youtube_music,
    )
    lyrics_manifest.write_manifest(run_dir, manifest)
