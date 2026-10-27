"""--embed: run the album tool offline on each album that gained or changed a .lrc.

album takes its own lock, backs up, embeds and checks as in any offline run. This tool
never takes album's lock: a folder another run holds is refused by album, and that
refusal is reported with the command to run.

album saves MP3s in place and holds Ctrl-C back until a save in hand finishes, so it is
run the way the sweep tool runs it (`sweep_child.run_child`): past the bound it is sent
Ctrl-C's own signal and given a grace to stop before any kill, and a Ctrl-C here reaches
it too and is given the same grace. Its output goes to stderr, so this tool's summary
alone reaches stdout. An interrupted run stops the folders after it, which are reported
not run.

**The folder album runs on** is the one the sweep tool walks a library by (library_walk).
When any track found lies two or more folders below --path, --path is a library, and a
written track is embedded through its ``Artist/Album`` folder, the two folders under
--path that hold it; a multi-disc album's disc folders are one run. A written track
nearer --path is in no such folder: it is reported not embedded, with album's command for
that one track. Otherwise --path is one album and is embedded once, as a whole (a named
track: its folder). So album holds the lock the sweep would take, and the two never write
one MP3 at once, when --path is the library root the sweep walks, or one album.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
from lyrics_records import is_mp3_name
from sweep_child import ChildResult, Spawn, album_argv, run_child
from sy_types import EmbedResult, EmbedStop

#: One album folder, offline: backed up, embedded and checked. Minutes at most.
ALBUM_TIMEOUT_S: Final = 1800.0
#: Runs album's argv with a bound; how the run ended.
AlbumRunner = Callable[[Sequence[str], float], ChildResult]
#: How many folders below a library's root its albums lie: an artist's, then an album's.
LIBRARY_DEPTH: Final = 2


@dataclass(frozen=True)
class EmbedUnits:
    """The folders album runs on, and the written tracks it cannot embed."""

    units: tuple[Path, ...]
    loose: tuple[Path, ...] = ()
    #: Written tracks that are no MP3: album embeds into MP3s only, so their .lrc
    #: stays a sidecar and no album run is made for them.
    sidecars: tuple[Path, ...] = ()


def _parts(track: Path, root: Path) -> tuple[str, ...]:
    """*track*'s path below *root*: the folders between, then its own name."""
    return track.relative_to(root).parts


def _sorted_unique(folders: Iterable[Path]) -> tuple[Path, ...]:
    """*folders* once each, sorted, so the runs and the summary keep one order."""
    return tuple(sorted(set(folders)))


def embed_units(
    written: Sequence[Path], discovered: Sequence[Path], root: Path
) -> EmbedUnits:
    """The folders that embed *written*, given every track *discovered* under *root*.

    Only the written MP3s are embedded, by album's own test of a name; every track
    found, of any format, still decides whether *root* is a library.
    """
    mp3s: list[Path] = []
    others: list[Path] = []
    for track in written:
        (mp3s if is_mp3_name(track.name) else others).append(track)
    sidecars = _sorted_unique(others)
    if not mp3s:
        return EmbedUnits((), sidecars=sidecars)
    if all(len(_parts(track, root)) <= LIBRARY_DEPTH for track in discovered):
        return EmbedUnits((root,), sidecars=sidecars)
    deep = {track for track in mp3s if len(_parts(track, root)) > LIBRARY_DEPTH}
    units = (root.joinpath(*_parts(track, root)[:LIBRARY_DEPTH]) for track in deep)
    loose = (track for track in mp3s if track not in deep)
    return EmbedUnits(_sorted_unique(units), _sorted_unique(loose), sidecars)


def run_album(
    argv: Sequence[str], timeout: float, *, popen: Spawn = subprocess.Popen
) -> ChildResult:
    """Run *argv* bounded by *timeout*; how it ended.

    A run that cannot start ends with no exit code and no pid, which no run that started
    does: `run_child` names the pid of a child it could not reap.
    """
    try:
        return run_child(argv, timeout, popen=popen)
    except OSError:
        return ChildResult(None)


def embed_result(folder: Path, child: ChildResult) -> EmbedResult:
    """*folder*'s result from how its album run ended.

    A child left running is named first, since it may still be writing; then a Ctrl-C
    and the bound, each of which cut the run short whatever it exited with.
    """
    if child.exit_code is None:
        stop = EmbedStop.NOT_STARTED if child.pid is None else EmbedStop.NOT_REAPED
    elif child.interrupted:
        stop = EmbedStop.INTERRUPTED
    elif child.timed_out:
        stop = EmbedStop.TIMED_OUT
    else:
        return EmbedResult(folder, child.exit_code)
    return EmbedResult(folder, stop)


def embed_folders(
    folders: EmbedUnits, entry: Path, run: AlbumRunner = run_album
) -> list[EmbedResult]:
    """Run album on each unit in turn until one is interrupted; each result, loose last.

    A Ctrl-C that lands between runs, outside the runner's own wait, stops the run the
    same way: every unit not yet run is reported not run.
    """
    results: list[EmbedResult] = []
    try:
        for folder in folders.units:
            # The sweep's own command line for an offline run of *folder*, in this
            # interpreter, so the two tools run album the same way.
            argv = album_argv(entry, folder, lang=None, offline=True)
            child = run(argv, ALBUM_TIMEOUT_S)
            results.append(embed_result(folder, child))
            if child.interrupted:
                break
    except KeyboardInterrupt:
        # Not swallowed: the interrupt stops the loop, and every unit it left is
        # reported not run below, so the operator sees what still needs embedding.
        pass
    rest = folders.units[len(results) :]
    results += [EmbedResult(folder, EmbedStop.NOT_RUN) for folder in rest]
    loose = folders.loose
    return results + [EmbedResult(t.parent, EmbedStop.LOOSE, track=t) for t in loose]
