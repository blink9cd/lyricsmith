"""What the album suites need to spell the same case on POSIX and on Windows.

`on_host` roots an invented absolute POSIX path at the drive the suites run on, so it
is absolute on Windows too, and is the very same text off Windows; `as_written` is
how the changed-list writer then writes it. Both, and `HOST_DRIVE`, are `fixtures`'
own, named here for the suites that spell them through this module. `is_link_loop` tells
a link loop by either host's error: POSIX's ``ELOOP``, or Windows' "the name of the
file cannot be resolved", which reaches Python as ``EINVAL`` with its own Windows code.
`os_error_text` is what an `OSError` prints before the file name it names, the host's
own words, so a test of a printed error never spells one host's.
"""

from __future__ import annotations

import errno

from fixtures import HOST_DRIVE as HOST_DRIVE  # re-exported: the one definition
from fixtures import as_written as as_written  # re-exported: the one definition
from fixtures import on_host as on_host  # re-exported: the one definition

#: Windows' ERROR_CANT_RESOLVE_FILENAME: what it raises where POSIX raises ``ELOOP``.
WINDOWS_LOOP = 1921


def is_link_loop(error: OSError) -> bool:
    """Whether *error* is a link loop's: ``ELOOP``, or Windows' unresolvable name."""
    return (
        error.errno == errno.ELOOP or getattr(error, "winerror", None) == WINDOWS_LOOP
    )


def os_error_text(error: OSError) -> str:
    """*error* as it prints, less the file name it ends with: the host's own words."""
    return str(error).removesuffix(f": {error.filename!r}")
