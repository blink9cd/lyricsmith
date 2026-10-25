"""The run an album tool child made on an album, found and read: its id and its tally.

The sweep (`sweep_run`) notes an album's run ids before its child starts; once the
child has ended, `tally_made_run` finds the one new run, reads its manifest and tallies
it, as far as each step allows, and marks the child's result interrupted when Ctrl-C
cut the read short. A run folder that can no longer be listed once the child ran is no
record, never could not run: the album tool ran, and may have changed tags. Nothing
here prints: each line goes to the caller's *progress*.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import lyrics_exit
import sweep_child
from sweep_tally import AlbumTally, tally_manifest


def _made_run_id(
    album: Path, before: frozenset[str], name: str, progress: Callable[[str], None]
) -> str | None:
    """The id of the one run made on *album* since *before*, or None.

    Several new runs mean another album tool ran on the album alongside: none is
    taken, since which is whose cannot be told, and *progress* says so.
    """
    new = sweep_child.new_run_ids(album, before)
    if len(new) > 1:
        progress(
            f"{name}: {len(new)} new runs appeared while the album tool ran on it; "
            "none is read, so the album has no record"
        )
    if len(new) != 1:
        return None
    (run_id,) = new
    return run_id


@dataclass(frozen=True)
class MadeRun:
    """The run a child made, as far as it was read.

    *run_id* is None when no run was found, or when the one found could not be read
    to its end; *tally* is None until the manifest was read and tallied.
    """

    run_id: str | None
    tally: AlbumTally | None
    result: sweep_child.ChildResult


def tally_made_run(
    album: Path,
    name: str,
    before: frozenset[str],
    result: sweep_child.ChildResult,
    *,
    netease: bool,
    progress: Callable[[str], None],
) -> MadeRun:
    """The run made on *album* since *before*, its tally, and the child's result.

    *name* is the album's name in a line said through *progress*. A child given
    ``--no-netease`` (*netease* False) asked LRCLIB's synced step alone, so the tally
    reads its refusal as every synced step's.

    No run is read for a child left unreaped, which may still be writing it, nor when
    the album tool could not run. A run whose manifest cannot be read has no run id,
    and neither has one whose run folder can no longer be listed, which *progress*
    says.
    Ctrl-C while the run is read drops the tally but keeps the run id already known
    (the manifest's, or the one new run's), so the album's undo command is still
    printed; the result is marked interrupted, so the album is recorded as not done
    and the sweep stops after it.
    """
    # The album tool exits `EXIT_CANNOT_RUN` when it could not start or failed before
    # any MP3 changed, so a new run then is another's. `EXIT_RECORD_NOT_WRITTEN` is a
    # run made whose record, list or report could not be written: it is read like a
    # finished one.
    if result.exit_code in (None, lyrics_exit.EXIT_CANNOT_RUN):
        return MadeRun(None, None, result)
    run_id: str | None = None
    try:
        try:
            run_id = _made_run_id(album, before, name, progress)
        except OSError as error:
            # The album tool ran, and may have changed tags: the album is no record,
            # its exit code kept, never could not run (`sweep_run._visit`).
            progress(
                f"{name}: the run folder cannot be listed after the album tool ran: "
                f"{error}"
            )
            return MadeRun(None, None, result)
        manifest = None if run_id is None else sweep_child.read_run(album, run_id)
        if manifest is None:
            return MadeRun(None, None, result)
        run_id = manifest.run_id
        tally = tally_manifest(manifest, netease=netease)
    except KeyboardInterrupt:
        return MadeRun(run_id, None, dataclasses.replace(result, interrupted=True))
    return MadeRun(run_id, tally, result)
