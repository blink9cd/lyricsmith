"""The two modes: a run over a path, and the model fetch.

A run checks its setup first and touches no track when anything is missing: the
child's packages, ffmpeg, a model, the path. Every outside call the modes make comes
through `Seams`, so the black-box tests fake each one. A folder the walk cannot read, a
found name that is not a regular file, and a track whose .lrc an earlier track already
claims, are each a failed row, never dropped; a walked link is a skipped one.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import shlex
import shutil
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
import at_discover
import at_engine
import at_models
import sy_embed
import sy_models
import sy_plan
import sy_summary
import sweep_report
from at_modes import NOT_REGULAR_DETAIL, say
from sy_track import time_or_skip
from sy_types import (
    FAILED,
    UNTIMED_OUTCOMES,
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_SETUP,
    LRC_SUFFIX,
    Action,
    Label,
    Options,
    Outcome,
    Planned,
    SetupError,
    TrackResult,
)

#: The packages the child process imports, checked without importing them.
REQUIRED_MODULES: Final = (
    "torch",
    "torchaudio",
    "demucs",
    "safetensors",
    "yaml",
    "numpy",
)
FFMPEG: Final = "ffmpeg"
REQUIREMENTS: Final = Path(__file__).resolve().parent.parent / "requirements-sync.txt"
#: Each action that writes a .lrc, and the outcome a write it finished ends as.
WRITING_OUTCOMES: Final = MappingProxyType(
    {Action.TIME: Outcome.TIMED, Action.RETIME: Outcome.RETIMED}
)
#: A directory entry's identity: its inode and its modification time in nanoseconds.
Identity = tuple[int, int]


@dataclass(frozen=True)
class Seams:
    """Every outside call a run makes, so a test can fake each."""

    run: at_engine.Runner
    embed: sy_embed.AlbumRunner
    album_entry: Path
    find_spec: Callable[[str], object | None]
    which: Callable[[str], str | None]
    version: Callable[[str], str]


@dataclass(frozen=True)
class InHand:
    """The track being timed: its place in the plan, its plan, its .lrc entry before."""

    index: int
    planned: Planned
    before: Identity | None


def _entry(lrc: Path) -> os.stat_result | None:
    """The entry named *lrc*, never followed; None when there is none to read."""
    try:
        return os.lstat(lrc)
    except OSError:
        return None


def lrc_identity(lrc: Path) -> Identity | None:
    """The identity of the entry named *lrc*, never followed; None when there is none."""
    entry = _entry(lrc)
    return None if entry is None else _identity(entry)


def _identity(entry: os.stat_result) -> Identity:
    """*entry*'s inode and modification time, which an atomic replace both change."""
    return entry.st_ino, entry.st_mtime_ns


def real_seams(album_entry: Path) -> Seams:
    """The seams a real run uses."""
    return Seams(
        run=at_engine.run_bounded,
        embed=sy_embed.run_album,
        album_entry=album_entry,
        find_spec=importlib.util.find_spec,
        which=shutil.which,
        version=importlib.metadata.version,
    )


def setup_problems(
    path: Path, options: Options, seams: Seams, prog: Sequence[str]
) -> list[str]:
    """Each reason the run cannot start, in the order a person would fix them.

    *prog* is this tool's words as it was run, which the model fetch's command names.
    """
    problems = []
    if not path.exists():
        problems.append(f"--path does not exist: {path}")
    missing = [name for name in REQUIRED_MODULES if seams.find_spec(name) is None]
    if missing:
        install = f"pip install -r {shlex.quote(str(REQUIREMENTS))}"
        problems.append(f"the sync install is missing {', '.join(missing)}: {install}")
    if seams.which(FFMPEG) is None:
        problems.append("ffmpeg is not on PATH")
    # Read at call time, not bound as a default, so the tests' stand-in manifest counts.
    models = sy_models.model_problems(options.model_dir, sy_models.MANIFEST)
    if models:
        fetch = shlex.join(
            [*prog, "--mode", "fetch-models", "--model-dir", str(options.model_dir)]
        )
        problems += [*models, f"fetch them: {fetch}"]
    return problems


def _unreadable_folder(error: OSError, root: Path) -> Planned:
    """The row of a folder the walk could not read; it names the root when unnamed."""
    folder = root if error.filename is None else Path(str(error.filename))
    reason = error.strerror or str(error)
    return Planned(
        folder, Action.ERROR_TEXT, detail=f"could not read the folder: {reason}"
    )


def _entry_row(track: Path, named: bool) -> Planned | None:
    """The row of a found name that is no track to time; None when it is one.

    Judged exactly as the transcribe tool judges it (`at_discover.classify`), before
    any sidecar is read: a link the walk found is skipped, as album skips a linked MP3,
    and a FIFO, socket or device is refused, since ffmpeg would block on a FIFO for its
    whole bound. A --path naming a link is the operator's choice, so it is followed.
    """
    try:
        entry = at_discover.classify(track, named=named)
    except OSError as error:
        return Planned(track, Action.ERROR_AUDIO, detail=f"could not read it: {error}")
    if entry is at_discover.Entry.LINKED:
        return Planned(track, Action.SKIP_LINKED)
    if entry is at_discover.Entry.NOT_REGULAR:
        return Planned(track, Action.ERROR_AUDIO, detail=NOT_REGULAR_DETAIL)
    return None


def _plan_one(track: Path, path: Path, overwrite: bool) -> Planned:
    """*track*'s row when it is no track to time, else its plan from its sidecars."""
    row = _entry_row(track, named=track == path)
    return row or sy_plan.plan_track(track, overwrite=overwrite)


def plan_all(
    path: Path, options: Options, root: Path
) -> tuple[list[Planned], list[Path]]:
    """Each unreadable folder's row and each track's plan, claims settled; every track.

    Every track found, planned or not, decides the folders --embed runs album on; a
    name that is no track (a link, a FIFO, one that could not be read) decides nothing.
    """
    errors: list[OSError] = []
    audio = at_discover.find_audio(path, options.extensions, errors)
    plans = [_plan_one(track, path, options.overwrite) for track in audio]
    folders = [_unreadable_folder(error, root) for error in errors]
    tracks = [plan.audio for plan in plans if plan.action not in sy_plan.UNCLAIMED]
    return folders + sy_plan.claim_lrcs(plans), tracks


def run_mode(
    options: Options, seams: Seams, *, tool_version: str, prog: Sequence[str]
) -> int:
    """Plan every track under --path, then time each (or print the plan); the exit code.

    *prog* is this tool's words as it was run (``lyricsmith sync`` dispatched, its
    entry file's name alone); every command printed names the tools the same way.
    """
    path = options.path
    if path is None:
        raise SetupError("--path is required in run mode")
    problems = setup_problems(path, options, seams, prog)
    album = sweep_report.album_prog(tuple(prog), seams.album_entry.name)
    if problems and not options.dry_run:
        raise SetupError("; ".join(problems))
    root = at_discover.walk_root(path)
    plans, discovered = plan_all(path, options, root) if path.exists() else ([], [])
    if options.dry_run:
        return _dry_run(plans, problems, root)
    label = Label(tool_version, seams.version("demucs"), seams.version("torchaudio"))
    results, interrupted, in_hand = _time_each(plans, options, label, seams, root)
    if interrupted:
        return _interrupted(plans, results, in_hand, discovered, root, album)
    written = sy_summary.written_tracks(results)
    units = sy_embed.embed_units(written, discovered, root)
    return _finish(results, units, options.embed, seams, album)


def _time_each(
    plans: Sequence[Planned], options: Options, label: Label, seams: Seams, root: Path
) -> tuple[list[TrackResult], bool, InHand | None]:
    """Each plan's result, its rows printed as it ends; whether a Ctrl-C stopped it.

    The child shares the terminal, so a Ctrl-C reaches it too, and the interrupted
    track's temporary folder is removed on the way out; its .lrc is written atomically
    or not at all. A result is kept before its rows print, so a Ctrl-C while they print
    cannot cost a written track its album command. The last track taken in hand comes
    back too, with its .lrc entry as it was just before the track was timed.
    """
    results: list[TrackResult] = []
    in_hand: InHand | None = None
    try:
        for index, planned in enumerate(plans):
            before = lrc_identity(planned.audio.with_suffix(LRC_SUFFIX))
            in_hand = InHand(index, planned, before)
            result = time_or_skip(planned, options.model_dir, label, seams.run)
            results.append(result)
            for line in sy_summary.result_lines(result, root):
                say(line)
    except KeyboardInterrupt:
        # Not swallowed: the caller prints the footer with the album commands for
        # what was written, embeds nothing, and exits 130.
        return results, True, in_hand
    return results, False, None


def _written_in_hand(in_hand: InHand | None, done: int) -> tuple[TrackResult, ...]:
    """The result of the track a Ctrl-C stopped in hand, when it wrote its .lrc.

    *done* counts the results kept; a track in hand at that place has none. A .lrc
    may sit beside a track planned to be timed and stay there (a stamps-only file a
    .txt with words outranks, a link, a folder), so only a regular file whose entry
    differs from the one before the track was taken counts. The write replaces the
    entry atomically, under a new inode, so a re-time is told apart too.
    """
    if in_hand is None or in_hand.index != done:
        return ()
    outcome = WRITING_OUTCOMES.get(in_hand.planned.action)
    audio = in_hand.planned.audio
    entry = _entry(audio.with_suffix(LRC_SUFFIX))
    if outcome is None or entry is None or not stat.S_ISREG(entry.st_mode):
        return ()
    if _identity(entry) == in_hand.before:
        return ()
    return (TrackResult(audio, outcome),)


def _interrupted(
    plans: Sequence[Planned],
    results: Sequence[TrackResult],
    in_hand: InHand | None,
    discovered: Sequence[Path],
    root: Path,
    album: Sequence[str],
) -> int:
    """After a Ctrl-C while timing: the footer with the album commands, no embed; 130.

    *album* names the album tool in each command. The track in hand counts as timed
    when its new .lrc was already in place.
    """
    ended = [*results, *_written_in_hand(in_hand, len(results))]
    units = sy_embed.embed_units(sy_summary.written_tracks(ended), discovered, root)
    footer = sy_summary.footer_lines(
        ended,
        album=album,
        units=units.units,
        loose=units.loose,
        sidecars=units.sidecars,
    )
    left = len(plans) - len(ended)
    for line in [sy_summary.interrupted_line(left), *footer]:
        say(line)
    return EXIT_INTERRUPTED


def _finish(
    results: Sequence[TrackResult],
    units: sy_embed.EmbedUnits,
    embed: bool,
    seams: Seams,
    album: Sequence[str],
) -> int:
    """Embed *units* when asked, else name their commands; print the footer; the exit.

    *album* names the album tool in each command.
    """
    if embed:
        embeds = sy_embed.embed_folders(units, seams.album_entry, seams.embed)
        footer = sy_summary.footer_lines(results, album=album, sidecars=units.sidecars)
    else:
        embeds = []
        footer = sy_summary.footer_lines(
            results,
            album=album,
            units=units.units,
            loose=units.loose,
            sidecars=units.sidecars,
        )
    for line in sy_summary.embed_lines(embeds, album) + footer:
        say(line)
    return sy_summary.exit_code(results, embeds)


def _dry_run(plans: Sequence[Planned], problems: Sequence[str], root: Path) -> int:
    """Print the setup check and each plan row; exit 2 on a setup problem, 1 on a refusal."""
    for problem in problems or ["ok"]:
        say(f"setup: {problem}")
    for planned in plans:
        for line in sy_summary.plan_lines(planned, root):
            say(line)
    if problems:
        return EXIT_SETUP
    refused = any(UNTIMED_OUTCOMES.get(planned.action) in FAILED for planned in plans)
    return EXIT_FAILED if refused else EXIT_OK


def fetch_mode(options: Options, opener: at_models.Opener) -> int:
    """Download each missing or mismatched model into the model folder; the exit code."""
    options.model_dir.mkdir(parents=True, exist_ok=True)
    results = at_models.fetch_models(options.model_dir, opener, sy_models.MANIFEST)
    for line in at_models.fetch_lines(results):
        say(line)
    return at_models.fetch_exit_code(results)
