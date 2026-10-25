"""Whether a run asked each optional provider, as its manifest records it.

Each state is optional when a manifest is read, so one written before the field existed
reads its provider as off, under the same manifest schema. Nothing here reads or writes
a file.
"""

from __future__ import annotations

from enum import StrEnum


class BandcampState(StrEnum):
    """Whether a run asked Bandcamp; a manifest written before it existed reads OFF."""

    ON = "on"
    OFF = "off"


class CoolingState(StrEnum):
    """Whether a run asked a cooling source, and why not; one written before it reads OFF.

    COOLING_DOWN: a refusal's cool-down still held when the run began, so it was not
    asked. STOPPED: it refused or challenged a request during the run, which then asked
    it no more.
    """

    ON = "on"
    OFF = "off"
    COOLING_DOWN = "cooling down"
    STOPPED = "stopped"


class SilenceCheck(StrEnum):
    """Whether a run measured its missed tracks; a manifest written before it reads OFF."""

    ON = "on"
    OFF = "off"


def cooling_off() -> CoolingState:
    """OFF: the state of a run that never asks a cooling source, a strip's among them."""
    return CoolingState.OFF
