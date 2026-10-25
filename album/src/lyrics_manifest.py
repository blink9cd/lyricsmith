"""The run folder's JSON files: the manifest and a frame's exact copy, and their codec.

`write_manifest` replaces the manifest through `lyrics_files.write_confirmed`, atomically
and read back before it returns, so a reader sees the old manifest or the new one and a
write that did not land is an error.

`parse_manifest` and `parse_frame` read files a person or another tool may have edited,
so both are parsers of untrusted input. Every field is type-checked, a boolean is never
taken for a number, and any malformed shape raises `ManifestSchemaError`, never
another exception. A manifest path cannot leave its root: a track path and a displaced
copy's path are relative to the album, a backup file relative to the run folder, none
absolute nor climbing with ``..``, and a sidecar name is a plain file name. So an
edited manifest cannot make undo write outside the album; undo itself refuses the other
way out, a link put in the album since. A link put in the run folder since is refused
on read (`lyrics_files.inside_run_folder`), the manifest's own name included, and a
manifest past `MAX_MANIFEST_BYTES` is refused before it is read whole. A sidecar backed up as a link records its
target instead of a copy, kept as the link stored it: undo recreates the link and writes
nothing through it. A sidecar hash is a SHA-256 in lowercase hex. A track path names an
MP3 and appears once, and a track with no ID3 version has no backed-up frame, since a
file with no ID3v2 tag holds none.

JSON is written with ASCII escapes, so any text a frame holds, a lone surrogate
included, survives the round trip. Nothing here prints; the caller holds the album lock.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import lyrics_files
from lyrics_model import (
    FrameKind,
    FrameRecord,
    LyricsKind,
    ManifestSchemaError,
    QueryMarker,
    SidecarRename,
    Source,
    SyltEntry,
)
from lyrics_records import (
    MANIFEST_NAME,
    MANIFEST_SCHEMA,
    CheckReason,
    CheckResult,
    CheckRule,
    ErrorStage,
    FrameExport,
    GeniusState,
    Manifest,
    ManifestTrack,
    OutcomeStatus,
    ProviderRefusal,
    RunState,
    SidecarCopy,
    SidecarHash,
    StageError,
    TrackBefore,
    TrackOutcome,
    is_mp3_name,
)
from lyrics_manifest_fields import (
    Fields,
    file_name,
    malformed,
    member,
    number_value,
    real_value,
    relative_path,
    text_value,
)
from lyrics_provider_states import BandcampState, CoolingState, SilenceCheck
from lyrics_stages import ReadManifest, WriteManifest

#: The largest manifest read: a real one runs to kilobytes, so anything past this is
#: no manifest this code wrote, and is refused unread rather than parsed whole.
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
#: The JSON indent: the manifest is meant to be read by a person too.
_INDENT = 2
#: The only value a manifest's ``album`` holds: every path in it is album-relative.
_ALBUM = "."
#: The length of a stored ID3 version, ``[2, minor]``.
_PAIR = 2
#: The ``after`` fields older manifests lack: the sidecar links ignored, the provider
#: calls that refused, what a refetch of untimed or implausible lyrics records, and a
#: silence check's.
_LINKS_IGNORED, _IMPLAUSIBLE = "sidecar_links_ignored", "implausible"
_REFUSALS, _REFETCH, _UNREADABLE = "refusals", "refetch_untimed", "sidecar_unreadable"
_PEAK, _SILENCE_FAILED = "peak_dbfs", "silence_failed"
_REFETCH_IMPLAUSIBLE = "refetch_implausible"
#: A SHA-256 digest as lowercase hex.
_SHA256 = re.compile(r"[0-9a-f]{64}")


# --- Encoding ------------------------------------------------------------------------


def _json_bytes(record: Manifest | FrameRecord) -> bytes:
    """Dataclass *record* as indented, ASCII-escaped JSON, newline-terminated.

    Every field goes in under its own name: a nested record becomes an object, a
    tuple a list, and an enum member its value, since each is a string.
    """
    document = dataclasses.asdict(record)
    return (json.dumps(document, indent=_INDENT, ensure_ascii=True) + "\n").encode()


def manifest_bytes(manifest: Manifest) -> bytes:
    """*manifest* as the bytes `write_manifest` stores."""
    return _json_bytes(manifest)


def frame_bytes(frame: FrameRecord) -> bytes:
    """*frame*, every field verbatim, as the bytes of its exact JSON copy."""
    return _json_bytes(frame)


# --- Decoding ------------------------------------------------------------------------


def _frame_export(value: object, where: str) -> FrameExport:
    """One ``before.frames`` entry."""
    fields = Fields(value, where)
    return FrameExport(
        kind=member(FrameKind)(fields.value("kind"), fields.at("kind")),
        lang=fields.text("lang"),
        desc=fields.text("desc"),
        encoding=fields.number("encoding"),
        format=fields.optional("format", number_value),
        type=fields.optional("type", number_value),
        file=relative_path(fields.value("file"), fields.at("file")),
        exact=fields.optional("exact", relative_path),
    )


def _link_target(value: object, where: str) -> str:
    """*value* as a link's target: any non-empty text but a NUL, or refused.

    A target may be absolute or climb with ``..``: undo recreates the link as it was
    and writes nothing through it, so where it points is the link's own business.
    """
    text = text_value(value, where)
    if not text or "\x00" in text:
        raise malformed(where, "a link target")
    return text


def _sidecar_copy(value: object, where: str) -> SidecarCopy:
    """One ``before.sidecars`` entry: a file copy, or a link by its target.

    A manifest written before links were recorded has no ``link`` field, and each of
    its entries is a file copy, as it always was. Either field left out reads as null,
    so a link record without ``file`` is a link; one with neither is refused.
    """
    fields = Fields(value, where)
    name = file_name(fields.value("name"), fields.at("name"))
    file = fields.optional("file", relative_path) if fields.has("file") else None
    link = fields.optional("link", _link_target) if fields.has("link") else None
    if (file is None) == (link is None):
        raise malformed(where, "a file copy or a link")
    return SidecarCopy(name, file, link)


def _before(value: object, where: str) -> TrackBefore:
    """A track's ``before`` block."""
    fields = Fields(value, where)
    return TrackBefore(
        fields.each("frames", _frame_export), fields.each("sidecars", _sidecar_copy)
    )


def _reason(value: object, where: str) -> CheckReason:
    """One flag or skip of a check."""
    fields = Fields(value, where)
    rule = member(CheckRule)(fields.value("rule"), fields.at("rule"))
    return CheckReason(rule, fields.text("detail"))


def _check(value: object, where: str) -> CheckResult:
    """A check's findings."""
    fields = Fields(value, where)
    return CheckResult(fields.each("flags", _reason), fields.each("skips", _reason))


def _stage_error(value: object, where: str) -> StageError:
    """A track's error."""
    fields = Fields(value, where)
    stage = member(ErrorStage)(fields.value("stage"), fields.at("stage"))
    return StageError(stage, fields.text("exc_type"), fields.text("message"))


def _rename(value: object, where: str) -> SidecarRename:
    """One sidecar rename."""
    fields = Fields(value, where)
    old = file_name(fields.value("old"), fields.at("old"))
    return SidecarRename(old, file_name(fields.value("new"), fields.at("new")))


def _sidecar_hash(value: object, where: str) -> SidecarHash:
    """One sidecar a run left: a plain file name and its SHA-256 as lowercase hex."""
    fields = Fields(value, where)
    digest = fields.text("sha256")
    if not _SHA256.fullmatch(digest):
        raise malformed(fields.at("sha256"), "a SHA-256 in lowercase hex")
    return SidecarHash(file_name(fields.value("name"), fields.at("name")), digest)


def _refusal(value: object, where: str) -> ProviderRefusal:
    """One provider refusal; its detail reads capped, as the record caps it."""
    fields = Fields(value, where)
    provider = member(Source)(fields.value("provider"), fields.at("provider"))
    step = member(LyricsKind)(fields.value("step"), fields.at("step"))
    return ProviderRefusal(provider, step, fields.text("detail"))


def _after(value: object, where: str) -> TrackOutcome:
    """A track's ``after`` block."""
    fields = Fields(value, where)
    return TrackOutcome(
        status=member(OutcomeStatus)(fields.value("status"), fields.at("status")),
        source=fields.optional("source", member(Source)),
        kind=fields.optional("kind", member(LyricsKind)),
        query=fields.each("query", member(QueryMarker)),
        sidecar_written=fields.optional("sidecar_written", file_name),
        sidecars_removed=fields.each("sidecars_removed", file_name),
        sidecars_renamed=fields.each("sidecars_renamed", _rename),
        sidecar_hashes=fields.each("sidecar_hashes", _sidecar_hash),
        frames_written=fields.flag("frames_written"),
        check=fields.optional("check", _check),
        error=fields.optional("error", _stage_error),
        # A manifest written before the field existed lacks it: nothing was ignored.
        sidecar_links_ignored=(
            fields.each(_LINKS_IGNORED, file_name) if fields.has(_LINKS_IGNORED) else ()
        ),
        # Likewise: a manifest from before refusals were recorded holds none.
        refusals=fields.each(_REFUSALS, _refusal) if fields.has(_REFUSALS) else (),
        # And one from before a refetch was recorded asked no track again.
        refetch_untimed=fields.has(_REFETCH) and fields.flag(_REFETCH),
        sidecar_unreadable=fields.later(_UNREADABLE, text_value),
        implausible=fields.later(_IMPLAUSIBLE, text_value),
        refetch_implausible=fields.has(_REFETCH_IMPLAUSIBLE)
        and fields.flag(_REFETCH_IMPLAUSIBLE),
        peak_dbfs=fields.later(_PEAK, real_value),
        silence_failed=fields.later(_SILENCE_FAILED, text_value),
    )


def _version(value: object, where: str) -> tuple[int, int]:
    """An ID3 version, ``[2, minor]``."""
    if not isinstance(value, list) or len(value) != _PAIR:
        raise malformed(where, "a version pair")
    return (number_value(value[0], where), number_value(value[1], where))


def _mp3_path(value: object, where: str) -> str:
    """*value* as an album-relative path to an MP3, the suffix in any case."""
    text = relative_path(value, where)
    if not is_mp3_name(text):
        raise malformed(where, "a path to an MP3")
    return text


def _track(value: object, where: str) -> ManifestTrack:
    """One manifest track; frames backed up from a file with no ID3v2 tag are refused."""
    fields = Fields(value, where)
    track = ManifestTrack(
        path=_mp3_path(fields.value("path"), fields.at("path")),
        id3_version=fields.optional("id3_version", _version),
        before=fields.optional("before", _before),
        after=fields.optional("after", _after),
        undone=fields.flag("undone"),
        displaced=fields.each("displaced", relative_path),
    )
    if track.id3_version is None and track.before is not None and track.before.frames:
        raise ManifestSchemaError(
            f"malformed manifest: {where} has backed-up frames but no ID3 version"
        )
    return track


def _tracks(fields: Fields) -> tuple[ManifestTrack, ...]:
    """The manifest's tracks; a path listed twice is refused."""
    tracks = fields.each("tracks", _track)
    seen: set[str] = set()
    for track in tracks:
        if track.path in seen:
            raise ManifestSchemaError(
                f"malformed manifest: {track.path!r} is listed more than once"
            )
        seen.add(track.path)
    return tracks


def _manifest(document: object) -> Manifest:
    """The manifest *document* holds; the schema is checked before anything else."""
    fields = Fields(document, "manifest")
    schema = fields.value("schema")
    if type(schema) is not int or schema != MANIFEST_SCHEMA:
        raise ManifestSchemaError(f"unknown manifest schema: {schema!r}")
    if fields.text("album") != _ALBUM:
        raise malformed(fields.at("album"), f"{_ALBUM!r}")
    return Manifest(
        run_id=fields.text("run_id"),
        lang=fields.text("lang"),
        state=member(RunState)(fields.value("state"), fields.at("state")),
        genius=member(GeniusState)(fields.value("genius"), fields.at("genius")),
        tracks=_tracks(fields),
        # Optional: a manifest written before the field, or holding null, reads off.
        bandcamp=fields.later("bandcamp", member(BandcampState)) or BandcampState.OFF,
        youtube_music=fields.later("youtube_music", member(CoolingState))
        or CoolingState.OFF,
        tekstowo=fields.later("tekstowo", member(CoolingState)) or CoolingState.OFF,
        silence_check=fields.later("silence_check", member(SilenceCheck))
        or SilenceCheck.OFF,
    )


def _decode(data: bytes, what: str) -> object:
    """*data* as a JSON document; bytes that are not UTF-8 JSON are refused."""
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError) as error:
        # ValueError covers both a bad byte and bad JSON; RecursionError is a nesting
        # deep enough to exhaust the parser, which only a damaged file holds.
        raise ManifestSchemaError(f"malformed {what}: {error}") from error


# fuzz-target
def parse_manifest(data: bytes) -> Manifest:
    """The manifest *data* holds; raise `ManifestSchemaError` on anything else.

    An unknown schema is named as such, before any other field is read, so a manifest
    from a newer layout is refused for what it is.
    """
    return _manifest(_decode(data, "manifest"))


def _entry(value: object, where: str) -> SyltEntry:
    """One SYLT entry, stored as ``{"text": ..., "time": ...}``.

    A negative time is refused by SyltEntry itself, as a ValueError.
    """
    fields = Fields(value, where)
    return SyltEntry(fields.text("text"), fields.number("time"))


def _payload(value: object, where: str) -> str | tuple[SyltEntry, ...]:
    """A frame's payload: USLT text, or SYLT entries."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise malformed(where, "text or a list of entries")
    return tuple(_entry(item, f"{where}[{i}]") for i, item in enumerate(value))


# fuzz-target
def parse_frame(data: bytes) -> FrameRecord:
    """The frame an exact JSON copy holds; raise `ManifestSchemaError` on anything else."""
    fields = Fields(_decode(data, "frame copy"), "frame")
    try:
        return FrameRecord(
            kind=member(FrameKind)(fields.value("kind"), fields.at("kind")),
            lang=fields.text("lang"),
            desc=fields.text("desc"),
            encoding=fields.number("encoding"),
            format=fields.optional("format", number_value),
            type=fields.optional("type", number_value),
            payload=_payload(fields.value("payload"), fields.at("payload")),
        )
    except ValueError as error:
        # FrameRecord refuses fields that contradict its kind, a USLT with entries say.
        raise ManifestSchemaError(f"malformed frame copy: {error}") from error


# --- The manifest file ---------------------------------------------------------------


def write_manifest(run_dir: Path, manifest: Manifest) -> None:
    """Replace *run_dir*'s manifest atomically with *manifest*, then read it back."""
    lyrics_files.write_confirmed(run_dir / MANIFEST_NAME, manifest_bytes(manifest))


def read_manifest(run_dir: Path) -> Manifest:
    """Read *run_dir*'s manifest; raise `ManifestSchemaError` unless it is well formed.

    A manifest reached through a link out of the run folder, or larger than
    `MAX_MANIFEST_BYTES`, is refused as malformed too, the oversized one unread past
    the cap. A missing or unreadable file is the `OSError` reading it raised.
    """
    try:
        path = lyrics_files.inside_run_folder(run_dir, MANIFEST_NAME)
    except lyrics_files.OutsideRunFolderError as error:
        raise ManifestSchemaError(f"malformed manifest: {error}") from error
    with path.open("rb") as handle:
        data = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestSchemaError(
            f"malformed manifest: too large, over {MAX_MANIFEST_BYTES} bytes"
        )
    return parse_manifest(data)


#: Each stage function bound to its protocol, so the type checker holds the signatures.
STAGES: tuple[WriteManifest, ReadManifest] = (write_manifest, read_manifest)
