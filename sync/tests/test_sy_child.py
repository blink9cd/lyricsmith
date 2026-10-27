"""The child module loads without PyTorch: its parser, its network refusal, its main.

The functions that need PyTorch are replaced by stand-ins here; the live smoke test runs
the real ones.
"""

from __future__ import annotations

import contextlib
import io
import os
import random
import shutil
import socket
import subprocess
import sys
import unittest
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
#: ffmpeg's demuxers that open other files a track's bytes name, which none may reach.
PLAYLIST_DEMUXERS = ("concat", "hls", "dash", "image2", "image2pipe", "imf")
#: Each socket entry point the refusal replaces, which every test that runs it restores.
SOCKET_ENTRY_POINTS: tuple[tuple[object, str], ...] = (
    (socket.socket, "connect"),
    (socket.socket, "connect_ex"),
    (socket.socket, "sendto"),
    (socket.socket, "sendmsg"),
    (socket, "create_connection"),
    (socket, "getaddrinfo"),
)
#: The frames and seconds the stand-in emissions hold.
FRAMES, SECONDS = 5, 0.1
#: Starting the child as a script, with no request to read, takes a second or two.
SCRIPT_TIMEOUT_S = 60.0
#: An invented second of tone from ffmpeg's own generator: no file or network read.
TONE_ARGV = (
    "ffmpeg",
    "-nostdin",
    "-v",
    "error",
    "-f",
    "lavfi",
    "-i",
    "sine=duration=1",
)
#: Decoding or writing a second of audio takes well under a second.
FFMPEG_TIMEOUT_S = 60.0
#: A letter outside ASCII, so the child's error output shows which encoding it wrote.
ACCENTED = "\u00e9"


class Refused(Exception):
    """What the stand-in network refusal raises, so a test sees when it ran."""


def stand_in_align(emissions: object, ids: Sequence[int]) -> list[Span]:
    """One span a token, back to back, as the real alignment gives."""
    return [Span(index, index + 1, 0.9) for index in range(len(ids))]


class ChildTest(sy_cases.IsolatedCase):
    def test_both_files_are_required(self) -> None:
        parsed = sy_child.build_parser().parse_args(["--request", "r", "--reply", "p"])
        self.assertEqual((parsed.request, parsed.reply), ("r", "p"))
        with contextlib.redirect_stderr(io.StringIO()):
            for missing in (["--request", "r"], ["--reply", "p"]):
                with self.assertRaises(SystemExit):
                    sy_child.build_parser().parse_args(missing)

    def test_main_refuses_the_network_before_it_reads_the_request(self) -> None:
        argv = ["--request", str(self.tmp / "absent.json"), "--reply", "p"]
        with (
            mock.patch.object(sy_child, "_refuse_network", side_effect=Refused),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(Refused):
                sy_child.main(argv)

    def test_the_child_starts_as_a_script_on_its_own_bootstrap(self) -> None:
        # Run as the parent runs it, with nothing of this suite's sys.path, the script's
        # own folder left off it, and an ASCII terminal: it must still import, reach
        # main, and fail on the missing request in UTF-8, as the parent decodes it.
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        env |= {"PYTHONSAFEPATH": "1", "PYTHONIOENCODING": "ascii"}
        request = self.tmp / ACCENTED / "request.json"
        argv = [sys.executable, sy_child.__file__, "--request", str(request)]
        # reliability: ignore[RL005] the child fails on the missing request before it starts any process of its own
        done = subprocess.run(
            [*argv, "--reply", str(self.tmp / "reply.json")],
            capture_output=True,
            env=env,
            timeout=SCRIPT_TIMEOUT_S,
            check=False,
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(b"FileNotFoundError", done.stderr)
        self.assertIn(ACCENTED.encode("utf-8"), done.stderr)

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
                with self.assertRaisesRegex(OSError, REFUSAL):
                    plain.connect_ex(ADDRESS)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
                with self.assertRaisesRegex(OSError, REFUSAL):
                    datagram.sendto(b"x", ADDRESS)
            if sys.platform == "win32":  # pragma: no cover - sendmsg is not on Windows
                return
            # A local pair, so an unrefused send would leave the machine nothing.
            left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
            with left, right:
                with self.assertRaisesRegex(OSError, REFUSAL):
                    left.sendmsg([b"x"])

    def test_ffmpeg_reads_the_track_as_a_file_and_nothing_else(self) -> None:
        audio = Path("/music/http:x.mp3")
        argv = sy_child.decode_argv(audio)
        source = argv.index("-i")
        self.assertEqual(argv[source + 1], "file:/music/http:x.mp3")
        whitelist = argv.index("-protocol_whitelist")
        self.assertEqual(argv[whitelist + 1], "file")
        self.assertLess(whitelist, source)

    def test_ffmpeg_opens_the_track_with_an_audio_demuxer_only(self) -> None:
        argv = sy_child.decode_argv(Path("/music/a.mp3"))
        whitelist = argv.index("-format_whitelist")
        allowed = argv[whitelist + 1].split(",")
        self.assertEqual(allowed, sorted(set(sy_child.DEMUXERS.values())))
        self.assertLess(whitelist, argv.index("-i"))
        for playlist in PLAYLIST_DEMUXERS:
            with self.subTest(demuxer=playlist):
                self.assertNotIn(playlist, allowed)

    def test_ffmpeg_decodes_no_more_than_the_longest_track(self) -> None:
        argv = sy_child.decode_argv(Path("/music/a.mp3"))
        limit = argv.index("-t")
        self.assertEqual(argv[limit + 1], str(sy_child.MAX_TRACK_S))
        self.assertLess(limit, argv.index("-i"))

    def test_a_relative_track_is_never_handed_to_ffmpeg(self) -> None:
        for relative in ("pipe:0", "a.mp3"):
            with self.subTest(audio=relative):
                with self.assertRaisesRegex(ValueError, "absolute"):
                    sy_child.decode_argv(Path(relative))

    def test_a_decode_returns_ffmpegs_output(self) -> None:
        audio = Path("/music/a.mp3")
        done = subprocess.CompletedProcess(["ffmpeg"], 0, stdout=b"pcm", stderr=b"")
        with mock.patch.object(sy_child.subprocess, "run", return_value=done) as run:
            self.assertEqual(sy_child.decoded_bytes(audio), b"pcm")
        self.assertEqual(run.call_args.args[0], sy_child.decode_argv(audio))

    def test_a_failed_decode_names_ffmpegs_last_error_lines(self) -> None:
        lines = [f"line {index}" for index in range(sy_child.FFMPEG_ERROR_LINES + 2)]
        stderr = "\n\n".join(lines).encode("utf-8")
        failed = subprocess.CalledProcessError(234, ["ffmpeg"], b"", stderr)
        with mock.patch.object(sy_child.subprocess, "run", side_effect=failed):
            with self.assertRaises(sy_child.DecodeError) as caught:
                sy_child.decoded_bytes(Path("/music/a.mp3"))
        first, *rest = str(caught.exception).splitlines()
        self.assertIn("ffmpeg exit 234", first)
        self.assertEqual(rest, lines[-sy_child.FFMPEG_ERROR_LINES :])

    def test_a_failed_decode_with_no_error_output_says_so(self) -> None:
        failed = subprocess.CalledProcessError(1, ["ffmpeg"], b"", b"")
        with mock.patch.object(sy_child.subprocess, "run", side_effect=failed):
            with self.assertRaisesRegex(sy_child.DecodeError, "no error output"):
                sy_child.decoded_bytes(Path("/music/a.mp3"))

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
        with contextlib.ExitStack() as stack:
            for target, name in SOCKET_ENTRY_POINTS:
                # Read by name, since sendmsg is not on Windows; create=True lets the
                # patch remove there what the refusal added.
                original = getattr(target, name, None)
                stack.enter_context(
                    mock.patch.object(target, name, original, create=True)
                )
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


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg on PATH")
class WhitelistTest(sy_cases.IsolatedCase):
    """The real ffmpeg, run on the child's own command line over invented files."""

    def test_a_playlist_named_as_a_track_decodes_nothing(self) -> None:
        done = run_ffmpeg(sy_child.decode_argv(self.playlist()))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout, b"")

    def test_the_playlist_opens_its_sibling_when_no_whitelist_is_given(self) -> None:
        # The control: the same file decodes without the format whitelist, so the test
        # above fails on the whitelist and not on a broken playlist.
        argv = sy_child.decode_argv(self.playlist())
        at = argv.index("-format_whitelist")
        done = run_ffmpeg([*argv[:at], *argv[at + 2 :]])
        self.assertEqual(done.returncode, 0)
        self.assertNotEqual(done.stdout, b"")

    def test_a_real_track_decodes(self) -> None:
        track = sy_cases.fixtures.build_silent_mp3(self.tmp / "a.mp3", 1.0)
        done = run_ffmpeg(sy_child.decode_argv(track))
        self.assertEqual(done.returncode, 0)
        self.assertNotEqual(done.stdout, b"")

    def test_the_refusal_reaches_the_decode_error(self) -> None:
        with self.assertRaisesRegex(sy_child.DecodeError, "not on whitelist"):
            sy_child.decoded_bytes(self.playlist())

    def playlist(self) -> Path:
        """An ffconcat playlist saved as ``x.mp3``, naming an invented tone beside it."""
        tone = self.tmp / "tone.wav"
        made = run_ffmpeg([*TONE_ARGV, str(tone)])
        self.assertEqual(made.returncode, 0, made.stderr)
        track = self.tmp / "x.mp3"
        playlist = f"ffconcat version 1.0\nfile '{tone.name}'\n"
        track.write_text(playlist, encoding="utf-8", newline="\n")
        return track


def run_ffmpeg(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """*argv* run to its end within `FFMPEG_TIMEOUT_S`, its output captured."""
    # reliability: ignore[RL005] ffmpeg starts no child to hold the pipes open
    return subprocess.run(
        argv, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False
    )
