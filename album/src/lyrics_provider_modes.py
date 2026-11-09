"""The modes that ask the providers: a run, its dry run, and a compare.

A run and a compare each resolve the optional providers, their cool-downs and the
Genius key, ask the providers, and print the summary; a dry run plans the album and
prints the plan. `discover`, the album and its tracks with each link skipped, is
shared with a strip. A run and a compare each hand on only the steps of the
providers --only-providers names, whatever table the wiring built (`lyrics_only`).
The entry point passes its own notice writer, so this module owns no stream of its
own: only the summary goes to stdout from here.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import lyrics_album
import lyrics_breakers
import lyrics_changed
import lyrics_choices
import lyrics_compare
import lyrics_cooldown
import lyrics_exit
import lyrics_modes
import lyrics_only
import lyrics_run
import lyrics_silence
import lyrics_summary
from lyrics_exit import EXIT_OK, EXIT_TRACK_ERRORS
from lyrics_fetch import PROVIDER_DEADLINE_S
from lyrics_genre import NO_LYRICS_GENRES
from lyrics_model import ProviderTable, Source
from lyrics_options import DEFAULT_LANG
from lyrics_records import GeniusState
from lyrics_wiring import Wiring

#: The entry point's notice writer: one line to stderr, made printable.
Notice = Callable[[str], None]


def discover(
    args: argparse.Namespace, notice: Notice
) -> tuple[Path, lyrics_album.Discovery]:
    """The album and its tracks, each link skipped with a notice."""
    album = lyrics_album.resolve_album(args.album)
    found = lyrics_album.discover_tracks(album)
    for link in found.links:
        relative = link.relative_to(album)
        notice(f"skipped {relative}: a symbolic link or junction is not followed")
    return album, found


def _cooling_report(
    source: Source,
    chosen: lyrics_choices.OptionalProviders,
    providers: ProviderTable,
    notice: Notice,
) -> str:
    """*source*'s summary label once a run or compare ended; a failed write noticed."""
    stop = lyrics_cooldown.table_stop(providers, source)
    return lyrics_choices.cooling_report(source, chosen.cooling(source), stop, notice)


def _report_cooling_on_failure(
    chosen: lyrics_choices.OptionalProviders, providers: ProviderTable, notice: Notice
) -> None:
    """Each cooling source's failed-write notice, said as a run or compare raises."""
    # The album's own failure decides the exit, never a stream too closed to take a
    # notice; and one source's lost notice never skips the next source's. The sources
    # are the cool-down table's, read when called, so a new one needs no edit here.
    for source in lyrics_cooldown.STATE_FILES:
        with contextlib.suppress(OSError):
            _cooling_report(source, chosen, providers, notice)
    with contextlib.suppress(OSError):
        lyrics_breakers.report_write_errors(providers, notice)


def _dry_run(
    args: argparse.Namespace,
    album: Path,
    tracks: Sequence[Path],
    wiring: Wiring,
    prog: str,
    notice: Notice,
) -> int:
    """Report what a run would do, writing nothing."""
    no_lyrics_genres = lyrics_choices.no_lyrics_choice(args, wiring.environ, notice)
    # Resolved though the plan asks no provider, so an unusable --config stops it too.
    lyrics_choices.musicbrainz_contact(args.config, wiring.environ, notice)
    _key, label = lyrics_choices.genius_choice(args, wiring.environ, notice)
    lang = args.lang or DEFAULT_LANG
    planned = lyrics_modes.plan_album(
        album,
        tracks,
        wiring.stages,
        offline=bool(args.offline),
        lang=lang,
        fresh=bool(args.fresh),
        refetch_untimed=bool(args.refetch_untimed),
        refetch_implausible=bool(args.refetch_implausible),
        no_lyrics_genres=no_lyrics_genres,
    )
    plan = lyrics_summary.format_plan(planned, album=album, prog=prog, genius=label)
    with lyrics_exit.report_writes():
        print(plan, end="", file=sys.stdout)
    return EXIT_TRACK_ERRORS if any(t.error for t in planned) else EXIT_OK


@dataclass(frozen=True)
class AskedProviders:
    """The providers a run asks, as its options chose them, and its summary's labels.

    *label* is the Genius label the summary shows; *chosen* the optional providers'
    settings and cool-downs (`lyrics_choices.OptionalProviders`); *table* the steps
    asked, none for an offline run.
    """

    genius: GeniusState
    label: str
    chosen: lyrics_choices.OptionalProviders
    table: ProviderTable


def choose_providers(
    args: argparse.Namespace, wiring: Wiring, notice: Notice
) -> AskedProviders:
    """The providers *args* ask for: the Genius key, the optional ones, --only-providers.

    Genius first: whether a key was found decides whether its breaker file is read.
    """
    key, label = lyrics_choices.genius_choice(args, wiring.environ, notice)
    chosen = lyrics_choices.optional_providers(
        args, wiring.environ, wiring.now, notice, genius=key is not None
    )
    table = (
        {}
        if args.offline
        else lyrics_only.restrict(
            wiring.build_providers(key, PROVIDER_DEADLINE_S, chosen.settings),
            args.only_providers,
        )
    )
    genius = GeniusState.CONFIGURED if key is not None else GeniusState.ABSENT
    return AskedProviders(genius, label, chosen, table)


def provider_options(
    args: argparse.Namespace,
    asked: AskedProviders,
    *,
    no_lyrics_genres: tuple[str, ...] = NO_LYRICS_GENRES,
    ffmpeg: str | None = None,
) -> lyrics_run.RunOptions:
    """The run options *args* and *asked* give, each cool-down read where it is used."""
    chosen, table = asked.chosen, asked.table
    return lyrics_run.RunOptions(
        args.lang or DEFAULT_LANG,
        PROVIDER_DEADLINE_S,
        asked.genius,
        fresh=bool(args.fresh),
        refetch_untimed=bool(args.refetch_untimed),
        refetch_implausible=bool(args.refetch_implausible),
        no_lyrics_genres=no_lyrics_genres,
        bandcamp=chosen.bandcamp.state,
        youtube_music=chosen.reader(Source.YOUTUBE_MUSIC, table),
        ffmpeg=ffmpeg,
        tekstowo=chosen.reader(Source.TEKSTOWO, table),
        retried=chosen.retried(),
    )


def run_reported(
    asked: AskedProviders, notice: Notice, run: Callable[[], lyrics_run.RunReport]
) -> lyrics_run.RunReport:
    """*run*'s report; as it raises, each cool-down's and breaker's failed write said."""
    try:
        return run()
    except BaseException:
        _report_cooling_on_failure(asked.chosen, asked.table, notice)
        raise


def finish_run(
    args: argparse.Namespace,
    album: Path,
    report: lyrics_run.RunReport,
    asked: AskedProviders,
    prog: str,
    notice: Notice,
    *,
    silence: str | None,
    held: Path | None = None,
) -> int:
    """Write the changed list and the run's summary, and return the run's exit code.

    *silence* is the silence check's label, None for a run that measures nothing.
    *held* is a fix's ``--hold-for-genius`` file, written beside the changed list.
    """
    chosen, providers = asked.chosen, asked.table
    with lyrics_exit.report_writes():
        youtube_label = _cooling_report(Source.YOUTUBE_MUSIC, chosen, providers, notice)
        tekstowo_label = _cooling_report(Source.TEKSTOWO, chosen, providers, notice)
        lyrics_breakers.report_write_errors(providers, notice)
        written = lyrics_changed.write_changed_list(
            args.changed_list,
            lambda: lyrics_changed.run_changed(album, report.tracks),
            notice,
        )
        held_written = lyrics_changed.write_changed_list(
            held,
            lambda: lyrics_changed.held_changed(album, report.tracks),
            notice,
            flag=lyrics_changed.HOLD_FLAG,
        )
        written = lyrics_changed.joined(written, held_written)
        summary = lyrics_summary.format_run(
            report,
            album=album,
            prog=prog,
            command=args.command_prefix,
            genius=asked.label,
            bandcamp=chosen.bandcamp.label,
            youtube_music=youtube_label,
            silence=silence,
            tekstowo=tekstowo_label,
        )
        print(summary, end="", file=sys.stdout)
        return lyrics_exit.report_exit(report, prog, written, notice)


def run_mode(
    args: argparse.Namespace, wiring: Wiring, prog: str, notice: Notice
) -> int:
    """Run the album, or plan it for a dry run, and print the summary."""
    album, found = discover(args, notice)
    tracks = lyrics_album.track_selection(album, found, args.track)
    if args.dry_run:
        return _dry_run(args, album, tracks, wiring, prog, notice)
    no_lyrics_genres = lyrics_choices.no_lyrics_choice(args, wiring.environ, notice)
    asked = choose_providers(args, wiring, notice)
    ffmpeg = wiring.find_ffmpeg()
    options = provider_options(
        args, asked, no_lyrics_genres=no_lyrics_genres, ffmpeg=ffmpeg
    )
    report = run_reported(
        asked,
        notice,
        lambda: lyrics_run.run_album(
            album,
            tracks,
            asked.table,
            options,
            wiring.stages,
            acquire_lock=wiring.acquire_lock,
            started=wiring.now(),
            progress=notice,
        ),
    )
    silence = lyrics_silence.check_label(ffmpeg)
    return finish_run(args, album, report, asked, prog, notice, silence=silence)


def compare_mode(
    args: argparse.Namespace, wiring: Wiring, prog: str, notice: Notice
) -> int:
    """Compare each untimed-only track's lyrics with the providers' answers; write nothing."""
    album, found = discover(args, notice)
    tracks = lyrics_album.track_selection(album, found, args.track)
    key, label = lyrics_choices.genius_choice(args, wiring.environ, notice)
    chosen = lyrics_choices.optional_providers(
        args, wiring.environ, wiring.now, notice, genius=key is not None
    )
    providers = lyrics_only.restrict(
        wiring.build_providers(key, PROVIDER_DEADLINE_S, chosen.settings),
        args.only_providers,
    )
    try:
        report = lyrics_compare.compare_album(
            album,
            tracks,
            providers,
            wiring.stages,
            lang=args.lang or DEFAULT_LANG,
            deadline_s=PROVIDER_DEADLINE_S,
            notice=notice,
        )
    except BaseException:
        _report_cooling_on_failure(chosen, providers, notice)
        raise
    with lyrics_exit.report_writes():
        youtube_label = _cooling_report(Source.YOUTUBE_MUSIC, chosen, providers, notice)
        tekstowo_label = _cooling_report(Source.TEKSTOWO, chosen, providers, notice)
        lyrics_breakers.report_write_errors(providers, notice)
        summary = lyrics_summary.format_compare(
            report,
            album=album,
            prog=prog,
            command=args.command_prefix,
            genius=label,
            bandcamp=chosen.bandcamp.label,
            youtube_music=youtube_label,
            tekstowo=tekstowo_label,
        )
        print(summary, end="", file=sys.stdout)
    return EXIT_TRACK_ERRORS if report.has_errors else EXIT_OK
