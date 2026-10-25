"""The end-of-run reports as text: run, strip, dry run, check, undo and compare.

Each report opens with a header naming the tool, the report (the run's id, or "dry run")
and the album, then a table with one row per track, then a line of counts. The entry
point prints them to stdout and nothing else, so redirecting stdout captures exactly the
report. Every line passes through `printable`, and every table cell before its column
is measured, so a name holding a control character prints as visible escapes and its
row still lines up. Pure: this module builds strings and never prints.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Sequence
from pathlib import Path

from lyrics_check import shown
from lyrics_compare import (
    COMPARED,
    CompareReport,
    ComparedTrack,
    CompareVerdict,
    ProviderScore,
)
from lyrics_model import REFUSED_OUTCOMES, LyricsKind, QueryMarker
from lyrics_modes import CheckedTrack, CheckReport, PlannedTrack
from lyrics_outcomes import (
    ACCEPTED,
    describe_removed,
    describe_status,
    is_genre_flagged,
    printable,
)
from lyrics_records import (
    RUN_FOLDER_NAME,
    CheckVerdict,
    ManifestTrack,
    OutcomeStatus,
    TrackOutcome,
    UndoReport,
    UndoStatus,
    UndoTrackResult,
)
from lyrics_run import RunReport
from lyrics_tally import DOT, format_tally, track_count

#: What fills a cell with nothing to say.
EMPTY = "-"
#: A track the run never reached, because it was interrupted first.
NOT_REACHED = "not reached"
#: Between two table columns.
_GAP = "  "
#: The narrowest the row-number column is, so a short table still lines up its header.
_NUMBER_WIDTH = 2
#: How a row's note opens its list of the sidecar links the run left unread.
_LINKS_IGNORED = "sidecar link outside the album ignored"


def header_lines(prog: str, what: str, counted: str, album: Path) -> list[str]:
    """The report's first line and the blank line after it.

    *what* names the report, *counted* what it covers (``3 tracks``).
    """
    return [f"{Path(prog).stem} {what}: {counted} under {album.name}", ""]


def report_text(lines: Iterable[str]) -> str:
    """The report's *lines* as printed text, each one through `printable`."""
    return "".join(f"{printable(line)}\n" for line in lines)


def _note(markers: Sequence[QueryMarker], links_ignored: Sequence[str]) -> str:
    """A row's trailing note: the query's markers, then each sidecar link left unread.

    The markers say how the query departed from the track's own tags; a link is left
    unread when it leads out of the album, and without the note it would look like
    one that leads nowhere.
    """
    parts = [f"query: {', '.join(markers)}"] if markers else []
    if links_ignored:
        parts.append(f"{_LINKS_IGNORED}: {', '.join(links_ignored)}")
    return "; ".join(parts)


def table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], notes: Sequence[str]
) -> list[str]:
    """Numbered rows under *headers*, columns padded, each row's note at its end.

    The first column is the row number, right-aligned; the last is not padded, so no
    line ends in spaces.
    """
    numbered = [[str(i), *map(printable, row)] for i, row in enumerate(rows, 1)]
    cells = [list(headers), *numbered]
    widths = [max(len(row[col]) for row in cells) for col in range(len(headers))]
    widths[0] = max(widths[0], _NUMBER_WIDTH)
    lines = []
    for row, note in zip(cells, ["", *map(printable, notes)], strict=True):
        padded = [row[0].rjust(widths[0])]
        padded += [cell.ljust(width) for cell, width in zip(row[1:-1], widths[1:-1])]
        lines.append(_GAP.join([*padded, row[-1], note]).rstrip())
    return lines


def counts_line(*pairs: tuple[str, int | str]) -> str:
    """The closing counts line, one name and value per pair."""
    return DOT.join(f"{name} {value}" for name, value in pairs)


# --- The run -------------------------------------------------------------------------


def _verdict(after: TrackOutcome) -> str:
    """The check column: the error, the outcome, or the check's verdict.

    A written track shows its verdict alone; an unchanged one says so before it.
    """
    if after.error is not None:
        return after.error.summary()
    if after.status is not OutcomeStatus.WRITTEN:
        return describe_status(after)
    return after.check.summary() if after.check is not None else EMPTY


def run_row(track: ManifestTrack) -> tuple[str, str, str]:
    """One run row's source, kind and check columns."""
    after = track.after
    if after is None:
        return EMPTY, EMPTY, NOT_REACHED
    source = str(after.source) if after.source is not None else EMPTY
    kind = str(after.kind) if after.kind is not None else EMPTY
    return source, kind, _verdict(after)


def _run_counts_line(outcomes: Sequence[TrackOutcome], genius: str) -> str:
    """The run's counts: what was written, unchanged, found nothing, errored or flagged.

    Found nothing is five counts: not found, no lyrics expected (a marked track's
    miss, lyrics_genre), silent (a miss whose audio is silent, lyrics_silence), kept
    existing, and removed, a refetch's track whose implausible old lyrics went.

    Each track counts once among the statuses: synced and unsynced count the written
    tracks by kind, and an unchanged track counts as unchanged alone, whatever its
    kind. Flagged is counted apart, over the tracks whose lyrics were accepted,
    written or unchanged, and any track carrying the no-lyrics-genre FLAG, a kept one
    included: a fresh run's declined answer was never accepted, so its own FLAGs leave
    it counted as kept existing alone.
    """

    def count(status: OutcomeStatus, kind: LyricsKind | None = None) -> int:
        return sum(1 for o in outcomes if o.status is status and kind in (None, o.kind))

    flagged = sum(
        1
        for o in outcomes
        if o.check is not None
        and o.check.verdict is CheckVerdict.FLAG
        and (o.status in ACCEPTED or is_genre_flagged(o.check))
    )
    return counts_line(
        ("synced", count(OutcomeStatus.WRITTEN, LyricsKind.SYNCED)),
        ("unsynced", count(OutcomeStatus.WRITTEN, LyricsKind.UNSYNCED)),
        ("unchanged", count(OutcomeStatus.UNCHANGED)),
        ("not found", count(OutcomeStatus.NOT_FOUND)),
        ("no lyrics expected", count(OutcomeStatus.NO_LYRICS_EXPECTED)),
        ("silent", count(OutcomeStatus.SILENT)),
        ("kept existing", count(OutcomeStatus.KEPT_EXISTING)),
        ("removed", count(OutcomeStatus.REMOVED)),
        ("errors", count(OutcomeStatus.ERROR)),
        ("flagged", flagged),
        ("genius:", genius),
    )


def _optional_lines(
    bandcamp: str | None,
    youtube_music: str | None,
    tekstowo: str | None,
    silence: str | None = None,
) -> list[str]:
    """The optional providers' lines after the counts, then the silence check's, if given."""
    providers = [
        ("bandcamp", bandcamp),
        ("youtube music", youtube_music),
        ("tekstowo", tekstowo),
    ]
    lines = [*providers, ("silence check", silence)]
    return [f"{name}: {label}" for name, label in lines if label is not None]


def command_prefix(display: str, given: str | None) -> str:
    """The words a printed command starts with, quoted for a shell.

    Run alone (*given* None), the tool's *display* name is one word whatever it holds.
    Given, it is the lyricsmith dispatcher's ``<dispatcher> album``, which the dispatcher
    builds from words it controls, so splitting it on its spaces is exact.
    """
    words = (display,) if given is None else tuple(given.split(" "))
    return shlex.join(words)


def _backup_footer(report: RunReport, album: Path, command: str) -> str:
    """The closing line of a run or a strip: where its backup is, and how to undo it.

    The header names the tool by *prog*'s stem; this hint starts with *command*, the
    `command_prefix` of the name the tool was run by, so it can be pasted and run.
    """
    undo = f"{command} --album {shlex.quote(str(album))} --mode undo"
    return f"backup: {RUN_FOLDER_NAME}/{report.run_id}/   undo: {undo}"


def format_run(
    report: RunReport,
    *,
    album: Path,
    prog: str,
    command: str,
    genius: str,
    bandcamp: str | None = None,
    youtube_music: str | None = None,
    silence: str | None = None,
    tekstowo: str | None = None,
) -> str:
    """The run summary: rows, counts, the optional lines (`_optional_lines`), tally, backup."""
    rows = [(t.path, *run_row(t)) for t in report.tracks]
    notes = [
        _note(t.after.query, t.after.sidecar_links_ignored) if t.after else ""
        for t in report.tracks
    ]
    outcomes = [t.after for t in report.tracks if t.after is not None]
    lines = [
        *header_lines(prog, f"run {report.run_id}", track_count(len(rows)), album),
        *table(("#", "track", "source", "kind", "check"), rows, notes),
        _run_counts_line(outcomes, genius),
        *_optional_lines(bandcamp, youtube_music, tekstowo, silence),
        format_tally(report.providers),
        _backup_footer(report, album, command),
    ]
    return report_text(lines)


def _strip_cell(track: ManifestTrack) -> str:
    """The removed column: what the strip removed, its error, or not reached."""
    after = track.after
    if after is None:
        return NOT_REACHED
    if after.error is not None:
        return after.error.summary()
    return describe_removed(after)


def format_strip(report: RunReport, *, album: Path, prog: str, command: str) -> str:
    """The strip summary: what each track lost, the counts, its backup and undo.

    A track with nothing to remove still counts as stripped: it holds no lyrics now.
    """
    statuses = [t.after.status for t in report.tracks if t.after is not None]
    counts = (
        ("stripped", statuses.count(OutcomeStatus.STRIPPED)),
        ("errors", statuses.count(OutcomeStatus.ERROR)),
    )
    rows = [(t.path, _strip_cell(t)) for t in report.tracks]
    lines = [
        *header_lines(prog, f"strip {report.run_id}", track_count(len(rows)), album),
        *table(("#", "track", "removed"), rows, [""] * len(rows)),
        counts_line(*counts),
        _backup_footer(report, album, command),
    ]
    return report_text(lines)


# --- The dry run, the check and undo -------------------------------------------------


def format_plan(
    planned: Sequence[PlannedTrack], *, album: Path, prog: str, genius: str
) -> str:
    """The dry run's report: what each track would use, and that nothing was written."""
    rows = [(t.path, t.error.summary() if t.error else t.plan) for t in planned]
    errors = sum(1 for t in planned if t.error is not None)
    notes = [_note(t.markers, t.links_ignored) for t in planned]
    lines = [
        *header_lines(prog, "dry run", track_count(len(rows)), album),
        *table(("#", "track", "plan"), rows, notes),
        counts_line(("errors", errors), ("genius:", genius)),
        "dry run: nothing was written",
    ]
    return report_text(lines)


def _check_cell(track: CheckedTrack) -> str:
    """The check column: the error, the verdict, or nothing for a track not written."""
    if track.error is not None:
        return track.error.summary()
    return track.check.summary() if track.check is not None else EMPTY


def format_check(report: CheckReport, *, album: Path, prog: str) -> str:
    """The check mode's report: each track's verdict, and the counts."""
    verdicts = [t.check.verdict for t in report.tracks if t.check is not None]
    rows = [(t.path, _check_cell(t)) for t in report.tracks]
    counts = (
        ("passed", verdicts.count(CheckVerdict.PASS)),
        ("flagged", verdicts.count(CheckVerdict.FLAG)),
        ("skipped", verdicts.count(CheckVerdict.SKIP)),
        ("not checked", sum(1 for t in report.tracks if not t.written)),
        ("errors", sum(1 for t in report.tracks if t.error is not None)),
    )
    lines = [
        *header_lines(prog, f"check {report.run_id}", track_count(len(rows)), album),
        *table(("#", "track", "check"), rows, [""] * len(rows)),
        counts_line(*counts),
    ]
    return report_text(lines)


def _displaced(track: UndoTrackResult) -> str:
    """The trailing note naming each sidecar copy an undo set aside, by album path."""
    return f"displaced: {', '.join(track.displaced)}" if track.displaced else ""


def format_undo(report: UndoReport, *, album: Path, prog: str) -> str:
    """The undo mode's report: each track's undo, the copies it set aside, the counts.

    A track's displaced copies are its row's note, an errored row's too, so the
    report alone says where each copy went. A track an interrupted undo never began
    follows the others as `not reached`.
    """
    rows = [
        (t.path, t.error.summary() if t.error is not None else str(t.status))
        for t in report.tracks
    ]
    rows += [(path, NOT_REACHED) for path in report.not_reached]
    notes = [_displaced(t) for t in report.tracks] + ["" for _ in report.not_reached]
    statuses = [t.status for t in report.tracks]
    counts = (
        ("undone", statuses.count(UndoStatus.UNDONE)),
        ("already undone", statuses.count(UndoStatus.ALREADY_UNDONE)),
        ("errors", statuses.count(UndoStatus.ERROR)),
    )
    lines = [
        *header_lines(prog, f"undo {report.run_id}", track_count(len(rows)), album),
        *table(("#", "track", "undo"), rows, notes),
        counts_line(*counts),
    ]
    return report_text(lines)


# --- Compare -------------------------------------------------------------------------

#: What a failed call shows in the scores column, and how a flagged row's verdict opens.
_FAILED, _FLAGGED = "ERROR", f"FLAG: {CompareVerdict.NO_SOURCE_AGREES}"


#: The note a row carries when no run manifest names its lyrics' source, and the cell
#: suffix for the provider that was set aside.
_UNRECORDED, _OWN = "source unrecorded", "own"


def _score_cell(score: ProviderScore) -> str:
    """One provider's part of the scores column: its similarity, ``-`` or ``ERROR``."""
    if score.similarity is not None:
        return f"{score.source} {shown(score.similarity)}"
    return f"{score.source} {_FAILED if score.outcome in REFUSED_OUTCOMES else EMPTY}"


def _scores_cell(track: ComparedTrack) -> str:
    """The scores column: each score in provider order, the set-aside provider as ``own``."""
    cells = {s.source: _score_cell(s) for s in track.scores}
    if track.own is not None:
        cells[track.own] = f"{track.own} {_OWN}"
    ordered = [cells[source] for source in COMPARED if source in cells]
    return DOT.join(ordered) or EMPTY


def _compare_note(track: ComparedTrack) -> str:
    """The trailing note: ``source unrecorded`` first, then the hints."""
    unrecorded = [] if track.recorded else [_UNRECORDED]
    return "; ".join([*unrecorded, *track.hints])


def _compare_cell(track: ComparedTrack) -> str:
    """The verdict column: the error, the verdict (a flag's best, a reason), or ``-``."""
    if track.error is not None:
        return track.error.summary()
    if track.verdict is CompareVerdict.NO_SOURCE_AGREES:
        best = max(s.similarity for s in track.scores if s.similarity is not None)
        return f"{_FLAGGED} (best {shown(best)})"
    if track.verdict is CompareVerdict.NOT_JUDGED:
        return EMPTY
    return f"{track.verdict} ({track.reason})" if track.reason else str(track.verdict)


def format_compare(
    report: CompareReport,
    *,
    album: Path,
    prog: str,
    command: str,
    genius: str,
    bandcamp: str | None = None,
    youtube_music: str | None = None,
    tekstowo: str | None = None,
) -> str:
    """Compare's report: rows, hints, a strip line per flag, counts, the providers' lines."""
    rows = [(t.path, _compare_cell(t), _scores_cell(t)) for t in report.tracks]
    strips = [
        f"strip #{number}: {command} --album {shlex.quote(str(album))} --mode strip "
        f"--track {shlex.quote(str(album / t.path))}"
        for number, t in enumerate(report.tracks, 1)
        if t.verdict is CompareVerdict.NO_SOURCE_AGREES
    ]
    verdicts = [t.verdict for t in report.tracks]
    counts = (
        ("agrees", verdicts.count(CompareVerdict.AGREES)),
        ("flagged", verdicts.count(CompareVerdict.NO_SOURCE_AGREES)),
        ("no second source", verdicts.count(CompareVerdict.NO_SECOND_SOURCE)),
        ("errors", sum(1 for t in report.tracks if t.error is not None)),
        ("not judged", verdicts.count(CompareVerdict.NOT_JUDGED)),
        (
            _UNRECORDED,
            sum(
                1
                for t in report.tracks
                if t.verdict is not CompareVerdict.NOT_JUDGED and not t.recorded
            ),
        ),
        ("genius:", genius),
    )
    notes = [_compare_note(t) for t in report.tracks]
    lines = [
        *header_lines(prog, "compare", track_count(len(rows)), album),
        *table(("#", "track", "verdict", "scores"), rows, notes),
        *strips,
        counts_line(*counts),
        *_optional_lines(bandcamp, youtube_music, tekstowo),
        format_tally(report.providers),
    ]
    return report_text(lines)
