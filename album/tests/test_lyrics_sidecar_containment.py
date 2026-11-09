"""The sidecar stage stays inside the album, and reads no sidecar past the lyrics cap.

A link under a sidecar name is read only when it resolves to a file inside the album:
one leading out, by an absolute target or by climbing with ``..``, holds no lyrics,
as a dangling link does, so the chain fetches and nothing from outside is embedded.
The route that matters most is the one an edited manifest opens: undo recreates a
backed-up link as recorded, so a link climbing out of the album can be planted by an
undo, and the next run must still not read through it.

A sidecar over `lyrics_records.MAX_LYRICS_CHARS` characters is refused, its bytes
measured first so a huge file is never read whole, before anything on disk changes.
Every lyric here is invented.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: Invented timed lyrics kept outside the album: they must never be embedded.
_OUTSIDE_LRC = "[00:01.00]A verse that lives outside the album\n"
#: Invented words with no time stamp, kept outside the album.
_OUTSIDE_TXT = "Words from a folder nobody pointed the run at\n"
#: The UTF-8 byte-order mark some editors write at the start of a file.
_BOM = b"\xef\xbb\xbf"
#: A character UTF-8 encodes in four bytes, the most any character takes.
_WIDE = "\U0001f3b5"


def _ignored(name: str) -> lm.SidecarState:
    """The state of a track whose one sidecar, the link *name*, leads out: ignored."""
    return lm.SidecarState(None, links_ignored=(name,))


class _ContainedCase(fixtures.IsolatedCase):
    """An album under the test folder, and a file beside the album, outside it."""

    def setUp(self) -> None:
        """An empty album folder and the outside file's folder."""
        super().setUp()
        self.album = self.tmp / "album"
        self.album.mkdir()

    def track(
        self,
        relpath: str = "01 Track.mp3",
        *,
        lrc: str | None = None,
        txt: str | None = None,
    ) -> Path:
        """Build one track at *relpath*, with the given sidecars' text."""
        one = TrackSpec(relpath, seconds=1.0, lrc=lrc, txt=txt)
        return fixtures.build_album(self.album, (one,))[0]

    def outside(self, name: str, text: str) -> Path:
        """A file holding *text* beside the album folder, outside it."""
        path = self.tmp / name
        path.write_text(text, encoding="utf-8", newline="")
        return path

    def normalize(self, mp3: Path, *, apply: bool) -> lm.SidecarState:
        """The real sidecar stage on *mp3*, for this test's album."""
        return lyrics_sidecar.normalize_sidecars(mp3, album=self.album, apply=apply)

    def assert_both_modes(self, mp3: Path, expected: lm.SidecarState) -> None:
        """Assert a dry run and an applied one both report *expected*."""
        self.assertEqual(self.normalize(mp3, apply=False), expected)
        self.assertEqual(self.normalize(mp3, apply=True), expected)


class OutsideLinkTest(_ContainedCase):
    """A sidecar link leading out of the album holds no lyrics, whatever it reaches."""

    def test_an_absolute_link_out_of_the_album_is_not_read(self) -> None:
        mp3 = self.track()
        target = self.outside("secret.lrc", _OUTSIDE_LRC)
        mp3.with_suffix(".lrc").symlink_to(target)
        self.assert_both_modes(mp3, _ignored("01 Track.lrc"))
        self.assertEqual(lyrics_files.read_link(mp3.with_suffix(".lrc")), str(target))

    def test_a_link_climbing_out_with_dot_dot_is_not_read(self) -> None:
        mp3 = self.track()
        self.outside("secret.txt", _OUTSIDE_TXT)
        mp3.with_suffix(".txt").symlink_to("../secret.txt")
        self.assert_both_modes(mp3, _ignored("01 Track.txt"))

    def test_an_untimed_link_out_is_not_read_so_not_renamed(self) -> None:
        # Deciding a rename needs the file's words; outside, they are never read.
        mp3 = self.track()
        self.outside("secret.lrc", _OUTSIDE_TXT)
        mp3.with_suffix(".lrc").symlink_to("../secret.lrc")
        self.assert_both_modes(mp3, _ignored("01 Track.lrc"))
        self.assertEqual(rb.sidecars(mp3), {"01 Track.lrc": rb.LinkTo("../secret.lrc")})

    def test_a_link_out_beside_a_timed_lrc_is_removed_as_a_dangling_one_is(
        self,
    ) -> None:
        mp3 = self.track(lrc=fixtures.LIGHTHOUSE_LRC)
        target = self.outside("secret.txt", _OUTSIDE_TXT)
        mp3.with_suffix(".txt").symlink_to(target)
        state = self.normalize(mp3, apply=True)
        self.assertIsInstance(state.lyrics, lm.SyncedLyrics)
        self.assertEqual(state.removed, ("01 Track.txt",))
        self.assertEqual(target.read_text(encoding="utf-8"), _OUTSIDE_TXT)

    def test_a_link_out_never_beats_a_txt_inside(self) -> None:
        mp3 = self.track(txt="Words the album keeps\n")
        mp3.with_suffix(".lrc").symlink_to(self.outside("secret.lrc", _OUTSIDE_LRC))
        state = self.normalize(mp3, apply=False)
        expected = lm.PlainLyrics("Words the album keeps\n")
        self.assertEqual(
            state, lm.SidecarState(expected, links_ignored=("01 Track.lrc",))
        )

    def test_an_inside_link_to_a_link_leading_out_is_not_read(self) -> None:
        mp3 = self.track()
        (self.album / "hop.lrc").symlink_to(self.outside("secret.lrc", _OUTSIDE_LRC))
        mp3.with_suffix(".lrc").symlink_to("hop.lrc")
        self.assert_both_modes(mp3, _ignored("01 Track.lrc"))


class InsideLinkTest(_ContainedCase):
    """A sidecar link resolving inside the album is read through, as it always was."""

    def test_a_link_to_a_file_elsewhere_in_the_album_is_read(self) -> None:
        mp3 = self.track("CD1/01.mp3")
        shared = self.album / "shared.lrc"
        shared.write_text(fixtures.LIGHTHOUSE_LRC, encoding="utf-8", newline="")
        mp3.with_suffix(".lrc").symlink_to("../shared.lrc")
        expected = lm.SidecarState(lm.parse_lrc(fixtures.LIGHTHOUSE_LRC))
        self.assert_both_modes(mp3, expected)
        self.assertTrue(mp3.with_suffix(".lrc").is_symlink())

    def test_an_album_reached_through_a_link_still_reads_its_own_links(self) -> None:
        mp3 = self.track()
        (self.album / "shared.txt").write_text(
            "An album verse\n", encoding="utf-8", newline=""
        )
        mp3.with_suffix(".txt").symlink_to("shared.txt")
        alias = self.tmp / "alias"
        alias.symlink_to(self.album)
        state = lyrics_sidecar.normalize_sidecars(
            alias / mp3.name, album=alias, apply=False
        )
        self.assertEqual(state, lm.SidecarState(lm.PlainLyrics("An album verse\n")))


class SidecarSizeTest(_ContainedCase):
    """A sidecar past the lyrics cap is refused before anything on disk changes."""

    def assert_refused(self, mp3: Path) -> None:
        """Assert *mp3*'s sidecars are refused as too large, in both modes, untouched."""
        before = rb.sidecars(mp3)
        for apply in (False, True):
            with self.subTest(apply=apply):
                with self.assertRaisesRegex(lm.LyricsError, "too large") as caught:
                    self.normalize(mp3, apply=apply)
                self.assertEqual(
                    type(caught.exception).__name__, "SidecarTooLargeError"
                )
        self.assertEqual(rb.sidecars(mp3), before)

    def test_a_txt_one_character_past_the_cap_is_refused(self) -> None:
        mp3 = self.track(txt="w" * (lr.MAX_LYRICS_CHARS + 1))
        self.assert_refused(mp3)

    def test_an_lrc_one_character_past_the_cap_is_refused(self) -> None:
        mp3 = self.track(lrc="[00:01.00]" + "w" * lr.MAX_LYRICS_CHARS)
        self.assert_refused(mp3)

    def test_bytes_no_text_under_the_cap_could_fill_are_refused_unread(self) -> None:
        # Not UTF-8 at all: only the byte count can refuse it as too large, so a
        # decode error here would mean the whole file was read first.
        mp3 = self.track()
        limit = 4 * lr.MAX_LYRICS_CHARS + len(_BOM)
        mp3.with_suffix(".txt").write_bytes(b"\xff" * (limit + 1))
        self.assert_refused(mp3)

    def test_the_cap_itself_in_four_byte_characters_and_a_mark_is_accepted(
        self,
    ) -> None:
        mp3 = self.track()
        text = _WIDE * lr.MAX_LYRICS_CHARS
        mp3.with_suffix(".txt").write_bytes(_BOM + text.encode("utf-8"))
        state = self.normalize(mp3, apply=False)
        self.assertEqual(state, lm.SidecarState(lm.PlainLyrics(text)))


class UndoPlantedLinkTest(cli_fakes.RealWiringCliCase):
    """An edited manifest's link out, recreated by undo, is never read by the next run."""

    def plant_link(self, name: str, link: str) -> None:
        """Record in the run's manifest that the first track had a sidecar link *link*."""
        manifest = lr.run_folder(self.album, run_fakes.RUN_ID) / lr.MANIFEST_NAME
        doc = json.loads(manifest.read_bytes())
        doc["tracks"][0]["before"]["sidecars"] = [
            {"name": name, "file": None, "link": link}
        ]
        manifest.write_text(json.dumps(doc), encoding="utf-8", newline="")

    def test_a_run_after_the_undo_fetches_and_embeds_nothing_from_outside(
        self,
    ) -> None:
        (mp3,) = fixtures.build_album(
            self.album, (TrackSpec("01 Keeper.mp3", title="The Keeper"),)
        )
        (self.tmp / "secret.lrc").write_text(_OUTSIDE_LRC, encoding="utf-8", newline="")
        self.assertEqual(self.run_album().code, album.EXIT_OK)
        self.plant_link("01 Keeper.lrc", "../secret.lrc")
        self.assertEqual(self.run_album("--mode", "undo").code, album.EXIT_OK)
        self.assertEqual(
            rb.sidecars(mp3), {"01 Keeper.lrc": rb.LinkTo("../secret.lrc")}
        )

        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        rerun = self.run_album()
        self.assertEqual(rerun.code, album.EXIT_OK)
        cells = cli_fakes.row_cells(rerun.stdout, "01 Keeper.mp3")
        self.assertEqual(cells[2:4], ["LRCLIB", "synced"])
        embedded = [frame.payload for frame in lyrics_tags.read_tags(mp3).frames]
        self.assertNotIn("outside the album", repr(embedded))
        self.assertIn("The keeper climbs", repr(embedded))
