"""The hearing mode's own options: resolved and checked, and refused in every other mode.

``--hearing-log`` is required in the hearing; ``--kinds``, ``--tracks-from`` and
``--whisper-model`` are optional there. Each is for the hearing only, and the hearing,
which writes no ``.lrc``, takes neither ``--embed`` nor ``--overwrite``; a hearing by
any whisper model but the default, a comparison, takes neither ``--heard-dir`` nor
``--by-hand-list``, so it writes only the log. Each option's
omitted value is None, so a given one is told apart from its default.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import sy_located  # noqa: F401 - puts album's modules on sys.path
from at_models import WHISPER_MODEL_NAME
from lyrics_hearing_log import HeardKind
from sy_types import SetupError

#: The mode's name on the command line.
MODE_HEAR: Final = "hear"
#: The kinds heard when --kinds is not given: a provider's timed texts.
DEFAULT_KINDS: Final = frozenset({HeardKind.PROVIDER_TIMED})
#: The options for the hearing only, by their attribute on the parsed namespace.
HEAR_ONLY: Final = ("hearing_log", "kinds", "tracks_from", "whisper_model")
#: The options a hearing refuses: it writes no .lrc to embed or overwrite.
NOT_FOR_HEAR: Final = ("embed", "overwrite")
#: What a hearing by any whisper model but the default refuses: its not-sung record
#: would replace the default's transcript, against which a later run judges a fixed
#: text, and its row would skip a timing on another model's verdict.
NOT_FOR_COMPARISON: Final = ("heard_dir", "by_hand_list")


@dataclass(frozen=True)
class HearOptions:
    """The hearing's own options: its log, the kinds it hears, the tracks it is given."""

    hearing_log: Path
    kinds: frozenset[HeardKind]
    tracks_from: Path | None = None


def _flag(name: str) -> str:
    """The option an attribute of the parsed namespace stands for."""
    return "--" + name.replace("_", "-")


def refuse_misplaced(namespace: argparse.Namespace) -> None:
    """Raise `SetupError` for an option given in a mode it is not for."""
    if namespace.mode == MODE_HEAR:
        for name in NOT_FOR_HEAR:
            if getattr(namespace, name):
                raise SetupError(
                    f"{_flag(name)} is not for --mode hear: a hearing writes no .lrc"
                )
        _refuse_kept_by_comparison(namespace)
        return
    for name in HEAR_ONLY:
        if getattr(namespace, name) is not None:
            raise SetupError(f"{_flag(name)} is for --mode hear only")


def _refuse_kept_by_comparison(namespace: argparse.Namespace) -> None:
    """Raise `SetupError` for a store or list given to a hearing by a non-default model."""
    model = namespace.whisper_model
    if model in (None, WHISPER_MODEL_NAME):
        return
    for name in NOT_FOR_COMPARISON:
        if getattr(namespace, name) is not None:
            raise SetupError(
                f"{_flag(name)} is not for --whisper-model {model}: a comparison "
                "hearing writes only --hearing-log"
            )


def parse_kinds(text: str) -> frozenset[HeardKind]:
    """--kinds as the kinds it names; `SetupError` when it names none or another."""
    names = {part.strip() for part in text.split(",")} - {""}
    known = {str(kind) for kind in HeardKind}
    if not names or not names <= known:
        raise SetupError(
            f"--kinds must name one or more of {','.join(sorted(known))}: {text!r}"
        )
    return frozenset(HeardKind(name) for name in names)


def resolve_hear(namespace: argparse.Namespace) -> HearOptions:
    """The hearing's options, each path made absolute against the working folder."""
    log = namespace.hearing_log
    if log is None:
        raise SetupError("--hearing-log is required in hear mode")
    kinds = DEFAULT_KINDS if namespace.kinds is None else parse_kinds(namespace.kinds)
    listed = namespace.tracks_from
    return HearOptions(
        log.absolute(), kinds, None if listed is None else listed.absolute()
    )
