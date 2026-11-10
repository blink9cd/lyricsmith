"""``sweep --mode genius-check`` end to end: a track list in, three lists out, nothing else.

`main` runs the check over an invented library of tagged tracks, its search client
scripted (`genius_check_fixtures`), so every finding is reached without a request
leaving the process. It writes ``has-it.tsv``, ``not-on-genius.txt`` and ``unsure.tsv``
into ``--out``, writes nothing in the library, and never prints or writes the key.
Every title, artist and key here is invented.
"""

from __future__ import annotations

import os
import sys
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - puts album and the libraries on sys.path
import genius_check_fixtures as gcf  # noqa: E402 - import follows the sys.path bootstrap above
import genius_search_fakes as gsf  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes as gf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_genius_check as gc  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_genius_lists as gl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_genius_seams as gs  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_config import GENIUS_TOKEN_ENV  # noqa: E402 - follows the sys.path bootstrap
from lyrics_model import TrackTags  # noqa: E402 - follows the sys.path bootstrap

_HAS_IT_HEADER = "track\tgenius url\tgenius title\tgenius artist\n"
_UNSURE_HEADER = "track\treason\tclosest genius url\n"


class OutcomesTest(gcf.CheckCase):
    """Each track lands in one of the three lists."""

    def setUp(self) -> None:
        """Three tagged tracks: one Genius has, one it lacks, one near."""
        super().setUp()
        album = self.library / "Invented Artist" / "Lanterns"
        self.has = gcf.tagged_track(album, "01.mp3", "Invented Artist", "Keeper")
        self.lacks = gcf.tagged_track(album, "02.mp3", "Invented Artist", "Ferry")
        self.near = gcf.tagged_track(album, "03.mp3", "Invented Artist", "Gutter")
        self.write_list(self.has, self.lacks, self.near)

    def _client(self) -> gsf.FakeSearchClient:
        """Replies matching the first track, far from the second, near the third."""
        return gsf.FakeSearchClient(
            [
                gsf.search_reply(gsf.song_result("Keeper", "Invented Artist")),
                gsf.search_reply(gsf.song_result("Paper Boats", "Someone Else")),
                gsf.search_reply(gsf.song_result("Gutter", "Someone Else")),
            ]
        )

    def test_each_list_holds_its_tracks(self) -> None:
        """has-it, not-on-genius and unsure each hold their one track."""
        result = self.run_check(self._client())
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(
            self.read_out(gl.HAS_IT_NAME),
            _HAS_IT_HEADER + f"{self.has}\t{gsf.SONG_URL}\tKeeper\tInvented Artist\n",
        )
        self.assertEqual(
            lyrics_changed.parse_changed_list(
                (self.out / gl.NOT_ON_GENIUS_NAME).read_bytes()
            ),
            (str(self.lacks),),
        )
        self.assertEqual(
            self.read_out(gl.UNSURE_NAME),
            _UNSURE_HEADER + f"{self.near}\tnear miss\t{gsf.SONG_URL}\n",
        )

    def test_the_summary_counts_and_names_the_files(self) -> None:
        """Stdout counts each finding and names each file written."""
        result = self.run_check(self._client())
        self.assertIn("has it: 1", result.stdout)
        self.assertIn("not on Genius: 1", result.stdout)
        self.assertIn("unsure: 1", result.stdout)
        for name in (gl.HAS_IT_NAME, gl.NOT_ON_GENIUS_NAME, gl.UNSURE_NAME):
            self.assertIn(f"wrote: {self.out / name}", result.stdout)

    def test_each_search_is_the_title_then_the_artist(self) -> None:
        """The term is the title and the artist, and a page of five is asked."""
        client = self._client()
        self.run_check(client)
        self.assertEqual(
            client.calls,
            [
                ("Keeper Invented Artist", 5),
                ("Ferry Invented Artist", 5),
                ("Gutter Invented Artist", 5),
            ],
        )

    def test_the_library_is_not_written(self) -> None:
        """Every file of the library is as it was, and nothing is added to it."""
        before = {
            p: (p.read_bytes(), p.stat().st_mtime_ns) for p in _files(self.library)
        }
        self.run_check(self._client())
        after = {
            p: (p.read_bytes(), p.stat().st_mtime_ns) for p in _files(self.library)
        }
        self.assertEqual(before, after)
        self.assertEqual(
            sorted(p.name for p in self.out.iterdir()),
            sorted((gl.HAS_IT_NAME, gl.NOT_ON_GENIUS_NAME, gl.UNSURE_NAME)),
        )

    def test_the_key_is_never_printed_or_written(self) -> None:
        """The key is handed to the client builder, and appears in no output."""
        result = self.run_check(self._client())
        self.assertEqual([secret.reveal() for secret in self.built], [gcf.TOKEN])
        written = "".join(self.read_out(p.name) for p in self.out.iterdir())
        for text in (result.stdout, result.stderr, written):
            self.assertNotIn(gcf.TOKEN, text)


class RefusedTest(gcf.CheckCase):
    """A refused search, and the breaker it opens, leave tracks unsure."""

    def setUp(self) -> None:
        """Two tagged tracks in the list."""
        super().setUp()
        album = self.library / "Invented Artist" / "Lanterns"
        self.first = gcf.tagged_track(album, "01.mp3", "Invented Artist", "Keeper")
        self.second = gcf.tagged_track(album, "02.mp3", "Invented Artist", "Ferry")
        self.write_list(self.first, self.second)

    def test_a_challenge_past_every_retry_is_refused_then_the_breaker_holds(
        self,
    ) -> None:
        """The first track is refused after its retries; the second is never asked."""
        client = gsf.FakeSearchClient([gf.challenge(403)] * 4)
        result = self.run_check(client)
        self.assertEqual(result.code, gc.EXIT_UNANSWERED, result.stderr)
        self.assertEqual(gc.EXIT_UNANSWERED, 1)
        self.assertEqual(len(client.calls), 4)
        self.assertEqual(
            self.read_out(gl.UNSURE_NAME),
            _UNSURE_HEADER
            + f"{self.first}\trefused: HTTP 403 after 3 retries\t\n"
            + f"{self.second}\tnot asked: breaker open\t\n",
        )
        self.assertEqual(self.read_out(gl.HAS_IT_NAME), _HAS_IT_HEADER)
        self.assertEqual(self.read_out(gl.NOT_ON_GENIUS_NAME), "")

    def test_a_breaker_already_open_sends_nothing(self) -> None:
        """A breaker a run left open holds every track back unasked."""
        self.run_check(gsf.FakeSearchClient([gf.challenge(403)] * 4))
        client = gsf.FakeSearchClient([])
        result = self.run_check(client)
        self.assertEqual(result.code, 1)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.read_out(gl.UNSURE_NAME).count("breaker open"), 2)
        self.assertIn("unsure: 2", result.stdout)

    def test_a_breaker_file_not_written_is_said(self) -> None:
        """A breaker whose file cannot be written is noticed, and the check goes on."""
        self.state.mkdir(parents=True)
        (self.state / "lyricsmith").write_bytes(b"not a folder")
        result = self.run_check(gsf.FakeSearchClient([gf.challenge(403)] * 4))
        self.assertEqual(result.code, 1, result.stderr)
        self.assertIn("could not write the breaker file", result.stderr)

    def test_a_reply_of_unreadable_hits_is_refused_never_lacking(self) -> None:
        """Hits of a drifted shape, none read, leave the track unsure, exit 1."""
        record = gsf.song_result("Keeper", "Invented Artist")
        drifted = {"hits": [{"type": "Song", "result": record}]}
        reply = gsf.search_reply(gsf.song_result("Ferry", "Invented Artist"))
        result = self.run_check(gsf.FakeSearchClient([drifted, reply]))
        self.assertEqual(result.code, 1, result.stderr)
        self.assertEqual(
            self.read_out(gl.UNSURE_NAME),
            _UNSURE_HEADER + f"{self.first}\trefused: hits unreadable\t\n",
        )
        self.assertEqual(self.read_out(gl.NOT_ON_GENIUS_NAME), "")


class TracksTest(gcf.CheckCase):
    """Tracks read by their tags, by their file name, or not at all."""

    def test_a_track_with_no_tags_is_asked_by_its_file_name(self) -> None:
        """An untagged track's term is its file stem, its track number dropped."""
        track = sf.add_track(self.library / "A" / "B", "04 - Keeper.mp3")
        self.write_list(track)
        client = gsf.FakeSearchClient([gsf.search_reply()])
        self.run_check(client)
        self.assertEqual(client.calls, [("Keeper", 5)])

    def test_a_track_asked_by_its_file_name_is_never_lacking(self) -> None:
        """Nothing close to a file name's guess is unsure: the tags might find it."""
        track = sf.add_track(self.library / "A" / "B", "04 Keeper.mp3")
        self.write_list(track)
        result = self.run_check(gsf.FakeSearchClient([gsf.search_reply()]))
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.read_out(gl.NOT_ON_GENIUS_NAME), "")
        self.assertEqual(
            self.read_out(gl.UNSURE_NAME),
            _UNSURE_HEADER + f"{track}\tno close hit, searched by file name\t\n",
        )

    def test_a_track_listed_twice_is_searched_and_listed_once(self) -> None:
        """A repeated path keeps its first place, and is asked once."""
        album = self.library / "Invented Artist" / "Lanterns"
        first = gcf.tagged_track(album, "01.mp3", "Invented Artist", "Keeper")
        second = gcf.tagged_track(album, "02.mp3", "Invented Artist", "Ferry")
        # Written by hand: the list writer itself names each path once.
        lines = "".join(f"{track}\n" for track in (first, second, first, second))
        self.list_path.write_bytes(lines.encode())
        client = gsf.FakeSearchClient([gsf.search_reply()] * 2)
        result = self.run_check(client)
        self.assertEqual(len(client.calls), 2)
        self.assertIn("2 tracks", result.stdout)
        self.assertEqual(self.read_out(gl.NOT_ON_GENIUS_NAME), f"{first}\n{second}\n")

    def test_a_track_that_cannot_be_read_is_unsure_unasked(self) -> None:
        """A listed track that is gone is unsure, and no search is made for it."""
        gone = self.library / "A" / "B" / "gone.mp3"
        self.write_list(gone)
        client = gsf.FakeSearchClient([])
        result = self.run_check(client)
        # Genius answered every search asked: the track itself was unreadable.
        self.assertEqual(result.code, 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(
            self.read_out(gl.UNSURE_NAME),
            _UNSURE_HEADER + f"{gone}\ttrack unreadable\t\n",
        )

    def test_an_empty_list_writes_empty_lists(self) -> None:
        """No track, no search: three lists with their headers alone."""
        self.write_list()
        result = self.run_check(gsf.FakeSearchClient([]))
        self.assertEqual(result.code, 0)
        self.assertEqual(self.read_out(gl.UNSURE_NAME), _UNSURE_HEADER)


class InterruptTest(gcf.CheckCase):
    """Ctrl-C keeps what was checked, lists the rest unsure, and exits as a sweep does."""

    def test_the_tracks_left_are_unsure_and_the_lists_written(self) -> None:
        """The track in hand and those after it are unsure, unasked."""
        album = self.library / "Invented Artist" / "Lanterns"
        tracks = [
            gcf.tagged_track(album, f"0{n}.mp3", "Invented Artist", f"Song {n}")
            for n in (1, 2, 3)
        ]
        self.write_list(*tracks)
        client = gsf.FakeSearchClient([gsf.search_reply()])
        real = self.read_tags
        reads = iter((real, KeyboardInterrupt))

        def read(path: Path) -> TrackTags:
            step = next(reads)
            if step is KeyboardInterrupt:
                raise KeyboardInterrupt
            return real(path)

        self.read_tags = read
        result = self.run_check(client)
        self.assertEqual(result.code, gc.EXIT_INTERRUPTED)
        self.assertIn("interrupted", result.stderr)
        self.assertEqual(
            self.read_out(gl.UNSURE_NAME),
            _UNSURE_HEADER
            + f"{tracks[1]}\tnot asked: interrupted\t\n"
            + f"{tracks[2]}\tnot asked: interrupted\t\n",
        )
        self.assertEqual(self.read_out(gl.NOT_ON_GENIUS_NAME), f"{tracks[0]}\n")


class _LockingClient(gsf.FakeSearchClient):
    """A scripted client whose every search answers through *lock*, its side effect."""

    def __init__(
        self, script: list[gsf.Step], *, lock: Callable[[], Mapping[str, object]]
    ) -> None:
        """Play *script*, then answer each search with what *lock* returns."""
        super().__init__(script)
        self.lock = lock

    def search_songs(
        self, search_term: str, per_page: int | None = None, page: int | None = None
    ) -> Mapping[str, object]:
        """Record the call as the script would, then answer through *lock*."""
        super().search_songs(search_term, per_page, page)
        return self.lock()


class InterruptedSearchTest(gcf.CheckCase):
    """Ctrl-C during a search: that track and the rest are unsure, unasked."""

    @unittest.skipUnless(gcf.HOST_SIGNALS_THREADS, gcf.NO_THREAD_SIGNAL)
    def test_a_search_cut_short_leaves_the_rest_unsure(self) -> None:
        """The first track is judged; the one in hand and the third are interrupted."""
        taken = gcf.interrupt_taken(self)
        client = gcf.CtrlCClient([gsf.search_reply()] * 3, taken=taken)
        self.assert_cut_after_the_first(client)

    def test_an_interrupt_on_the_search_thread_is_the_same_interrupt(self) -> None:
        """A `KeyboardInterrupt` from the search's own code stops it, never a refusal."""
        script: list[gsf.Step] = [gsf.search_reply(), KeyboardInterrupt()]
        self.assert_cut_after_the_first(gsf.FakeSearchClient(script))

    def assert_cut_after_the_first(self, client: gsf.FakeSearchClient) -> None:
        """Three tracks checked on *client*, cut short by its second search."""
        album = self.library / "Invented Artist" / "Lanterns"
        tracks = [
            gcf.tagged_track(album, f"0{n}.mp3", "Invented Artist", f"Song {n}")
            for n in (1, 2, 3)
        ]
        self.write_list(*tracks)
        result = self.run_check(client)
        self.assertEqual(result.code, gc.EXIT_INTERRUPTED, result.stderr)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(self.read_out(gl.NOT_ON_GENIUS_NAME), f"{tracks[0]}\n")
        self.assertEqual(
            self.read_out(gl.UNSURE_NAME),
            _UNSURE_HEADER
            + f"{tracks[1]}\tnot asked: interrupted\t\n"
            + f"{tracks[2]}\tnot asked: interrupted\t\n",
        )
        self.assertEqual(self.read_out(gl.HAS_IT_NAME), _HAS_IT_HEADER)


class SetupTest(gcf.CheckCase):
    """What stops the check before any search: each exits 2, nothing written."""

    def setUp(self) -> None:
        """One tagged track in the list."""
        super().setUp()
        album = self.library / "Invented Artist" / "Lanterns"
        self.write_list(gcf.tagged_track(album, "01.mp3", "Invented Artist", "Keeper"))

    def assert_refused(self, result: gcf.Result, words: str) -> None:
        """*result* exited 2, saying *words*, with no search and no file written."""
        self.assertEqual(result.code, 2)
        self.assertIn(words, result.stderr)
        self.assertFalse(self.out.exists())

    def test_no_key_exits_2(self) -> None:
        """With no key found the check stops, saying where it looked."""
        del os.environ[GENIUS_TOKEN_ENV]
        client = gsf.FakeSearchClient([])
        self.assert_refused(self.run_check(client), "no Genius key found")
        self.assertEqual(self.built, [])

    def test_a_config_file_warning_is_said(self) -> None:
        """An unreadable default config file is a notice, then no key."""
        del os.environ[GENIUS_TOKEN_ENV]
        config = self.home / ".config" / "lyricsmith" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_bytes(b"[genius\n")
        result = self.run_check(gsf.FakeSearchClient([]))
        self.assert_refused(result, "genius: ")

    def test_a_malformed_list_exits_2(self) -> None:
        """A list with a relative path is not a track list."""
        self.list_path.write_bytes(b"relative/track.mp3\n")
        result = self.run_check(gsf.FakeSearchClient([]))
        self.assert_refused(result, "not a track list")

    def test_a_missing_list_exits_2(self) -> None:
        """A list that cannot be read stops the check."""
        self.list_path.unlink()
        result = self.run_check(gsf.FakeSearchClient([]))
        self.assert_refused(result, "cannot be read")

    def test_a_list_past_the_cap_exits_2(self) -> None:
        """A list larger than the cap is refused unread past it."""
        with mock.patch.object(gc, "MAX_TRACKS_FROM_BYTES", 4):
            result = self.run_check(gsf.FakeSearchClient([]))
        self.assert_refused(result, "more than 4 bytes")

    def test_an_out_that_cannot_be_written_exits_2(self) -> None:
        """An --out that is a file cannot hold the lists: refused before any search."""
        self.out.write_bytes(b"a file")
        reply = gsf.search_reply(gsf.song_result("Keeper", "Invented Artist"))
        client = gsf.FakeSearchClient([reply])
        result = self.run_check(client)
        self.assertEqual(result.code, 2)
        self.assertIn("--out cannot be written", result.stderr)
        self.assertEqual(client.calls, [])

    def test_an_unwritable_out_exits_2_unsearched(self) -> None:
        """An --out folder that cannot be written to stops the check before a search."""
        self.out.mkdir()
        self.out.chmod(0o500)
        self.addCleanup(self.out.chmod, 0o700)
        client = gsf.FakeSearchClient([gsf.search_reply()])
        result = self.run_check(client)
        self.assertEqual(result.code, 2)
        self.assertIn("--out cannot be written", result.stderr)
        self.assertEqual(client.calls, [])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_an_out_whose_parent_cannot_be_written_exits_2(self) -> None:
        """An --out that cannot be made stops the check before a search."""
        parent = self.tmp / "locked"
        parent.mkdir()
        parent.chmod(0o500)
        self.addCleanup(parent.chmod, 0o700)
        self.out = parent / "checked"
        client = gsf.FakeSearchClient([gsf.search_reply()])
        result = self.run_check(client)
        self.assertEqual(result.code, 2)
        self.assertIn("--out cannot be written", result.stderr)
        self.assertEqual(client.calls, [])

    def test_an_out_made_unwritable_during_the_searches_exits_2(self) -> None:
        """An --out that stops taking files after setup is said, and exits 2."""
        reply = gsf.search_reply(gsf.song_result("Keeper", "Invented Artist"))

        def lock_out() -> dict[str, object]:
            self.out.chmod(0o500)
            return reply

        self.addCleanup(lambda: self.out.chmod(0o700))
        client = _LockingClient([reply], lock=lock_out)
        result = self.run_check(client)
        self.assertEqual(result.code, 2)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("--out cannot be written", result.stderr)

    def test_an_out_inside_the_library_exits_2(self) -> None:
        """An --out inside the library is refused, as the draw refuses one."""
        self.out = self.library / "checked"
        client = gsf.FakeSearchClient([gsf.search_reply()])
        self.assert_refused(self.run_check(client), "inside the library")
        self.assertEqual(client.calls, [])


class SeamsTest(gcf.CheckCase):
    """The real seams are the real clock, sleep, environment and client."""

    def test_the_real_seams(self) -> None:
        """Each real seam is the one a real run uses."""
        seams = gs.real_seams()
        self.assertIs(seams.clock, gs.time.monotonic)
        self.assertIs(seams.sleeper, gs.time.sleep)
        self.assertIs(seams.environ, gs.os.environ)
        self.assertIs(seams.build_client, gs.build_search_client)
        self.assertIs(seams.read_tags, gs.read_tags)
        self.assertIsNotNone(seams.now().tzinfo)


def _files(root: Path) -> list[Path]:
    """Every file under *root*, sorted."""
    return sorted(p for p in root.rglob("*") if p.is_file())
