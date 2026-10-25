"""The title-suffix strip the fetch chain's retry asks with, as pure functions.

`strip_title_suffixes` removes a title's trailing bracketed qualifiers, ``(Live)``,
``[Remastered]``, ``(feat. X)``, so a retry can ask for the song by its bare name, and
`stripped_retry` decides whether that retry runs: the one rule the chain applies over
a whole pass and compare applies per provider. It sits apart from `lyrics_model`, the
shared types, because it is a rule of the query, not a type another stage shares.

Pure: no I/O, no printing, no third-party import. The runtime floor is Python 3.12.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

from lyrics_model import AttemptOutcome, TrackQuery

#: Bracket pairs a title's trailing qualifier may be wrapped in, closer -> opener.
_TITLE_BRACKETS = {")": "(", "]": "["}
_TITLE_OPENERS = frozenset(_TITLE_BRACKETS.values())


def _trailing_group_start(title: str) -> int | None:
    """Where the well-nested bracket group closing *title* opens, or None.

    None both when *title* does not end with a closer and when the group it closes is
    unbalanced or crossed with the other bracket kind, as in ``([Live)]``.
    """
    pending: list[str] = []
    for index in range(len(title) - 1, -1, -1):
        char = title[index]
        if char in _TITLE_BRACKETS:
            pending.append(char)
        elif char in _TITLE_OPENERS:
            if not pending or _TITLE_BRACKETS[pending.pop()] != char:
                return None
            if not pending:
                return index
        if not pending:
            return None
    return None


def strip_title_suffixes(title: str) -> str:
    """*title* without its trailing bracketed groups, or *title* itself.

    Removes any ``(...)`` or ``[...]`` group at the end, repeatedly, so
    ``Song (feat. X) [Live]`` becomes ``Song``. Only trailing groups go, so a group
    mid-title stays. When nothing was removed, or removing would leave nothing, the
    title comes back unchanged, so ``strip_title_suffixes(t) != t`` is exactly "a
    stripped retry has a different query to try". So does a title whose trailing
    brackets are unbalanced or crossed, as in ``Song ([Live)]``: which part is the
    qualifier cannot be told, and a guess could cut into the name itself.
    """
    remaining = title.rstrip()
    removed = False
    while (start := _trailing_group_start(remaining)) is not None:
        remaining = remaining[:start].rstrip()
        removed = True
    if remaining[-1:] in _TITLE_BRACKETS or not removed or not remaining:
        return title
    return remaining


def stripped_retry(
    query: TrackQuery, outcomes: Iterable[AttemptOutcome], *, found: bool
) -> TrackQuery | None:
    """The query for the stripped-title retry, or None when the retry should not run.

    It runs only on "nothing found": no lyrics were *found* and some step answered,
    with no lyrics. Steps that all failed are the fetch error, and a title that strips
    to itself has nothing new to ask. *outcomes* are the steps' outcomes, a whole
    pass's for the chain, one provider's for compare.
    """
    stripped_title = strip_title_suffixes(query.title)
    answered = any(outcome is AttemptOutcome.EMPTY for outcome in outcomes)
    if found or not answered or stripped_title == query.title:
        return None
    return dataclasses.replace(query, title=stripped_title, stripped=True)
