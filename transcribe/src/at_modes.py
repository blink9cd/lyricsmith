"""The two modes: transcribe (setup check, then each file in turn) and fetch-models.

The entry point parses and validates the options, then hands them here. A transcribe
run checks its setup before touching any file (exit 2 on a problem); after that one
file's failure is that file's row and the run goes on (the bulk rule). Every line
that quotes a path or an argument value is printed through `say`, so a hostile file
name cannot put a raw control or direction character on the terminal.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import at_discover
import at_engine
import at_label
import at_models
import at_place
import at_plan
import at_summary
import stdio_utf8
from at_types import (
    EXIT_OK,
    EXIT_SETUP,
    Action,
    FileResult,
    Label,
    Options,
    Outcome,
    Planned,
    SetupError,
    spell_unsafe,
)

WHISPER_CLI = "whisper-cli"
#: One file's planned action, or the failed row of a file or folder planning could
#: not judge (an unreadable folder or output location): reported, never dropped.
Item = Planned | FileResult
#: The failed row's reason for a found name that is not a regular file.
NOT_REGULAR_DETAIL = "not a regular file (a FIFO, socket or device): never opened"


def terminal_safe(text: str) -> str:
    """*text* with each control or direction character spelled, then stdio_utf8's own."""
    return stdio_utf8.printable(spell_unsafe(text))


def say(line: str, stream: IO[str] | None = None) -> None:
    """Print one human-facing line, escaped, to *stream* (stdout when None), flushed."""
    print(terminal_safe(line), file=stream or sys.stdout, flush=True)


def _which(name: str) -> Path | None:
    """shutil.which as a Path."""
    found = shutil.which(name)
    return Path(found) if found else None


@dataclass(frozen=True)
class SetupCheck:
    """What the setup check found: its printed lines, any problems, the program, its version."""

    lines: tuple[str, ...]  # what --dry-run prints
    problems: tuple[str, ...]  # empty when the run may start
    cli: Path | None
    engine_version: str | None


def _engine_line(found: Path | None, version: str | None, read_version: bool) -> str:
    """The setup check's whisper-cli line."""
    if found is None:
        return f"{WHISPER_CLI:<12} not found"
    if not read_version:
        return f"{WHISPER_CLI:<12} {found} (version not read in a dry run)"
    shown = f" (whisper.cpp {version})" if version else ""
    return f"{WHISPER_CLI:<12} {found}{shown}"


def _executable_problem(path: Path) -> str | None:
    """Why path is not a file this process may run, or None when it is."""
    try:
        runnable = path.is_file() and os.access(path, os.X_OK)
    except OSError as error:  # 3.12 raises where a folder above is unsearchable
        return f"{path} cannot be read: {error}"
    return None if runnable else f"{path} is not an executable file"


def _model_check(label: str, path: Path) -> tuple[str, str | None]:
    """A model's setup line, and its problem when it is missing or unreadable."""
    try:
        present = path.is_file()
    except OSError as error:  # 3.12 raises where a folder above is unsearchable
        return f"{label:<12} {path} (unreadable)", f"{path} cannot be read: {error}"
    if present and not os.access(path, os.R_OK):
        # Without this a mode-000 model passes setup and then fails every file.
        return f"{label:<12} {path} (unreadable)", f"{path} cannot be read"
    if present:
        return f"{label:<12} {path} (present)", None
    return (
        f"{label:<12} {path} (missing)",
        f"{path} is missing: run --mode fetch-models first",
    )


def _engine_problems(
    found: Path | None, version: str | None, read_version: bool
) -> list[str]:
    """Why whisper-cli cannot run, if it cannot."""
    if found is None:
        return [
            "whisper-cli was not found on PATH: brew install whisper.cpp, "
            "or pass --whisper-cli"
        ]
    problem = _executable_problem(found)
    if problem is not None:
        return [problem]
    if read_version and version is None:
        return [
            f"{found} --version printed no version; is it whisper.cpp's whisper-cli?"
        ]
    return []


def check_setup(
    options: Options, run: at_engine.Runner, *, read_version: bool
) -> SetupCheck:
    """Find whisper-cli, read its version when asked, and look for both models.

    The program found is made absolute, so the file checked here is the file run: a
    relative --whisper-cli, or one found through a relative PATH entry, would
    otherwise be checked against the working folder and then run by PATH search.
    """
    named = options.whisper_cli or _which(WHISPER_CLI)
    found = None if named is None else named.absolute()
    version: str | None = None
    if read_version and found is not None and _executable_problem(found) is None:
        version = at_engine.read_engine_version(found, run=run)
    lines = [_engine_line(found, version, read_version)]
    problems = _engine_problems(found, version, read_version)
    for label, path in (("model", options.model), ("vad model", options.vad_model)):
        line, problem = _model_check(label, path)
        lines.append(line)
        if problem is not None:
            problems.append(problem)
    return SetupCheck(tuple(lines), tuple(problems), found, version)


def _write_failed(planned: Planned, error: OSError) -> FileResult:
    """A file whose output could not be written; its row says why."""
    return FileResult(
        planned.audio, Outcome.FAILED, (f"could not write the output: {error}",)
    )


def _transcribe_file(
    planned: Planned, options: Options, cli: Path, label: Label, run: at_engine.Runner
) -> FileResult:
    """Engine, label and place for one file; any failure is this file's row.

    A failed file removes the destination folders it created, while they are empty.
    """
    created = at_place.missing_folders(planned.lrc.parent)
    try:
        workdir = at_place.make_workdir(planned.lrc.parent)
    except OSError as error:
        result = _write_failed(planned, error)
    else:
        result = _run_in_workdir(planned, options, cli, label, run, workdir)
    if result.outcome is Outcome.FAILED:
        at_place.prune_folders(created)
    return result


def _run_in_workdir(
    planned: Planned,
    options: Options,
    cli: Path,
    label: Label,
    run: at_engine.Runner,
    workdir: Path,
) -> FileResult:
    """Engine, label and place inside the file's temporary folder, then remove it."""
    try:
        argv = at_engine.build_argv(
            cli,
            options.model,
            options.vad_model,
            options.language,
            planned.audio,
            workdir,
        )
        engine = at_engine.transcribe_one(argv, workdir, run=run)
        if not engine.ok:
            return FileResult(planned.audio, Outcome.FAILED, engine.detail)
        at_place.place_pair(workdir, at_label.relabel(engine.lrc_text, label), planned)
    except OSError as error:
        # Also a failure after the .lrc landed: that .lrc is ours, so --overwrite
        # repairs the pair.
        return _write_failed(planned, error)
    finally:
        # A leftover dot-folder is harmless: the walk skips it, and the failure
        # to remove it must not turn a placed file into a failed one.
        with contextlib.suppress(OSError):
            at_place.remove_workdir(workdir)
    done = (
        Outcome.OVERWRITTEN
        if planned.action is Action.OVERWRITE
        else Outcome.TRANSCRIBED
    )
    return FileResult(planned.audio, done)


def _result_for(
    item: Item, options: Options, cli: Path, label: Label, run: at_engine.Runner
) -> FileResult:
    """A row already failed in planning, or a skipped or refused file's, as is."""
    if isinstance(item, FileResult):
        return item
    if item.action is Action.SKIPPED:
        return FileResult(item.audio, Outcome.SKIPPED)
    if item.action is Action.REFUSED:
        return FileResult(item.audio, Outcome.REFUSED)
    return _transcribe_file(item, options, cli, label, run)


def _unreadable_folder(error: OSError, root: Path) -> FileResult:
    """The row of a folder the walk could not read: counted, never dropped.

    An OSError need not carry a filename; the row then names the walk root.
    """
    reason = error.strerror or str(error)
    folder = root if error.filename is None else Path(str(error.filename))
    return FileResult(
        folder,
        Outcome.FAILED,
        (f"could not read the folder: {reason}",),
    )


def _entry_row(audio: Path, named: bool) -> FileResult | None:
    """The row of a found name that is no audio to run; None when it is audio.

    A link the walk found is a skipped row; anything that is not a regular file, or
    cannot be stat'ed, is a failed one (`at_discover.classify`).
    """
    try:
        entry = at_discover.classify(audio, named=named)
    except OSError as error:
        return FileResult(audio, Outcome.FAILED, (f"could not read it: {error}",))
    if entry is at_discover.Entry.LINKED:
        return FileResult(audio, Outcome.LINKED)
    if entry is at_discover.Entry.NOT_REGULAR:
        return FileResult(audio, Outcome.FAILED, (NOT_REGULAR_DETAIL,))
    return None


def _plan_one(audio: Path, root: Path, options: Options, named: bool) -> Item:
    """One file's planned action, or its row when it is no audio or its output is unknown.

    *named* is whether --path named this file itself rather than a folder holding it.
    """
    row = _entry_row(audio, named)
    if row is not None:
        return row
    try:
        [planned] = at_plan.plan_files(
            [audio], root, options.output_dir, options.overwrite
        )
    except OSError as error:  # 3.12 raises where a folder above is unsearchable
        detail = (f"could not check the output: {error}",)
        return FileResult(audio, Outcome.FAILED, detail)
    return planned


def _plan(options: Options, path: Path, root: Path) -> list[Item]:
    """Discover, then plan each file; unreadable folders lead as failed rows.

    A file whose .lrc an earlier file already claims becomes a failed row too.
    """
    errors: list[OSError] = []
    found = at_discover.find_audio(path, options.extensions, errors)
    items: list[Item] = [_unreadable_folder(error, root) for error in errors]
    named = root != path  # walk_root is the file's folder when --path names a file
    items.extend(_plan_one(audio, root, options, named) for audio in found)
    return at_plan.claim_outputs(items, root)


def _dry_run(options: Options, path: Path, root: Path, setup: SetupCheck) -> int:
    """Print the setup check and each file's planned action; touch nothing."""
    for line in setup.lines:
        say(line)
    items = _plan(options, path, root)
    if not items:
        say(f"no audio files found under {path}")
    failed = [item for item in items if isinstance(item, FileResult)]
    for item in items:
        for line in _dry_run_lines(item, root):
            say(line)
    return EXIT_SETUP if setup.problems else at_summary.exit_code(failed)


def _dry_run_lines(item: Item, root: Path) -> list[str]:
    """A planned file's row, or a failed row with its reasons."""
    if isinstance(item, FileResult):
        return at_summary.result_lines(item, root)
    return [at_summary.plan_line(item, root)]


def _run_files(
    planned: Sequence[Item],
    root: Path,
    options: Options,
    cli: Path,
    label: Label,
    run: at_engine.Runner,
) -> list[FileResult]:
    """Each file in turn, its rows printed as it finishes."""
    results = []
    for item in planned:
        result = _result_for(item, options, cli, label, run)
        for line in at_summary.result_lines(result, root):
            say(line)
        results.append(result)
    return results


def transcribe_mode(
    options: Options, run: at_engine.Runner, *, tool_version: str, prog: str
) -> int:
    """Setup check first; then each file in turn, and the count line."""
    path = options.path
    if path is None:  # pragma: no cover - resolve_options refused it already
        raise SetupError("--mode transcribe needs --path")
    root = at_discover.walk_root(path)
    setup = check_setup(options, run, read_version=not options.dry_run)
    for problem in setup.problems:
        say(f"{prog}: {problem}", sys.stderr)
    if options.dry_run:
        return _dry_run(options, path, root, setup)
    if setup.problems or setup.cli is None or setup.engine_version is None:
        return EXIT_SETUP
    model = at_label.model_name(options.model)
    label = Label(tool_version, setup.engine_version, model)
    planned = _plan(options, path, root)
    if not planned:
        say(f"no audio files found under {path}")
        return EXIT_OK
    results = _run_files(planned, root, options, setup.cli, label, run)
    say(at_summary.count_line(results))
    return at_summary.exit_code(results)


def fetch_mode(
    options: Options,
    opener: at_models.Opener,
    manifest: Sequence[at_models.ModelFile],
) -> int:
    """Download what is missing or mismatched; one row per file."""
    results = at_models.fetch_models(
        options.model_dir, opener=opener, manifest=manifest
    )
    for line in at_models.fetch_lines(results):
        say(line)
    return at_models.fetch_exit_code(results)
