"""Which genres mark a track as one no lyrics source holds: stand-up and spoken word.

A track is marked when any of its genres equals an entry of the list, compared whole
after trimming surrounding whitespace and case-folding. Never a substring: ``Comedy``
alone is not ``Stand-up Comedy``, so musical comedy, which has lyrics, is not marked.
The config file's ``[no_lyrics]`` table replaces the built-in list (lyrics_config).
"""

from __future__ import annotations

from collections.abc import Sequence

#: The genres whose tracks no lyrics source holds, as taggers commonly spell them.
NO_LYRICS_GENRES: tuple[str, ...] = ("Stand-up Comedy", "Stand-Up", "Spoken Word")


def _folded(genre: str) -> str:
    """*genre* as compared: surrounding whitespace trimmed, case folded."""
    return genre.strip().casefold()


# fuzz-target
def no_lyrics_genre(genres: Sequence[str], listed: Sequence[str]) -> str | None:
    """The first of *genres* that equals an entry of *listed*, as tagged; else None."""
    wanted = {_folded(entry) for entry in listed}
    return next((genre for genre in genres if _folded(genre) in wanted), None)
