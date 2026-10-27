"""`--only-providers`: the providers' option names, the option's value, what it leaves.

The sweep passes the option when it revisits an album still owed answers, so the
revisit asks no provider that already answered. Its value names providers by their
option names, one per provider of the chain (`lyrics_tally.PROVIDERS`), each derived
from the provider's label: lower case, a space a hyphen. So ``youtube-music`` is the
provider `--no-youtube-music` leaves out, and the names are never typed a second time.
LRCLIB, which has no `--no-*` option, is ``lrclib``; MusicBrainz is no provider of its
own, as only the Bandcamp step asks it.

A value is one comma-separated word, its names lowercase as every option value here
is; an empty name, an unknown one or one named twice is an argparse type error, so
the parser exits 2. Pure: nothing here reads a file or prints.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from types import MappingProxyType

from lyrics_model import ProviderTable, Source
from lyrics_tally import PROVIDERS

#: What parts the names in the option's value.
SEPARATOR = ","


def option_name(source: Source) -> str:
    """*source*'s option name: its label in lower case, each space a hyphen."""
    return source.label.lower().replace(" ", "-")


#: Each provider's option name, in chain order, and the provider it names.
PROVIDER_NAMES: Mapping[str, Source] = MappingProxyType(
    {option_name(source): source for source in PROVIDERS}
)
#: The names as an error message lists them.
_LISTING = ", ".join(PROVIDER_NAMES)


def parse_only_providers(text: str) -> frozenset[Source]:
    """The providers *text* names, or an argparse type error saying what is wrong."""
    if not text:
        raise argparse.ArgumentTypeError(f"names no provider; name some of {_LISTING}")
    names = text.split(SEPARATOR)
    if "" in names:
        raise argparse.ArgumentTypeError(
            f"an empty name in {text!r}; part the names with {SEPARATOR!r} alone"
        )
    for name in names:
        if name not in PROVIDER_NAMES:
            raise argparse.ArgumentTypeError(
                f"no provider is named {name!r}; name some of {_LISTING}"
            )
    for index, name in enumerate(names):
        if name in names[:index]:
            raise argparse.ArgumentTypeError(f"{name!r} is named twice")
    return frozenset(PROVIDER_NAMES[name] for name in names)


def left_out(only: frozenset[Source] | None, source: Source) -> bool:
    """Whether *only*, the option's value or None if not given, leaves *source* out."""
    return only is not None and source not in only


def restrict(table: ProviderTable, only: frozenset[Source] | None) -> ProviderTable:
    """*table*'s steps whose provider *only* keeps, in order; all of them for None."""
    return {key: step for key, step in table.items() if not left_out(only, key.source)}
