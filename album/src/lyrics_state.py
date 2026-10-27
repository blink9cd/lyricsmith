"""The state folder: where every record lyricsmith keeps outside the library sits.

One rule, held here alone: ``$XDG_STATE_HOME`` when it holds an absolute path, else
``.local/state`` under the home folder. The cooling sources' cool-down files and the
breakers' files (`lyrics_cooldown.state_folder`), accept's records
(`lyrics_accept_records.records_root`) and the sweep's ledger and silence list
(`sweep_ledger.ledger_path`) all sit under it, so they never disagree about where
lyricsmith keeps its state.

**Private.** What is kept there names tracks, albums and the providers' refusals, so
no other account may read it: each state file is written 0o600 (`write_state_file`),
and each state folder a tool makes, the state folder itself included, is made 0o700
(`make_state_folder`). A folder that already exists keeps whatever mode it has: it may
be shared with other tools, and its mode is the user's choice. The umask still
narrows both. Where the state folder is is pure: nothing read from disk, nothing
printed.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final

# The module, not the function: a test that wraps the confirmed write sees each call.
import lyrics_files

#: The variable naming the XDG state folder; a relative value is ignored.
STATE_ENV: Final = "XDG_STATE_HOME"
#: The home variable, read from the environment given before the process's own.
HOME_ENV: Final = "HOME"
#: The state folder under the home folder when the variable gives none.
HOME_STATE_DIR: Final = Path(".local") / "state"
#: A state file's mode, and a state folder's: readable by the user alone.
STATE_FILE_MODE: Final = 0o600
STATE_FOLDER_MODE: Final = 0o700


def state_root(environ: Mapping[str, str]) -> Path:
    """The state folder *environ* names: XDG_STATE_HOME when absolute, else under HOME.

    A relative or empty XDG_STATE_HOME is ignored, as the XDG convention asks, so the
    answer never depends on the working directory. HOME is read from *environ* too;
    the process's home folder stands in only when *environ* has none, or an empty one.
    """
    state = environ.get(STATE_ENV, "")
    if state and Path(state).is_absolute():
        return Path(state)
    home = environ.get(HOME_ENV)
    base = Path(home) if home else Path.home()
    return base / HOME_STATE_DIR


def make_state_folder(folder: Path) -> None:
    """Make *folder* and each missing folder above it, each 0o700; keep what exists.

    A folder already there, or a link to one, is left as it is, its mode included.
    A file in the way raises `FileExistsError`, and any other failure its `OSError`.
    """
    if folder.is_dir():
        return
    make_state_folder(folder.parent)
    # exist_ok: a folder another process made in the meantime is as good as ours.
    folder.mkdir(mode=STATE_FOLDER_MODE, exist_ok=True)


def write_state_file(path: Path, data: bytes) -> None:
    """Write *data* to the state file *path*, 0o600, atomically and confirmed.

    Its folder is made first (`make_state_folder`). Raises `OSError` or
    `lyrics_model.ReadBackError` when the file cannot be written.
    """
    make_state_folder(path.parent)
    lyrics_files.write_confirmed(path, data, mode=STATE_FILE_MODE)
