"""lyrics_tags: reading a track's tags and lyrics frames, and writing, stripping, restoring them.

Every write keeps the file's own ID3 version (v2.3 stays v2.3, v2.4 stays v2.4), refuses
an ID3v2.2 tag untouched, leaves every frame but the lyrics ones and any ID3v1 tail as they
were, and is confirmed by reading the file back. The confirmation compares the frames'
payload, never the encoding byte, because a v2.3 save re-encodes UTF-8 text as UTF-16;
the mismatch path is driven by a fault injected at the save seam.
Every track is a silent MP3 built at test time, and every lyric is invented.
"""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from unittest import mock

from mutagen import MutagenError
from mutagen.id3 import ID3, TRCK

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags as lt  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above

#: The two ID3 versions a save keeps, as `TrackTags.id3_version` reports them.
_V23, _V24 = (2, 3), (2, 4)
_WRITABLE = (_V23, _V24)
#: How far mutagen's duration may sit from the built length: about two MPEG frames.
_DURATION_TOLERANCE_S = 0.05
#: The year frames of v2.3 (TYER) and v2.4 (TDRC).
_DATE_IDS = ("TYER", "TDRC")

_SYNCED = lm.SyncedLyrics(
    (
        lm.SyncedLine(1000, "The keeper climbs at half past four"),
        lm.SyncedLine(5500, "and counts the ships along the shore,"),
        lm.SyncedLine(19000, ""),
        lm.SyncedLine(21000, "He writes them down in pencil gray, é"),
    )
)
_PLAIN = lm.PlainLyrics(fixtures.ORCHARD_PLAIN)


#: Two frames of each kind, differing in language, description, encoding and format.
_MIXED = (
    tb.uslt("a ferry hums below the hill", encoding=tb.LATIN1),
    tb.uslt("eine Fähre summt", lang="deu", desc="alt", encoding=tb.UTF16),
    tb.sylt((200, "first"), (100, "second, stored after"), encoding=tb.UTF8),
    tb.sylt((40, "counted"), lang="fra", desc="frames", time_format=tb.MPEG_FRAMES),
)


def _payloads(frames: Iterable[lm.FrameRecord]) -> Counter[tuple[object, ...]]:
    """*frames* as a multiset of everything but the encoding byte, ignoring order."""
    return Counter(
        (f.kind, f.lang, f.desc, f.format, f.type, f.payload) for f in frames
    )


def _v1_tail(path: Path) -> bytes:
    """*path*'s last ID3v1-sized stretch of bytes."""
    return path.read_bytes()[-tb.V1_TAG_BYTES :]


def _expected_synced(lyrics: lm.SyncedLyrics, lang: str) -> list[lm.FrameRecord]:
    """The two frames a synced write leaves: the timed SYLT and its plain USLT."""
    entries = tuple((line.ms, line.text) for line in lyrics.lines)
    return [tb.sylt(*entries, lang=lang), tb.uslt(lyrics.plain_text(), lang=lang)]


class TagsCase(fixtures.IsolatedCase):
    """Builds tracks under the test's temporary directory."""

    def track(
        self,
        version: tuple[int, int] | None = _V24,
        *,
        artist: str | None = "Invented Artist",
        title: str | None = "Invented Title",
        name: str = "track.mp3",
    ) -> Path:
        """A silent MP3 called *name*, tagged at *version* with *artist* and *title*."""
        return fixtures.build_silent_mp3(
            self.tmp / name, tb.SECONDS, artist, title, id3_version=version
        )

    def assert_payloads(self, path: Path, expected: Iterable[lm.FrameRecord]) -> None:
        """*path*'s lyrics frames read back hold exactly *expected*'s payloads."""
        self.assertEqual(_payloads(lt.read_tags(path).frames), _payloads(expected))


class ReadTagsTest(TagsCase):
    """The query tags, the duration, the version and every lyrics frame."""

    def test_a_tagged_track_reads_its_artist_title_duration_and_version(self) -> None:
        for version in _WRITABLE:
            with self.subTest(version=version):
                tags = lt.read_tags(self.track(version))
                self.assertEqual(tags.artist, "Invented Artist")
                self.assertEqual(tags.title, "Invented Title")
                self.assertEqual(tags.id3_version, version)
                self.assertEqual(tags.frames, ())
                assert tags.duration_s is not None
                self.assertAlmostEqual(
                    tags.duration_s, tb.SECONDS, delta=_DURATION_TOLERANCE_S
                )

    def test_a_missing_or_blank_tag_reads_as_none(self) -> None:
        tags = lt.read_tags(self.track(artist=None, title=" "))
        self.assertIsNone(tags.artist)
        self.assertIsNone(tags.title)

    def test_a_file_with_no_id3_header_has_no_version_tags_or_frames(self) -> None:
        tags = lt.read_tags(self.track(None))
        self.assertEqual((tags.artist, tags.title, tags.id3_version), (None,) * 3)
        self.assertEqual(tags.frames, ())
        self.assertIsNotNone(tags.duration_s)

    def test_an_id3v1_only_file_reads_its_artist_but_has_no_v2_version(self) -> None:
        tags = lt.read_tags(tb.build_v1_only_mp3(self.tmp / "v1.mp3", "Old Artist"))
        self.assertEqual(tags.artist, "Old Artist")
        self.assertIsNone(tags.id3_version)

    def test_an_id3v2_2_tag_reads_with_its_version(self) -> None:
        path = tb.build_v22_mp3(self.tmp / "v22.mp3", lyrics="a ferry hums")
        tags = lt.read_tags(path)
        self.assertEqual((tags.title, tags.id3_version), ("Invented Title", (2, 2)))
        self.assertEqual(tags.frames, (tb.uslt("a ferry hums", encoding=tb.LATIN1),))

    def test_audio_whose_length_cannot_be_read_has_no_duration(self) -> None:
        path = fixtures.build_silent_mp3(self.tmp / "empty.mp3", 0.0)
        self.assertIsNone(lt.read_tags(path).duration_s)

    def test_the_track_number_reads_as_written(self) -> None:
        path = self.track(_V24)
        tags = ID3(path)
        tags.add(TRCK(encoding=3, text=["3/12"]))
        tags.save(path)
        self.assertEqual(lt.read_track_number(path), "3/12")

    def test_no_track_number_or_no_tag_reads_as_none(self) -> None:
        self.assertIsNone(lt.read_track_number(self.track(_V24)))
        self.assertIsNone(lt.read_track_number(self.track(None)))

    def test_every_lyrics_frame_reads_verbatim_encoding_included(self) -> None:
        path = self.track(_V24)
        tb.plant_frames(path, _MIXED)
        self.assertEqual(Counter(lt.read_tags(path).frames), Counter(_MIXED))


class WriteLyricsTest(TagsCase):
    """Embedding keeps the version, replaces every frame of a kind, and confirms."""

    def test_a_synced_write_leaves_a_sylt_and_its_derived_uslt(self) -> None:
        for version in _WRITABLE:
            with self.subTest(version=version):
                path = self.track(version)
                tb.plant_frames(path, _MIXED)
                lt.write_lyrics(path, _SYNCED, "deu")
                after = lt.read_tags(path)
                self.assertEqual(after.id3_version, version)
                self.assert_payloads(path, _expected_synced(_SYNCED, "deu"))
                self.assertEqual(after.artist, "Invented Artist")

    def test_an_unsynced_write_leaves_one_uslt_and_no_sylt(self) -> None:
        for version in _WRITABLE:
            with self.subTest(version=version):
                path = self.track(version)
                tb.plant_frames(path, _MIXED)
                lt.write_lyrics(path, _PLAIN, "eng")
                self.assertEqual(lt.read_tags(path).id3_version, version)
                self.assert_payloads(path, [tb.uslt(_PLAIN.text)])

    def test_a_v2_3_save_re_encodes_utf8_and_still_confirms(self) -> None:
        path = self.track(_V23)
        lt.write_lyrics(path, _PLAIN, "eng")
        (frame,) = lt.read_tags(path).frames
        self.assertEqual((frame.encoding, frame.payload), (tb.UTF16, _PLAIN.text))

    def test_a_file_with_no_id3_header_is_written_as_v2_4(self) -> None:
        path = self.track(None)
        lt.write_lyrics(path, _PLAIN, "eng")
        self.assertEqual(lt.read_tags(path).id3_version, _V24)
        self.assert_payloads(path, [tb.uslt(_PLAIN.text)])

    def test_an_id3v1_only_file_gains_a_v2_4_tag(self) -> None:
        path = tb.build_v1_only_mp3(self.tmp / "v1.mp3", "Old Artist")
        tail = _v1_tail(path)
        lt.write_lyrics(path, _PLAIN, "eng")
        after = lt.read_tags(path)
        self.assertEqual((after.id3_version, after.artist), (_V24, "Old Artist"))
        self.assertEqual(_v1_tail(path), tail)

    def test_an_id3v2_2_tag_is_refused_and_left_untouched(self) -> None:
        path = tb.build_v22_mp3(self.tmp / "v22.mp3")
        before = path.read_bytes()
        with self.assertRaises(lm.TagVersionError):
            lt.write_lyrics(path, _PLAIN, "eng")
        self.assertEqual(path.read_bytes(), before)

    def test_the_writable_check_refuses_what_a_write_would(self) -> None:
        path = tb.build_v22_mp3(self.tmp / "v22.mp3")
        with self.assertRaisesRegex(lm.TagVersionError, "ID3v2.2"):
            lt.check_writable(path)
        v1_only = tb.build_v1_only_mp3(self.tmp / "v1.mp3", "Old Artist")
        bare, v23 = self.track(None, name="bare.mp3"), self.track(_V23, name="v23.mp3")
        for writable in (bare, v23, v1_only):
            with self.subTest(path=writable.name):
                before = (writable.read_bytes(), writable.stat().st_mtime_ns)
                lt.check_writable(writable)
                after = (writable.read_bytes(), writable.stat().st_mtime_ns)
                self.assertEqual(after, before)

    @tb.needs_binding_modes
    def test_the_writable_check_refuses_a_file_a_write_could_not_open(self) -> None:
        path = tb.make_read_only(self, self.track(_V24))
        before = path.read_bytes()
        # mutagen wraps the refused open; the check lets it through bare.
        with self.assertRaises(MutagenError) as wrapped:
            lt.write_lyrics(path, _PLAIN, "eng")
        self.assertIsInstance(wrapped.exception.__cause__, PermissionError)
        with self.assertRaises(PermissionError):
            lt.check_writable(path)
        self.assertEqual(path.read_bytes(), before)

    def test_a_save_that_did_not_take_is_a_read_back_error(self) -> None:
        path = self.track(_V24)
        with mock.patch.object(lt, "_save"), self.assertRaises(lm.ReadBackError):
            lt.write_lyrics(path, _SYNCED, "eng")


class StripLyricsTest(TagsCase):
    """Stripping removes every lyrics frame and nothing else."""

    def test_every_lyrics_frame_goes_and_the_version_stays(self) -> None:
        for version in _WRITABLE:
            with self.subTest(version=version):
                path = self.track(version)
                tb.plant_frames(path, _MIXED)
                lt.strip_lyrics(path)
                after = lt.read_tags(path)
                self.assertEqual((after.id3_version, after.frames), (version, ()))
                self.assertEqual(after.title, "Invented Title")

    def test_a_file_without_lyrics_frames_is_not_rewritten(self) -> None:
        # A v2.2 tag among them: with nothing to remove, it is not refused either.
        paths = (
            self.track(None, name="bare.mp3"),
            self.track(_V23, name="v23.mp3"),
            tb.build_v22_mp3(self.tmp / "v22.mp3"),
        )
        for path in paths:
            with self.subTest(path=path.name):
                before = path.read_bytes()
                lt.strip_lyrics(path)
                self.assertEqual(path.read_bytes(), before)

    def test_an_id3v2_2_tag_holding_lyrics_is_refused_untouched(self) -> None:
        path = tb.build_v22_mp3(self.tmp / "v22.mp3", lyrics="a ferry hums")
        before = path.read_bytes()
        with self.assertRaises(lm.TagVersionError):
            lt.strip_lyrics(path)
        self.assertEqual(path.read_bytes(), before)

    def test_a_strip_that_did_not_take_is_a_read_back_error(self) -> None:
        path = self.track(_V24)
        tb.plant_frames(path, _MIXED)
        with mock.patch.object(lt, "_save"), self.assertRaises(lm.ReadBackError):
            lt.strip_lyrics(path)


class RestoreFramesTest(TagsCase):
    """Restoring puts recorded frames back verbatim, at the recorded version."""

    def test_v2_4_restores_every_field_byte_for_byte(self) -> None:
        path = self.track(_V24)
        lt.write_lyrics(path, _SYNCED, "eng")
        lt.restore_frames(path, _MIXED, _V24)
        self.assertEqual(Counter(lt.read_tags(path).frames), Counter(_MIXED))

    def test_v2_3_restores_every_payload_and_stays_v2_3(self) -> None:
        path = self.track(_V23)
        lt.write_lyrics(path, _SYNCED, "eng")
        lt.restore_frames(path, _MIXED, _V23)
        self.assertEqual(lt.read_tags(path).id3_version, _V23)
        self.assert_payloads(path, _MIXED)

    def test_a_file_now_at_another_version_is_refused_untouched(self) -> None:
        # Converting the tag would rewrite or drop frames this tool does not own.
        for current, recorded in ((_V24, _V23), (_V23, _V24)):
            with self.subTest(current=current):
                path = self.track(current)
                before = path.read_bytes()
                with self.assertRaises(lm.TagVersionError):
                    lt.restore_frames(path, _MIXED[:1], recorded)
                self.assertEqual(path.read_bytes(), before)

    def test_an_id3v1_only_file_restored_at_v2_3_keeps_its_v1_fields(self) -> None:
        path = tb.build_v1_only_mp3(self.tmp / "v1.mp3", "Old Artist")
        tail = _v1_tail(path)
        lt.restore_frames(path, _MIXED[:1], _V23)
        after = lt.read_tags(path)
        self.assertEqual((after.id3_version, after.artist), (_V23, "Old Artist"))
        self.assertEqual(_v1_tail(path), tail)

    def test_an_id3v1_only_file_restored_at_v2_3_dates_it_the_v2_3_way(self) -> None:
        # The v1 year loads as a v2.4 TDRC; a v2.3 tag dates a track with TYER instead.
        path = tb.build_v1_only_mp3(self.tmp / "v1.mp3", "Old Artist")
        lt.restore_frames(path, _MIXED[:1], _V23)
        tags = ID3(path, translate=False, load_v1=False)
        dates = {
            key: [str(v) for v in tags[key].text] for key in _DATE_IDS if key in tags
        }
        self.assertEqual(dates, {"TYER": [tb.V1_YEAR]})

    def test_no_recorded_frames_leaves_none(self) -> None:
        path = self.track(_V24)
        lt.write_lyrics(path, _SYNCED, "eng")
        lt.restore_frames(path, (), _V24)
        self.assertEqual(lt.read_tags(path).frames, ())

    def test_a_file_that_lost_its_header_gets_one_at_the_recorded_version(self) -> None:
        path = self.track(None)
        lt.restore_frames(path, _MIXED, _V23)
        self.assertEqual(lt.read_tags(path).id3_version, _V23)
        self.assert_payloads(path, _MIXED)

    def test_an_unwritable_recorded_version_is_refused_untouched(self) -> None:
        path = self.track(_V24)
        before = path.read_bytes()
        for version in ((2, 2), (1, 1)):
            with self.subTest(version=version), self.assertRaises(lm.TagVersionError):
                lt.restore_frames(path, _MIXED, version)
        self.assertEqual(path.read_bytes(), before)

    def test_frames_one_tag_cannot_hold_together_are_a_read_back_error(self) -> None:
        # Two USLT frames with one language and description share mutagen's key, so
        # the second replaces the first and the file cannot hold what was asked.
        twins = (tb.uslt("one version"), tb.uslt("another version"))
        with self.assertRaises(lm.ReadBackError):
            lt.restore_frames(self.track(_V24), twins, _V24)

    def test_a_restore_that_did_not_take_is_a_read_back_error(self) -> None:
        path = self.track(_V24)
        with mock.patch.object(lt, "_save"), self.assertRaises(lm.ReadBackError):
            lt.restore_frames(path, _MIXED, _V24)


class OtherTagsTest(TagsCase):
    """Every write leaves the frames it does not own, and any ID3v1 tail, as they were."""

    def cycle(
        self, path: Path, version: tuple[int, int]
    ) -> list[tuple[list[str], bytes]]:
        """Write, strip and restore *path* at *version*: how each step left the rest."""
        snapshots = []
        for step in (
            lambda: lt.write_lyrics(path, _SYNCED, "eng"),
            lambda: lt.strip_lyrics(path),
            lambda: lt.restore_frames(path, _MIXED, version),
        ):
            step()
            snapshots.append((tb.other_frames(path), _v1_tail(path)))
        return snapshots

    def test_every_non_lyrics_frame_survives_write_strip_and_restore(self) -> None:
        for version in _WRITABLE:
            with self.subTest(version=version):
                path = tb.build_rich_mp3(self.tmp / "rich.mp3", version[1])
                before = (tb.other_frames(path), _v1_tail(path))
                self.assertEqual(self.cycle(path, version), [before] * 3)

    def test_a_v1_tail_beside_a_v2_tag_stays_byte_identical(self) -> None:
        for version in _WRITABLE:
            with self.subTest(version=version):
                path = tb.build_rich_mp3(self.tmp / "both.mp3", version[1])
                tail = tb.append_v1_tail(path)
                before = (tb.other_frames(path), tail)
                self.assertEqual(self.cycle(path, version), [before] * 3)

    def test_a_v2_3_tag_keeps_each_value_of_a_multi_valued_frame(self) -> None:
        # A v2.3 save joining values with a slash would merge the two composers into
        # one value; the value already holding a slash must stay one value too.
        path = tb.build_rich_mp3(self.tmp / "multi.mp3", _V23[1])
        lt.write_lyrics(path, _PLAIN, "eng")
        tags = ID3(path, translate=False, load_v1=False)
        self.assertEqual(
            (list(tags["TCOM"].text), list(tags["TPE2"].text)),
            (["Alpha Composer", "Beta Composer"], ["Gamma/Delta"]),
        )

    def test_a_v1_tail_that_did_not_go_back_is_a_read_back_error(self) -> None:
        path = tb.build_rich_mp3(self.tmp / "both.mp3", _V24[1])
        tail = tb.append_v1_tail(path)
        real_save = lt._save

        def save_losing_the_tail(tags: ID3, target: Path, minor: int) -> bytes:
            """Save, then cut the re-appended ID3v1 tail off, as a failed append would."""
            saved_tail = real_save(tags, target, minor)
            with target.open("r+b") as stream:
                stream.truncate(target.stat().st_size - len(tail))
            return saved_tail

        with (
            mock.patch.object(lt, "_save", side_effect=save_losing_the_tail),
            self.assertRaisesRegex(lm.ReadBackError, "ID3v1"),
        ):
            lt.write_lyrics(path, _PLAIN, "eng")

    def test_an_untranslated_v2_3_tag_still_reads_its_query_and_frames(self) -> None:
        path = tb.build_rich_mp3(self.tmp / "rich.mp3", _V23[1])
        tb.plant_frames(path, _MIXED)
        tags = lt.read_tags(path)
        self.assertEqual(
            (tags.artist, tags.title, tags.id3_version),
            ("Invented Artist", "Invented Title", _V23),
        )
        self.assertIsNotNone(tags.duration_s)
        self.assertEqual(_payloads(tags.frames), _payloads(_MIXED))


class SeamTest(TagsCase):
    """The save seam the faults are injected at is the one every write goes through."""

    def test_each_write_saves_exactly_once_through_the_seam(self) -> None:
        path = self.track(_V24)
        tb.plant_frames(path, _MIXED)
        writes = (
            lambda: lt.write_lyrics(path, _PLAIN, "eng"),
            lambda: lt.restore_frames(path, _MIXED, _V24),
            lambda: lt.strip_lyrics(path),
        )
        for write in writes:
            with mock.patch.object(lt, "_save", wraps=lt._save) as seam:
                write()
            self.assertEqual(seam.call_count, 1)
            self.assertIsInstance(seam.call_args.args[0], ID3)


if __name__ == "__main__":
    unittest.main()
