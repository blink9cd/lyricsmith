"""One LRC provider's answer to one query, for a synced step and a plain step at once.

An LRC provider's reply holds a song's timed and plain text together, so one call
settles both of its steps. The fetch chain keeps this answer for the rest of the track's
fetch (`lyrics_fetch`), and the plain pass reads it instead of asking the provider
again.

A kind the provider could not settle carries the failure that stopped it, so the step
of that kind records the error without a second request, while a kind already settled
by an earlier request keeps its text.

Pure: no I/O, nothing printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from lyrics_model import LyricsKind

#: What one kind of step gets: its text, None for none, or the failure to raise.
KindAnswer: TypeAlias = str | None | Exception


@dataclass(frozen=True)
class LrcAnswer:
    """What one LRC provider answered for one query, for each kind of step."""

    synced: KindAnswer
    plain: KindAnswer

    @classmethod
    def same(cls, text: str | None) -> LrcAnswer:
        """An answer giving both kinds of step *text*: the chain decides what each takes."""
        return cls(synced=text, plain=text)

    def text(self, kind: LyricsKind) -> str | None:
        """The text a step of *kind* gets; raise the failure that kept it unsettled."""
        answer = self.synced if kind is LyricsKind.SYNCED else self.plain
        if isinstance(answer, Exception):
            raise answer
        return answer
