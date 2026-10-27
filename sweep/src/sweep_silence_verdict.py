"""The silence pass's verdict on a silent track: likely meant, a ripping mistake, or unsure.

A silent track is judged from three plain values: its title, its length in seconds
(None when unknown) and how many silent tracks its album holds. The first rule that
matches wins:

1. a placeholder title, a length of at most `GAP_MAX_S`, in an album holding at least
   `SPACER_RUN_MIN` silent tracks: likely meant, a spacer run (the silent tracks some
   discs carry before a hidden track);
2. a placeholder title and a length of at most `GAP_MAX_S`: likely meant, a gap track;
3. a real title and a length over `GAP_MAX_S`: likely a ripping mistake, a real song
   that came out silent;
4. anything else: unsure.

An unknown length is neither short nor long, so it always falls to unsure; a length
that is not a finite number (NaN, infinite) counts as unknown. Each verdict
carries a short reason, free of tabs and line breaks, for the list's reason column.
Nothing here reads a file or prints.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

#: The longest a silent track may be, in seconds, and still read as a deliberate gap.
GAP_MAX_S = 10.0
#: How many silent tracks an album must hold for its short placeholders to be a spacer run.
SPACER_RUN_MIN = 3
#: Placeholder titles once folded (`_fold`): exact words, or "track" and a number.
_PLACEHOLDER_WORDS = frozenset({"", "untitled", "silence", "silent"})
#: Any decimal digit, so a number in another script's digits counts too.
_TRACK_NUMBER = re.compile(r"track\d+")


class Verdict(StrEnum):
    """A row's verdict, as the list writes it."""

    #: A gap or spacer track: silent on purpose.
    LIKELY_MEANT = "likely meant"
    #: A real song that came out silent.
    RIPPING_MISTAKE = "likely a ripping mistake"
    #: Neither rule fits.
    UNSURE = "unsure"
    #: The track could not be measured, so it has no judgment at all.
    NOT_MEASURED = "not measured"


@dataclass(frozen=True)
class Judgment:
    """A silent track's verdict and the short reason for it."""

    verdict: Verdict
    reason: str


def _fold(title: str) -> str:
    """*title* compatibility-normalized, case-folded, and kept to letters and digits.

    NFKC first, so a full-width digit or letter folds to its plain form; dropping every
    other character strips spacing, brackets and punctuation in any script.
    """
    folded = unicodedata.normalize("NFKC", title).casefold()
    return "".join(char for char in folded if char.isalnum())


def is_placeholder_title(title: str | None) -> bool:
    """Whether *title* is a placeholder: missing, empty, a placeholder word, or a number.

    The words are untitled, silence and silent. A missing title (no title tag) counts
    as empty. "Track" and a number counts in any spacing (``Track 5``, ``track05``);
    "Track" alone does not.
    """
    folded = _fold(title or "")
    return folded in _PLACEHOLDER_WORDS or _TRACK_NUMBER.fullmatch(folded) is not None


def _seconds(length_s: float) -> str:
    """*length_s* as the reasons write it: one decimal place and a unit."""
    return f"{length_s:.1f} s"


def judge_silent_track(
    title: str | None, length_s: float | None, silent_in_album: int
) -> Judgment:
    """The verdict on a silent track, from its title, length and its album's silent count."""
    placeholder = is_placeholder_title(title)
    kind = "placeholder title" if placeholder else "real title"
    if length_s is None or not math.isfinite(length_s):
        return Judgment(Verdict.UNSURE, f"{kind}, length unknown")
    short = length_s <= GAP_MAX_S
    if placeholder and short and silent_in_album >= SPACER_RUN_MIN:
        return Judgment(
            Verdict.LIKELY_MEANT,
            f"spacer run: {kind}, {_seconds(length_s)}, "
            f"{silent_in_album} silent on the album",
        )
    if placeholder and short:
        return Judgment(
            Verdict.LIKELY_MEANT, f"gap track: {kind}, {_seconds(length_s)}"
        )
    if not placeholder and length_s > GAP_MAX_S:
        return Judgment(Verdict.RIPPING_MISTAKE, f"{kind}, {_seconds(length_s)}")
    return Judgment(Verdict.UNSURE, f"{kind}, {_seconds(length_s)}")
