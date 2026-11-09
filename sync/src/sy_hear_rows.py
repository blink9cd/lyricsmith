"""The hearing's rows: how each track ended, the counts, the footer and the exit code.

Pure: it builds lines; the mode prints them. A row is the timing run's: its label
padded to a shared width, then the track's path relative to --path.
"""

from __future__ import annotations

import enum
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

import sy_located  # noqa: F401 - puts album's modules on sys.path
from lyrics_heard import MIN_HEARD_PAIRS, Heard, shown_share
from lyrics_hearing_log import HeardKind, HeardVerdict
from sy_hear_plan import HearAction, HearPlan
from sy_summary import DETAIL_INDENT, LABEL_GAP, shown_path
from sy_types import EXIT_FAILED, EXIT_OK, EXIT_SETUP, Outcome


class HearOutcome(enum.Enum):
    """How one track's hearing ended; its row prints the value."""

    SUNG = "heard: sung"
    NOT_SUNG = "heard: not sung"
    LITTLE_HEARD = "heard: little heard"
    SKIP_HEARD = HearAction.SKIP_HEARD.value
    SKIP_KIND = HearAction.SKIP_KIND.value
    SKIP_FRAMES_ONLY = HearAction.SKIP_FRAMES_ONLY.value
    SKIP_NO_TEXT = HearAction.SKIP_NO_TEXT.value
    SKIP_LINKED = HearAction.SKIP_LINKED.value
    SKIP_LONG_TRACK = Outcome.SKIP_LONG_TRACK.value
    ERROR_TEXT = HearAction.ERROR_TEXT.value
    ERROR_AUDIO = HearAction.ERROR_AUDIO.value
    #: The child failed, timed out or replied malformed: nothing was heard.
    ERROR_HEAR = "ERROR hear"


#: Each verdict, and the outcome its hearing ends as.
OF_VERDICT: Final[Mapping[HeardVerdict, HearOutcome]] = MappingProxyType(
    {
        HeardVerdict.SUNG: HearOutcome.SUNG,
        HeardVerdict.NOT_SUNG: HearOutcome.NOT_SUNG,
        HeardVerdict.LITTLE_HEARD: HearOutcome.LITTLE_HEARD,
    }
)
#: Each planned action that hears nothing, and the outcome it ends as.
OF_ACTION: Final[Mapping[HearAction, HearOutcome]] = MappingProxyType(
    {
        action: HearOutcome(action.value)
        for action in HearAction
        if action is not HearAction.HEAR
    }
)
#: The outcomes that make the run exit 1.
FAILED: Final = frozenset(
    {HearOutcome.ERROR_TEXT, HearOutcome.ERROR_AUDIO, HearOutcome.ERROR_HEAR}
)
#: The widest label any row can carry, so the paths line up.
LABEL_WIDTH: Final = max(len(label.value) for label in (*HearAction, *HearOutcome))


@dataclass(frozen=True)
class HearResult:
    """How one track's hearing ended, with what its row says beneath it."""

    audio: Path
    outcome: HearOutcome
    detail: tuple[str, ...] = ()


def heard_detail(kind: HeardKind, heard: Heard, verdict: HeardVerdict) -> str:
    """The line beneath a hearing's row: the kind, then what was heard of the text."""
    if verdict is HeardVerdict.LITTLE_HEARD:
        return (
            f"{kind}: only {heard.pairs} word pairs heard, under {MIN_HEARD_PAIRS}:"
            " too few to judge the text"
        )
    return (
        f"{kind}: {shown_share(heard)} of the {heard.pairs} word pairs heard are"
        " the text's"
    )


def skipped(plan: HearPlan) -> HearResult:
    """The result of a plan that hears nothing: its outcome, its detail beneath it."""
    detail = (plan.detail,) if plan.detail else ()
    return HearResult(plan.audio, OF_ACTION[plan.action], detail)


def _row(label: str, audio: Path, root: Path) -> str:
    """One row: the label padded to the shared width, then the path."""
    return f"{label:<{LABEL_WIDTH + LABEL_GAP}}{shown_path(audio, root)}"


def plan_lines(plan: HearPlan, root: Path) -> list[str]:
    """The dry run's row for *plan*, then its detail as its own line."""
    row = _row(plan.action.value, plan.audio, root)
    return [row, f"{DETAIL_INDENT}{plan.detail}"] if plan.detail else [row]


def result_lines(result: HearResult, root: Path) -> list[str]:
    """*result*'s row, then each line of its detail."""
    row = _row(result.outcome.value, result.audio, root)
    return [row, *(f"{DETAIL_INDENT}{line}" for line in result.detail)]


def count_line(results: Sequence[HearResult]) -> str:
    """Each outcome seen and its count, in the order outcomes are declared."""
    counts = Counter(result.outcome for result in results)
    seen = [f"{o.value} {counts[o]}" for o in HearOutcome if counts[o]]
    return ", ".join(seen) or "nothing to hear"


def logged_lines(added: int, folder: Path) -> list[str]:
    """The footer's line naming the hearing log, when this run logged hearings in it."""
    return [f"logged {added} hearing(s) in {folder}"] if added else []


def interrupted_line(left: int) -> str:
    """The line a Ctrl-C during the hearing prints: how many tracks it left unheard."""
    return (
        f"interrupted: hearing stopped with {left} track(s) left; a second run hears"
        " the rest"
    )


def plans_exit(plans: Sequence[HearPlan], problems: Sequence[str]) -> int:
    """A dry run's exit: 2 on a setup problem, 1 on an error row, else 0."""
    if problems:
        return EXIT_SETUP
    refused = any(OF_ACTION.get(plan.action) in FAILED for plan in plans)
    return EXIT_FAILED if refused else EXIT_OK


def exit_code(results: Sequence[HearResult], failed: bool) -> int:
    """1 when any track failed or a record could not be kept (*failed*); else 0."""
    if failed or any(result.outcome in FAILED for result in results):
        return EXIT_FAILED
    return EXIT_OK
