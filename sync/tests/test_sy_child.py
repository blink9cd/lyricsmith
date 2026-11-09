"""The child module loads without PyTorch: its parser, its network refusal, its main.

The functions that need PyTorch are replaced by stand-ins here; the live smoke test runs
the real ones.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import random
import shutil
import socket
import subprocess
import sys
import unittest
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_child  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import Heard  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard_store import transcript_fits  # noqa: E402 - follows the bootstrap
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
from sy_breaks import Levels  # noqa: E402 - import follows the sys.path bootstrap above
from sy_lines import Span  # noqa: E402 - import follows the sys.path bootstrap above
from sy_text import NO_LINE  # noqa: E402 - import follows the sys.path bootstrap above

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
#: A track of one frame a second, its vocal stem 40 dB under the band over frames 20
#: to 34: a 15 s break, two frames trimmed from each edge.
BREAK_TRACK_FRAMES = 40
QUIET_FRAMES = range(20, 35)
TRIMMED_BREAK = ((22, 33),)
#: A track's folder and the track, absolute on the host as the walk names them: a
#: decode refuses a track that is not.
MUSIC = sy_cases.host_absolute("/music")
TRACK = MUSIC / "a.mp3"
#: Two lines of one letter each, the second sung at frame 2.
TWO_LINES = rq.Request(
    Path("/music/a.mp3"),
    (2, 1, 3),
    Path("/m"),
    (0, NO_LINE, 1),
    Path("/w"),
    Path("/wm"),
)
FIRST = [Span(0, 1, 0.9), Span(1, 2, 0.9), Span(2, 3, 0.9)]
SECOND = [Span(0, 1, 0.8), Span(1, 2, 0.8), Span(36, 37, 0.8)]


def levels(frames: int, quiet: range = range(0)) -> Levels:
    """*frames* frames of a band at 1.0, the vocal stem at 0.01 over *quiet*, else 1.0."""
    vocal = tuple(0.01 if index in quiet else 1.0 for index in range(frames))
    return Levels(vocal, (1.0,) * frames)


class FakeAligner:
    """An aligner giving each of *results* in turn, recording what it aligned over."""

    def __init__(self, *results: list[Span]) -> None:
        self.results = list(results)
        self.over: list[object] = []

    def __call__(self, emissions: object, ids: Sequence[int]) -> list[Span]:
        self.over.append(emissions)
        return self.results.pop(0)


class FakeMask:
    """A mask recording the ranges it was given and returning a marker."""

    MASKED = "masked emissions"

    def __init__(self) -> None:
        self.ranges: list[tuple[tuple[int, int], ...]] = []

    def __call__(self, emissions: object, ranges: Sequence[tuple[int, int]]) -> str:
        self.ranges.append(tuple(ranges))
        return self.MASKED


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


#: An invented sung line of 21 distinct word pairs, none of them in a one-word text.
WRONG_SONG = (
    "red kites over the harbor wall seven lanterns in the rain a quiet engine hums"
    " all night and nobody comes home again"
)
#: What a child run leaves in its folder: its whisper work folder is removed.
NAMES_LEFT = ["reply.json", "request.json"]


def whisper_hears(text: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    """A whisper-cli stand-in that writes *text* as its transcript and exits 0."""

    def run(argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        out = Path(argv[argv.index("-of") + 1])
        out.with_suffix(".txt").write_text(text, encoding="utf-8", newline="\n")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    return run


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
        audio = MUSIC / "http:x.mp3"
        argv = sy_child.decode_argv(audio)
        source = argv.index("-i")
        self.assertEqual(argv[source + 1], f"file:{audio}")
        whitelist = argv.index("-protocol_whitelist")
        self.assertEqual(argv[whitelist + 1], "file")
        self.assertLess(whitelist, source)

    def test_ffmpeg_opens_the_track_with_an_audio_demuxer_only(self) -> None:
        argv = sy_child.decode_argv(TRACK)
        whitelist = argv.index("-format_whitelist")
        allowed = argv[whitelist + 1].split(",")
        self.assertEqual(allowed, sorted(set(sy_child.DEMUXERS.values())))
        self.assertLess(whitelist, argv.index("-i"))
        for playlist in PLAYLIST_DEMUXERS:
            with self.subTest(demuxer=playlist):
                self.assertNotIn(playlist, allowed)

    def test_ffmpeg_decodes_no_more_than_the_longest_track(self) -> None:
        argv = sy_child.decode_argv(TRACK)
        limit = argv.index("-t")
        self.assertEqual(argv[limit + 1], str(sy_child.MAX_TRACK_S))
        self.assertLess(limit, argv.index("-i"))

    def test_a_relative_track_is_never_handed_to_ffmpeg(self) -> None:
        for relative in ("pipe:0", "a.mp3"):
            with self.subTest(audio=relative):
                with self.assertRaisesRegex(ValueError, "absolute"):
                    sy_child.decode_argv(Path(relative))

    def test_a_decode_returns_ffmpegs_output(self) -> None:
        audio = TRACK
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
                sy_child.decoded_bytes(TRACK)
        first, *rest = str(caught.exception).splitlines()
        self.assertIn("ffmpeg exit 234", first)
        self.assertEqual(rest, lines[-sy_child.FFMPEG_ERROR_LINES :])

    def test_a_failed_decode_with_no_error_output_says_so(self) -> None:
        failed = subprocess.CalledProcessError(1, ["ffmpeg"], b"", b"")
        with mock.patch.object(sy_child.subprocess, "run", side_effect=failed):
            with self.assertRaisesRegex(sy_child.DecodeError, "no error output"):
                sy_child.decoded_bytes(TRACK)

    def test_text_longer_than_the_frames_is_too_long_with_no_spans(self) -> None:
        reply = self.run_main((2, 3, 4, 5, 6, 7))
        self.assertEqual((reply.status, reply.spans), (rq.STATUS_TOO_LONG, ()))
        self.assertEqual(reply.frames, FRAMES)

    def test_text_that_fits_is_aligned_one_span_a_token(self) -> None:
        reply = self.run_main((2, 3))
        self.assertEqual((reply.status, len(reply.spans)), (rq.STATUS_OK, 2))
        self.assertAlmostEqual(reply.frame_seconds, SECONDS / FRAMES)

    def test_a_break_after_the_second_line_is_masked_and_aligned_again(self) -> None:
        align, mask = FakeAligner(FIRST, SECOND), FakeMask()
        spans = self.reply_spans(TWO_LINES, align, mask, QUIET_FRAMES)
        self.assertEqual(spans, SECOND)
        self.assertEqual(mask.ranges, [TRIMMED_BREAK])
        self.assertEqual(align.over[1], FakeMask.MASKED)

    def test_with_no_break_the_first_alignment_stands(self) -> None:
        align, mask = FakeAligner(FIRST), FakeMask()
        self.assertEqual(self.reply_spans(TWO_LINES, align, mask), FIRST)
        self.assertEqual((len(align.over), mask.ranges), (1, []))

    def test_a_track_of_one_line_has_no_break(self) -> None:
        one_line = dataclasses.replace(TWO_LINES, line_of=(0, NO_LINE, 0))
        align, mask = FakeAligner(FIRST), FakeMask()
        self.assertEqual(self.reply_spans(one_line, align, mask, QUIET_FRAMES), FIRST)
        self.assertEqual(mask.ranges, [])

    def test_a_quiet_stretch_before_the_second_line_is_left_alone(self) -> None:
        late = [Span(0, 1, 0.9), Span(1, 2, 0.9), Span(22, 23, 0.9)]
        align, mask = FakeAligner(late), FakeMask()
        self.assertEqual(self.reply_spans(TWO_LINES, align, mask, QUIET_FRAMES), late)
        self.assertEqual(mask.ranges, [])

    def test_a_mask_leaving_too_few_frames_for_the_text_keeps_the_first(self) -> None:
        # 30 letters need 30 frames; the track has 40, and the break takes 11 of them.
        ids, line_of = (2, 3) * 15, (0,) * 15 + (1,) * 15
        request = dataclasses.replace(TWO_LINES, ids=ids, line_of=line_of)
        first = [Span(index, index + 1, 0.9) for index in range(30)]
        align, mask = FakeAligner(first), FakeMask()
        self.assertEqual(self.reply_spans(request, align, mask, QUIET_FRAMES), first)
        self.assertEqual(mask.ranges, [])

    def test_a_mask_leaving_exactly_the_frames_the_text_needs_is_applied(self) -> None:
        # 29 letters need 29 frames: the 40 less the break's 11, so it still fits.
        ids, line_of = (2, 3) * 14 + (2,), (0,) * 14 + (1,) * 15
        request = dataclasses.replace(TWO_LINES, ids=ids, line_of=line_of)
        first = [Span(index, index + 1, 0.9) for index in range(29)]
        align, mask = FakeAligner(first, SECOND), FakeMask()
        self.assertEqual(self.reply_spans(request, align, mask, QUIET_FRAMES), SECOND)
        self.assertEqual(mask.ranges, [TRIMMED_BREAK])

    def test_main_replies_with_the_second_alignment_over_a_break(self) -> None:
        reply = self.run_main(
            TWO_LINES.ids,
            TWO_LINES.line_of,
            frames=BREAK_TRACK_FRAMES,
            quiet=QUIET_FRAMES,
            align=FakeAligner(FIRST, SECOND),
        )
        self.assertEqual((reply.status, list(reply.spans)), (rq.STATUS_OK, SECOND))

    def test_a_text_that_is_not_heard_is_refused_before_scoring(self) -> None:
        reply = self.child_reply((2, 3), heard=WRONG_SONG)
        self.assertEqual(reply, rq.NotSung(Heard(21, 0.0), WRONG_SONG))
        self.assertFalse(self.scored.called)

    def test_a_text_not_sung_heard_at_length_replies_with_no_transcript(self) -> None:
        heard = " ".join([WRONG_SONG] * 600)
        self.assertFalse(transcript_fits(heard))
        reply = self.child_reply((2, 3), heard=heard)
        # One pair more than the song once: its last word leads into its first.
        self.assertEqual(reply, rq.NotSung(Heard(22, 0.0), None))

    def test_an_aligned_reply_carries_what_was_heard(self) -> None:
        reply = self.run_main((2, 3), heard="et et et")
        self.assertEqual((reply.status, reply.heard), (rq.STATUS_OK, Heard(1, 0.0)))
        self.assertTrue(self.scored.called)
        too_long = self.run_main((2, 3, 4, 5, 6, 7))
        self.assertEqual(too_long.heard, Heard(0, 0.0))

    def reply_spans(
        self,
        request: rq.Request,
        align: FakeAligner,
        mask: FakeMask,
        quiet: range = range(0),
    ) -> list[Span]:
        """The spans the child replies with for *request* over the break track."""
        emissions = SimpleNamespace(shape=(BREAK_TRACK_FRAMES,))
        track_levels = levels(BREAK_TRACK_FRAMES, quiet)
        return sy_child.reply_spans(emissions, request, track_levels, 1.0, align, mask)

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

    def run_main(
        self,
        ids: tuple[int, ...],
        line_of: tuple[int, ...] | None = None,
        *,
        frames: int = FRAMES,
        quiet: range = range(0),
        align: FakeAligner | None = None,
        heard: str = "",
    ) -> rq.Reply:
        """`child_reply`, which must be an aligned or too-long reply."""
        reply = self.child_reply(
            ids, line_of, frames=frames, quiet=quiet, align=align, heard=heard
        )
        assert isinstance(reply, rq.Reply), reply
        return reply

    def child_reply(
        self,
        ids: tuple[int, ...],
        line_of: tuple[int, ...] | None = None,
        *,
        frames: int = FRAMES,
        quiet: range = range(0),
        align: FakeAligner | None = None,
        heard: str = "",
    ) -> rq.Reply | rq.NotSung:
        """The child's main on a request for *ids*, PyTorch's steps stood in; its reply.

        The track has *frames* frames over *frames* seconds when *quiet* names a break,
        else over `SECONDS`; *align*, when given, stands in for the alignment, and
        whisper-cli hears *heard*: by default nothing, which cannot judge the text.
        """
        work = self.tmp / "work"
        work.mkdir(exist_ok=True)
        request, reply = work / "request.json", work / "reply.json"
        lines = line_of if line_of is not None else tuple(range(len(ids)))
        asked = dataclasses.replace(
            TWO_LINES, audio=self.tmp / "a.mp3", ids=ids, model_dir=self.tmp
        )
        dumped = rq.dump_request(dataclasses.replace(asked, line_of=lines))
        request.write_text(dumped, encoding="utf-8", newline="\n")
        emissions = SimpleNamespace(shape=(frames,))
        seconds = float(frames) if quiet else SECONDS
        found = (emissions, seconds, levels(frames, quiet))
        with (
            mock.patch.object(sy_child, "_refuse_network"),
            mock.patch.object(sy_child, "_decode"),
            mock.patch.object(sy_child, "_stems", return_value=(None, ())),
            mock.patch.object(sy_child, "_pcm16", return_value=b"\0\0" * 16000),
            mock.patch.object(sy_child.at_engine, "run_bounded", whisper_hears(heard)),
            mock.patch.object(
                sy_child, "_emissions", return_value=found
            ) as self.scored,
            mock.patch.object(sy_child, "_align", side_effect=align or stand_in_align),
            mock.patch.object(sy_child, "_mask", FakeMask()),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = sy_child.main(["--request", str(request), "--reply", str(reply)])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in work.iterdir()), NAMES_LEFT)
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
