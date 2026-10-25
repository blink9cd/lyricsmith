"""The machine-made label: whisper.cpp's header block swapped for ours.

A finished .lrc opens with exactly two header lines, the [re:] line naming this
program, the engine and the model, and the [by:machine transcript] line that marks
the file as ours. Every value comes from a Label built at run time. Pure: nothing
here reads a file or runs a process.
"""

from __future__ import annotations

from pathlib import Path

from at_types import (
    MACHINE_BY_LINE,
    TIMESTAMP_LINE,
    TOOL_LABEL,
    Label,
    spell_unsafe,
    split_lines,
)


def re_line(label: Label) -> str:
    """The [re:] header: this program, the engine and the model, all read at run time.

    Each value is escaped: a model's stem or the tool's version may
    hold a line break, which would split the header and put a line of the value's
    before the by-line, so the file would no longer read as ours.
    """
    tool, engine, model = (
        spell_unsafe(value)
        for value in (label.tool_version, label.engine_version, label.model_name)
    )
    return f"[re:{TOOL_LABEL} {tool}; whisper.cpp {engine}; {model}]"


# fuzz-target
def relabel(lrc_text: str, label: Label) -> str:
    """Replace whisper.cpp's header block with ours; the timed body is kept verbatim.

    The header block is every line before the first timestamped line. The result
    has LF line endings and one final newline.

    Raises:
        ValueError: the text has no timestamped line, so there is no body to keep.
    """
    lines = split_lines(lrc_text)
    first = next(
        (i for i, line in enumerate(lines) if TIMESTAMP_LINE.match(line)), None
    )
    if first is None:
        raise ValueError("the .lrc has no timestamped line")
    return "\n".join([re_line(label), MACHINE_BY_LINE, *lines[first:]]) + "\n"


def model_name(model: Path) -> str:
    """The name the label shows: the model file's stem."""
    return model.stem
