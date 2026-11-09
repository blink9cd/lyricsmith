"""``--mode fix-text``: replace each wrong text sync found, or remove and flag it.

The text is a track's untimed text alone or, failing that, a provider's timed ``.lrc``
(`lyrics_fix_text.text_of`), the step drawing for each as `lyrics_fix_step` says.

The mode reads the transcript store `--heard-dir` names (`lyrics_heard_store`), matches
its records to the album's tracks, or the one ``--track`` names, by file identity
(`records_for`), and plans each matched track before anything is locked: skipped, with
why (`lyrics_fix_text.skip_reason`), or taken. Each store file that cannot be read is
one notice, and the rest are still used.

Before that, the mode reads whether the album looks spoken (`lyrics_spoken`): a spoken
genre on any of its tracks, or most of all its tracks, whatever ``--track`` names,
holding a record. On such an album the step withholds every removal
(`FixPlan.spoken`).

A dry run prints that plan, a taken track's line saying whether another track's text
on the album would be written (that much is offline) or the providers asked, and on a
spoken album that no removal would follow; it writes nothing and asks no provider. So
does a run with no track to take, closing on ``nothing to fix``.

Otherwise each skipped track is one notice, and the taken tracks alone go to
`lyrics_run.run_album` with the fix's step (`lyrics_fix_step.fix_step`), so only they
are backed up, and a fix is undone as a run is. The providers are chosen, their
cool-downs and breakers reported, and the summary printed exactly as for a run
(`lyrics_provider_modes`); each provider left out because it is cooling down is
passed on to the step (`FixPlan.cooling`), which then removes nothing.

With ``--hold-for-genius`` Genius is left out as ``--no-genius`` leaves it
(`lyrics_choices.genius_choice`), the step holds every removal its guards allow
(`FixPlan.hold`), and each held track is written to the option's file, as
``--changed-list`` writes its own (`lyrics_changed.held_changed`). A dry run, or a run
with nothing to fix, writes neither list.

The fix only reads the store: every record is kept, whatever the track's end. A
written track's record is sync's: album never touches sync's by-hand list, so the
track's ``not sung`` row stays, and sync's rerun finds the record's fingerprint
changed, judges the new text against the kept transcript without transcribing again,
times it, and deletes the record itself; without the record, that row would skip the
track for good. A removed track holds no untimed text, so sync plans nothing for it
and a later fix skips it as not untimed only, until undo brings the wrong text back,
which matches the record again for a fix to take. The rest go when the store's
folder is deleted once the library's run is done.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import lyrics_album
import lyrics_choices
import lyrics_exit
import lyrics_run
import lyrics_summary
from lyrics_exit import EXIT_OK, EXIT_TRACK_ERRORS
from lyrics_fix_step import FixPlan, fix_step
from lyrics_fix_text import (
    UNRECORDED_SIBLING,
    SiblingTexts,
    first_sung,
    fix_skip_reason,
    sibling_texts,
    text_of,
    words_of_text,
)
from lyrics_heard_store import HeardRecord, list_records, records_for
from lyrics_modes import PlannedTrack
from lyrics_cooldown import STATE_FILES
from lyrics_model import Source
from lyrics_options import DEFAULT_LANG
from lyrics_provider_states import CoolingState
from lyrics_provenance import recorded_sources
from lyrics_provider_modes import (
    AskedProviders,
    Notice,
    choose_providers,
    discover,
    finish_run,
    provider_options,
    run_reported,
)
from lyrics_records import ErrorStage, StageError
from lyrics_spoken import album_genres, spoken_reason
from lyrics_wiring import Wiring

#: The plan of a taken track whose transcript sings no other text on the album.
PLAN_ASK = "ask the providers"
#: What that plan adds on an album that looks spoken, before why it does.
PLAN_NO_REMOVAL = "no removal"
#: How a skipped track's plan opens, and the closing line when no track is taken.
SKIPPED, NOTHING_TO_FIX = "skipped", "nothing to fix: nothing was written"
#: How a fix's plan names itself when it is no dry run.
_FIX = "fix-text"


@dataclass(frozen=True)
class FixTrack:
    """One track with a record: its record, and its plan, skip or error, if any.

    *plan* is set for a taken track, *skip* for a skipped one, and *error* for one
    whose text could not be read, which is taken too: the run reports its error.
    """

    path: Path
    rel: str
    record: HeardRecord
    plan: str | None = None
    skip: str | None = None
    error: StageError | None = None


@dataclass(frozen=True)
class AlbumPlan:
    """What a fix plans for its album: each track's plan, its texts, why it is spoken.

    *spoken* is `lyrics_spoken.spoken_reason`'s, None when the album does not look so.
    """

    album: Path
    fixes: list[FixTrack]
    siblings: SiblingTexts
    spoken: str | None = None


def _plan_taken(
    rel: str,
    record: HeardRecord,
    words: list[str],
    siblings: SiblingTexts,
    spoken: str | None,
    *,
    timed: bool = False,
) -> str:
    """A taken track's plan: the sibling whose text would win, else ask the providers.

    A *timed* track draws the album's timed texts too, as the step does. Asking, on a
    spoken album, says no removal would follow, and why.
    """
    others = siblings.every_besides(rel) if timed else siblings.besides(rel)
    won = first_sung(others, record.transcript, words)
    if won is None:
        if spoken is None:
            return PLAN_ASK
        return f"{PLAN_ASK}; {PLAN_NO_REMOVAL}: {spoken}"
    # Another track's unrecorded text names no source beyond that track.
    named = "" if won.source is UNRECORDED_SIBLING else f" ({won.source})"
    return f"take {won.origin}'s text{named}"


def plan_track(
    args: argparse.Namespace,
    wiring: Wiring,
    album: Path,
    path: Path,
    record: HeardRecord,
    siblings: SiblingTexts,
    spoken: str | None = None,
) -> FixTrack:
    """*path*'s plan: skipped and why, taken and what would be tried, or its error.

    *spoken* says why the album looks spoken, None when it does not.
    """
    rel = path.relative_to(album).as_posix()
    lang = args.lang or DEFAULT_LANG
    try:
        tags = wiring.stages.read_tags(path)
        stored = text_of(wiring.stages, album, path, tags, lang)
    # A track that cannot be read is still taken: the run reads it again under the
    # lock and reports its error at its own stage, as a dry run's row does here.
    except Exception as error:  # noqa: BLE001 - every read failure is the track's row
        failure = StageError(ErrorStage.SIDECAR, type(error).__name__, str(error))
        return FixTrack(path, rel, record, error=failure)
    reason, refused = fix_skip_reason(stored, record), stored.lyrics
    if refused is None or reason is not None:
        return FixTrack(path, rel, record, skip=reason)
    words = words_of_text(refused.plain_text())
    plan = _plan_taken(rel, record, words, siblings, spoken, timed=stored.timed)
    return FixTrack(path, rel, record, plan)


def _planned_row(track: FixTrack) -> PlannedTrack:
    """*track* as a plan report's row."""
    if track.skip is not None:
        return PlannedTrack(track.rel, f"{SKIPPED}: {track.skip}")
    return PlannedTrack(track.rel, track.plan or "", error=track.error)


def _print_plan(
    args: argparse.Namespace,
    wiring: Wiring,
    album: Path,
    fixes: Sequence[FixTrack],
    prog: str,
    notice: Notice,
) -> int:
    """Print the plan of *fixes*: a dry run's, or a run's with nothing to fix."""
    # Resolved though the plan asks no provider, so an unusable --config stops it too.
    lyrics_choices.musicbrainz_contact(args.config, wiring.environ, notice)
    _key, label = lyrics_choices.genius_choice(args, wiring.environ, notice)
    rows = [_planned_row(track) for track in fixes]
    named = {} if args.dry_run else {"what": _FIX, "closing": NOTHING_TO_FIX}
    report = lyrics_summary.format_plan(
        rows, album=album, prog=prog, genius=label, **named
    )
    with lyrics_exit.report_writes():
        print(report, end="", file=sys.stdout)
    return EXIT_TRACK_ERRORS if any(track.error for track in fixes) else EXIT_OK


def cooling_sources(asked: AskedProviders) -> frozenset[Source]:
    """The providers *asked* left out because each is cooling down."""
    return frozenset(
        source
        for source in STATE_FILES
        if asked.chosen.cooling(source).state is CoolingState.COOLING_DOWN
    )


def _plan_album(args: argparse.Namespace, wiring: Wiring, notice: Notice) -> AlbumPlan:
    """The album, a plan for each of its chosen tracks with a record, its texts.

    Whether it looks spoken is read over all its tracks, whatever ``--track`` names.
    """
    album, found = discover(args, notice)
    tracks = lyrics_album.track_selection(album, found, args.track)
    listing = list_records(args.heard_dir.absolute())
    for problem in listing.problems:
        notice(problem)
    heard = records_for(listing, found.tracks)
    genres = album_genres(wiring.stages, found.tracks)
    spoken = spoken_reason(genres, len(heard), len(found.tracks))
    records = {path: heard[path] for path in tracks if path in heard}
    sources = recorded_sources(album, wiring.stages.read_manifest)
    for run_dir, kind in sources.passed_over:
        shown = run_dir.relative_to(album).as_posix()
        notice(f"passed over the run {shown}: its manifest cannot be read ({kind})")
    lang = args.lang or DEFAULT_LANG
    siblings = sibling_texts(wiring.stages, album, found.tracks, lang, sources)
    fixes = [
        plan_track(args, wiring, album, path, records[path], siblings, spoken)
        for path in tracks
        if path in records
    ]
    return AlbumPlan(album, fixes, siblings, spoken)


def fix_text_mode(
    args: argparse.Namespace, wiring: Wiring, prog: str, notice: Notice
) -> int:
    """Fix each wrong text the store holds a transcript for; or plan it."""
    planned = _plan_album(args, wiring, notice)
    album, fixes = planned.album, planned.fixes
    for track in fixes:
        if track.skip is not None:
            notice(f"{SKIPPED} {track.rel}: {track.skip}; its transcript is kept")
    taken = [track for track in fixes if track.skip is None]
    if args.dry_run or not taken:
        return _print_plan(args, wiring, album, fixes, prog, notice)
    asked = choose_providers(args, wiring, notice)
    options = provider_options(args, asked)
    records = {track.rel: track.record for track in taken}
    hold = args.hold_for_genius
    plan = FixPlan(
        records,
        planned.siblings,
        cooling_sources(asked),
        planned.spoken,
        hold=hold is not None,
    )
    report = run_reported(
        asked,
        notice,
        lambda: lyrics_run.run_album(
            album,
            [track.path for track in taken],
            asked.table,
            options,
            wiring.stages,
            acquire_lock=wiring.acquire_lock,
            started=wiring.now(),
            progress=notice,
            step=partial(fix_step, plan),
        ),
    )
    return finish_run(args, album, report, asked, prog, notice, silence=None, held=hold)
