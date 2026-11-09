"""The entry point's choices for a run: Genius, genres, contact, Bandcamp, cooling sources.

A choice that reads the environment or warns is handed the *environ* and the *notice*
each warning goes to, so nothing here prints. A provider `--only-providers` leaves out
(`lyrics_only`) is off as its own `--no-*` option turns it off, and its summary label
names whichever of the two options left it out, its own first.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import lyrics_config
from lyrics_breaker import GUARDED_SOURCES
from lyrics_breakers import breaker_setups
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
from lyrics_only import left_out
from lyrics_options import ONLY_FLAG
from lyrics_provider_states import BandcampState, CoolingState
from lyrics_records import Secret
from lyrics_retried import RetriedCounts, RetryTally, no_retries
from lyrics_table import ProviderSettings


@dataclass(frozen=True)
class BandcampChoice:
    """Whether the table asks Bandcamp, the state its manifest records, the summary label."""

    on: bool
    state: BandcampState
    label: str


def leaving_option(
    args: argparse.Namespace, source: Source, *, off: bool, flag: str
) -> str | None:
    """The option leaving *source* out: *flag* if *off*, else --only-providers, or None.

    *off* is *source*'s own `--no-*` option, *flag*; it is named first, as it would
    leave the provider out whatever --only-providers named.
    """
    if off:
        return flag
    if left_out(args.only_providers, source):
        return ONLY_FLAG
    return None


def bandcamp_choice(args: argparse.Namespace) -> BandcampChoice:
    """Bandcamp's choice: off offline, with --no-bandcamp or left out, else on.

    Offline is named first: an offline run asks no provider, whatever else is given.
    Compare refuses --offline, so it sees only the others.
    """
    if args.offline:
        return BandcampChoice(False, BandcampState.OFF, "off (offline)")
    leaving = leaving_option(
        args, Source.BANDCAMP, off=bool(args.no_bandcamp), flag="--no-bandcamp"
    )
    if leaving is not None:
        return BandcampChoice(False, BandcampState.OFF, f"off ({leaving})")
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
    # Left out by --only-providers, or by a fix's --hold-for-genius, reads as
    # --no-genius does: no key is looked for, so no request is ever sent to Genius.
    held = args.hold_for_genius is not None
    if args.no_genius or held or left_out(args.only_providers, Source.GENIUS):
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

    def retried(self) -> Callable[[], RetriedCounts]:
        """What reads the run's retries so far: its tally's counts, else none."""
        tally = self.settings.retried
        return no_retries if tally is None else tally.counts


def asked_guarded(
    args: argparse.Namespace, *, bandcamp: bool, netease: bool, genius: bool
) -> list[Source]:
    """The guarded providers (`GUARDED_SOURCES`) this run asks, in their order.

    LRCLIB is asked unless --only-providers leaves it out; each of the others only
    when its own choice, *bandcamp*, *netease* or *genius* (a key found), is on.
    """
    asked = {
        Source.LRCLIB: not left_out(args.only_providers, Source.LRCLIB),
        Source.NETEASE: netease,
        Source.GENIUS: genius,
        Source.BANDCAMP: bandcamp,
    }
    return [source for source in GUARDED_SOURCES if asked[source]]


def optional_providers(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    clock: Callable[[], datetime],
    notice: Callable[[str], None],
    *,
    genius: bool,
) -> OptionalProviders:
    """The optional steps' settings, the contact read from the config, and each choice.

    YouTube Music's and Tekstowo's each read their own cool-down file once, now on
    *clock*, which each setup keeps to stamp a stop; one's cool-down never turns the
    other off. The breaker file of each guarded provider the run asks
    (`asked_guarded`; *genius* is whether a Genius key was found) is read once too,
    unless offline, which asks no provider and so reads and writes none
    (`lyrics_breakers`). A provider turned off, left out by --only-providers, or
    Genius with no key, has its file neither read nor written.
    The settings carry a fresh retry tally (`lyrics_retried`).
    """
    contact = musicbrainz_contact(args.config, environ, notice)
    bandcamp = bandcamp_choice(args)
    youtube_off = leaving_option(
        args,
        Source.YOUTUBE_MUSIC,
        off=bool(args.no_youtube_music),
        flag="--no-youtube-music",
    )
    tekstowo_off = leaving_option(
        args, Source.TEKSTOWO, off=bool(args.no_tekstowo), flag="--no-tekstowo"
    )
    youtube = cooling_choice(
        Source.YOUTUBE_MUSIC,
        flag=youtube_off or "--no-youtube-music",
        off=youtube_off is not None,
        offline=bool(args.offline),
        environ=environ,
        now=clock(),
        notice=notice,
        clock=clock,
    )
    tekstowo = cooling_choice(
        Source.TEKSTOWO,
        flag=tekstowo_off or "--no-tekstowo",
        off=tekstowo_off is not None,
        offline=bool(args.offline),
        environ=environ,
        now=clock(),
        notice=notice,
        clock=clock,
    )
    netease = (
        leaving_option(
            args, Source.NETEASE, off=bool(args.no_netease), flag="--no-netease"
        )
        is None
    )
    settings = ProviderSettings(
        bandcamp=bandcamp.on,
        netease=netease,
        musicbrainz_contact=contact,
        youtube_music=youtube.setup,
        tekstowo=tekstowo.setup,
        breakers={}
        if args.offline
        else breaker_setups(
            environ,
            clock,
            notice,
            sources=asked_guarded(
                args, bandcamp=bandcamp.on, netease=netease, genius=genius
            ),
        ),
        retried=RetryTally(),
    )
    return OptionalProviders(settings, bandcamp, youtube, tekstowo)
