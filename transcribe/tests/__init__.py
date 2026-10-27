"""Test suite for transcribe.

Puts the tool folder and its src/ folder on sys.path, then the shared libraries the
project's pyproject.toml names, through the project's manifest reader, as the entry
point does; so a suite runs alone (python -m unittest tests.test_at_label) as well as
under the grouped suite, even when it imports a private module that imports a shared
library before anything has imported the entry point.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _folder in (_ROOT, _ROOT / "src"):
    if str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))
# The project's manifest reader, in its own folder under the project root
# (lyricsmith_project/lyricsmith_project.md).
_READER_DIR = str(_ROOT.parent / "lyricsmith_project")
# Moved first on sys.path, so no same-named module can stand in for the reader.
sys.path[:] = [_READER_DIR, *(entry for entry in sys.path if entry != _READER_DIR)]
import lyricsmith_project  # noqa: E402 - import follows the sys.path bootstrap above

lyricsmith_project.add_shared_libraries()
