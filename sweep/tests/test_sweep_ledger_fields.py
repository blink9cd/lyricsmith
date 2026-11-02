"""The ledger's later fields: owed providers, refusals, retries, and the split counts.

Each is optional, so a line written before it existed still reads, as its default. Each
is validated as the first fields are: a provider, a reason, a category must be one the
tool knows, every count a whole number of 0 or more, and anything else is a
`LedgerLineError`. Which albums are owed, and what, is read like the done set.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import lyrics_categories as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_tally import Counts  # noqa: E402 - import follows the sys.path bootstrap above

_TIME = "2026-10-19T09:30:00+00:00"
_COUNTS = Counts(
    written=3,
    not_found=1,
    removed=2,
    withheld=1,
    written_by={lc.WrittenCategory.UPGRADED: 2, lc.WrittenCategory.NEW_SYNCED: 1},
    removed_by={lc.RemovedReason.PLACEHOLDER: 2},
)
_OWED = sl.LedgerEntry(
    album="Artist/Album",
    run_id="20261019T120000Z",
    exit_code=0,
    outcome=sl.Outcome.OWED,
    time=_TIME,
    counts=_COUNTS,
    refusing_tracks=2,
    refusing_providers=("Genius", "LRCLIB"),
    owed_providers=(lm.Source.LRCLIB, lm.Source.GENIUS),
    challenged_providers=(lm.Source.GENIUS,),
    refusals={
        lm.Source.LRCLIB: {lrf.Refusal.OVERLOADED: 1},
        lm.Source.GENIUS: {lrf.Refusal.BLOCKED: 1, lrf.Refusal.UNRECOGNIZED: 1},
    },
    retried={lm.Source.LRCLIB: {lrf.Refusal.OVERLOADED: 7}},
)


def _document(**changes: object) -> dict[str, object]:
    """`_OWED`'s line as an object, with *changes* made."""
    document = json.loads(sl.encode_record(_OWED))
    document.update(changes)
    return document


def _parsed(document: dict[str, object]) -> sl.LedgerEntry:
    """*document* parsed as an album's entry."""
    record = sl.parse_record(json.dumps(document))
    assert isinstance(record, sl.LedgerEntry)
    return record


class RoundTripTest(sf.IsolatedCase):
    """The later fields write as the manifest's values and read back exactly."""

    def test_an_owed_entry_round_trips(self) -> None:
        self.assertEqual(sl.parse_record(sl.encode_record(_OWED)), _OWED)

    def test_the_line_holds_the_stored_values(self) -> None:
        document = _document()
        self.assertEqual(document["owed_providers"], ["Lrclib", "Genius"])
        self.assertEqual(document["challenged_providers"], ["Genius"])
        self.assertEqual(
            document["refusals"],
            {
                "Lrclib": {"overloaded": 1},
                "Genius": {"blocked": 1, "unrecognized": 1},
            },
        )
        self.assertEqual(document["retried"], {"Lrclib": {"overloaded": 7}})
        counts = document["counts"]
        assert isinstance(counts, dict)
        self.assertEqual(counts["removed"], 2)
        self.assertEqual(counts["withheld"], 1)
        self.assertEqual(counts["written_by"], {"upgraded": 2, "new synced": 1})
        self.assertEqual(counts["removed_by"], {"placeholder": 2})


class OlderLineTest(sf.IsolatedCase):
    """A line written before the later fields reads each as its default."""

    def test_a_line_without_them_reads_their_defaults(self) -> None:
        document = _document()
        for name in ("owed_providers", "challenged_providers", "refusals", "retried"):
            del document[name]
        counts = document["counts"]
        assert isinstance(counts, dict)
        for name in ("removed", "withheld", "written_by", "removed_by"):
            del counts[name]
        entry = _parsed(document)
        self.assertEqual(
            (entry.owed_providers, entry.refusals, entry.retried), ((), {}, {})
        )
        self.assertEqual(entry.challenged_providers, ())
        assert entry.counts is not None
        self.assertEqual(
            (entry.counts.removed, entry.counts.written_by, entry.counts.removed_by),
            (0, {}, {}),
        )
        self.assertEqual(entry.counts.withheld, 0)
        # An older line's not found still includes its removals, read as written.
        self.assertEqual(entry.counts.not_found, 1)


class RecordsDigestTest(sf.IsolatedCase):
    """A fix entry's records digest: optional, a SHA-256, read back exactly."""

    def test_a_digest_round_trips_and_an_entry_without_one_writes_none(self) -> None:
        fixed = replace(_OWED, pass_=sl.SweepPass.FIX_TEXT, records_digest="ab" * 32)
        self.assertEqual(sl.parse_record(sl.encode_record(fixed)), fixed)
        self.assertNotIn("records_digest", _document())

    def test_a_line_without_a_digest_reads_as_none(self) -> None:
        self.assertIsNone(_parsed(_document()).records_digest)

    def test_a_digest_that_is_not_a_sha256_is_refused(self) -> None:
        for bad in (None, 7, "AB" * 32, "ab" * 31, "ab" * 32 + "\n"):
            with self.subTest(bad=bad), self.assertRaises(sl.LedgerLineError):
                sl.parse_record(json.dumps(_document(records_digest=bad)))


class RefusedValueTest(sf.IsolatedCase):
    """Each shape a later field may not take is refused."""

    def _refuses(self, document: dict[str, object]) -> None:
        with self.assertRaises(sl.LedgerLineError):
            sl.parse_record(json.dumps(document))

    def _refuses_counts(self, **changes: object) -> None:
        counts = _document()["counts"]
        assert isinstance(counts, dict)
        self._refuses(_document(counts={**counts, **changes}))

    def test_owed_providers_of_the_wrong_shape_are_refused(self) -> None:
        bad: list[object] = [
            "Lrclib",
            [1],
            ["Spotify"],
            # No --only-providers name can ask these: no provider of the chain.
            ["sidecar"],
            ["Musixmatch"],
            ["Lrclib", "Lrclib"],
            None,
        ]
        for value in bad:
            with self.subTest(value=value):
                self._refuses(_document(owed_providers=value))

    def test_challenged_providers_of_the_wrong_shape_are_refused(self) -> None:
        bad: list[object] = ["Genius", [1], ["Spotify"], ["sidecar"], ["Genius"] * 2]
        for value in [*bad, None, {"Genius": 1}]:
            with self.subTest(value=value):
                self._refuses(_document(challenged_providers=value))

    def test_refusal_and_retry_maps_of_the_wrong_shape_are_refused(self) -> None:
        bad: list[object] = [
            [],
            None,
            {"Spotify": {"overloaded": 1}},
            {"Lrclib": {"sleepy": 1}},
            {"Lrclib": {"overloaded": -1}},
            {"Lrclib": {"overloaded": True}},
            {"Lrclib": {"overloaded": 1.5}},
            {"Lrclib": [1]},
            # A provider is listed only for a reason it was counted under.
            {"Lrclib": {}},
        ]
        for name in ("refusals", "retried"):
            for value in bad:
                with self.subTest(name=name, value=value):
                    self._refuses(_document(**{name: value}))

    def test_split_counts_of_the_wrong_shape_are_refused(self) -> None:
        bad_maps: list[object] = [
            [],
            None,
            {"upgraded-ish": 1},
            {"upgraded": -1},
            {"upgraded": False},
        ]
        for value in bad_maps:
            with self.subTest(written_by=value):
                self._refuses_counts(written_by=value)
        for value in ([], {"placeholder": -2}, {"placeholders": 1}):
            with self.subTest(removed_by=value):
                self._refuses_counts(removed_by=value)
        for value in (-1, "2", True, None):
            with self.subTest(removed=value):
                self._refuses_counts(removed=value)

    def test_a_negative_count_is_refused(self) -> None:
        self._refuses_counts(written=-1)


class OwedAlbumsTest(sf.IsolatedCase):
    """The albums owed, with their providers: the newest word on each since a restart."""

    def _entry(
        self,
        album: str,
        outcome: sl.Outcome,
        owed: tuple[lm.Source, ...] = (),
        lang: str = sweep.album.DEFAULT_LANG,
        pass_: sl.SweepPass = sl.SweepPass.NORMAL,
    ) -> sl.LedgerEntry:
        return sl.LedgerEntry(
            album, None, 0, outcome, _TIME, lang=lang, pass_=pass_, owed_providers=owed
        )

    def _owed(self, *records: sl.LedgerRecord) -> dict[str, tuple[lm.Source, ...]]:
        return sl.owed_albums(records, sweep.album.DEFAULT_LANG, sl.SweepPass.NORMAL)

    def test_an_owed_album_is_owed_its_providers(self) -> None:
        owed = (lm.Source.LRCLIB, lm.Source.GENIUS)
        self.assertEqual(
            self._owed(self._entry("A/B", sl.Outcome.OWED, owed)), {"A/B": owed}
        )

    def test_an_owed_album_is_never_done(self) -> None:
        entry = self._entry("A/B", sl.Outcome.OWED, (lm.Source.LRCLIB,))
        self.assertEqual(
            sl.done_albums([entry], sweep.album.DEFAULT_LANG, sl.SweepPass.NORMAL),
            frozenset(),
        )

    def test_the_newest_entry_for_an_album_wins(self) -> None:
        owed = self._entry("A/B", sl.Outcome.OWED, (lm.Source.LRCLIB,))
        done = self._entry("A/B", sl.Outcome.DONE)
        self.assertEqual(self._owed(owed, done), {})
        self.assertEqual(self._owed(done, owed), {"A/B": (lm.Source.LRCLIB,)})

    def test_a_refused_entry_s_providers_are_not_owed(self) -> None:
        refused = self._entry("A/B", sl.Outcome.REFUSED, (lm.Source.LRCLIB,))
        self.assertEqual(self._owed(refused), {})

    def test_a_restart_mark_forgets_what_was_owed(self) -> None:
        owed = self._entry("A/B", sl.Outcome.OWED, (lm.Source.LRCLIB,))
        self.assertEqual(self._owed(owed, sl.RestartMark(_TIME)), {})

    def test_another_language_or_pass_is_its_own(self) -> None:
        owed = (lm.Source.LRCLIB,)
        other_lang = self._entry("A/B", sl.Outcome.OWED, owed, lang="deu")
        other_pass = self._entry(
            "A/C", sl.Outcome.OWED, owed, pass_=sl.SweepPass.REFETCH_UNTIMED
        )
        self.assertEqual(self._owed(other_lang, other_pass), {})
