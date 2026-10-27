"""Put the album tool and the shared libraries on ``sys.path`` for any sweep private module.

A private module that imports a shared library, one of the album tool's private modules
or the album tool itself imports this first, so it imports alone, in an interpreter
whose path holds only ``src/``, as well as after ``sweep.py`` has run the same
bootstrap. The album tool is a fixed folder under the project root: its ``src/`` goes
first on ``sys.path``, as ``album.py`` puts it, and its own folder last, checked to be
where ``album`` loads from (`lyricsmith_project.confirm_from`). The ``src/`` is moved
first even when an earlier bootstrap left it later, so no same-named module ahead of it
stands in for one of the album tool's. The album tool's own located module then puts
the shared libraries on the path. Importing it again does nothing more.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ALBUM_DIR = Path(__file__).resolve().parents[2] / "album"
_ALBUM_SRC = str(_ALBUM_DIR / "src")
sys.path[:] = [_ALBUM_SRC, *(entry for entry in sys.path if entry != _ALBUM_SRC)]

import lyrics_located  # noqa: E402, F401 - puts the shared libraries on sys.path
import lyricsmith_project  # noqa: E402 - on sys.path through the module above

sys.path += [] if str(_ALBUM_DIR) in sys.path else [str(_ALBUM_DIR)]
lyricsmith_project.confirm_from("album", _ALBUM_DIR)
