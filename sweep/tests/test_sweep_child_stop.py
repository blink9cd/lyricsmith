"""The bound's stop of the album tool, on a host that sends signals and on one that cannot.

Where a signal can be sent (POSIX) a child past its bound is sent Ctrl-C's own signal and
keeps its own exit code. Where none can (Windows, whose `Popen.send_signal` refuses
SIGINT) it is sent SIGTERM, which `Popen` carries out as a terminate, and the stop still
reads as the one a POSIX host records: timed out, with the album tool's interrupted code,
never a terminated child's exit 1. The host is played by patching
`sweep_child.CAN_FORWARD_SIGNALS` (`child_fixtures.HostCase`), so both paths run on every
host; a real child crosses the process boundary on the Windows-shaped path here, and on
the POSIX one in `test_sweep_child`, wherever a real `Popen` can send SIGINT.
"""

from __future__ import annotations

import signal
import sys
from collections.abc import Sequence
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import album  # noqa: E402 - import follows the sys.path bootstrap above
import child_fixtures as cf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above

#: A bound no fake child here comes near, so only the scripted waits decide.
_GENEROUS_S = 60.0
#: The grace and the kill wait the fake runs are given, told apart in the waits recorded.
_GRACE_S = 5.0
_KILL_WAIT_S = 3.0
#: The exit code Windows' `Popen.terminate` gives the child it ends.
_TERMINATED_CODE = 1
#: A real child that runs past its bound and handles no signal, so the interrupted code
#: recorded for it can come only from the stop's own record, never from the child.
_SLEEPS_PAST_ITS_BOUND = "import time\ntime.sleep(60)\n"


def _python(code: str) -> list[str]:
    """The argv running *code* in a fresh, isolated interpreter."""
    return [sys.executable, "-I", "-c", code]


def _run(process: cf.ScriptedProcess) -> sweep_child.ChildResult:
    """`run_child` over the fake *process*, with a short grace and kill wait."""

    def popen(argv: Sequence[str], **_kwargs: object) -> cf.ScriptedProcess:
        return process

    return sweep_child.run_child(
        ["child"],
        _GENEROUS_S,
        popen=popen,
        stop_grace_s=_GRACE_S,
        kill_wait_s=_KILL_WAIT_S,
    )


class StopWhereNoSignalGoesTest(cf.HostCase):
    """Windows: the bound's stop is SIGTERM, recorded as the stop POSIX records."""

    can_forward_signals = False

    def test_a_child_past_its_bound_is_terminated_and_recorded_as_stopped(
        self,
    ) -> None:
        process = cf.ScriptedProcess(
            [cf.expired(), _TERMINATED_CODE], refuses_signals=True
        )
        result = _run(process)
        self.assertEqual(
            result, sweep_child.ChildResult(album.EXIT_INTERRUPTED, timed_out=True)
        )
        self.assertEqual((process.signals, process.kills), ([signal.SIGTERM], 0))
        self.assertEqual(process.waits, [_GENEROUS_S, _GRACE_S])

    def test_a_terminated_child_that_will_not_go_is_killed_then_abandoned(
        self,
    ) -> None:
        process = cf.ScriptedProcess(
            [cf.expired(), cf.expired(), cf.expired()], refuses_signals=True
        )
        result = _run(process)
        self.assertEqual(
            result, sweep_child.ChildResult(None, timed_out=True, pid=cf.PID)
        )
        self.assertEqual((process.signals, process.kills), ([signal.SIGTERM], 1))
        self.assertEqual(process.waits, [_GENEROUS_S, _GRACE_S, _KILL_WAIT_S])

    def test_ctrl_c_in_the_grace_after_the_bound_kills_and_is_still_stopped(
        self,
    ) -> None:
        # The terminated child is killed at a second Ctrl-C; reaped, it still reads as
        # the stop a POSIX host records, never the kill's own code.
        process = cf.ScriptedProcess(
            [cf.expired(), KeyboardInterrupt(), _TERMINATED_CODE], refuses_signals=True
        )
        result = _run(process)
        self.assertEqual(
            result,
            sweep_child.ChildResult(
                album.EXIT_INTERRUPTED, timed_out=True, interrupted=True
            ),
        )
        self.assertEqual((process.signals, process.kills), ([signal.SIGTERM], 1))
        self.assertEqual(process.waits, [_GENEROUS_S, _GRACE_S, _KILL_WAIT_S])

    def test_ctrl_c_waits_for_the_child_and_sends_it_nothing(self) -> None:
        # The console hands Ctrl-C to the child as well, so nothing is sent to it.
        process = cf.ScriptedProcess([KeyboardInterrupt(), 130], refuses_signals=True)
        result = _run(process)
        self.assertEqual(result, sweep_child.ChildResult(130, interrupted=True))
        self.assertEqual((process.signals, process.kills), ([], 0))

    def test_a_real_child_past_its_bound_is_stopped_and_recorded(self) -> None:
        result = sweep_child.run_child(_python(_SLEEPS_PAST_ITS_BOUND), 1.0)
        self.assertEqual(
            result, sweep_child.ChildResult(album.EXIT_INTERRUPTED, timed_out=True)
        )


class StopWhereSignalsGoTest(cf.HostCase):
    """POSIX: the bound's stop is Ctrl-C's own signal, and the child's code is kept."""

    def test_the_child_is_sent_sigint_and_its_own_code_is_kept(self) -> None:
        process = cf.ScriptedProcess([cf.expired(), 7])
        result = _run(process)
        self.assertEqual(result, sweep_child.ChildResult(7, timed_out=True))
        self.assertEqual(process.signals, [signal.SIGINT])
