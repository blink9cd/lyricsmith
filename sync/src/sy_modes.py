"""The two modes: a run over a path, and the model fetch.

A run checks its setup first and touches no track when anything is missing: the
child's packages, ffmpeg, a model, the path. Every outside call the modes make comes
through `Seams`, so the black-box tests fake each one. A folder the walk cannot read,
and a track whose .lrc an earlier track already claims, are each a failed row, never
dropped.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shlex
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import at_discover
import at_engine
import at_models
import sy_embed
import sy_models
import sy_plan
import sy_summary
from at_modes import say
from sy_track import time_or_skip
from sy_types import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_SETUP,
    Action,
    Label,
    Options,
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


@dataclass(frozen=True)
class Seams:
    """Every outside call a run makes, so a test can fake each."""

    run: at_engine.Runner
    embed: sy_embed.AlbumRunner
    album_entry: Path
    find_spec: Callable[[str], object | None]
    which: Callable[[str], str | None]
    version: Callable[[str], str]


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


def setup_problems(path: Path, options: Options, seams: Seams) -> list[str]:
    """Each reason the run cannot start, in the order a person would fix them."""
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
        folder = shlex.quote(str(options.model_dir))
        fetch = f"lyricsmith sync --mode fetch-models --model-dir {folder}"
        problems += [*models, f"fetch them: {fetch}"]
    return problems


def _unreadable_folder(error: OSError, root: Path) -> Planned:
    """The row of a folder the walk could not read; it names the root when unnamed."""
    folder = root if error.filename is None else Path(str(error.filename))
    reason = error.strerror or str(error)
    return Planned(
        folder, Action.ERROR_TEXT, detail=f"could not read the folder: {reason}"
    )


def plan_all(
    path: Path, options: Options, root: Path
) -> tuple[list[Planned], list[Path]]:
    """Each unreadable folder's row and each track's plan, claims settled; every track.

    Every track found, planned or not, decides the folders --embed runs album on.
    """
    errors: list[OSError] = []
    audio = at_discover.find_audio(path, options.extensions, errors)
    plans = [sy_plan.plan_track(track, overwrite=options.overwrite) for track in audio]
    folders = [_unreadable_folder(error, root) for error in errors]
    return folders + sy_plan.claim_lrcs(plans), audio


def run_mode(options: Options, seams: Seams, *, tool_version: str) -> int:
    """Plan every track under --path, then time each (or print the plan); the exit code."""
    path = options.path
    if path is None:
        raise SetupError("--path is required in run mode")
    problems = setup_problems(path, options, seams)
    if problems and not options.dry_run:
        raise SetupError("; ".join(problems))
    root = at_discover.walk_root(path)
    plans, discovered = plan_all(path, options, root) if path.exists() else ([], [])
    if options.dry_run:
        return _dry_run(plans, problems, root)
    label = Label(tool_version, seams.version("demucs"), seams.version("torchaudio"))
    results = []
    for planned in plans:
        result = time_or_skip(planned, options.model_dir, label, seams.run)
        for line in sy_summary.result_lines(result, root):
            say(line)
        results.append(result)
    written = sy_summary.written_tracks(results)
    units = sy_embed.embed_units(written, discovered, root)
    return _finish(results, units, options.embed, seams)


def _finish(
    results: Sequence[TrackResult],
    units: sy_embed.EmbedUnits,
    embed: bool,
    seams: Seams,
) -> int:
    """Embed *units* when asked, else name their commands; print the footer; the exit."""
    if embed:
        embeds = sy_embed.embed_folders(units, seams.album_entry, seams.embed)
        footer = sy_summary.footer_lines(results)
    else:
        embeds = []
        footer = sy_summary.footer_lines(results, units=units.units, loose=units.loose)
    for line in sy_summary.embed_lines(embeds) + footer:
        say(line)
    return sy_summary.exit_code(results, embeds)


def _dry_run(plans: Sequence[Planned], problems: Sequence[str], root: Path) -> int:
    """Print the setup check and each plan row; exit 2 on a setup problem, 1 on a refusal."""
    for problem in problems or ["ok"]:
        say(f"setup: {problem}")
    for planned in plans:
        say(sy_summary.plan_line(planned, root))
    if problems:
        return EXIT_SETUP
    refused = any(planned.action is Action.ERROR_TEXT for planned in plans)
    return EXIT_FAILED if refused else EXIT_OK


def fetch_mode(options: Options, opener: at_models.Opener) -> int:
    """Download each missing or mismatched model into the model folder; the exit code."""
    options.model_dir.mkdir(parents=True, exist_ok=True)
    results = at_models.fetch_models(options.model_dir, opener, sy_models.MANIFEST)
    for line in at_models.fetch_lines(results):
        say(line)
    return at_models.fetch_exit_code(results)
