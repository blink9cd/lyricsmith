"""The sweep's reports as text: the survey, the final table, the undo and resume commands.

Albums are named relative to the library; a command a person may paste names the album
or the library by its full path, quoted for the shell. Every line passes through
`stdio_utf8.spell_unsafe`, and every table cell before its column is measured, as the
album tool's own reports do: a folder name is text the sweep did not write, and one
holding a control character prints as visible escapes, its row still lined up. A
pasted command then holds those escapes as text, the trade-off the album tool's own
backup footer makes. Nothing here prints.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_model import Source
from stdio_utf8 import spell_unsafe

from sweep_child import HEARD_DIR_OPTION, IMPLAUSIBLE_OPTION, REFETCH_OPTION
from sweep_ledger import LedgerEntry, Outcome
from sweep_run import StopReason, SweepOptions, SweepReport
from sweep_survey import AlbumSurvey, VisitPlan
from sweep_totals import fix_lines, total_lines
from sweep_waiting import PassedOver, waiting_lines

#: Said after every survey, so the uploads a first sweep can cause are expected.
RESAVE_NOTE = (
    "note: an album whose lyrics were embedded by other tools may have each track\n"
    "  saved once by its first run, even where the words stay the same: the frames\n"
    "  written from a timed sidecar keep blank lines those tools left out. In a\n"
    "  library kept in a synced cloud folder, that is one upload per track."
)
#: Said under the albums a run passes over as done; ``{options}`` is the pass's option
#: and ``--refetch-implausible``, each if given, so the hint names the run that would
#: visit them.
RESTART_HINT = "a run with --mode run{options} --restart visits these as well"
_SURVEY_HEADER = ("album", "synced", "untimed", "none", "unreadable")
_SWEEP_HEADER = (
    "album",
    "run",
    "exit",
    "outcome",
    "written",
    "unchanged",
    "not found",
    "removed",
    "no lyrics expected",
    "silent",
    "kept",
    "errors",
    "flagged",
    "refusing",
)
_STOPS = {
    StopReason.FINISHED: "finished",
    StopReason.LIMIT: "stopped at --limit",
    StopReason.INTERRUPTED: "interrupted",
    StopReason.LEDGER: "stopped: the ledger cannot be read or written",
}
_GAP = "  "


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """*rows* under *header*, the first column left-aligned and the rest right-aligned.

    Each cell is made printable first, so its column is measured as it prints.
    """
    rows = [tuple(map(spell_unsafe, row)) for row in rows]
    widths = [max(len(row[i]) for row in (header, *rows)) for i in range(len(header))]
    return [
        _GAP.join(
            cell.ljust(width) if i == 0 else cell.rjust(width)
            for i, (cell, width) in enumerate(zip(row, widths, strict=True))
        ).rstrip()
        for row in (header, *rows)
    ]


def _text(lines: Iterable[str]) -> str:
    """*lines* as printed text, each one through `spell_unsafe`."""
    return "".join(f"{spell_unsafe(line)}\n" for line in lines)


def _name(album: Path, library: Path) -> str:
    """*album* relative to *library*, as the reports name it."""
    return album.relative_to(library).as_posix()


def _survey_row(name: str, survey: AlbumSurvey) -> tuple[str, ...]:
    """One survey table row."""
    counts = (survey.synced, survey.untimed, survey.none, len(survey.unreadable))
    return (name, *(str(count) for count in counts))


def refetch_options(*, refetch_untimed: bool, refetch_implausible: bool) -> list[str]:
    """The refetch options a run was given, in the order a command line names them."""
    given = (
        (refetch_untimed, REFETCH_OPTION),
        (refetch_implausible, IMPLAUSIBLE_OPTION),
    )
    return [option for asked, option in given if asked]


def _plan_lines(plan: VisitPlan, library: Path, *, refetches: list[str]) -> list[str]:
    """The albums a run would visit, then any it passes over as done, with the way in."""
    lines = [f"albums a run would visit: {len(plan.to_visit)}"]
    lines += [f"  {_name(album, library)}" for album in plan.to_visit]
    if plan.done:
        lines.append(f"already done, passed over by a run: {len(plan.done)}")
        lines += [f"  {_name(album, library)}" for album in plan.done]
        options = "".join(f" {option}" for option in refetches)
        lines.append(RESTART_HINT.format(options=options))
    return lines


def _owes(name: str, debt: Sequence[Source]) -> str:
    """Album *name* and the providers it owes, as the survey and the run name them."""
    return f"{name}  owes: {', '.join(map(str, debt))}"


def _earlier_lines(earlier: Mapping[str, Sequence[Source]]) -> list[str]:
    """The albums ``--owe-earlier-runs`` adds to the visits, each with its debt."""
    lines = [f"owed by earlier runs, visited again: {len(earlier)}"]
    return lines + [f"  {_owes(name, debt)}" for name, debt in earlier.items()]


def format_survey(
    surveys: Sequence[AlbumSurvey],
    library: Path,
    plan: VisitPlan,
    *,
    refetch_untimed: bool = False,
    refetch_implausible: bool = False,
    earlier: Mapping[str, Sequence[Source]] | None = None,
    waiting: Sequence[PassedOver] = (),
) -> str:
    """The survey: a row per album, the totals, the tracks named, the albums to visit.

    The tracks named are those unreadable, those serving implausible lyrics when
    the survey judged that (``--refetch-implausible``), and those whose machine-made
    timed sidecar is not embedded, each with why.

    The table describes every album's tracks; *plan* says which albums a run visits
    and which it passes over as done. *refetch_untimed* says the plan is the refetch
    pass's, so the restart hint names that pass's option; *refetch_implausible*, which
    keeps the pass, is named there too. *earlier*, under ``--owe-earlier-runs``, is
    each album that option adds to the visits, with its debt; None without it.
    *waiting* is each owed album a run would pass over, its whole debt held, which
    *plan* already leaves out of its visits (`sweep_waiting.split_waiting`).
    """
    albums = [s for s in surveys if s.has_mp3]
    total = AlbumSurvey(
        library,
        synced=sum(s.synced for s in albums),
        untimed=sum(s.untimed for s in albums),
        none=sum(s.none for s in albums),
        unreadable=tuple(t for s in albums for t in s.unreadable),
    )
    rows = [_survey_row(_name(s.album, library), s) for s in albums]
    noun = "album" if len(albums) == 1 else "albums"
    rows.append(_survey_row(f"total ({len(albums)} {noun})", total))
    lines = _table(_SURVEY_HEADER, rows)
    lines += [
        f"unreadable: {_name(s.album, library)}/{track}"
        for s in albums
        for track in s.unreadable
    ]
    lines += [
        f"implausible: {_name(s.album, library)}/{track}"
        for s in albums
        for track in s.implausible
    ]
    lines += [
        f"unembedded: {_name(s.album, library)}/{track}"
        for s in albums
        for track in s.unembedded
    ]
    empty = len(surveys) - len(albums)
    if empty:
        lines.append(f"folders with no MP3: {empty}")
    refetches = refetch_options(
        refetch_untimed=refetch_untimed, refetch_implausible=refetch_implausible
    )
    lines += _plan_lines(plan, library, refetches=refetches)
    lines += waiting_lines(waiting)
    if earlier is not None:
        lines += _earlier_lines(earlier)
    lines += RESAVE_NOTE.splitlines()
    return _text(lines)


def _sweep_row(entry: LedgerEntry) -> tuple[str, ...]:
    """One final-table row; a visit with no manifest read shows dashes for its counts."""
    exit_code = "-" if entry.exit_code is None else str(entry.exit_code)
    head = (entry.album, entry.run_id or "-", exit_code, entry.outcome.value)
    if entry.counts is None:
        return (*head, *("-" * (len(_SWEEP_HEADER) - len(head))))
    counts = entry.counts
    numbers = (
        counts.written,
        counts.unchanged,
        counts.not_found,
        counts.removed,
        counts.no_lyrics_expected,
        counts.silent,
        counts.kept,
        counts.errors,
        counts.flagged,
        entry.refusing_tracks,
    )
    return (*head, *(str(number) for number in numbers))


#: The words a printed command starts with: one when run alone, more when dispatched.
Words = tuple[str, ...]
#: The album tool's command under the lyricsmith dispatcher, after the dispatcher's words.
ALBUM_COMMAND = "album"


def command_words(display: str, given: str | None) -> Words:
    """The words a printed command names the sweep by.

    Run alone (*given* None), the sweep's *display* name is one word whatever it holds.
    Given, it is the lyricsmith dispatcher's ``<dispatcher> sweep``, which the dispatcher
    builds from words it controls, so splitting it on its spaces is exact.
    """
    return (display,) if given is None else tuple(given.split(" "))


def album_prog(words: Words, album_file: str) -> Words:
    """The album tool's words as this sweep was run: ``<dispatcher> album``, else *album_file*.

    Only a dispatched sweep's name is more than one word (`command_words`).
    """
    return (album_file,) if len(words) < 2 else (*words[:-1], ALBUM_COMMAND)


def resume_prog(words: Words, sweep_file: str) -> Words:
    """The sweep's words in a resume command: *words* when dispatched, else *sweep_file*.

    Run alone, the entry file's own name is used rather than argv[0]'s, which a test
    runner or a symlink can make something no pasted command would find.
    """
    return (sweep_file,) if len(words) < 2 else words


def undo_command(prog: Sequence[str], album: Path, run_id: str) -> str:
    """The album tool's command undoing run *run_id* of *album*; *prog*'s words name it."""
    return shlex.join(
        [*prog, "--album", str(album), "--mode", "undo", "--run-id", run_id]
    )


def _stop_line(report: SweepReport, options: SweepOptions) -> str:
    """Why the sweep stopped, and what is left."""
    if report.stop is StopReason.REFUSALS:
        why = f"stopped after {options.stop_after} consecutive refused albums"
    else:
        why = _STOPS[report.stop]
    return f"{why}; albums left: {report.remaining}"


def format_sweep(
    report: SweepReport,
    *,
    library: Path,
    options: SweepOptions,
    undo_prog: Sequence[str],
) -> str:
    """The final report: the albums visited, their totals, each one's undo, the stop.

    The totals (`sweep_totals`) follow the table; then each owed album, with what it
    owes, then each owed album passed over, with what it waits on, then each album's
    undo command.
    """
    lines = [f"already done: {report.skipped_done}"]
    if not report.visited:
        lines += waiting_lines(report.passed_over)
    else:
        entries = [v.entry for v in report.visited]
        lines += _table(_SWEEP_HEADER, [_sweep_row(entry) for entry in entries])
        lines += total_lines(entries)
        lines += fix_lines(entries) if options.heard_dir is not None else []
        lines += [
            _owes(entry.album, entry.owed_providers)
            for entry in entries
            if entry.outcome is Outcome.OWED
        ]
        lines += waiting_lines(report.passed_over)
        lines += [
            f"{v.entry.album}  undo: {undo_command(undo_prog, v.album, v.entry.run_id)}"
            for v in report.visited
            if v.entry.run_id is not None
        ]
        lines += [
            f"{v.entry.album}  could not run: {v.error}"
            for v in report.visited
            if v.error is not None
        ]
        providers = sorted(
            {p for v in report.visited for p in v.entry.refusing_providers}
        )
        if providers:
            lines.append(f"refusing providers: {', '.join(providers)}")
    lines += [
        f"{name}  not run: reached through a link since the survey"
        for name in report.dropped
    ]
    lines.append(_stop_line(report, options))
    return _text(lines)


def _sweep_mode(options: SweepOptions) -> str:
    """The sweep's own mode a resume runs: the fix with a transcript store, else run."""
    return "run" if options.heard_dir is None else "fix-text"


def resume_line(
    prog: Sequence[str],
    library: Path,
    options: SweepOptions,
    changed_list: Path | None,
) -> str:
    """The ``resume:`` line printed when a sweep stops early (`resume_command`).

    Escaped like the report: a library named with a control character prints as
    visible escapes, so a pasted command then holds them as text.
    """
    command = resume_command(prog, library, options, changed_list=changed_list)
    return spell_unsafe(f"resume: {command}")


def resume_command(
    prog: Sequence[str],
    library: Path,
    options: SweepOptions,
    *,
    changed_list: Path | None = None,
) -> str:
    """The command that resumes this sweep, named by *prog*'s words: never ``--restart``.

    It carries *changed_list* when there is one, so the next leg adds to the same list,
    and ``--owe-earlier-runs`` when given, so it still owes what it has not revisited.
    A fix pass's resumes the fix, on the same ``--heard-dir``.
    """
    parts = [*prog, "--mode", _sweep_mode(options), "--library", str(library)]
    if options.heard_dir is not None:
        parts += [HEARD_DIR_OPTION, str(options.heard_dir)]
    if options.lang is not None:
        parts += ["--lang", options.lang]
    if options.offline:
        parts.append("--offline")
    parts += refetch_options(
        refetch_untimed=options.refetch_untimed,
        refetch_implausible=options.refetch_implausible,
    )
    if options.owe_earlier_runs:
        parts.append("--owe-earlier-runs")
    parts += options.skip
    parts += ["--pause-seconds", str(options.pause_s)]
    parts += ["--stop-after", str(options.stop_after)]
    if options.limit is not None:
        parts += ["--limit", str(options.limit)]
    if changed_list is not None:
        parts += ["--changed-list", str(changed_list)]
    return shlex.join(parts)
