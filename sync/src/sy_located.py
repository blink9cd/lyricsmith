"""Put the other tools and the shared libraries on ``sys.path`` for any sync private module.

A private module that imports a shared library, or a module of the album, sweep or
transcribe tool, imports this first, so it imports alone, in an interpreter whose path
holds only ``src/``, as well as after ``sync.py`` has run the same bootstrap. Each tool
is a fixed folder under the project root. The sweep tool's ``src/`` and the transcribe
tool's go first on ``sys.path``, as each entry file puts its own, moved there even when
an earlier bootstrap left them later; the sweep tool's located module adds the album
tool's and the shared libraries. Each tool's own folder
goes last, checked to be where its entry module loads from
(`lyricsmith_project.confirm_from`). Importing it again does nothing more.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parents[2]
#: The tools whose modules sync's private modules import, each a folder of its name.
_SIBLINGS = ("album", "sweep", "transcribe")
_SOURCES = [str(_PROJECT_DIR / tool / "src") for tool in ("sweep", "transcribe")]
sys.path[:] = [*_SOURCES, *(entry for entry in sys.path if entry not in _SOURCES)]

import sweep_located  # noqa: E402, F401 - puts album and the shared libraries on sys.path
import lyricsmith_project  # noqa: E402 - on sys.path through the module above

for _tool in _SIBLINGS:
    _folder = _PROJECT_DIR / _tool
    sys.path += [] if str(_folder) in sys.path else [str(_folder)]
    lyricsmith_project.confirm_from(_tool, _folder)
