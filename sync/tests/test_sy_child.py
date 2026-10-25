"""The child module loads without PyTorch: its parser, its network refusal, its main.

The functions that need PyTorch are replaced by stand-ins here; the live smoke test runs
the real ones.
"""

from __future__ import annotations

import contextlib
import io
import random
import socket
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_child  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
from sy_lines import Span  # noqa: E402 - import follows the sys.path bootstrap above

#: The message every refused network call carries.
REFUSAL = "never uses the network"
#: A documentation address and name: refused before any packet could leave.
ADDRESS = ("192.0.2.1", 80)
#: The frames and seconds the stand-in emissions hold.
FRAMES, SECONDS = 5, 0.1


def stand_in_align(emissions: object, ids: Sequence[int]) -> list[Span]:
    """One span a token, back to back, as the real alignment gives."""
    return [Span(index, index + 1, 0.9) for index in range(len(ids))]


class ChildTest(sy_cases.IsolatedCase):
    def test_both_files_are_required(self) -> None:
        parsed = sy_child.build_parser().parse_args(["--request", "r", "--reply", "p"])
        self.assertEqual((parsed.request, parsed.reply), ("r", "p"))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                sy_child.build_parser().parse_args(["--request", "r"])

    def test_the_window_is_twenty_seconds_at_the_models_rate(self) -> None:
        self.assertEqual(sy_child.WINDOW_SAMPLES, 20 * 16000)

    def test_the_separation_shift_is_drawn_the_same_every_run(self) -> None:
        state = random.getstate()
        self.addCleanup(random.setstate, state)
        sy_child._seed_shift()
        first = random.randint(0, 22050)
        random.random()
        sy_child._seed_shift()
        self.assertEqual(random.randint(0, 22050), first)

    def test_the_network_is_refused_in_the_childs_process(self) -> None:
        with self.restored_sockets():
            sy_child._refuse_network()
            with self.assertRaisesRegex(OSError, REFUSAL):
                socket.create_connection(ADDRESS, timeout=1.0)
            with self.assertRaisesRegex(OSError, REFUSAL):
                socket.getaddrinfo("example.invalid", 80)
            with socket.socket() as plain:
                with self.assertRaisesRegex(OSError, REFUSAL):
                    plain.connect(ADDRESS)

    def test_text_longer_than_the_frames_is_too_long_with_no_spans(self) -> None:
        reply = self.run_main((2, 3, 4, 5, 6, 7))
        self.assertEqual((reply.status, reply.spans), (rq.STATUS_TOO_LONG, ()))
        self.assertEqual(reply.frames, FRAMES)

    def test_text_that_fits_is_aligned_one_span_a_token(self) -> None:
        reply = self.run_main((2, 3))
        self.assertEqual((reply.status, len(reply.spans)), (rq.STATUS_OK, 2))
        self.assertAlmostEqual(reply.frame_seconds, SECONDS / FRAMES)

    @contextlib.contextmanager
    def restored_sockets(self) -> Iterator[None]:
        """Put back whatever stands in each socket entry point, the tripwire included."""
        with (
            mock.patch.object(socket.socket, "connect", socket.socket.connect),
            mock.patch.object(socket, "create_connection", socket.create_connection),
            mock.patch.object(socket, "getaddrinfo", socket.getaddrinfo),
        ):
            yield

    def run_main(self, ids: tuple[int, ...]) -> rq.Reply:
        """The child's main on a request for *ids*, PyTorch's steps stood in; its reply."""
        request, reply = self.tmp / "request.json", self.tmp / "reply.json"
        dumped = rq.dump_request(rq.Request(self.tmp / "a.mp3", ids, self.tmp))
        request.write_text(dumped, encoding="utf-8", newline="\n")
        emissions = SimpleNamespace(shape=(FRAMES,))
        with (
            mock.patch.object(sy_child, "_refuse_network"),
            mock.patch.object(sy_child, "_decode"),
            mock.patch.object(sy_child, "_vocals"),
            mock.patch.object(
                sy_child, "_emissions", return_value=(emissions, SECONDS)
            ),
            mock.patch.object(sy_child, "_align", side_effect=stand_in_align),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = sy_child.main(["--request", str(request), "--reply", str(reply)])
        self.assertEqual(code, 0)
        return rq.load_reply(reply.read_text(encoding="utf-8"), len(ids))
