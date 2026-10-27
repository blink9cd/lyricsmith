"""The album tool's modes, and which of its options each mode, or each other option, refuses.

The entry point declares every option on its parser, where its companion doc's
consistency check reads them; this module holds what the options are checked
against: the modes, the default language and the language-code type, and
`refuse_misplaced`, which exits 2 through the parser when an option is given to a mode
that has no use for it, or beside an option it contradicts. Pure argparse: nothing here
reads a file or prints, apart from argparse's own error exit.
"""

from __future__ import annotations

import argparse
import re

#: The modes, as --mode names them.
MODE_RUN, MODE_CHECK, MODE_UNDO, MODE_STRIP, MODE_COMPARE, MODE_PRUNE, MODE_ACCEPT = (
    "run",
    "check",
    "undo",
    "strip",
    "compare",
    "prune",
    "accept",
)
#: The language code a run writes into new frames when --lang is not given.
DEFAULT_LANG = "eng"
#: An ISO 639-2 language code, as ID3 stores one: three lowercase letters.
_LANG_CODE = re.compile(r"[a-z]{3}")
#: The option limiting the providers asked (`lyrics_only` holds its names), as the
#: parser declares it and every message names it. Kept here, not with the names, so
#: this module imports nothing beyond the standard library.
ONLY_FLAG = "--only-providers"
#: The option limiting the providers asked, which an offline run, asking none, refuses.
_ONLY_PROVIDERS = ("only_providers", ONLY_FLAG)
#: The run-only options a mode's entry in `_REFUSED` names on their own: a compare
#: refuses each, and a prune takes the dry run alone.
_DRY_RUN = ("dry_run", "--dry-run")
_OFFLINE = ("offline", "--offline")
_FRESH = ("fresh", "--fresh")
#: The two refetch options, each asking some tracks again as though they held nothing.
_REFETCHES = (
    ("refetch_untimed", "--refetch-untimed"),
    ("refetch_implausible", "--refetch-implausible"),
)
#: The options a run uses: check, undo and strip refuse them; compare refuses some
#: (its entry in `_REFUSED`).
_RUN_ONLY = (
    _DRY_RUN,
    _OFFLINE,
    ("config", "--config"),
    ("no_genius", "--no-genius"),
    ("no_netease", "--no-netease"),
    ("no_bandcamp", "--no-bandcamp"),
    ("no_youtube_music", "--no-youtube-music"),
    ("no_tekstowo", "--no-tekstowo"),
    _ONLY_PROVIDERS,
    ("lang", "--lang"),
    _FRESH,
    *_REFETCHES,
)
#: The option that picks a run of a finished run: a new run refuses it.
_RUN_PICKER = ("run_id", "--run-id")
#: The option naming the file a run, a strip or an undo lists the MP3s it saved in.
_CHANGED_LIST = ("changed_list", "--changed-list")
#: The option that lets an accept remove what its report lists: every other mode
#: refuses it.
_APPLY = ("apply", "--apply")
#: Options a mode has no use for, refused rather than silently ignored. A strip is a
#: new run that asks no provider, of every track or the one --track names, so it
#: refuses --run-id and every run-only option. A check changes nothing, so its list
#: would always be empty. A compare reads the album as it is now, asks the providers
#: and writes nothing, so it refuses a run to read, an offline or a dry run, the three
#: replacing options and a changed list, and takes every other run-only option. A
#: prune judges every run folder by its rules, so it names no run and no track, asks
#: no provider and saves no MP3: of the run-only options it takes --dry-run alone. An
#: accept takes every run, as prune does, and only reports unless told otherwise: it
#: takes --apply alone, and refuses --dry-run, which leaving out --apply already is.
_REFUSED: dict[str, tuple[tuple[str, str], ...]] = {
    MODE_RUN: (_RUN_PICKER, _APPLY),
    MODE_CHECK: (*_RUN_ONLY, _CHANGED_LIST, _APPLY),
    MODE_UNDO: (*_RUN_ONLY, _APPLY),
    MODE_STRIP: (_RUN_PICKER, *_RUN_ONLY, _APPLY),
    MODE_COMPARE: (
        _RUN_PICKER,
        _DRY_RUN,
        _OFFLINE,
        _FRESH,
        *_REFETCHES,
        _CHANGED_LIST,
        _APPLY,
    ),
    MODE_PRUNE: (
        _RUN_PICKER,
        ("track", "--track"),
        *(option for option in _RUN_ONLY if option != _DRY_RUN),
        _CHANGED_LIST,
        _APPLY,
    ),
    MODE_ACCEPT: (_RUN_PICKER, ("track", "--track"), *_RUN_ONLY, _CHANGED_LIST),
}


def iso_639_2(text: str) -> str:
    """*text* when it is a three-letter lowercase language code, else an argparse error."""
    if not _LANG_CODE.fullmatch(text):
        raise argparse.ArgumentTypeError(
            f"must be a three-letter ISO 639-2 code such as eng, not {text!r}"
        )
    return text


def refuse_misplaced(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Exit 2 through *parser* on an option its mode, or another option given, refuses."""
    for attr, flag in _REFUSED[args.mode]:
        if getattr(args, attr) not in (None, False):
            parser.error(f"{flag} cannot be used with --mode {args.mode}")
    # A fresh run asks only the providers, and an offline one asks none of them. A
    # refetch asks the providers too, and accepts the answers a fresh run declines.
    if args.fresh and args.offline:
        parser.error("--fresh cannot be used with --offline")
    # Limiting the providers asked means nothing to a run that asks none, so it is
    # refused rather than ignored, as --fresh is. A dry run takes it, as it takes the
    # --no-* options: it asks no provider either, but plans what the run would do.
    if getattr(args, _ONLY_PROVIDERS[0]) is not None and args.offline:
        parser.error(f"{_ONLY_PROVIDERS[1]} cannot be used with --offline")
    # A dry run changes nothing either.
    if args.dry_run and args.changed_list is not None:
        parser.error(f"{_CHANGED_LIST[1]} cannot be used with --dry-run")
    # Either refetch asks the providers again, so a fresh run, which asks every track,
    # and an offline one, which would remove implausible lyrics unasked, refuse both.
    # The two refetches together ask the union of their tracks.
    for refetch, name in _REFETCHES:
        for attr, flag in (("fresh", "--fresh"), ("offline", "--offline")):
            if getattr(args, refetch) and getattr(args, attr):
                parser.error(f"{name} cannot be used with {flag}")
