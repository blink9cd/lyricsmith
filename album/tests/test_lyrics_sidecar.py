"""The sidecar stage: normalizing the sidecars beside an MP3, and writing one.

Every row of the sidecar table runs on a temporary album, both as a dry run (the disk
untouched, the outcome still reported) and applied. Two reading rules get their own
tests: a byte-order mark is accepted, and a file that is not UTF-8 is a typed error that
leaves the disk as it was, never a decode with replacement characters.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages as ls  # noqa: E402 - import follows the sys.path bootstrap above

# Bound to their protocols, so the type checker holds both to the orchestrator's calls.
_NORMALIZE: ls.NormalizeSidecars = lyrics_sidecar.normalize_sidecars
_WRITE: ls.WriteSidecar = lyrics_sidecar.write_sidecar

#: An invented lyric with no time stamp, as a sidecar mislabeled ``.lrc`` holds it.
_UNTIMED = "Paper boats along the gutter\nrace the rain to the river mouth\n"
#: An invented lyric in a ``.txt``, distinct from every other text here.
_PLAIN = "The tram bell rings the hour wrong\nand nobody minds at all\n"
#: Bytes that are not UTF-8: a Latin-1 e-acute in an otherwise plain line.
_NOT_UTF8 = b"caf\xe9 at the corner\n"
#: Invented synced lyrics, built directly so their lines are typed as synced.
_SYNCED = lm.SyncedLyrics(
    (
        lm.SyncedLine(1000, "Lanterns on the ferry rail"),
        lm.SyncedLine(4500, "count us home"),
    )
)
#: A timed ``.lrc`` whose every line is blank, as an instrumental marker is written.
_STAMPS_ONLY = "[00:01.00]\n[00:05.00]  \n"
#: The UTF-8 byte-order mark some editors write at the start of a file.
_BOM = b"\xef\xbb\xbf"


def _digest(text: str) -> str:
    """The SHA-256 of *text* as UTF-8, as lowercase hex: the oracle for every hash here."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _landed(text: str) -> tuple[tuple[str, str], ...]:
    """The hash a rename of the track's ``.lrc`` holding *text* records for its ``.txt``."""
    return (("01 Track.txt", _digest(text)),)


def _snapshot(album: Path) -> dict[str, bytes]:
    """Every file under *album*, by name, with its bytes."""
    return {path.name: path.read_bytes() for path in album.iterdir()}


class _AlbumCase(fixtures.IsolatedCase):
    """A one-track album per test; ``self.mp3`` is its track."""

    album: Path

    def _track(self, lrc: str | None = None, txt: str | None = None) -> Path:
        """Build the album's one track with the given sidecars; return the MP3."""
        self.album = self.tmp / "album"
        track = fixtures.TrackSpec("01 Track.mp3", seconds=1.0, lrc=lrc, txt=txt)
        return fixtures.build_album(self.album, (track,))[0]

    def _both_modes(
        self, mp3: Path, expected: lm.SidecarState
    ) -> tuple[dict[str, bytes], dict[str, bytes]]:
        """Normalize dry, then applied; assert both report *expected*.

        Returns the album's files before and after the applied run, and asserts the
        dry run changed nothing.
        """
        before = _snapshot(self.album)
        self.assertEqual(
            lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=False),
            expected,
        )
        self.assertEqual(_snapshot(self.album), before)
        self.assertEqual(
            lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=True),
            expected,
        )
        return before, _snapshot(self.album)


class NormalizeTableTest(_AlbumCase):
    """One test per row of the sidecar table."""

    def test_a_timed_lrc_is_used_as_synced_and_left_alone(self) -> None:
        mp3 = self._track(lrc=fixtures.LIGHTHOUSE_LRC)
        expected = lm.SidecarState(lm.parse_lrc(fixtures.LIGHTHOUSE_LRC))
        before, after = self._both_modes(mp3, expected)
        self.assertIsInstance(expected.lyrics, lm.SyncedLyrics)
        self.assertEqual(after, before)

    def test_an_untimed_lrc_is_renamed_to_txt_and_used_as_unsynced(self) -> None:
        mp3 = self._track(lrc=_UNTIMED)
        rename = lm.SidecarRename("01 Track.lrc", "01 Track.txt")
        expected = lm.SidecarState(
            lm.PlainLyrics(_UNTIMED), renamed=(rename,), hashes=_landed(_UNTIMED)
        )
        _, after = self._both_modes(mp3, expected)
        self.assertNotIn("01 Track.lrc", after)
        self.assertEqual(after["01 Track.txt"], _UNTIMED.encode())

    def test_an_untimed_lrc_replaces_an_existing_txt(self) -> None:
        # The rename comes first and the result is the .txt-only row, so the renamed
        # file wins over the old .txt.
        mp3 = self._track(lrc=_UNTIMED, txt=_PLAIN)
        rename = lm.SidecarRename("01 Track.lrc", "01 Track.txt")
        expected = lm.SidecarState(
            lm.PlainLyrics(_UNTIMED),
            renamed=(rename,),
            removed=("01 Track.txt",),
            hashes=_landed(_UNTIMED),
        )
        _, after = self._both_modes(mp3, expected)
        self.assertNotIn("01 Track.lrc", after)
        self.assertEqual(after["01 Track.txt"], _UNTIMED.encode())

    def test_a_txt_alone_is_used_as_unsynced(self) -> None:
        mp3 = self._track(txt=_PLAIN)
        before, after = self._both_modes(mp3, lm.SidecarState(lm.PlainLyrics(_PLAIN)))
        self.assertEqual(after, before)

    def test_a_timed_lrc_and_a_txt_keep_the_lrc(self) -> None:
        mp3 = self._track(lrc=fixtures.LIGHTHOUSE_LRC, txt=_PLAIN)
        expected = lm.SidecarState(
            lm.parse_lrc(fixtures.LIGHTHOUSE_LRC), removed=("01 Track.txt",)
        )
        _, after = self._both_modes(mp3, expected)
        self.assertNotIn("01 Track.txt", after)
        self.assertEqual(after["01 Track.lrc"], fixtures.LIGHTHOUSE_LRC.encode())

    def test_no_sidecar_means_no_lyrics(self) -> None:
        mp3 = self._track()
        before, after = self._both_modes(mp3, lm.SidecarState(None))
        self.assertEqual(after, before)


class NormalizeEdgeTest(_AlbumCase):
    """Blank survivors, and line endings."""

    def test_a_blank_txt_is_no_lyrics_and_stays(self) -> None:
        mp3 = self._track(txt=" \n\n")
        before, after = self._both_modes(mp3, lm.SidecarState(None))
        self.assertEqual(after, before)

    def test_a_blank_lrc_is_still_renamed_but_is_no_lyrics(self) -> None:
        mp3 = self._track(lrc="\n")
        rename = lm.SidecarRename("01 Track.lrc", "01 Track.txt")
        expected = lm.SidecarState(None, renamed=(rename,), hashes=_landed("\n"))
        _, after = self._both_modes(mp3, expected)
        self.assertNotIn("01 Track.lrc", after)
        self.assertEqual(after["01 Track.txt"], b"\n")

    def test_a_stamps_only_lrc_is_no_lyrics_and_stays(self) -> None:
        # Stamps with no words mark an instrumental: timed, so never renamed, but
        # nothing to embed, so the chain still fetches.
        mp3 = self._track(lrc=_STAMPS_ONLY)
        before, after = self._both_modes(mp3, lm.SidecarState(None))
        self.assertEqual(after, before)

    def test_a_stamps_only_lrc_gives_way_to_a_worded_txt(self) -> None:
        # A blank timed .lrc must never cost real lyrics: it goes, and the .txt is
        # then the .txt-only row.
        mp3 = self._track(lrc=_STAMPS_ONLY, txt=_PLAIN)
        expected = lm.SidecarState(lm.PlainLyrics(_PLAIN), removed=("01 Track.lrc",))
        _, after = self._both_modes(mp3, expected)
        self.assertEqual(set(after), {"01 Track.mp3", "01 Track.txt"})
        self.assertEqual(after["01 Track.txt"], _PLAIN.encode())

    def test_a_stamps_only_lrc_beside_a_blank_txt_stays(self) -> None:
        mp3 = self._track(lrc=_STAMPS_ONLY, txt=" \n\n")
        expected = lm.SidecarState(None, removed=("01 Track.txt",))
        _, after = self._both_modes(mp3, expected)
        self.assertEqual(set(after), {"01 Track.mp3", "01 Track.lrc"})
        self.assertEqual(after["01 Track.lrc"], _STAMPS_ONLY.encode())

    def test_a_timed_lrc_with_one_worded_line_is_kept(self) -> None:
        mp3 = self._track(lrc="[00:01.00]\n[00:02.50]Only the bridge\n")
        state = lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=False)
        self.assertIsInstance(state.lyrics, lm.SyncedLyrics)

    def test_windows_line_endings_read_as_newlines(self) -> None:
        mp3 = self._track(txt="one line\r\ntwo lines\r\n")
        state = lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=False)
        self.assertEqual(state.lyrics, lm.PlainLyrics("one line\ntwo lines\n"))


class EncodingTest(_AlbumCase):
    """A byte-order mark is accepted; bytes that are not UTF-8 are a typed error."""

    def test_a_bom_txt_reads_without_the_mark(self) -> None:
        mp3 = self._track()
        mp3.with_suffix(".txt").write_bytes(_BOM + _PLAIN.encode())
        state = lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=False)
        self.assertEqual(state.lyrics, lm.PlainLyrics(_PLAIN))

    def test_a_bom_lrc_is_still_timed(self) -> None:
        mp3 = self._track()
        mp3.with_suffix(".lrc").write_bytes(_BOM + fixtures.LIGHTHOUSE_LRC.encode())
        state = lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=False)
        self.assertEqual(state.lyrics, lm.parse_lrc(fixtures.LIGHTHOUSE_LRC))

    def _assert_refused(self, mp3: Path, name: str) -> None:
        """Both modes raise naming *name*, and neither changes the disk."""
        before = _snapshot(self.album)
        for apply in (False, True):
            with self.subTest(apply=apply):
                with self.assertRaisesRegex(lm.SidecarDecodeError, name):
                    lyrics_sidecar.normalize_sidecars(
                        mp3, album=self.album, apply=apply
                    )
                self.assertEqual(_snapshot(self.album), before)

    def test_an_undecodable_lrc_is_refused(self) -> None:
        mp3 = self._track(txt=_PLAIN)
        mp3.with_suffix(".lrc").write_bytes(_NOT_UTF8)
        self._assert_refused(mp3, "01 Track.lrc")

    def test_an_undecodable_txt_survivor_is_refused(self) -> None:
        mp3 = self._track()
        mp3.with_suffix(".txt").write_bytes(_NOT_UTF8)
        self._assert_refused(mp3, "01 Track.txt")

    def test_an_undecodable_txt_a_timed_lrc_removes_is_never_read(self) -> None:
        mp3 = self._track(lrc=fixtures.LIGHTHOUSE_LRC)
        mp3.with_suffix(".txt").write_bytes(_NOT_UTF8)
        state = lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=True)
        self.assertEqual(state.removed, ("01 Track.txt",))

    def test_an_undecodable_txt_beside_a_stamps_only_lrc_is_refused(self) -> None:
        # The blank .lrc needs the .txt read to decide which survives, so its bytes
        # are judged before anything is renamed or removed.
        mp3 = self._track(lrc=_STAMPS_ONLY)
        mp3.with_suffix(".txt").write_bytes(_NOT_UTF8)
        self._assert_refused(mp3, "01 Track.txt")

    def test_the_error_is_a_lyrics_error(self) -> None:
        self.assertTrue(issubclass(lm.SidecarDecodeError, lm.LyricsError))


class WriteSidecarTest(_AlbumCase):
    """Writing the accepted kind, and removing the other."""

    def test_synced_writes_the_lrc_and_removes_a_txt(self) -> None:
        mp3 = self._track(txt=_PLAIN)
        synced = _SYNCED
        result = lyrics_sidecar.write_sidecar(mp3, synced)
        text = lm.format_lrc(synced.lines)
        expected = lm.SidecarWrite("01 Track.lrc", _digest(text), ("01 Track.txt",))
        self.assertEqual(result, expected)
        files = _snapshot(self.album)
        self.assertNotIn("01 Track.txt", files)
        self.assertEqual(files["01 Track.lrc"], text.encode())

    def test_unsynced_writes_the_txt_and_removes_an_lrc(self) -> None:
        mp3 = self._track(lrc=fixtures.LIGHTHOUSE_LRC)
        result = lyrics_sidecar.write_sidecar(mp3, lm.PlainLyrics(_PLAIN))
        expected = lm.SidecarWrite("01 Track.txt", _digest(_PLAIN), ("01 Track.lrc",))
        self.assertEqual(result, expected)
        files = _snapshot(self.album)
        self.assertEqual(set(files), {"01 Track.mp3", "01 Track.txt"})
        self.assertEqual(files["01 Track.txt"], _PLAIN.encode())

    def test_an_existing_sidecar_of_the_same_kind_is_replaced(self) -> None:
        mp3 = self._track(txt=_UNTIMED)
        result = lyrics_sidecar.write_sidecar(mp3, lm.PlainLyrics(_PLAIN))
        self.assertEqual(result, lm.SidecarWrite("01 Track.txt", _digest(_PLAIN)))
        self.assertEqual(mp3.with_suffix(".txt").read_bytes(), _PLAIN.encode())

    def test_text_is_written_as_utf8_with_its_newlines_untranslated(self) -> None:
        mp3 = self._track()
        text = "café lights\non the quay\n"
        lyrics_sidecar.write_sidecar(mp3, lm.PlainLyrics(text))
        self.assertEqual(mp3.with_suffix(".txt").read_bytes(), text.encode("utf-8"))

    def test_the_written_sidecar_reads_back_through_normalize(self) -> None:
        mp3 = self._track()
        synced = _SYNCED
        lyrics_sidecar.write_sidecar(mp3, synced)
        state = lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=False)
        self.assertEqual(state, lm.SidecarState(synced))

    def test_a_failed_replace_leaves_no_temp_file_and_the_old_sidecar(self) -> None:
        mp3 = self._track(lrc=fixtures.LIGHTHOUSE_LRC, txt=_UNTIMED)
        before = _snapshot(self.album)
        with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                lyrics_sidecar.write_sidecar(mp3, lm.PlainLyrics(_PLAIN))
        self.assertEqual(_snapshot(self.album), before)

    def test_a_new_sidecar_gets_ordinary_permissions(self) -> None:
        # A temporary file made private (mode 0600) would carry that mode over the
        # replace; the sidecar should be as readable as any file the user creates.
        mp3 = self._track()
        lyrics_sidecar.write_sidecar(mp3, lm.PlainLyrics(_PLAIN))
        probe = self.album / "probe"
        probe.write_bytes(b"x")
        mode = mp3.with_suffix(".txt").stat().st_mode & 0o777
        self.assertEqual(mode, probe.stat().st_mode & 0o777)


class SidecarKeepsTest(fixtures.IsolatedCase):
    """Whether lyrics written as their sidecar read back with the same words."""

    @staticmethod
    def synced(*lines: tuple[int, str]) -> lm.SyncedLyrics:
        """Synced lyrics of *lines*, given as ``(ms, text)`` in stored order."""
        return lm.SyncedLyrics(tuple(lm.SyncedLine(ms, text) for ms, text in lines))

    def test_untimed_lyrics_always_keep(self) -> None:
        self.assertTrue(lyrics_sidecar.sidecar_keeps(lm.PlainLyrics("A\n\nB [x]\n")))

    def test_single_line_entries_keep_whatever_their_order_and_padding(self) -> None:
        lyrics = self.synced(
            (4000, "and holds "), (1000, "A lamp swings\n"), (2000, "")
        )
        self.assertTrue(lyrics_sidecar.sidecar_keeps(lyrics))

    def test_an_entry_opening_with_a_line_break_keeps(self) -> None:
        # Many taggers open each SYLT entry with one; the .lrc holds the text stripped.
        self.assertTrue(
            lyrics_sidecar.sidecar_keeps(self.synced((1000, "\nA lamp swings")))
        )

    def test_an_entry_holding_a_line_break_does_not_keep(self) -> None:
        for text in ("A lamp swings\non the landing", "\nA lamp\nswings"):
            with self.subTest(text=text):
                lyrics = self.synced((1000, text))
                self.assertFalse(lyrics_sidecar.sidecar_keeps(lyrics))

    def test_an_entry_opening_with_a_stamp_does_not_keep(self) -> None:
        lyrics = self.synced((1000, "[00:05.00]A lamp swings"))
        self.assertFalse(lyrics_sidecar.sidecar_keeps(lyrics))


class SidecarHashTest(_AlbumCase):
    """A sidecar the run leaves is hashed to the bytes it wrote or renamed into place."""

    def test_a_written_sidecar_is_hashed_from_the_bytes_written(self) -> None:
        mp3 = self._track()
        result = lyrics_sidecar.write_sidecar(mp3, lm.PlainLyrics(_PLAIN))
        assert result is not None
        on_disk = mp3.with_suffix(".txt").read_bytes()
        self.assertEqual(result.sha256, hashlib.sha256(on_disk).hexdigest())

    def test_a_renamed_sidecar_is_hashed_from_the_bytes_read(self) -> None:
        mp3 = self._track()
        raw = _BOM + _UNTIMED.encode()
        mp3.with_suffix(".lrc").write_bytes(raw)
        state = lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=True)
        # The mark is part of the file the rename lands, so it is part of the hash.
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual(state.hashes, (("01 Track.txt", digest),))
        self.assertEqual(mp3.with_suffix(".txt").read_bytes(), raw)

    def test_the_digest_is_lowercase_sha256_hex(self) -> None:
        digest = lyrics_sidecar.sidecar_digest(_PLAIN.encode())
        self.assertEqual(digest, _digest(_PLAIN))
        self.assertEqual(len(digest), 64)


class LinkedSidecarTest(_AlbumCase):
    """A link under a sidecar name: read through to a file in the album, only acted on itself.

    A link that leads to no file holds no lyrics. Removing or replacing a sidecar
    removes or replaces the link, never the file it points at.
    """

    def _link(self, mp3: Path, suffix: str, target: Path | str) -> Path:
        """A link named after *mp3* with *suffix*, pointing at *target*."""
        slot = mp3.with_suffix(suffix)
        slot.symlink_to(target)
        return slot

    def _shared(self, text: str) -> Path:
        """A file elsewhere in the album holding *text*: a link out is never read."""
        shared = self.album / "shared"
        shared.write_text(text, encoding="utf-8", newline="")
        return shared

    def test_a_link_to_a_timed_lrc_is_read_through_and_kept(self) -> None:
        mp3 = self._track()
        slot = self._link(mp3, ".lrc", self._shared(fixtures.LIGHTHOUSE_LRC))
        state = lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=True)
        self.assertIsInstance(state.lyrics, lm.SyncedLyrics)
        self.assertTrue(slot.is_symlink())

    def test_a_dangling_txt_beside_a_timed_lrc_is_removed(self) -> None:
        mp3 = self._track(lrc=fixtures.LIGHTHOUSE_LRC)
        slot = self._link(mp3, ".txt", self.tmp / "gone.txt")
        state = lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=True)
        self.assertEqual(state.removed, ("01 Track.txt",))
        self.assertFalse(os.path.lexists(slot))

    def test_a_dangling_lrc_holds_no_lyrics_and_stays(self) -> None:
        mp3 = self._track()
        slot = self._link(mp3, ".lrc", "gone.lrc")
        self.assertEqual(
            lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=True),
            lm.SidecarState(None),
        )
        self.assertEqual(os.readlink(slot), "gone.lrc")

    def test_a_link_to_a_folder_holds_no_lyrics(self) -> None:
        mp3 = self._track(txt=_PLAIN)
        # Inside the album: this is about a folder, not about leading out of it.
        folder = self.album / "folder"
        folder.mkdir()
        self._link(mp3, ".lrc", folder)
        state = lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=False)
        self.assertEqual(state, lm.SidecarState(lm.PlainLyrics(_PLAIN)))

    def test_a_write_replaces_the_link_and_never_its_target(self) -> None:
        mp3 = self._track()
        shared = self._shared(_UNTIMED)
        slot = self._link(mp3, ".txt", shared)
        lyrics_sidecar.write_sidecar(mp3, lm.PlainLyrics(_PLAIN))
        self.assertFalse(slot.is_symlink())
        self.assertEqual(slot.read_text(encoding="utf-8"), _PLAIN)
        self.assertEqual(shared.read_text(encoding="utf-8"), _UNTIMED)

    def test_a_write_removes_a_dangling_link_of_the_other_kind(self) -> None:
        mp3 = self._track()
        slot = self._link(mp3, ".lrc", "gone.lrc")
        result = lyrics_sidecar.write_sidecar(mp3, lm.PlainLyrics(_PLAIN))
        assert result is not None
        self.assertEqual(result.removed, ("01 Track.lrc",))
        self.assertFalse(os.path.lexists(slot))

    def test_a_folder_under_the_other_name_is_left_alone(self) -> None:
        mp3 = self._track()
        mp3.with_suffix(".lrc").mkdir()
        result = lyrics_sidecar.write_sidecar(mp3, lm.PlainLyrics(_PLAIN))
        assert result is not None
        self.assertEqual(result.removed, ())
        self.assertTrue(mp3.with_suffix(".lrc").is_dir())
