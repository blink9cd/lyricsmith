"""Shared fixtures for the album tool child's suites: a scripted process and a host.

`ScriptedProcess` plays a child whose waits return or raise as scripted, recording the
signals and kills it is sent; Windows-shaped, it refuses every signal but SIGTERM, as
Windows' `Popen.send_signal` refuses SIGINT. `HostCase` plays a host that can, or
cannot, send a child Ctrl-C's own signal, by patching `sweep_child.CAN_FORWARD_SIGNALS`
for each test, so a suite describing one host's stop runs that stop on every host
rather than silently taking the host's own branch.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``, after importing the entry point, which puts ``src/`` there.
"""

from __future__ import annotations

import signal
import subprocess
from dataclasses import dataclass, field
from unittest import mock

import sweep_child
import sweep_fixtures as sf

#: The pid the scripted processes report.
PID = 4242


@dataclass
class ScriptedProcess:
    """A process whose waits play *steps*; Windows-shaped when *refuses_signals*.

    A step is an exit code to return or an exception to raise.
    """

    steps: list[int | BaseException]
    refuses_signals: bool = False
    pid: int = PID
    waits: list[float | None] = field(default_factory=list)
    signals: list[int] = field(default_factory=list)
    kills: int = 0

    def wait(self, timeout: float | None = None) -> int:
        """Play the next step: return its exit code, or raise its exception."""
        self.waits.append(timeout)
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def send_signal(self, sig: int) -> None:
        """Record *sig*, or refuse it as Windows refuses all but SIGTERM."""
        if self.refuses_signals and sig != signal.SIGTERM:
            raise ValueError(f"Unsupported signal: {sig}")
        self.signals.append(sig)

    def kill(self) -> None:
        """Record the kill."""
        self.kills += 1


def expired() -> subprocess.TimeoutExpired:
    """The exception a bounded wait raises when its bound passes."""
    return subprocess.TimeoutExpired("child", 0.0)


class HostCase(sf.IsolatedCase):
    """A case run as a host that can, or cannot, send a child Ctrl-C's signal."""

    can_forward_signals = True

    def setUp(self) -> None:
        """Play the host this case names."""
        super().setUp()
        patch = mock.patch.object(
            sweep_child, "CAN_FORWARD_SIGNALS", self.can_forward_signals
        )
        patch.start()
        self.addCleanup(patch.stop)
