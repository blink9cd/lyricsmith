"""The album tool child: its command line, its bound, and finding the run it made.

The runner is driven against real child interpreters running tiny invented scripts, so
the process boundary itself is exercised: an exit code passed through, the per-album
bound stopping a child with Ctrl-C's own signal, and a child that ignores that signal
killed. Only the parent's own Ctrl-C is played by a fake process, since a real one would
interrupt the test run.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_bandcamp import (  # noqa: E402 - import follows the sys.path bootstrap above
    BandcampProvider,
)
from lyrics_fetch import (  # noqa: E402 - import follows the sys.path bootstrap above
    CHAIN,
    PROVIDER_DEADLINE_S,
    AnswersEveryKind,
    OwnsDeadline,
)
from lyrics_genius import (  # noqa: E402 - import follows the sys.path bootstrap above
    GeniusProvider,
    GeniusSong,
)
from lyrics_model import (  # noqa: E402 - import follows the sys.path bootstrap above
    LyricsKind,
    Provider,
    ProviderKey,
    Source,
)
from lyrics_musicbrainz import (  # noqa: E402 - import follows the sys.path bootstrap above
    MusicBrainzClient,
)
from lyrics_silence import SILENCE_TIMEOUT_S  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_table import (  # noqa: E402 - import follows the sys.path bootstrap above
    build_provider_table,
)
from lyrics_tekstowo import (  # noqa: E402 - import follows the sys.path bootstrap above
    TekstowoClient,
    TekstowoProvider,
)
from lyrics_youtube_music import (  # noqa: E402 - import follows the sys.path bootstrap above
    YouTubeMusicClient,
)
from lyrics_youtube_provider import (  # noqa: E402 - import follows the sys.path bootstrap above
    YouTubeMusicProvider,
)

#: A bound no child script here comes near, so only the bound under test ever fires.
_GENEROUS_S = 60.0
#: The pid the fake processes below report.
_PID = 4242
#: A child that stops at Ctrl-C's signal with the shell's interrupted code.
_STOPS_ON_SIGINT = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGINT, lambda *_: sys.exit(130))\n"
    "time.sleep(60)\n"
)
#: A child that ignores Ctrl-C's signal altogether.
_IGNORES_SIGINT = (
    "import signal, time\n"
    "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
    "time.sleep(60)\n"
)


class _UnaskedGenius:
    """A Genius client the bound's derivation builds a step around and never asks."""

    def search_song(  # pragma: no cover - never called: only the bound is read
        self, title: str | None = None, artist: str = ""
    ) -> GeniusSong:
        """Fail the test: the derivation asks Genius nothing."""
        raise AssertionError(f"Genius asked for {title!r} by {artist!r}")


def _watchdog_bound(provider: Provider) -> float:
    """The bound the chain's watchdog gives one call to *provider*."""
    if isinstance(provider, OwnsDeadline):
        return provider.step_deadline_s()
    return PROVIDER_DEADLINE_S


def _python(code: str) -> list[str]:
    """The argv running *code* in a fresh, isolated interpreter."""
    return [sys.executable, "-I", "-c", code]


class ArgvTest(sf.IsolatedCase):
    """The command line the sweep gives the album tool."""

    def test_a_plain_run_names_the_album_and_the_run_mode(self) -> None:
        entry = self.tmp / "album.py"
        argv = sweep_child.album_argv(
            entry, self.library / "A" / "B", lang=None, offline=False
        )
        self.assertEqual(
            argv,
            [
                sys.executable,
                str(entry),
                "--album",
                str(self.library / "A" / "B"),
                "--mode",
                "run",
            ],
        )

    def test_lang_and_offline_pass_through_and_fresh_never_appears(self) -> None:
        argv = sweep_child.album_argv(
            self.tmp / "e.py", self.library, lang="deu", offline=True
        )
        self.assertEqual(argv[-3:], ["--lang", "deu", "--offline"])
        self.assertNotIn("--fresh", argv)

    def test_every_option_parses_as_the_album_tools_own_parser_reads_it(
        self,
    ) -> None:
        # Golden: the album tool's real parser reads each option the sweep can pass,
        # all of them on, into the field it means, and leaves every other at its
        # default; a renamed or mistyped flag fails here, not in a live sweep.
        folder, listed = self.library / "A" / "B", self.tmp / "changed.txt"
        argv = sweep_child.album_argv(
            self.tmp / "album.py",
            folder,
            lang="deu",
            offline=True,
            refetch_untimed=True,
            changed_list=listed,
            skip=tuple(flag for _, flag in sweep_child.SKIP_OPTIONS),
        )
        self.assertEqual(argv[:2], [sys.executable, str(self.tmp / "album.py")])
        parser = album.build_parser()
        # An abbreviation of a real flag would parse too, and then break the day the
        # album tool adds a second flag sharing that prefix: only exact flags pass.
        parser.allow_abbrev = False
        defaults = vars(parser.parse_args(["--album", str(folder)]))
        expected = {
            **defaults,
            "mode": album.MODE_RUN,
            "lang": "deu",
            "offline": True,
            "refetch_untimed": True,
            "changed_list": listed,
            **{attr: True for attr, _ in sweep_child.SKIP_OPTIONS},
        }
        self.assertEqual(vars(parser.parse_args(argv[2:])), expected)

    def test_the_real_entry_point_is_the_album_tool_beside_this_tool(self) -> None:
        tool = Path(sweep.__file__).resolve().parent
        self.assertEqual(
            sweep.ALBUM_ENTRY,
            tool.parent / "album" / "album.py",
        )


class RunChildTest(sf.IsolatedCase):
    """`run_child` against real child interpreters."""

    def test_the_exit_code_passes_through(self) -> None:
        result = sweep_child.run_child(_python("raise SystemExit(3)"), _GENEROUS_S)
        self.assertEqual(result, sweep_child.ChildResult(3))

    def test_a_child_past_its_bound_is_stopped_with_ctrl_c_and_recorded(self) -> None:
        result = sweep_child.run_child(_python(_STOPS_ON_SIGINT), 1.0)
        self.assertEqual(result, sweep_child.ChildResult(130, timed_out=True))

    def test_a_child_that_ignores_ctrl_c_is_killed_after_the_grace(self) -> None:
        result = sweep_child.run_child(_python(_IGNORES_SIGINT), 0.5, stop_grace_s=0.5)
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.exit_code, 0)

    def test_the_bounds_are_derived_from_the_album_tools_own_steps(self) -> None:
        # The chain's real steps, the Genius one built as a key would build it and the
        # Bandcamp, YouTube Music and Tekstowo ones as the run turns them on, each taken
        # at the bound the watchdog gives it. An every-kind provider is asked once per
        # pass, so its steps share one call: YouTube Music's two steps are one instance.
        table = dict(build_provider_table(None))
        table[ProviderKey(Source.GENIUS, LyricsKind.UNSYNCED)] = GeniusProvider(
            _UnaskedGenius(), deadline_s=PROVIDER_DEADLINE_S
        )
        table[ProviderKey(Source.BANDCAMP, LyricsKind.UNSYNCED)] = BandcampProvider(
            MusicBrainzClient()
        )
        youtube = YouTubeMusicProvider(YouTubeMusicClient(self.tmp / "s.json"))
        for kind in LyricsKind:
            table[ProviderKey(Source.YOUTUBE_MUSIC, kind)] = youtube
        table[ProviderKey(Source.TEKSTOWO, LyricsKind.UNSYNCED)] = TekstowoProvider(
            TekstowoClient(self.tmp / "t.json")
        )
        calls = {
            (key.source if isinstance(table[key], AnswersEveryKind) else key): (
                _watchdog_bound(table[key])
            )
            for key in CHAIN
        }
        # Two passes of every call, and one silence measurement (a track is measured once).
        self.assertEqual(
            sweep_child.TRACK_WORST_S,
            2 * sum(calls.values()) + SILENCE_TIMEOUT_S,
        )
        self.assertEqual(
            sweep_child.ALBUM_TIMEOUT_S,
            sweep_child._LONG_ALBUM_TRACKS * sweep_child.TRACK_WORST_S,
        )
        self.assertGreater(sweep_child.STOP_GRACE_S, sweep_child.KILL_WAIT_S)

    def test_the_bandcamp_bound_is_the_steps_own(self) -> None:
        # The sweep reads Bandcamp's bound from a constant, so a step whose own deadline
        # moved without it would leave the per-album bound short.
        self.assertEqual(
            sweep_child._OWN_BOUNDS[Source.BANDCAMP],
            BandcampProvider(MusicBrainzClient()).step_deadline_s(),
        )

    def test_the_youtube_music_bound_is_the_steps_own(self) -> None:
        client = YouTubeMusicClient(self.tmp / "s.json")
        self.assertEqual(
            sweep_child._OWN_BOUNDS[Source.YOUTUBE_MUSIC],
            YouTubeMusicProvider(client).step_deadline_s(),
        )

    def test_the_tekstowo_bound_is_the_steps_own(self) -> None:
        client = TekstowoClient(self.tmp / "t.json")
        self.assertEqual(
            sweep_child._OWN_BOUNDS[Source.TEKSTOWO],
            TekstowoProvider(client).step_deadline_s(),
        )


@dataclass
class _InterruptedProcess:
    """A process whose first wait is cut short by the parent's Ctrl-C."""

    code: int = 130
    pid: int = _PID
    waits: list[float | None] = field(default_factory=list)

    def wait(self, timeout: float | None = None) -> int:
        """Raise KeyboardInterrupt the first time, then report *code*."""
        self.waits.append(timeout)
        if len(self.waits) == 1:
            raise KeyboardInterrupt
        return self.code

    def send_signal(self, signal: int) -> None:
        """Never expected: Ctrl-C already reached the child."""
        raise AssertionError(f"signal {signal} sent")

    def kill(self) -> None:
        """Never expected: the child stops by itself."""
        raise AssertionError("killed")


class InterruptTest(sf.IsolatedCase):
    """Ctrl-C reaches the parent and the child alike; the parent waits for the child."""

    def test_the_child_is_waited_for_and_the_run_marked_interrupted(self) -> None:
        process = _InterruptedProcess()

        def popen(argv: Sequence[str], **_kwargs: object) -> _InterruptedProcess:
            return process

        result = sweep_child.run_child(["child"], _GENEROUS_S, popen=popen)
        self.assertEqual(result, sweep_child.ChildResult(130, interrupted=True))
        self.assertEqual(process.waits, [_GENEROUS_S, sweep_child.STOP_GRACE_S])


@dataclass
class _ScriptedProcess:
    """A process whose waits play *steps* in order: an exit code, or an exception."""

    steps: list[int | BaseException]
    pid: int = _PID
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

    def send_signal(self, signal: int) -> None:
        """Record *signal*."""
        self.signals.append(signal)

    def kill(self) -> None:
        """Record the kill."""
        self.kills += 1


def _expired() -> subprocess.TimeoutExpired:
    """The exception a bounded wait raises when its bound passes."""
    return subprocess.TimeoutExpired("child", 0.0)


class SecondInterruptTest(sf.IsolatedCase):
    """A second Ctrl-C, or a child that will not die, never leaves the album unrecorded."""

    def _run(self, process: _ScriptedProcess) -> sweep_child.ChildResult:
        def popen(argv: Sequence[str], **_kwargs: object) -> _ScriptedProcess:
            return process

        return sweep_child.run_child(
            ["child"], _GENEROUS_S, popen=popen, stop_grace_s=5.0, kill_wait_s=3.0
        )

    def test_a_second_ctrl_c_in_the_grace_kills_the_child_and_waits_again(
        self,
    ) -> None:
        process = _ScriptedProcess([KeyboardInterrupt(), KeyboardInterrupt(), -9])
        result = self._run(process)
        self.assertEqual(result, sweep_child.ChildResult(-9, interrupted=True))
        self.assertEqual(process.kills, 1)
        self.assertEqual(process.waits, [_GENEROUS_S, 5.0, 3.0])

    def test_ctrl_c_in_the_grace_after_the_bound_is_both(self) -> None:
        process = _ScriptedProcess([_expired(), KeyboardInterrupt(), -9])
        result = self._run(process)
        self.assertEqual(
            result, sweep_child.ChildResult(-9, timed_out=True, interrupted=True)
        )
        self.assertEqual(process.kills, 1)

    def test_a_child_that_outlives_its_kill_is_abandoned_by_pid(self) -> None:
        process = _ScriptedProcess([KeyboardInterrupt(), _expired(), _expired()])
        result = self._run(process)
        self.assertEqual(
            result, sweep_child.ChildResult(None, interrupted=True, pid=_PID)
        )

    def test_a_ctrl_c_during_the_kill_wait_abandons_the_child_by_pid(self) -> None:
        process = _ScriptedProcess([_expired(), _expired(), KeyboardInterrupt()])
        result = self._run(process)
        self.assertEqual(
            result,
            sweep_child.ChildResult(None, timed_out=True, interrupted=True, pid=_PID),
        )


class NewRunTest(sf.IsolatedCase):
    """Finding the runs a child just made, by the runs that were not there before."""

    def setUp(self) -> None:
        """An album, and a fake album tool to make its runs."""
        super().setUp()
        self.album = sf.needy_album(self.library, "Artist", "Album")
        self.fake = sf.FakeAlbumTool()

    def _run(self) -> None:
        self.fake(["--album", str(self.album)], _GENEROUS_S)

    def test_an_album_never_run_has_no_runs(self) -> None:
        self.assertEqual(sweep_child.run_ids(self.album), frozenset())
        self.assertEqual(sweep_child.new_run_ids(self.album, frozenset()), frozenset())

    def test_the_new_run_is_read_through_the_album_tools_own_reader(self) -> None:
        self._run()
        before = sweep_child.run_ids(self.album)
        self._run()
        (run_id,) = sweep_child.new_run_ids(self.album, before)
        self.assertNotIn(run_id, before)
        manifest = sweep_child.read_run(self.album, run_id)
        assert manifest is not None
        self.assertEqual(manifest.run_id, run_id)
        self.assertEqual(len(manifest.tracks), len(sf.ANSWERED))

    def test_a_run_whose_id_sorts_first_is_still_the_new_one(self) -> None:
        # A clock set back between two runs gives the new run the older-looking id.
        self._run()
        before = sweep_child.run_ids(self.album)
        self.fake.clock -= timedelta(hours=1)
        self._run()
        (run_id,) = sweep_child.new_run_ids(self.album, before)
        self.assertLess(run_id, min(before))

    def test_no_new_run_is_an_empty_set(self) -> None:
        self._run()
        before = sweep_child.run_ids(self.album)
        self.assertEqual(sweep_child.new_run_ids(self.album, before), frozenset())

    def test_a_folder_without_a_manifest_is_no_run(self) -> None:
        (self.album / lr.RUN_FOLDER_NAME / "20261019T120000Z").mkdir(parents=True)
        self.assertEqual(sweep_child.run_ids(self.album), frozenset())

    def test_a_folder_a_prune_left_is_no_run(self) -> None:
        # A prune stopped between its rename and its delete leaves the manifest inside.
        self._run()
        (run_id,) = sweep_child.run_ids(self.album)
        parent = self.album / lr.RUN_FOLDER_NAME
        (parent / run_id).rename(parent / f"{run_id}.pruning")
        self.assertEqual(sweep_child.run_ids(self.album), frozenset())

    def test_a_linked_run_folder_holds_no_run(self) -> None:
        elsewhere = self.tmp / "elsewhere"
        self.fake(["--album", str(elsewhere)], _GENEROUS_S)
        self.album.joinpath(lr.RUN_FOLDER_NAME).symlink_to(
            elsewhere / lr.RUN_FOLDER_NAME
        )
        self.assertEqual(sweep_child.run_ids(self.album), frozenset())

    def test_an_unreadable_manifest_reads_as_none(self) -> None:
        self._run()
        (manifest,) = self.album.glob(f"{lr.RUN_FOLDER_NAME}/*/{lr.MANIFEST_NAME}")
        manifest.write_text("{}", encoding="utf-8", newline="")
        self.assertIsNone(sweep_child.read_run(self.album, manifest.parent.name))

    def test_a_run_removed_before_it_is_read_reads_as_none(self) -> None:
        self.assertIsNone(sweep_child.read_run(self.album, "20261019T120000Z"))


class RealPopenTest(sf.IsolatedCase):
    """The real spawn gives the child no stdin to read the terminal from."""

    def test_the_child_reads_end_of_file(self) -> None:
        code = "import sys; sys.exit(0 if sys.stdin.read() == '' else 9)"
        result = sweep_child.run_child(_python(code), _GENEROUS_S)
        self.assertEqual(result.exit_code, 0)
