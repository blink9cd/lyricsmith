"""The argparse types of the sweep's numeric options: a count, and a number of seconds.

Each turns the option's text into its value, or raises `argparse.ArgumentTypeError`
with what it must be, which argparse prints as a usage error. Pure: nothing here reads
a file or prints.
"""

from __future__ import annotations

import argparse
import math


def positive_int(text: str) -> int:
    """*text* as a whole number of at least 1, else an argparse error."""
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"must be a whole number of at least 1, not {text!r}"
        )
    return value


def seconds(text: str) -> float:
    """*text* as a finite number of seconds, zero or more, else an argparse error."""
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(
            f"must be a number of seconds, 0 or more, not {text!r}"
        )
    return value
