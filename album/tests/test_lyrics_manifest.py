"""The run manifest on disk: its JSON form, the atomic write, and the strict read.

A manifest is read back from a folder anything may have edited, so the reader is a
parser of untrusted input: every malformed shape is a `ManifestSchemaError`, never
another exception, and no path in it can point outside the album or the run folder.
The property tests at the bottom hold the parser to that over arbitrary bytes and over
valid manifests with one value replaced. Every lyric here is invented.
"""

from __future__ import annotations

import json
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages as ls  # noqa: E402 - import follows the sys.path bootstrap above
import manifest_builders as mb  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above

# Bound to their protocols, so the type checker holds both to the orchestrator's calls.
_WRITE: ls.WriteManifest = lmf.write_manifest
_READ: ls.ReadManifest = lmf.read_manifest

# A registered profile, never an @settings decorator: a fuzzing campaign
# that re-applies its own settings to each property is refused by Hypothesis on a
# decorated one.
settings.register_profile(
    "album_manifest_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("album_manifest_properties")


class ManifestCodecTest(fixtures.IsolatedCase):
    """A manifest reads back exactly as it was written."""

    def test_every_field_round_trips_through_the_run_folder(self) -> None:
        lmf.write_manifest(self.tmp, mb.FULL)
        self.assertEqual(lmf.read_manifest(self.tmp), mb.FULL)

    def test_the_file_is_json_with_album_relative_paths(self) -> None:
        lmf.write_manifest(self.tmp, mb.FULL)
        document = json.loads((self.tmp / lr.MANIFEST_NAME).read_bytes())
        self.assertEqual(document["schema"], lr.MANIFEST_SCHEMA)
        self.assertEqual(document["album"], ".")
        self.assertEqual(mb.first_track(document)["id3_version"], [2, 4])

    def test_a_rewrite_replaces_the_whole_file(self) -> None:
        lmf.write_manifest(self.tmp, mb.FULL)
        undone = lr.Manifest(
            mb.FULL.run_id, "eng", lr.RunState.UNDONE, lr.GeniusState.ABSENT, ()
        )
        lmf.write_manifest(self.tmp, undone)
        self.assertEqual(lmf.read_manifest(self.tmp), undone)
        self.assertEqual(
            sorted(p.name for p in self.tmp.iterdir()), ["home", "manifest.json"]
        )

    def test_a_source_no_step_asks_any_more_still_reads(self) -> None:
        # A run from before Musixmatch left the chain may have recorded it; undo must
        # still read that manifest.
        document = mb.document()
        cast(dict[str, object], mb.first_track(document)["after"])["source"] = (
            "Musixmatch"
        )
        after = lmf.parse_manifest(mb.encode(document)).tracks[0].after
        assert after is not None
        self.assertIs(after.source, lm.Source.MUSIXMATCH)

    def test_a_provider_is_stored_by_its_value_not_its_display_label(self) -> None:
        # The summary shows LRCLIB, while a manifest keeps the value runs have always
        # written, so every manifest, old or new, reads and undoes alike.
        lmf.write_manifest(self.tmp, mb.FULL)
        document = json.loads((self.tmp / lr.MANIFEST_NAME).read_bytes())
        after = cast(dict[str, object], mb.first_track(document)["after"])
        self.assertEqual(after["source"], "Lrclib")
        self.assertNotEqual(lm.Source.LRCLIB.label, "Lrclib")
        read_back = lmf.read_manifest(self.tmp).tracks[0].after
        assert read_back is not None
        self.assertIs(read_back.source, lm.Source.LRCLIB)

    def test_a_missing_manifest_is_an_os_error(self) -> None:
        with self.assertRaises(FileNotFoundError):
            lmf.read_manifest(self.tmp)


class ManifestRefusalTest(fixtures.IsolatedCase):
    """An unknown schema, or any malformed shape, is a ManifestSchemaError."""

    def assert_refused(self, data: bytes, words: str = "malformed") -> None:
        """Assert *data* is refused with *words* in the message."""
        with self.assertRaises(lm.ManifestSchemaError) as caught:
            lmf.parse_manifest(data)
        self.assertIn(words, str(caught.exception))

    def test_an_unknown_schema_is_refused_by_name(self) -> None:
        for schema in (2, 0, "1"):
            with self.subTest(schema=schema):
                self.assert_refused(
                    mb.encode({"schema": schema}), "unknown manifest schema"
                )

    def test_bytes_that_are_not_a_json_object_are_refused(self) -> None:
        for data in (b"\xff\xfe", b"{", b"[]", b"null", b"[" * 100_000):
            with self.subTest(data=data[:4]):
                self.assert_refused(data)

    def test_a_missing_or_mistyped_field_is_refused(self) -> None:
        edits: dict[str, object] = {
            "run_id": 7,
            "state": "halfway",
            "lang": None,
            "genius": True,
            "tracks": {},
            "album": "..",
        }
        for key, value in edits.items():
            with self.subTest(key=key):
                document = mb.document()
                document[key] = value
                self.assert_refused(mb.encode(document))
                del document[key]
                self.assert_refused(mb.encode(document))

    def test_a_boolean_is_not_a_number(self) -> None:
        document = mb.document()
        mb.first_track(document)["id3_version"] = [True, 4]
        self.assert_refused(mb.encode(document))

    def test_a_flag_is_true_or_false_only(self) -> None:
        for value in (1, "true", None):
            with self.subTest(value=value):
                document = mb.document()
                mb.first_track(document)["undone"] = value
                self.assert_refused(mb.encode(document))

    def test_a_version_must_be_a_pair(self) -> None:
        document = mb.document()
        mb.first_track(document)["id3_version"] = [2, 4, 0]
        self.assert_refused(mb.encode(document))

    def test_a_track_path_must_stay_inside_the_album(self) -> None:
        for path in (
            "/abs/01.mp3",
            "../01.mp3",
            "CD1/../../01.mp3",
            "",
            ".",
            "a//b.mp3",
            "a\x00.mp3",
        ):
            with self.subTest(path=path):
                document = mb.document()
                mb.first_track(document)["path"] = path
                self.assert_refused(mb.encode(document))

    def test_a_track_path_must_name_an_mp3(self) -> None:
        for path in ("01.flac", "CD1/01", "01.mp3/notes.txt", "CD1/.mp3"):
            with self.subTest(path=path):
                document = mb.document()
                mb.first_track(document)["path"] = path
                self.assert_refused(mb.encode(document))

    def test_an_upper_case_suffix_is_still_an_mp3(self) -> None:
        document = mb.document()
        mb.first_track(document)["path"] = "CD1/01 Café.MP3"
        self.assertEqual(
            lmf.parse_manifest(mb.encode(document)).tracks[0].path, "CD1/01 Café.MP3"
        )

    def test_a_track_listed_twice_is_refused(self) -> None:
        document = mb.document()
        tracks = cast(list[object], document["tracks"])
        tracks.append(tracks[0])
        self.assert_refused(mb.encode(document), "more than once")

    def test_frames_backed_up_without_an_id3_version_are_refused(self) -> None:
        document = mb.document()
        mb.first_track(document)["id3_version"] = None
        self.assert_refused(mb.encode(document), "no ID3 version")

    def test_a_backup_file_must_stay_inside_the_run_folder(self) -> None:
        document = mb.document()
        before = cast(
            dict[str, list[dict[str, object]]], mb.first_track(document)["before"]
        )
        before["frames"][1]["exact"] = "../../elsewhere.json"
        self.assert_refused(mb.encode(document))

    def test_a_sidecar_name_is_a_plain_file_name(self) -> None:
        for name in ("CD2/01.txt", "..", ""):
            with self.subTest(name=name):
                document = mb.document()
                before = cast(
                    dict[str, list[dict[str, object]]],
                    mb.first_track(document)["before"],
                )
                before["sidecars"][0]["name"] = name
                self.assert_refused(mb.encode(document))

    def test_a_renamed_sidecar_is_a_pair_of_names(self) -> None:
        document = mb.document()
        after = cast(dict[str, list[object]], mb.first_track(document)["after"])
        after["sidecars_renamed"] = [["a.lrc", "a.txt"]]
        self.assert_refused(mb.encode(document))


class SidecarLinkRecordTest(fixtures.IsolatedCase):
    """A sidecar backed up as a link records its target, and no copy of any bytes."""

    def assert_refused(self, document: object) -> None:
        """Assert *document* is refused as malformed."""
        with self.assertRaises(lm.ManifestSchemaError) as caught:
            lmf.parse_manifest(mb.encode(document))
        self.assertIn("malformed", str(caught.exception))

    def test_a_link_record_round_trips(self) -> None:
        link = lr.SidecarCopy("01.lrc", None, "../shared/01 \udcff.lrc")
        track = lr.ManifestTrack("01.mp3", (2, 4), lr.TrackBefore((), (link,)))
        manifest = lr.Manifest(
            "20261015T101500Z",
            "eng",
            lr.RunState.BACKED_UP,
            lr.GeniusState.ABSENT,
            (track,),
        )
        self.assertEqual(lmf.parse_manifest(lmf.manifest_bytes(manifest)), manifest)

    def test_a_record_without_a_link_field_is_a_file_copy(self) -> None:
        document = mb.document()
        mb.sidecar_record(document).pop("link", None)
        manifest = lmf.parse_manifest(mb.encode(document))
        before = manifest.tracks[0].before
        assert before is not None
        self.assertEqual(before.sidecars, mb.BEFORE.sidecars)

    def test_a_link_record_without_a_file_field_is_a_link(self) -> None:
        # Symmetric with a file copy that has no link field: the absent one is null.
        document = mb.document()
        record = mb.sidecar_record(document)
        record.pop("file")
        record["link"] = "elsewhere.txt"
        manifest = lmf.parse_manifest(mb.encode(document))
        before = manifest.tracks[0].before
        assert before is not None
        (copy,) = before.sidecars
        self.assertEqual((copy.file, copy.link), (None, "elsewhere.txt"))

    def test_a_record_with_neither_field_is_refused(self) -> None:
        document = mb.document()
        record = mb.sidecar_record(document)
        record.pop("file")
        record.pop("link", None)
        self.assert_refused(document)

    def test_a_record_is_a_file_copy_or_a_link_never_both(self) -> None:
        for file, link in (("sidecars/01.txt", "elsewhere.txt"), (None, None)):
            with self.subTest(file=file, link=link):
                document = mb.document()
                mb.sidecar_record(document).update(file=file, link=link)
                self.assert_refused(document)

    def test_a_record_built_as_both_or_neither_is_refused(self) -> None:
        for file, link in (("sidecars/01.txt", "elsewhere.txt"), (None, None)):
            with (
                self.subTest(file=file, link=link),
                self.assertRaisesRegex(ValueError, "not both, and not neither"),
            ):
                lr.SidecarCopy("01.txt", file, link)

    def test_a_link_target_is_text_with_no_nul(self) -> None:
        for link in ("", 3, "a\x00b"):
            with self.subTest(link=link):
                document = mb.document()
                mb.sidecar_record(document).update(file=None, link=link)
                self.assert_refused(document)


class StripRecordTest(fixtures.IsolatedCase):
    """The strip stage's error stage and outcome status round-trip through a manifest."""

    def test_strip_stage_and_status_round_trip(self) -> None:
        manifest = lr.Manifest(
            run_id="20261015T101500Z",
            lang="eng",
            state=lr.RunState.COMPLETE,
            genius=lr.GeniusState.ABSENT,
            tracks=(
                lr.ManifestTrack(
                    "01.mp3",
                    (2, 4),
                    lr.TrackBefore((), ()),
                    lr.TrackOutcome(
                        lr.OutcomeStatus.STRIPPED, sidecars_removed=("01.lrc",)
                    ),
                ),
                lr.ManifestTrack(
                    "02.mp3",
                    (2, 3),
                    lr.TrackBefore((), ()),
                    lr.TrackOutcome(
                        lr.OutcomeStatus.ERROR,
                        error=lr.StageError(
                            lr.ErrorStage.STRIP, "SlotNotAFileError", "a folder"
                        ),
                    ),
                ),
            ),
        )
        lmf.write_manifest(self.tmp, manifest)
        self.assertEqual(lmf.read_manifest(self.tmp), manifest)


class NoLyricsRecordTest(fixtures.IsolatedCase):
    """The no-lyrics-expected status and the genre FLAG round-trip through a manifest."""

    def test_status_and_genre_flag_round_trip(self) -> None:
        flag = lr.CheckReason(lr.CheckRule.NO_LYRICS_GENRE, "Stand-up Comedy")
        manifest = lr.Manifest(
            run_id="20261015T101500Z",
            lang="eng",
            state=lr.RunState.COMPLETE,
            genius=lr.GeniusState.ABSENT,
            tracks=(
                lr.ManifestTrack(
                    "01.mp3",
                    (2, 4),
                    lr.TrackBefore((), ()),
                    lr.TrackOutcome(lr.OutcomeStatus.NO_LYRICS_EXPECTED),
                ),
                lr.ManifestTrack(
                    "02.mp3",
                    (2, 4),
                    lr.TrackBefore((), ()),
                    lr.TrackOutcome(
                        lr.OutcomeStatus.KEPT_EXISTING,
                        check=lr.CheckResult((flag,), ()),
                    ),
                ),
            ),
        )
        lmf.write_manifest(self.tmp, manifest)
        self.assertEqual(lmf.read_manifest(self.tmp), manifest)


class FrameCodecTest(fixtures.IsolatedCase):
    """The exact copy of a frame reads back as that frame, every field included."""

    def test_frames_round_trip(self) -> None:
        frames = (
            tb.uslt("line one\r\nline two\n", encoding=tb.LATIN1),
            tb.sylt(
                (40, "\nframe-timed"), (10, "  spaced  "), time_format=tb.MPEG_FRAMES
            ),
            tb.sylt(lang="fra", desc="vide"),
            tb.uslt("lone \ud800 half", desc="odd"),
        )
        for frame in frames:
            with self.subTest(frame=frame):
                self.assertEqual(lmf.parse_frame(lmf.frame_bytes(frame)), frame)

    def test_a_malformed_frame_is_refused(self) -> None:
        good = json.loads(lmf.frame_bytes(tb.sylt((5, "a"))))
        edits: list[tuple[str, object]] = [
            ("format", None),
            ("payload", [{"text": "a", "time": -1}]),
            ("payload", [{"text": "a"}]),
            ("payload", [["a", 1]]),
            ("payload", 3),
            ("kind", "COMM"),
        ]
        for key, value in edits:
            with self.subTest(key=key, value=value):
                document = dict(good)
                document[key] = value
                with self.assertRaises(lm.ManifestSchemaError):
                    lmf.parse_frame(mb.encode(document))

    def test_a_uslt_payload_must_be_text(self) -> None:
        document = json.loads(lmf.frame_bytes(tb.uslt("x")))
        document["payload"] = [{"text": "x", "time": 1}]
        with self.assertRaises(lm.ManifestSchemaError):
            lmf.parse_frame(mb.encode(document))


# --- Properties --------------------------------------------------------------------

_JSON = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**40), max_value=2**40)
    | st.floats(allow_nan=False)
    | st.text(max_size=12),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=12,
)


def _leaf_paths(
    value: object, prefix: tuple[object, ...] = ()
) -> Iterator[tuple[object, ...]]:
    """Every path to a value inside *value*, itself included."""
    yield prefix
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _leaf_paths(child, (*prefix, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _leaf_paths(child, (*prefix, index))


def _replaced(document: object, path: tuple[object, ...], value: object) -> object:
    """*document* with the value at *path* replaced by *value*."""
    if not path:
        return value
    head, rest = path[0], path[1:]
    if isinstance(document, dict):
        copy = dict(document)
        copy[head] = _replaced(copy[head], rest, value)
        return copy
    items = list(cast(list[object], document))
    items[cast(int, head)] = _replaced(items[cast(int, head)], rest, value)
    return items


@st.composite
def _mutated_manifests(draw: st.DrawFn) -> bytes:
    """The full manifest with one value, anywhere in it, replaced by arbitrary JSON."""
    document = mb.document()
    path = draw(st.sampled_from(list(_leaf_paths(document))))
    return mb.encode(_replaced(document, path, draw(_JSON)))


class ManifestPropertyTest(fixtures.IsolatedCase):
    """The parsers never fail any other way than a ManifestSchemaError."""

    @given(
        st.one_of(st.binary(max_size=200), _JSON.map(mb.encode), _mutated_manifests())
    )
    def test_fuzz_parse_manifest(self, data: bytes) -> None:
        try:
            manifest = lmf.parse_manifest(data)
        except lm.ManifestSchemaError:
            return
        self.assertEqual(lmf.parse_manifest(lmf.manifest_bytes(manifest)), manifest)
        paths = [track.path for track in manifest.tracks]
        self.assertEqual(len(set(paths)), len(paths))
        for track in manifest.tracks:
            self.assertEqual(Path(track.path).suffix.lower(), ".mp3")
            if track.id3_version is None and track.before is not None:
                self.assertEqual(track.before.frames, ())

    @given(st.one_of(st.binary(max_size=100), _JSON.map(mb.encode)))
    def test_fuzz_parse_frame(self, data: bytes) -> None:
        try:
            frame = lmf.parse_frame(data)
        except lm.ManifestSchemaError:
            return
        self.assertEqual(lmf.parse_frame(lmf.frame_bytes(frame)), frame)


if __name__ == "__main__":
    unittest.main()
