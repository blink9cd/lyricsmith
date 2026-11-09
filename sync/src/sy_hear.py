"""The hearing mode: hear each selected track's text without timing it, and log the verdict.

A hearing separates each track's vocals, transcribes them with whisper-cli and judges
the text by the sung check's rule, as a timing run does before it aligns, then stops: no
``.lrc``, frame or sidecar is written. Every verdict, sung, not sung or too little
heard, goes to the hearing log (``--hearing-log``); a text not sung also goes to the
``--heard-dir`` store and the ``--by-hand-list``, as a timing run keeps one. The setup
check, the store's reading and cleaning, and the walk are the timing run's. A child
that fails, times out or replies malformed is that track's ``ERROR hear`` row, and the
run goes on; only what stops a timing run stops a hearing. A Ctrl-C stops it, every
hearing logged so far kept, and exits 130.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
import at_discover
import sy_by_hand
import sy_heard_dir
import sy_hear_plan
import sy_hear_rows
import sy_modes
import sy_summary
from at_models import WHISPER_MODEL_NAME
from at_modes import say
from lyrics_album import deferred_interrupt
from sy_hear_options import HearOptions
from sy_hear_plan import HearAction, HearPlan, Selection
from sy_hear_rows import HearResult
from sy_hear_track import Hearer, Recorder
from sy_types import EXIT_INTERRUPTED, Options, SetupError, Whisper


def _read_listed(
    target: Path | None,
) -> tuple[tuple[sy_by_hand.Row, ...], list[str]]:
    """The --by-hand-list rows, and the setup problem when it cannot be read."""
    if target is None:
        return (), []
    try:
        return sy_by_hand.read_by_hand_list(target), []
    except sy_by_hand.ByHandListError as error:
        return (), [f"{sy_by_hand.OPTION} {error}"]


def _read_tracks(target: Path | None) -> tuple[frozenset[str] | None, list[str]]:
    """The --tracks-from paths, None for every track; and why the list cannot be read."""
    if target is None:
        return None, []
    try:
        return frozenset(sy_hear_plan.read_tracks_from(target)), []
    except SetupError as error:
        return None, [str(error)]


def hear_mode(
    options: Options, hear: HearOptions, seams: sy_modes.Seams, *, prog: Sequence[str]
) -> int:
    """Plan every selected track under --path, then hear each (or print the plan).

    *prog* is this tool's words as it was run, which the setup check's commands name.
    """
    path = options.path
    if path is None:
        raise SetupError("--path is required in hear mode")
    listed, unread = _read_listed(options.by_hand_list)
    store, unread_store = sy_heard_dir.read_store(options.heard_dir)
    log = sy_hear_plan.read_log(hear.hearing_log)
    tracks, unread_tracks = _read_tracks(hear.tracks_from)
    whisper_cli = sy_modes.find_whisper_cli(seams)
    problems = [
        *sy_modes.setup_problems(path, options, seams, prog, whisper_cli),
        *unread,
        *unread_store,
        *log.setup,
        *unread_tracks,
    ]
    if problems and not options.dry_run:
        raise SetupError("; ".join(problems))
    keeper, _, said = sy_heard_dir.open_store(
        options.heard_dir, store, path, dry_run=options.dry_run
    )
    for line in [*said, *log.problems]:
        say(line)
    model = options.whisper_model or WHISPER_MODEL_NAME
    selection = Selection(hear.kinds, model, log.by_track, tracks)
    root = at_discover.walk_root(path)
    found = path.exists()
    plans = (
        sy_hear_plan.plan_hearings(path, options.extensions, selection) if found else []
    )
    if options.dry_run or whisper_cli is None:
        # Without whisper-cli the setup check failed, and only a dry run gets past it.
        return _dry_run(plans, problems, root)
    kept = sy_heard_dir.Kept(sy_by_hand.Recorder(options.by_hand_list, listed), keeper)
    whisper = Whisper(whisper_cli, options.whisper_model_dir)
    hearer = Hearer(options.model_dir, whisper, model, seams.run)
    recorder = Recorder(hear.hearing_log, model, kept)
    results, interrupted = _hear_each(plans, hearer, recorder, root)
    if interrupted:
        say(sy_hear_rows.interrupted_line(len(plans) - len(results)))
    _footer(results, recorder)
    if interrupted:
        return EXIT_INTERRUPTED
    return sy_hear_rows.exit_code(results, recorder.any_failed)


def _hear_each(
    plans: Sequence[HearPlan], hearer: Hearer, recorder: Recorder, root: Path
) -> tuple[list[HearResult], bool]:
    """Each plan's result, its rows printed as it ends; whether a Ctrl-C stopped it.

    A track's hearing is recorded with Ctrl-C held, so its log record, its store
    record and its list row are kept together or not at all, before its rows print.
    """
    results: list[HearResult] = []
    try:
        for plan in plans:
            if plan.action is HearAction.HEAR:
                heard = hearer.hear(plan)
                with deferred_interrupt():
                    results.append(recorder.record(plan, heard))
            else:
                results.append(sy_hear_rows.skipped(plan))
            for line in sy_hear_rows.result_lines(results[-1], root):
                say(line)
    except KeyboardInterrupt:
        # Not swallowed: the caller prints the footer and exits 130.
        return results, True
    return results, False


def _footer(results: Sequence[HearResult], recorder: Recorder) -> None:
    """Print the counts, then the lines naming the log, the list and the store."""
    kept = recorder.kept
    lines = [
        sy_hear_rows.count_line(results),
        *sy_hear_rows.logged_lines(recorder.added, recorder.folder),
        *sy_summary.by_hand_lines(kept.recorder.added, kept.recorder.target),
        *sy_heard_dir.kept_lines(kept.keeper),
    ]
    for line in lines:
        say(line)


def _dry_run(plans: Sequence[HearPlan], problems: Sequence[str], root: Path) -> int:
    """Print the setup check and each plan row; exit 2 on a setup problem, 1 on a refusal."""
    for problem in problems or ["ok"]:
        say(f"setup: {problem}")
    for plan in plans:
        for line in sy_hear_rows.plan_lines(plan, root):
            say(line)
    return sy_hear_rows.plans_exit(plans, problems)
