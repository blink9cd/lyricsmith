"""lyrics_wiring: what the entry point runs with, and the real binding of each part.

`Wiring` holds the stages, the provider factory, the lock, the environment, the
clock and the ffmpeg lookup; `real_wiring` binds the real ones, and a test passes its
own `Wiring` instead. The entry point re-exports `Wiring` and `real_wiring`, so
callers and tests reach both through it. Importing it assumes the entry point's
``sys.path`` bootstrap has run, as ``run_lock`` is found only through it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import lyrics_located  # noqa: F401 - puts the shared libraries on sys.path
import lyrics_album
import lyrics_backup
import lyrics_check
import lyrics_fetch
import lyrics_frames
import lyrics_manifest
import lyrics_sidecar
import lyrics_silence
import lyrics_stages
import lyrics_strip
import lyrics_table
import lyrics_tags
import lyrics_undo
import run_lock
from lyrics_cooldown import utc_now
from lyrics_model import ProviderTable
from lyrics_records import Secret
from lyrics_table import ProviderSettings


class BuildProviders(Protocol):
    """Build the run's provider table from the Genius key, the deadline and the settings."""

    def __call__(
        self,
        key: Secret | None,
        deadline_s: float,
        settings: ProviderSettings = ...,
    ) -> ProviderTable:
        """The table; building it sends nothing."""


def _no_ffmpeg() -> str | None:
    """No ffmpeg: a wiring built by hand leaves the silence check off."""
    return None


def find_ffmpeg() -> str | None:
    """The ffmpeg on PATH, looked up at call time so a test's patch of lyrics_silence holds."""
    return lyrics_silence.find_ffmpeg()


@dataclass(frozen=True)
class Wiring:
    """What `main` runs with: the stages, providers, lock and clock, and ffmpeg's lookup."""

    stages: lyrics_stages.Stages
    build_providers: BuildProviders
    acquire_lock: lyrics_album.AcquireLock
    environ: Mapping[str, str]
    now: Callable[[], datetime]
    find_ffmpeg: Callable[[], str | None] = _no_ffmpeg


def real_providers(
    key: Secret | None,
    deadline_s: float,
    settings: ProviderSettings = ProviderSettings(),
) -> ProviderTable:
    """The real provider table, every step's whole call bounded by the run's deadline.

    *settings* says which optional steps it holds; the defaults hold none of them.
    """
    return lyrics_table.build_provider_table(
        key, deadline_s=deadline_s, settings=settings
    )


def real_wiring() -> Wiring:
    """The real stages, provider table, run_lock's lock, UTC clock and PATH's ffmpeg."""
    stages = lyrics_stages.Stages(
        read_tags=lyrics_tags.read_tags,
        write_lyrics=lyrics_tags.write_lyrics,
        normalize_sidecars=lyrics_sidecar.normalize_sidecars,
        write_sidecar=lyrics_sidecar.write_sidecar,
        fetch=lyrics_fetch.fetch_lyrics,
        check=lyrics_check.check_track,
        back_up=lyrics_backup.back_up_album,
        write_manifest=lyrics_manifest.write_manifest,
        select_run=lyrics_backup.select_run,
        read_manifest=lyrics_manifest.read_manifest,
        load_before=lyrics_backup.load_before_frames,
        load_before_sidecar=lyrics_backup.load_before_sidecar,
        undo=lyrics_undo.undo_run,
        strip_track=lyrics_strip.strip_track,
        check_writable=lyrics_tags.check_writable,
        check_encodable=lyrics_frames.check_encodable,
        measure_level=lyrics_silence.measure_level,
    )
    lock, clock = run_lock.acquire, utc_now
    return Wiring(stages, real_providers, lock, os.environ, clock, find_ffmpeg)
