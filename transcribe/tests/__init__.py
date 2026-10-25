"""Test suite for transcribe.

Puts the project folder and its src/ folder on sys.path, so a suite runs alone
(python -m unittest tests.test_at_plan) as well as under the grouped suite.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _folder in (_ROOT, _ROOT / "src"):
    if str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))
