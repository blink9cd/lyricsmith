"""Put the shared libraries on ``sys.path`` for any of the album tool's private modules.

A private module that imports a shared library imports this first, so it imports alone,
in an interpreter whose path holds only ``src/``, as well as after ``album.py`` has run
the same bootstrap. The project's manifest reader is a fixed folder under the project
root, two folders above this one; it goes FIRST on ``sys.path``, so no same-named
module can stand in for it, and puts each library the project's ``pyproject.toml``
names on the path, refusing one that a same-named module would shadow
(``lyricsmith_project/lyricsmith_project.md``). Importing it again does nothing more.
"""

from __future__ import annotations

import sys
from pathlib import Path

_READER_DIR = str(Path(__file__).resolve().parents[2] / "lyricsmith_project")
sys.path[:] = [_READER_DIR, *(entry for entry in sys.path if entry != _READER_DIR)]

import lyricsmith_project  # noqa: E402 - import follows the sys.path bootstrap above

lyricsmith_project.add_shared_libraries()
