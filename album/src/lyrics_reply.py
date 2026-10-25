"""Reading fields from an LRC provider's decoded JSON reply, refusing any other shape.

The LRC adapters (`lyrics_lrclib`, `lyrics_netease`) read their replies through these,
so a field in a shape the provider is not known to answer in is a `ProviderError`
naming the provider, worded the same way for each, and never a crash or a silent
"nothing found". Each adapter keeps only what is its own, such as the unit a length
is given in.

Nothing here prints.
"""

from __future__ import annotations

from collections.abc import Mapping

from lyrics_model import Source
from lyrics_providers import ProviderError


#: The detail a run records for a reply in a shape the tool does not know.
UNEXPECTED_REPLY = "unexpected reply"


def unexpected_reply(source: Source, what: str) -> ProviderError:
    """The error for *source*'s reply whose *what* is not in the known shape."""
    message = f"{source} answered an unexpected reply: {what}"
    return ProviderError(message, detail=UNEXPECTED_REPLY)


def number_field(
    reply: Mapping[str, object], name: str, source: Source
) -> float | None:
    """Field *name* of *reply* as a number, or None when absent or null.

    JSON ``true`` and ``false`` are not numbers, though Python counts them as ints. A
    whole number too large for a float is out of shape too: JSON's integers have no
    bound, and Python decodes every digit.
    """
    value = reply.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise unexpected_reply(source, f"{name} is not a number")
    try:
        return float(value)
    except OverflowError as error:
        raise unexpected_reply(source, f"{name} is out of range") from error


def flag_field(reply: Mapping[str, object], name: str, source: Source) -> bool:
    """Flag *name* of *reply*; absent is false, and anything but true or false raises."""
    value = reply.get(name, False)
    if not isinstance(value, bool):
        raise unexpected_reply(source, f"{name} is not true or false")
    return value
