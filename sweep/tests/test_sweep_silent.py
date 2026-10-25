"""Silent tracks in the sweep: counted, shown, kept in the ledger, and bounded in time.

A track the album tool measured as silent has its own end state. The sweep counts it
beside no lyrics expected, treats an album of only silent tracks as empty-handed, shows
it in the final table, reads a ledger line written before the count as zero, and adds
one silence measurement to a track's worst case. The end-to-end case sweeps the sample
album with a child whose PATH holds only a fake ffmpeg that reports silence.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_silence  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run as sr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_tally import Counts  # noqa: E402 - import follows the sys.path bootstrap above

_SILENT = sf.status_outcome(lr.OutcomeStatus.SILENT)
#: How long building the album may take.
_BUILD_TIMEOUT_S = 120
_BUILDER = sweep.ALBUM_ENTRY.parent / "samples" / "build_sample_album.py"
#: A fake ffmpeg: reports a peak far below the silence threshold and succeeds.
_FAKE_FFMPEG = (
    '#!/bin/sh\necho "[Parsed_volumedetect_0 @ 0x0] max_volume: -91.0 dB" >&2\n'
)


class TallyTest(sf.IsolatedCase):
    """A silent track is its own count and an empty-handed answer."""

    def test_silent_is_counted_apart_from_not_found(self) -> None:
        tally = sweep_tally.tally_manifest(sf.manifest_of(_SILENT, sf.not_found()))
        self.assertEqual(tally.counts, Counts(silent=1, not_found=1))

    def test_an_album_of_only_silent_tracks_is_empty_handed(self) -> None:
        self.assertTrue(sweep_tally.shows_an_answer(_SILENT))
        refusal = sf.status_outcome(lr.OutcomeStatus.SILENT, refusals=(sf.refusal(),))
        tally = sweep_tally.tally_manifest(sf.manifest_of(refusal, _SILENT))
        self.assertFalse(tally.refused)


class ReportTest(sf.IsolatedCase):
    """The final table holds silent right after no lyrics expected."""

    def test_the_header_and_a_row_carry_the_count(self) -> None:
        counts = Counts(written=1, not_found=2, no_lyrics_expected=3, silent=4, kept=5)
        entry = sl.LedgerEntry("A/B", None, 0, sl.Outcome.DONE, "t", counts=counts)
        report = sr.SweepReport(
            (sr.Visit(self.library, entry),), 0, 0, sr.StopReason.FINISHED
        )
        text = sweep_report.format_sweep(
            report, library=self.library, options=sf.OPTIONS, undo_prog=("al",)
        )
        header, row = text.splitlines()[1:3]
        self.assertIn("  no lyrics expected  silent  kept  ", header)
        self.assertEqual(
            row.split()[-9:], ["1", "0", "2", "3", "4", "5", "0", "0", "0"]
        )


class LedgerTest(sf.IsolatedCase):
    """A ledger line keeps the silent count, and one written before it reads zero."""

    _COUNTS = Counts(written=1, no_lyrics_expected=2, silent=3)
    _ENTRY = sl.LedgerEntry(
        "A/B", "20261019T120000Z", 0, sl.Outcome.DONE, "t", counts=_COUNTS
    )

    def test_a_new_line_round_trips_the_count(self) -> None:
        record = sl.parse_record(sl.encode_record(self._ENTRY))
        assert isinstance(record, sl.LedgerEntry)
        self.assertEqual(record.counts, self._COUNTS)

    def test_a_line_written_before_the_field_reads_it_as_zero(self) -> None:
        document = json.loads(sl.encode_record(self._ENTRY))
        del document["counts"]["silent"]
        record = sl.parse_record(json.dumps(document))
        assert isinstance(record, sl.LedgerEntry)
        self.assertEqual(record.counts, replace(self._COUNTS, silent=0))


class BoundTest(sf.IsolatedCase):
    """A track's worst case holds one silence measurement."""

    def test_the_track_bound_adds_one_measurement(self) -> None:
        expected = (
            sweep_child._pass_worst_s() * sweep_child._PASSES_PER_TRACK
            + lyrics_silence.SILENCE_TIMEOUT_S
        )
        self.assertEqual(sweep_child.TRACK_WORST_S, expected)


class FakeFfmpegEndToEndTest(sf.IsolatedCase):
    """The real album tool, its PATH holding only a fake ffmpeg that hears silence."""

    def test_silent_tracks_are_counted_in_the_ledger(self) -> None:
        album = self.library / "The Invented Quartet" / "Sample Album"
        subprocess.run(  # reliability: ignore[RL005] DEVNULL sinks, no pipe to hold
            [sys.executable, str(_BUILDER), "--out", str(album)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_BUILD_TIMEOUT_S,
        )
        bin_dir = self.tmp / "fake-bin"
        bin_dir.mkdir()
        fake = bin_dir / "ffmpeg"
        fake.write_bytes(_FAKE_FFMPEG.encode())
        fake.chmod(0o755)
        self.enterContext(mock.patch.dict(os.environ, {"PATH": str(bin_dir)}))
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = sweep.main(
                ["--mode", "run", "--offline", "--library", str(self.library)]
            )
        self.assertEqual(code, 0)
        path = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        (entry,) = sl.read_ledger(path).records
        assert isinstance(entry, sl.LedgerEntry)
        # The two tracks not found and the one expecting no lyrics are measured silent
        # and so are neither; the placeholder's removal is not a silent end, so it
        # stays the one not found.
        self.assertEqual(
            entry.counts, Counts(written=2, not_found=1, silent=3, flagged=1)
        )
