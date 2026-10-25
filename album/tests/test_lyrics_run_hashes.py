"""A run records a hash of every sidecar it leaves beside a track, for undo to prove.

Undo removes or replaces a sidecar without keeping a copy only when its bytes still
hash to what the run recorded, so the run records the hash of each sidecar it wrote or
renamed into place, from the bytes it wrote or read: a file changed on disk afterward
does not change the record. A sidecar the run did not write gets no hash. Every lyric
is invented.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_album import RunCase  # noqa: E402 - import follows the sys.path bootstrap above

TrackSpec = fixtures.TrackSpec
#: What a later edit appends to a sidecar the run left.
_EDIT = b"a line added by hand\n"


def _hash_of(path: Path) -> lr.SidecarHash:
    """The record of *path* as it stands on disk."""
    return lr.SidecarHash(path.name, hashlib.sha256(path.read_bytes()).hexdigest())


class SidecarHashTest(RunCase):
    """The ``after`` block holds the hash of each sidecar the run left in place."""

    def test_a_written_sidecar_is_recorded_with_its_hash(self) -> None:
        (mp3,) = self.build((TrackSpec("01 Keeper.mp3", title="The Keeper"),))
        (outcome,) = self.outcomes(self.run_album()).values()
        assert outcome is not None
        self.assertEqual(outcome.sidecar_hashes, (_hash_of(mp3.with_suffix(".lrc")),))

    def test_a_renamed_sidecar_is_recorded_even_when_nothing_was_found(self) -> None:
        track = TrackSpec("01 Mill.mp3", title="The Mill", lrc="an untimed verse\n")
        (mp3,) = self.build((track,))
        (outcome,) = self.outcomes(self.run_album(providers={})).values()
        assert outcome is not None
        self.assertEqual(outcome.sidecar_hashes, (_hash_of(mp3.with_suffix(".txt")),))

    def test_a_sidecar_that_served_the_lyrics_is_not_the_runs(self) -> None:
        track = TrackSpec("01 Keeper.mp3", lrc=fixtures.LIGHTHOUSE_LRC)
        self.build((track,))
        (outcome,) = self.outcomes(self.run_album(providers={})).values()
        assert outcome is not None
        self.assertEqual(outcome.sidecar_hashes, ())

    def test_a_written_sidecar_changed_after_the_write_keeps_the_written_hash(
        self,
    ) -> None:
        (mp3,) = self.build((TrackSpec("01 Keeper.mp3", title="The Keeper"),))
        written: list[lr.SidecarHash] = []

        def write_sidecar(path: Path, lyrics: lm.Lyrics) -> lm.SidecarWrite | None:
            result = lyrics_sidecar.write_sidecar(path, lyrics)
            written.append(_hash_of(path.with_suffix(".lrc")))
            with path.with_suffix(".lrc").open("ab") as handle:
                handle.write(_EDIT)
            return result

        stages = run_fakes.make_stages(self.folder, write_sidecar=write_sidecar)
        (outcome,) = self.outcomes(self.run_album(stages)).values()
        assert outcome is not None
        self.assertEqual(outcome.sidecar_hashes, tuple(written))
        self.assertNotEqual(written, [_hash_of(mp3.with_suffix(".lrc"))])

    def test_a_renamed_sidecar_changed_after_the_rename_keeps_the_read_hash(
        self,
    ) -> None:
        track = TrackSpec("01 Mill.mp3", title="The Mill", lrc="an untimed verse\n")
        (mp3,) = self.build((track,))
        landed: list[lr.SidecarHash] = []

        def normalize(path: Path, *, album: Path, apply: bool) -> lm.SidecarState:
            state = lyrics_sidecar.normalize_sidecars(path, album=album, apply=apply)
            # Only the applied normalize renames; a planned one changes nothing.
            if apply:
                landed.append(_hash_of(path.with_suffix(".txt")))
                with path.with_suffix(".txt").open("ab") as handle:
                    handle.write(_EDIT)
            return state

        stages = run_fakes.make_stages(self.folder, normalize_sidecars=normalize)
        (outcome,) = self.outcomes(self.run_album(stages, providers={})).values()
        assert outcome is not None
        self.assertEqual(outcome.sidecar_hashes, tuple(landed))
        self.assertNotEqual(landed, [_hash_of(mp3.with_suffix(".txt"))])
