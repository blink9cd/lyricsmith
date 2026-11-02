"""The sweep's state files: read with a cap and never through a FIFO, and kept private.

The resume list, the ledger and an existing ``--changed-list`` are files a person may
swap for something else, so each is read with a size cap and refused at once when it is
a FIFO pipe or a device; the resume list and the ledger are refused as a link too,
while the ``--changed-list`` the operator named is followed. What the sweep writes
under the state folder (the ledger, its lock, the silence list and its page) is 0o600,
each state folder it makes 0o700; a folder that already exists keeps its mode, and the
``--changed-list`` keeps the default. A read that could block runs under album's
`fifo_fakes.within_deadline`. Every album here is invented.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]
# album's test helpers, last, so none of them shadows a module of this tool's own.
sys.path.append(str(_HERE.parents[1] / "album" / "tests"))

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import sweep_changed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_lock  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence_list as sil  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence_page as ssp  # noqa: E402 - import follows the sys.path bootstrap above
from fifo_fakes import make_pipe, within_deadline  # noqa: E402 - album's test helpers

_UMASK = 0o022
_OPEN_FILE, _OPEN_FOLDER = 0o644, 0o755
_PRIVATE_FILE, _PRIVATE_FOLDER = 0o600, 0o700
_TIME = "2026-10-25T09:08:07+00:00"
_ENTRY = sl.LedgerEntry("Artist/Album", None, 0, sl.Outcome.DONE, _TIME)
#: A small cap a test stands in, so an over-cap file stays small on disk.
_SMALL_CAP = 64
#: Windows' own value of ``os.O_BINARY``, faked here where the flag does not exist.
_FAKE_BINARY = 0x8000
#: The tool's own wording for a link it refuses, as accept's judgment phrases it.
_LINK_WORDING = "is a symbolic link"


def mode_of(path: Path) -> int:
    """*path*'s permission bits."""
    return stat.S_IMODE(path.stat().st_mode)


def a_list(*albums: str) -> bytes:
    """A silence list marking each of *albums* done, and nothing else."""
    blocks = "".join(sil.marker_line(album) for album in albums)
    return (sil.header_line() + blocks).encode()


@contextmanager
def recorded_open_flags() -> Iterator[list[int]]:
    """Fake ``os.O_BINARY`` at Windows' value; yield the flags each `os.open` gets.

    The recording open strips the fake bit again, because on this host it may mean
    something else (0x8000 is O_EVTONLY on macOS), and puts back the host's own flag,
    so the real open behaves as the code under test meant it to on any platform.
    """
    host_binary = getattr(os, "O_BINARY", 0)
    seen: list[int] = []
    real_open = os.open

    def recording_open(path: Path, flags: int, mode: int = 0o777) -> int:
        seen.append(flags)
        return real_open(path, (flags & ~_FAKE_BINARY) | host_binary, mode)

    with (
        mock.patch.object(os, "O_BINARY", _FAKE_BINARY, create=True),
        mock.patch.object(os, "open", recording_open),
    ):
        yield seen


class StateCase(sf.IsolatedCase):
    """Each test under `_UMASK`, the process's own restored after it."""

    def setUp(self) -> None:
        """Set the umask, and put the old one back when the test ends."""
        super().setUp()
        self.addCleanup(os.umask, os.umask(_UMASK))

    def linked_to(self, data: bytes, name: str) -> tuple[Path, Path]:
        """A link named *name* in the test's folder, and the real file holding *data*."""
        real = self.tmp / f"real-{name}"
        real.write_bytes(data)
        link = self.tmp / name
        link.symlink_to(real)
        return link, real


class ResumeListReadTest(StateCase):
    """The resume list: a FIFO, a link or an oversized file is refused at once."""

    def test_a_fifo_is_refused_without_waiting(self) -> None:
        pipe = self.tmp / "silent.tsv"
        make_pipe(pipe)
        with self.assertRaises(sil.SilenceListError) as caught:
            within_deadline(self, pipe, lambda: sil.read_resume(pipe))
        self.assertIn("not a regular file", str(caught.exception))

    def test_a_link_is_refused_even_to_a_good_list(self) -> None:
        link, _ = self.linked_to(a_list("Artist/Album"), "silent.tsv")
        for read in (sil.read_resume, sil.read_rows):
            with self.subTest(read=read.__name__):
                with self.assertRaises(sil.SilenceListError) as caught:
                    read(link)
                self.assertIn(str(link), str(caught.exception))
                self.assertIn(f"{link.name} {_LINK_WORDING}", str(caught.exception))
                self.assertNotIn(os.strerror(errno.ELOOP), str(caught.exception))

    def test_a_loop_above_the_list_keeps_the_os_wording(self) -> None:
        loop = self.tmp / "loop"
        loop.symlink_to(loop)
        path = loop / "silent.tsv"
        with self.assertRaises(sil.SilenceListError) as caught:
            sil.read_resume(path)
        self.assertNotIn(_LINK_WORDING, str(caught.exception))

    def test_a_list_past_the_cap_is_refused(self) -> None:
        path = self.tmp / "silent.tsv"
        path.write_bytes(a_list(*(f"Artist/Album {n}" for n in range(10))))
        with mock.patch.object(sil, "MAX_LIST_BYTES", _SMALL_CAP):
            for read in (sil.read_resume, sil.read_rows):
                with self.subTest(read=read.__name__):
                    with self.assertRaises(sil.SilenceListError) as caught:
                        read(path)
                    self.assertIn(
                        f"more than {_SMALL_CAP} bytes", str(caught.exception)
                    )

    def test_a_list_at_the_cap_is_read(self) -> None:
        path = self.tmp / "silent.tsv"
        data = a_list("Artist/Album")
        path.write_bytes(data)
        with mock.patch.object(sil, "MAX_LIST_BYTES", len(data)):
            self.assertEqual(sil.read_resume(path).done, frozenset({"Artist/Album"}))

    def test_a_resume_never_cuts_through_a_link(self) -> None:
        data = a_list("Artist/Album") + b"an unfinished album's line"
        link, real = self.linked_to(data, "silent.tsv")
        point = sil.parse_resume(data)
        with self.assertRaises(OSError):
            sweep_silence.open_list(link, point)
        self.assertEqual(real.read_bytes(), data)

    def test_a_resume_refuses_a_fifo_without_waiting(self) -> None:
        pipe = self.tmp / "silent.tsv"
        make_pipe(pipe)
        point = sil.parse_resume(a_list("Artist/Album"))
        with self.assertRaises(OSError):
            within_deadline(self, pipe, lambda: sweep_silence.open_list(pipe, point))

    def test_a_resume_refuses_a_fifo_with_a_reader_by_its_descriptor(self) -> None:
        pipe = self.tmp / "silent.tsv"
        make_pipe(pipe)
        # A reader on the other end lets the open for writing succeed at once, so
        # the refusal is the opened descriptor's own check.
        reader = os.open(pipe, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        self.addCleanup(os.close, reader)
        point = sil.parse_resume(a_list("Artist/Album"))
        with self.assertRaises(OSError) as caught:
            within_deadline(self, pipe, lambda: sweep_silence.open_list(pipe, point))
        self.assertIn("not a regular file", str(caught.exception))


class ListBinaryOpenTest(StateCase):
    """Each open of a silence list is binary, so Windows never writes an LF as CRLF."""

    def test_a_new_list_opens_in_binary_mode(self) -> None:
        path = sil.list_path(os.environ, sf.fixed_now())
        with recorded_open_flags() as seen:
            sweep_silence.open_list(path, None).close()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0] & _FAKE_BINARY)

    def test_a_resumed_list_opens_in_binary_mode(self) -> None:
        path = self.tmp / "silent.tsv"
        data = a_list("Artist/Album")
        path.write_bytes(data)
        point = sil.parse_resume(data)
        with recorded_open_flags() as seen:
            sweep_silence.open_list(path, point).close()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0] & _FAKE_BINARY)


class LedgerReadTest(StateCase):
    """The ledger: a FIFO, a link or an oversized file is the ledger's own error."""

    def setUp(self) -> None:
        """The library's ledger path, its folder made."""
        super().setUp()
        self.ledger = sl.ledger_path(self.library, os.environ)
        self.ledger.parent.mkdir(parents=True)

    def test_a_fifo_is_refused_without_waiting(self) -> None:
        make_pipe(self.ledger)
        for name, work in (
            ("read", lambda: sl.read_ledger(self.ledger)),
            ("append", lambda: sl.append_record(self.ledger, _ENTRY)),
            ("append unless last", lambda: sl.append_unless_last(self.ledger, _ENTRY)),
        ):
            with self.subTest(name=name):
                with self.assertRaises(OSError) as caught:
                    within_deadline(self, self.ledger, work)
                self.assertIn("not a regular file", str(caught.exception))

    def test_a_link_is_refused_and_its_target_never_written(self) -> None:
        data = (sl.encode_record(_ENTRY) + "\n").encode()
        real = self.tmp / "elsewhere.jsonl"
        real.write_bytes(data)
        self.ledger.symlink_to(real)
        for name, work in (
            ("read", lambda: sl.read_ledger(self.ledger)),
            ("append", lambda: sl.append_record(self.ledger, _ENTRY)),
        ):
            with self.subTest(name=name):
                with self.assertRaises(OSError) as caught:
                    work()
                self.assertIn(
                    f"{self.ledger.name} {_LINK_WORDING}", str(caught.exception)
                )
        self.assertEqual(real.read_bytes(), data)
        self.assertTrue(self.ledger.is_symlink())

    def test_a_ledger_past_the_cap_is_refused(self) -> None:
        self.ledger.write_bytes(b"\n" * (_SMALL_CAP + 1))
        with mock.patch.object(sl, "MAX_LEDGER_BYTES", _SMALL_CAP):
            with self.assertRaises(OSError) as caught:
                sl.read_ledger(self.ledger)
        self.assertIn(f"more than {_SMALL_CAP} bytes", str(caught.exception))

    def test_a_missing_ledger_is_still_an_empty_one(self) -> None:
        self.assertEqual(sl.read_ledger(self.ledger), sl.LedgerRead((), ()))


class ChangedListReadTest(StateCase):
    """An existing --changed-list: capped, a FIFO refused, a link the operator named followed."""

    def test_a_fifo_is_refused_without_waiting(self) -> None:
        pipe = self.tmp / "changed.txt"
        make_pipe(pipe)
        with self.assertRaises(sweep_changed.ExistingListError) as caught:
            within_deadline(self, pipe, lambda: sweep_changed.existing_paths(pipe))
        self.assertIn("not a regular file", str(caught.exception))

    def test_a_link_the_operator_named_is_followed(self) -> None:
        track = str(self.library / "Artist" / "Album" / "01.mp3")
        link, _ = self.linked_to(f"{track}\n".encode(), "changed.txt")
        self.assertEqual(sweep_changed.existing_paths(link), (track,))

    def test_a_list_past_the_cap_is_refused(self) -> None:
        track = str(self.library / "Artist" / "Album" / "01.mp3")
        path = self.tmp / "changed.txt"
        path.write_bytes(f"{track}\n".encode() * 10)
        with mock.patch.object(sweep_changed, "MAX_CHANGED_LIST_BYTES", _SMALL_CAP):
            with self.assertRaises(sweep_changed.ExistingListError) as caught:
                sweep_changed.existing_paths(path)
        self.assertIn(f"more than {_SMALL_CAP} bytes", str(caught.exception))


class StateModeTest(StateCase):
    """What the sweep writes under the state folder is private; nothing else changes."""

    def assert_private(self, path: Path, folders: int) -> None:
        """*path* is 0o600, and so are the *folders* folders above it 0o700."""
        self.assertEqual(mode_of(path), _PRIVATE_FILE)
        for folder in list(path.parents)[:folders]:
            with self.subTest(folder=folder.name):
                self.assertEqual(mode_of(folder), _PRIVATE_FOLDER)

    def test_the_ledger_and_its_lock_are_private(self) -> None:
        ledger = sl.ledger_path(self.library, os.environ)
        with sweep_lock.held(sweep_lock.lock_path(ledger)):
            sl.append_record(ledger, _ENTRY)
        self.assert_private(ledger, 2)
        self.assert_private(sweep_lock.lock_path(ledger), 2)

    def test_an_existing_state_folder_keeps_its_mode(self) -> None:
        existing = self.state / "lyricsmith"
        existing.mkdir(parents=True)
        existing.chmod(_OPEN_FOLDER)
        ledger = sl.ledger_path(self.library, os.environ)
        sl.append_record(ledger, _ENTRY)
        self.assertEqual(mode_of(existing), _OPEN_FOLDER)
        self.assert_private(ledger, 1)

    def test_a_new_silence_list_and_its_page_are_private(self) -> None:
        path = sil.list_path(os.environ, sf.fixed_now())
        with sweep_silence.open_list(path, None) as out:
            out.write(sil.marker_line("Artist/Album"))
        self.assert_private(path, 2)
        page = ssp.write_page(path, sf.fixed_now())
        self.assert_private(page, 0)

    def test_the_changed_list_keeps_the_default_mode(self) -> None:
        target = self.tmp / "changed.txt"
        with sweep_changed.changed_union(target, lambda _line: None):
            pass
        self.assertEqual(mode_of(target), _OPEN_FILE)
