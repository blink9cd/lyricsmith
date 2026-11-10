"""The sweep's modes past the album-visiting ones: names, help, options, refusals, runners.

Three of them are read-only sample modes, and the fourth, the paste walk, writes tracks,
one at a time through the album tool's own ``--mode paste``, never visiting a whole
album. The entry point is near the file-length bar, so each such mode registers here:
the entry point adds `MODE_NAMES` to ``--mode``'s choices and `MODE_HELP` to its help,
calls `epilog` on its own epilog, calls `refuse` before its own refusals, and dispatches
through `RUNNERS`. Each option's keywords are an `OptionSpec` here, but the entry point
names each flag in its own one-line ``add_argument`` call: the CLI-doc gate reads a
tool's options from its entry point's syntax tree and follows no call into another
module, so a flag added only here would be an option it cannot see. A new mode adds one
entry to each table, and one line per option to the entry point.

**The draw** (`sweep_draw`) takes ``--library``, ``--seed`` and ``--out``, both
required there, and ``--withheld``. **The hearing's report** (`sweep_hear_report`)
takes ``--library``, ``--hearing-log`` and ``--strata``, both required there,
``--model`` and ``--compare-model``, which may not name the same model. **The Genius
check** (`sweep_genius_check`) takes ``--tracks-from`` and ``--out``, both required
there; ``--out`` is the draw's too, so an option two modes here own is refused only by
the modes that own neither. **The paste walk** (`sweep_paste`) takes ``--from``, and
``--heard-dir``, both required there, ``--changed-list`` and ``--lang``: the last three
are the sweep's own options, which it shares (`_SHARED`), so the entry point's
refusals are told to let it have them. Each refuses
every other option set to anything but its default, by the parser's own defaults
(`refuse`), so an option added to the sweep later is refused by each with no change
here; every other mode refuses their options the same way. Nothing here prints but
through the parser's own errors.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final, TypedDict

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import sweep_draw
import sweep_genius_check
import sweep_genius_lists
import sweep_hear_report
import sweep_paste
from sweep_types import absolute_path

MODE_DRAW: Final = "draw"
MODE_HEAR_REPORT: Final = "hear-report"
MODE_GENIUS_CHECK: Final = "genius-check"
MODE_PASTE: Final = "paste"
#: The modes registered here, added to ``--mode``'s choices.
MODE_NAMES: Final = (MODE_DRAW, MODE_HEAR_REPORT, MODE_GENIUS_CHECK, MODE_PASTE)
#: Their part of ``--mode``'s help, after the album-visiting modes'.
MODE_HELP: Final = (
    " draw: draw a sample of provider-timed tracks to hear into --out as a track "
    "list and a strata file, reading only."
    " hear-report: report each group of a drawn sample's not-sung rate from the "
    "hearing log, reading only."
    " genius-check: split the --tracks-from list into --out by what Genius's keyed "
    "API has, writing no track."
    " paste: walk the --from has-it list, opening each track's Genius page and "
    "pasting the copied lyrics in through the album tool's paste, at a terminal."
)
#: An option, by its parsed name and its flag.
_Option = tuple[str, str]
#: Each mode's own options, refused by every other mode.
_OWN: Final[Mapping[str, tuple[_Option, ...]]] = MappingProxyType(
    {
        MODE_DRAW: (("seed", "--seed"), ("out", "--out"), ("withheld", "--withheld")),
        MODE_HEAR_REPORT: (
            ("hearing_log", "--hearing-log"),
            ("strata", "--strata"),
            ("model", "--model"),
            ("compare_model", "--compare-model"),
        ),
        MODE_GENIUS_CHECK: (("tracks_from", "--tracks-from"), ("out", "--out")),
        MODE_PASTE: (("from_list", "--from"),),
    }
)
#: The sweep's own options a mode here takes too, which the entry point lets it have.
_SHARED: Final[Mapping[str, tuple[_Option, ...]]] = MappingProxyType(
    {
        MODE_PASTE: (
            ("heard_dir", "--heard-dir"),
            ("changed_list", "--changed-list"),
            ("lang", "--lang"),
        ),
    }
)
#: Of those, the ones each mode requires.
_REQUIRED: Final[Mapping[str, tuple[_Option, ...]]] = MappingProxyType(
    {
        MODE_DRAW: (("seed", "--seed"), ("out", "--out")),
        MODE_HEAR_REPORT: (("hearing_log", "--hearing-log"), ("strata", "--strata")),
        MODE_GENIUS_CHECK: (("tracks_from", "--tracks-from"), ("out", "--out")),
        MODE_PASTE: (("from_list", "--from"), ("heard_dir", "--heard-dir")),
    }
)

#: What a mode's runner is called with: the parsed options, the resolved library,
#: the wiring and the command's words, as the entry point calls every mode.
Runner = Callable[[argparse.Namespace, Path, object, object], int]
#: Each mode's runner, by its name.
RUNNERS: Final[Mapping[str, Runner]] = MappingProxyType(
    {
        MODE_DRAW: sweep_draw.draw_mode,
        MODE_HEAR_REPORT: sweep_hear_report.hear_report_mode,
        MODE_GENIUS_CHECK: sweep_genius_check.genius_check_mode,
        MODE_PASTE: sweep_paste.paste_mode,
    }
)

_EPILOG_TEXT: Final = """\
  Draw writes nothing in the library and asks nothing: it reads each track's
  sidecars and frames, each album's run manifests and the ledger, classifies every
  track, and writes into --out a track list for sync --tracks-from (tracks.txt) and
  a strata file naming each track's group and the seed (strata.tsv), both replaced
  on a rerun. Every provider-timed track of a wrong-text album is taken; each
  provenance group (NetEase, YouTube Music, LRCLIB, the older sidecars) is drawn to
  its share by --seed; with --withheld it lists instead the untimed tracks whose
  removal a fix withheld. It prints the seed and approximate counts per group and
  per reason a track is left out. It exits 0 once both files are written, and 2
  for an --out inside the library, a ledger it cannot read or a file it cannot
  write.

  Hear-report writes nothing and asks nothing: it reads the strata file the draw
  wrote, the hearing log sync --mode hear wrote and the album texts, and prints
  each group's tracks heard, not-sung rate, little-heard count and skips by
  reason, a library-wide estimate weighing each drawn group's rate by its size in
  the library, and the shift test: each not-sung transcript judged against its
  album's other texts, suspect misses tallied by genre tag. With --compare-model
  it gives each track's share under both models. Every count is approximate. It
  exits 0 once the report prints, and 2 for a strata file it cannot read or that
  names a track outside the library, a hearing log that is not a folder, or a
  ledger it cannot read.

  Genius-check writes no track: for each track of --tracks-from, a
  plain track list, each path once, it reads the tags and asks Genius's keyed API
  (api.genius.com, with the key from GENIUS_ACCESS_TOKEN or the config file) one
  search, paced and retried as album's Genius step is, and never Genius's site. It
  writes into --out has-it.tsv (a matching song with complete lyrics),
  not-on-genius.txt (a track list, only when the search clearly found nothing
  close: every hit read, a page short of full, the track asked by its tags) and
  unsure.tsv (any doubt: a near miss, a match without complete lyrics, a hit it
  could not read, a full page, a file name's guess, a refused search, or a track
  not asked once Genius's breaker opened), replaced on a rerun, and prints each
  list's count. It exits 0 once the lists are written and Genius answered every
  search, 1 once they are written but a search was refused or a track not asked
  by the breaker, 2 for an --out inside the library or one it cannot make or
  write to, no key, a list it cannot read or that is not a track list, or a list
  it cannot write, and 130 after Ctrl-C, the tracks left listed unsure.

  Paste needs macOS (open, pbpaste) and a terminal. For each row of the --from
  has-it list it opens the Genius page in the browser and waits: Enter pastes
  the clipboard into the track through the album tool's --mode paste, which
  writes it, backed up for undo, in --lang, only when the --heard-dir
  transcript sings it; s skips the row, q stops. A row already pasted, with
  no record, outside the library or whose URL is not Genius's is passed over,
  so a rerun resumes. It prints each row's end. Only a paste not sung asks
  for another copy; any other failed child is that row's. It exits 0 once
  the walk ends, by the list's end or q, 1 when an album tool child ended
  unexpectedly, 2 for no terminal, a
  --from that is not a has-it list or a --heard-dir it cannot list, and 130
  after Ctrl-C, the --changed-list keeping every track written."""
#: Where the modes' paragraph goes in the entry point's epilog: before its exit codes.
_EXIT_CODES: Final = "\n\nExit codes:"


class OptionSpec(TypedDict, total=False):
    """The keywords one option is added with, each typed as argparse takes it."""

    type: Callable[[str], object]
    action: str
    default: str | None
    help: str


SEED: Final[OptionSpec] = {
    "type": int,
    "help": "draw only, and required there: the seed the sample is drawn by, "
    "written in the strata file's header.",
}
OUT: Final[OptionSpec] = {
    "type": absolute_path,
    "help": "draw and genius-check only, and required there: the folder, never "
    f"inside the library, the draw writes {sweep_draw.TRACKS_NAME} and "
    f"{sweep_draw.STRATA_NAME} into, or the check writes "
    f"{sweep_genius_lists.HAS_IT_NAME}, {sweep_genius_lists.NOT_ON_GENIUS_NAME} and "
    f"{sweep_genius_lists.UNSURE_NAME} into.",
}
TRACKS_FROM: Final[OptionSpec] = {
    "type": absolute_path,
    "help": "genius-check only, and required there: the track list to check, one "
    "absolute path per line in the --changed-list format, such as a fix's "
    "--hold-for-genius list; only read.",
}
WITHHELD: Final[OptionSpec] = {
    "action": "store_true",
    # None, not False, when omitted, as every on/off flag of the sweep.
    "default": None,
    "help": "draw only: list the untimed tracks whose removal a fix withheld, "
    "instead of the sample.",
}
HEARING_LOG: Final[OptionSpec] = {
    "type": absolute_path,
    "help": "hear-report only, and required there: the hearing log sync --mode "
    "hear wrote, read only.",
}
STRATA: Final[OptionSpec] = {
    "type": absolute_path,
    "help": "hear-report only, and required there: the strata file the draw wrote.",
}
MODEL: Final[OptionSpec] = {
    "default": sweep_hear_report.DEFAULT_MODEL,
    "help": "hear-report only: the whisper model file whose hearings are reported.",
}
FROM: Final[OptionSpec] = {
    "type": absolute_path,
    "help": "paste only, and required there: the has-it.tsv the Genius check "
    "wrote, its tracks walked in order; only read.",
}
COMPARE_MODEL: Final[OptionSpec] = {
    "help": "hear-report only: a second whisper model file; each track heard by "
    "both models is compared. No comparison when omitted.",
}


def epilog(base: str) -> str:
    """*base*, the entry point's epilog, with the modes' paragraph before its exit codes."""
    head, codes, tail = base.partition(_EXIT_CODES)
    return f"{head}\n{_EPILOG_TEXT}{codes}{tail}"


def _refuse_others(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Exit 2 through *parser* when a mode is given an option another mode here owns."""
    mine = (*_OWN.get(args.mode, ()), *_SHARED.get(args.mode, ()))
    for mode, options in _OWN.items():
        if mode == args.mode:
            continue
        for name, flag in options:
            if (name, flag) in mine:
                continue
            # Against the parser's default, never `in (None, False)`, which would
            # pass a 0 seed.
            if getattr(args, name) != parser.get_default(name):
                parser.error(f"{flag} cannot be used with --mode {args.mode}")


def refuse(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Exit 2 through *parser* when a mode here, or another, is given what it does not take."""
    _refuse_others(parser, args)
    if args.mode not in _OWN:
        return
    own = (*_OWN[args.mode], *_SHARED.get(args.mode, ()))
    takes = {"library", "mode", *(name for name, _ in own)}
    for name, value in vars(args).items():
        if name not in takes and value != parser.get_default(name):
            flag = "--" + name.replace("_", "-")
            parser.error(f"{flag} cannot be used with --mode {args.mode}")
    for name, flag in _REQUIRED[args.mode]:
        if getattr(args, name) is None:
            parser.error(f"{flag} is required with --mode {args.mode}")
    if args.mode == MODE_HEAR_REPORT and args.compare_model == args.model:
        parser.error("--compare-model names the --model: give a second model")
