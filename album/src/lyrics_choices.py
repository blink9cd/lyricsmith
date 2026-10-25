"""The entry point's choices for a run: Genius, genres, contact, Bandcamp, cooling sources.

A choice that reads the environment or warns is handed the *environ* and the *notice*
each warning goes to, so nothing here prints.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import lyrics_config
from lyrics_cooldown import (
    CoolingSetup,
    Stop,
    cooldown_path,
    format_utc,
    is_cooling,
    read_cooldown,
    table_stop,
)
from lyrics_genre import NO_LYRICS_GENRES
from lyrics_model import ProviderTable, Source
from lyrics_provider_states import BandcampState, CoolingState
from lyrics_records import Secret
from lyrics_table import ProviderSettings


@dataclass(frozen=True)
class BandcampChoice:
    """Whether the table asks Bandcamp, the state its manifest records, the summary label."""

    on: bool
    state: BandcampState
    label: str


def bandcamp_choice(args: argparse.Namespace) -> BandcampChoice:
    """Bandcamp's choice: off offline or with --no-bandcamp, else on.

    Offline is named first: an offline run asks no provider, whatever else is given.
    Compare refuses --offline, so it sees only the other two.
    """
    if args.offline:
        return BandcampChoice(False, BandcampState.OFF, "off (offline)")
    if args.no_bandcamp:
        return BandcampChoice(False, BandcampState.OFF, "off (--no-bandcamp)")
    return BandcampChoice(True, BandcampState.ON, "on")


@dataclass(frozen=True)
class CoolingChoice:
    """A cooling source's choice as the run began: its setup, its state and its label.

    *setup* is None when the run does not ask it; *state* is ON, OFF or COOLING_DOWN;
    *label* is the summary's, before any stop.
    """

    setup: CoolingSetup | None
    state: CoolingState
    label: str


def cooling_choice(
    source: Source,
    *,
    flag: str,
    off: bool,
    offline: bool,
    environ: Mapping[str, str],
    now: datetime,
    notice: Callable[[str], None],
    clock: Callable[[], datetime],
) -> CoolingChoice:
    """*source*'s choice: off *offline* or when *off* (by *flag*), off while cooling.

    Neither off case reads the cool-down file. Otherwise *source*'s file is read at
    *now*: one that cannot be used is a notice, under the source's name, and is
    ignored. The setup's *clock* stamps a stop, written to the same file.
    """
    if offline:
        return CoolingChoice(None, CoolingState.OFF, "off (offline)")
    if off:
        return CoolingChoice(None, CoolingState.OFF, f"off ({flag})")
    path = cooldown_path(environ, source)
    read = read_cooldown(path, now)
    if read.warning is not None:
        notice(f"{source.label.lower()}: {read.warning}")
    if read.cool_down is not None and is_cooling(read.cool_down, now):
        label = f"cooling down until {format_utc(read.cool_down.until)}"
        return CoolingChoice(None, CoolingState.COOLING_DOWN, label)
    return CoolingChoice(CoolingSetup(path, now=clock), CoolingState.ON, "on")


def cooling_state(choice: CoolingChoice, stop: Stop | None) -> CoolingState:
    """The state a manifest records: STOPPED once *stop* is set, else the choice's."""
    return CoolingState.STOPPED if stop is not None else choice.state


def cooling_label(choice: CoolingChoice, stop: Stop | None) -> str:
    """The summary's label: what stopped it and until when, else the choice's."""
    if stop is None:
        return choice.label
    return f"stopped after {stop.what}; cooling down until {format_utc(stop.until)}"


def cooling_report(
    source: Source,
    choice: CoolingChoice,
    stop: Stop | None,
    notice: Callable[[str], None],
) -> str:
    """The summary's label once the run or compare ended; a failed cool-down write noticed."""
    if stop is not None and stop.write_error is not None:
        prefix = source.label.lower()
        notice(f"{prefix}: could not write the cool-down file: {stop.write_error}")
    return cooling_label(choice, stop)


def genius_choice(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    notice: Callable[[str], None],
) -> tuple[Secret | None, str]:
    """The Genius key for this run, if any, and how the summary describes it."""
    if args.offline:
        return None, "offline"
    if args.no_genius:
        return None, "skipped"
    resolution = lyrics_config.resolve_genius_key(args.config, environ)
    for warning in resolution.warnings:
        notice(f"genius: {warning}")
    if resolution.secret is None:
        notice(f"genius: no key found (looked in {resolution.searched}); step skipped")
        return None, "absent"
    return resolution.secret, "configured"


def no_lyrics_choice(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    notice: Callable[[str], None],
) -> tuple[str, ...]:
    """The genres whose tracks expect no lyrics: the config file's, else the built-in list."""
    listing = lyrics_config.resolve_no_lyrics(args.config, environ)
    for warning in listing.warnings:
        notice(f"no_lyrics: {warning}")
    return NO_LYRICS_GENRES if listing.genres is None else listing.genres


def musicbrainz_contact(
    config: Path | None,
    environ: Mapping[str, str],
    notice: Callable[[str], None],
) -> str | None:
    """The contact MusicBrainz's User-Agent names, from the config file, if any.

    A default file's unusable contact is a notice, never quoting it; a named file's
    raises `ConfigError`, so the run cannot start.
    """
    listing = lyrics_config.resolve_musicbrainz_contact(config, environ)
    for warning in listing.warnings:
        notice(f"musicbrainz: {warning}")
    return listing.contact


@dataclass(frozen=True)
class OptionalProviders:
    """The optional steps a run or a compare asks: the table's settings, each choice."""

    settings: ProviderSettings
    bandcamp: BandcampChoice
    youtube_music: CoolingChoice
    tekstowo: CoolingChoice

    def cooling(self, source: Source) -> CoolingChoice:
        """The choice of *source*, one of the two cooling sources; KeyError for another."""
        chosen = {
            Source.YOUTUBE_MUSIC: self.youtube_music,
            Source.TEKSTOWO: self.tekstowo,
        }
        return chosen[source]

    def reader(
        self, source: Source, providers: ProviderTable
    ) -> Callable[[], CoolingState]:
        """What reads the state a manifest records for *source*, its stop read anew.

        A stop in *providers* is read at each call, so the write after it says stopped.
        """
        return lambda: cooling_state(
            self.cooling(source), table_stop(providers, source)
        )


def optional_providers(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    clock: Callable[[], datetime],
    notice: Callable[[str], None],
) -> OptionalProviders:
    """The optional steps' settings, the contact read from the config, and each choice.

    YouTube Music's and Tekstowo's each read their own cool-down file once, now on
    *clock*, which each setup keeps to stamp a stop; one's cool-down never turns the
    other off.
    """
    contact = musicbrainz_contact(args.config, environ, notice)
    bandcamp = bandcamp_choice(args)
    youtube = cooling_choice(
        Source.YOUTUBE_MUSIC,
        flag="--no-youtube-music",
        off=args.no_youtube_music,
        offline=args.offline,
        environ=environ,
        now=clock(),
        notice=notice,
        clock=clock,
    )
    tekstowo = cooling_choice(
        Source.TEKSTOWO,
        flag="--no-tekstowo",
        off=args.no_tekstowo,
        offline=args.offline,
        environ=environ,
        now=clock(),
        notice=notice,
        clock=clock,
    )
    settings = ProviderSettings(
        bandcamp=bandcamp.on,
        netease=not args.no_netease,
        musicbrainz_contact=contact,
        youtube_music=youtube.setup,
        tekstowo=tekstowo.setup,
    )
    return OptionalProviders(settings, bandcamp, youtube, tekstowo)
