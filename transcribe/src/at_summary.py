"""The printed rows, the count line and the run's exit code.

A row is a fixed-width label column followed by the audio path, shown relative to
the walk root. Pure: the caller prints what these return.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from at_types import (
    EXIT_FILE_FAILED,
    EXIT_OK,
    Action,
    FileResult,
    Outcome,
    Planned,
    shown_path,
)

#: The spaces between the longest label and the path.
LABEL_GAP = 2
DETAIL_INDENT = "    "


def label_width(labels: Iterable[str]) -> int:
    """The label column's width: the longest label, then the gap."""
    return max(map(len, labels)) + LABEL_GAP


#: Derived from every row label, planned and finished, so a longer one widens it.
LABEL_WIDTH = label_width(label.value for label in (*Action, *Outcome))


def plan_line(planned: Planned, root: Path) -> str:
    """One --dry-run row."""
    return f"{planned.action.value:<{LABEL_WIDTH}}{shown_path(planned.audio, root)}"


def result_lines(result: FileResult, root: Path) -> list[str]:
    """One result row, then its detail lines indented."""
    head = f"{result.outcome.value:<{LABEL_WIDTH}}{shown_path(result.audio, root)}"
    return [head, *(f"{DETAIL_INDENT}{line}" for line in result.detail)]


def count_line(results: Sequence[FileResult]) -> str:
    """A count per outcome, every outcome named, in Outcome order."""
    counts = Counter(result.outcome for result in results)
    return ", ".join(f"{outcome.name.lower()} {counts[outcome]}" for outcome in Outcome)


def exit_code(results: Sequence[FileResult]) -> int:
    """1 when any file failed, else 0."""
    failed = any(result.outcome is Outcome.FAILED for result in results)
    return EXIT_FILE_FAILED if failed else EXIT_OK
