"""The one Hypothesis profile every album property suite runs under, loaded on import.

``@given`` keeps the default settings of the moment it decorates a test. Hypothesis
keeps one default per process, and the gate runs every suite in this folder in one
process (`test_album.load_tests`), so a profile one suite loaded at its top would also
govern every suite imported after it that loads none of its own. This module is the one
source of the settings instead: each property suite imports it before it defines its
``@given`` tests, and `test_album_profile` fails on a suite that does not, on any other
module that registers or loads a profile, and on a property test decorated under any
other settings.

A registered profile, never an ``@settings`` decorator: a fuzzing campaign that
re-applies its own settings to each property is refused by Hypothesis on a decorated
one. Its settings are every suite's needs together:

- ``deadline=None``: a per-example wall-clock deadline measures machine load, not
  correctness, and some properties write and read a file per example.
- ``too_slow`` suppressed: a cold cache's first text draw builds Hypothesis's unicode
  tables inside that health check's window.
- ``filter_too_much`` suppressed: some strategies filter their draws with ``assume``
  (the sidecar match suite's), which that check would fail on a run of rejections.

The example count stays Hypothesis's default.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings

#: The profile's name, for a suite or a check that reads it back.
PROFILE = "album_properties"

settings.register_profile(
    PROFILE,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
settings.load_profile(PROFILE)
