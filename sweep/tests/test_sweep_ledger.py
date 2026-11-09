"""The ledger: where it lives, its lines, its tolerance of bad ones, and what is done.

The ledger is a file a person may edit, so its reader is a parser of untrusted input:
any line it cannot read is reported and skipped, never fatal, and the property tests
hold it to raising nothing else on any text at all.
"""

from __future__ import annotations

import json
import ntpath
import sys
from dataclasses import replace
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_categories as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal_tally as lrt  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_tally import PROVIDERS  # noqa: E402 - follows the sys.path bootstrap
from sweep_tally import Counts  # noqa: E402 - import follows the sys.path bootstrap above

# A registered profile, never an @settings decorator: a fuzzing campaign that re-applies
# its own settings to each property is refused by Hypothesis on a decorated one. Another
# suite's profile loaded later in the same process cannot replace it, as `@given` copies
# the settings loaded when it decorates a test. deadline=None: a per-example deadline
# measures machine load, not correctness.
settings.register_profile(
    "sweep_ledger_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("sweep_ledger_properties")

_TIME = "2026-10-19T09:30:00+00:00"
_COUNTS = Counts(
    written=3,
    unchanged=1,
    not_found=2,
    no_lyrics_expected=4,
    kept=0,
    errors=1,
    flagged=1,
)
_DONE = sl.LedgerEntry(
    album="Artist/Album",
    run_id="20261019T120000Z",
    exit_code=1,
    outcome=sl.Outcome.DONE,
    time=_TIME,
    counts=_COUNTS,
    refusing_tracks=1,
    refusing_providers=("Lrclib",),
    lang="deu",
)
_DEFAULT_LANG = sweep.album.DEFAULT_LANG


def _entry(
    album: str, outcome: sl.Outcome, lang: str = _DEFAULT_LANG
) -> sl.LedgerEntry:
    """A bare entry for *album* that ended as *outcome*, under *lang*."""
    return sl.LedgerEntry(album, None, 0, outcome, _TIME, lang=lang)


class LedgerPathTest(sf.IsolatedCase):
    """One ledger per library, under the state directory, outside the library."""

    def test_the_ledger_lives_under_xdg_state_home(self) -> None:
        path = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        self.assertEqual(path.parent, self.state / "lyricsmith" / "sweep")
        self.assertEqual(path.suffix, ".jsonl")
        self.assertFalse(path.is_relative_to(self.library))

    def test_without_xdg_state_home_it_lives_under_the_home_state_folder(self) -> None:
        path = sl.ledger_path(self.library, {})
        self.assertEqual(
            path.parent, self.home / ".local" / "state" / "lyricsmith" / "sweep"
        )

    def test_the_windows_home_lookup_is_the_test_home_too(self) -> None:
        # ntpath's expanduser is the lookup `Path.home()` makes on Windows, reading
        # USERPROFILE and never HOME, and it runs on any host, so the isolation's
        # redirect of Windows' home is proven here as well.
        self.assertEqual(ntpath.expanduser("~"), str(self.home))

    def test_without_xdg_state_home_it_lives_under_the_named_home(self) -> None:
        # A home folder that is not the process's, so only HOME in the mapping leads here.
        other = self.tmp / "other"
        path = sl.ledger_path(self.library, {"HOME": str(other)})
        self.assertNotEqual(other, Path.home())
        self.assertEqual(
            path.parent, other / ".local" / "state" / "lyricsmith" / "sweep"
        )

    def test_a_relative_xdg_state_home_is_ignored(self) -> None:
        path = sl.ledger_path(self.library, {"XDG_STATE_HOME": "relative/state"})
        self.assertEqual(
            path.parent, self.home / ".local" / "state" / "lyricsmith" / "sweep"
        )

    def test_two_libraries_get_two_ledgers_and_one_library_one(self) -> None:
        other = self.tmp / "other library"
        other.mkdir()
        env = {"XDG_STATE_HOME": str(self.state)}
        first = sl.ledger_path(self.library, env)
        self.assertNotEqual(first, sl.ledger_path(other, env))
        # The same library named another way is the same ledger.
        spelled = self.library / ".." / self.library.name
        self.assertEqual(first, sl.ledger_path(spelled, env))


class LedgerFileTest(sf.IsolatedCase):
    """Appending, reading back, and reporting the lines that cannot be read."""

    def setUp(self) -> None:
        """Name a ledger file that does not exist yet."""
        super().setUp()
        self.path = self.state / "lyricsmith" / "sweep" / "ledger.jsonl"

    def test_a_missing_ledger_reads_as_empty(self) -> None:
        self.assertEqual(sl.read_ledger(self.path), sl.LedgerRead((), ()))

    def test_appended_records_read_back_in_order(self) -> None:
        mark = sl.RestartMark(_TIME)
        sl.append_record(self.path, _DONE)
        sl.append_record(self.path, mark)
        self.assertEqual(sl.read_ledger(self.path), sl.LedgerRead((_DONE, mark), ()))
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 2)

    def test_an_entry_without_a_run_keeps_its_counts_empty(self) -> None:
        entry = sl.LedgerEntry("A/B", None, 2, sl.Outcome.COULD_NOT_RUN, _TIME)
        sl.append_record(self.path, entry)
        self.assertEqual(sl.read_ledger(self.path).records, (entry,))

    def test_malformed_lines_are_reported_and_skipped_and_kept(self) -> None:
        self.path.parent.mkdir(parents=True)
        good = sl.encode_record(_DONE)
        self.path.write_bytes(
            b"not json\n" + good.encode() + b'\n\n\xff\xfe\n{"kind": "album"}'
        )
        read = sl.read_ledger(self.path)
        self.assertEqual(read.records, (_DONE,))
        self.assertEqual(
            [p.split(":")[0] for p in read.problems], ["line 1", "line 4", "line 5"]
        )
        # The next append keeps every line it could not read, for a person to see.
        sl.append_record(self.path, _DONE)
        lines = self.path.read_bytes().split(b"\n")
        self.assertEqual(lines[0], b"not json")
        self.assertEqual(lines[4], b'{"kind": "album"}')
        self.assertEqual(sl.read_ledger(self.path).records, (_DONE, _DONE))

    def test_an_append_retried_after_it_landed_is_not_written_twice(self) -> None:
        sl.append_record(self.path, _DONE)
        sl.append_unless_last(self.path, _DONE)
        self.assertEqual(sl.read_ledger(self.path).records, (_DONE,))

    def test_an_append_retried_before_it_landed_is_written(self) -> None:
        mark = sl.RestartMark(_TIME)
        sl.append_record(self.path, mark)
        sl.append_unless_last(self.path, _DONE)
        self.assertEqual(sl.read_ledger(self.path).records, (mark, _DONE))

    def test_the_rewrite_leaves_no_temporary_file_behind(self) -> None:
        sl.append_record(self.path, _DONE)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])


class ParseRecordTest(sf.IsolatedCase):
    """Each shape a line may not take is refused as a `LedgerLineError`."""

    def _refuses(self, document: object) -> None:
        with self.assertRaises(sl.LedgerLineError):
            sl.parse_record(json.dumps(document))

    def _done(self, **changes: object) -> dict[str, object]:
        document = json.loads(sl.encode_record(_DONE))
        document.update(changes)
        return document

    def test_a_round_trip_is_exact(self) -> None:
        self.assertEqual(sl.parse_record(sl.encode_record(_DONE)), _DONE)

    def test_not_an_object_is_refused(self) -> None:
        self._refuses([1, 2])

    def test_an_unknown_kind_is_refused(self) -> None:
        self._refuses({"kind": "other", "time": _TIME})

    def test_a_restart_mark_without_a_time_is_refused(self) -> None:
        self._refuses({"kind": "restart"})

    def test_a_wrong_type_is_refused(self) -> None:
        self._refuses(self._done(exit_code="0"))

    def test_a_boolean_is_never_taken_for_a_number(self) -> None:
        self._refuses(self._done(exit_code=True))

    def test_an_unknown_outcome_is_refused(self) -> None:
        self._refuses(self._done(outcome="splendid"))

    def test_an_absolute_or_climbing_album_is_refused(self) -> None:
        self._refuses(self._done(album="/elsewhere/Album"))
        self._refuses(self._done(album="../Album"))
        self._refuses(self._done(album=""))

    def test_counts_missing_a_field_are_refused(self) -> None:
        self._refuses(self._done(counts={"written": 1}))

    def test_counts_written_before_no_lyrics_expected_read_it_as_zero(self) -> None:
        document = self._done()
        counts = document["counts"]
        assert isinstance(counts, dict)
        del counts["no_lyrics_expected"]
        record = sl.parse_record(json.dumps(document))
        assert isinstance(record, sl.LedgerEntry)
        self.assertEqual(record.counts, replace(_COUNTS, no_lyrics_expected=0))

    def test_a_written_entry_round_trips_no_lyrics_expected(self) -> None:
        record = sl.parse_record(sl.encode_record(_DONE))
        assert isinstance(record, sl.LedgerEntry) and record.counts is not None
        self.assertEqual(record.counts.no_lyrics_expected, 4)

    def test_counts_that_are_not_an_object_are_refused(self) -> None:
        self._refuses(self._done(counts=[1]))

    def test_providers_that_are_not_text_are_refused(self) -> None:
        self._refuses(self._done(refusing_providers=[1]))
        self._refuses(self._done(refusing_providers="Lrclib"))

    def test_a_run_id_that_is_not_text_is_refused(self) -> None:
        self._refuses(self._done(run_id=7))

    def test_a_lang_that_is_not_text_is_refused(self) -> None:
        self._refuses(self._done(lang=3))

    def test_an_entry_written_before_lang_was_recorded_reads_as_the_default(
        self,
    ) -> None:
        document = self._done()
        del document["lang"]
        record = sl.parse_record(json.dumps(document))
        assert isinstance(record, sl.LedgerEntry)
        self.assertEqual(record.lang, _DEFAULT_LANG)

    def test_lookups_a_breaker_left_unsent_round_trip_apart(self) -> None:
        refusals: lrt.RefusalCounts = {
            lm.Source.GENIUS: {lrf.Refusal.BLOCKED: 1, lrt.Unsent.BREAKER_OPEN: 20}
        }
        entry = replace(_DONE, refusals=refusals)
        line = sl.encode_record(entry)
        self.assertIn('"breaker open": 20', line)
        self.assertEqual(sl.parse_record(line), entry)

    def test_a_line_written_before_breaker_open_reads_as_it_was(self) -> None:
        document = self._done(refusals={"Lrclib": {"overloaded": 2}})
        record = sl.parse_record(json.dumps(document))
        assert isinstance(record, sl.LedgerEntry)
        self.assertEqual(
            record.refusals, {lm.Source.LRCLIB: {lrf.Refusal.OVERLOADED: 2}}
        )

    def test_a_retry_is_never_breaker_open(self) -> None:
        # A step the breaker skipped sent no request, so nothing was retried.
        self._refuses(self._done(retried={"Genius": {"breaker open": 1}}))

    def test_a_child_never_reaped_has_no_exit_code(self) -> None:
        entry = sl.LedgerEntry("A/B", None, None, sl.Outcome.ABANDONED, _TIME)
        self.assertEqual(sl.parse_record(sl.encode_record(entry)), entry)


class DoneAlbumsTest(sf.IsolatedCase):
    """Which albums a resumed sweep skips: the newest word on each since a restart."""

    def _done(self, records: list[sl.LedgerRecord]) -> frozenset[str]:
        return sl.done_albums(records, _DEFAULT_LANG, sl.SweepPass.NORMAL)

    def test_a_done_album_is_done(self) -> None:
        self.assertEqual(self._done([_entry("A/B", sl.Outcome.DONE)]), {"A/B"})

    def test_no_outcome_but_done_is_done(self) -> None:
        records: list[sl.LedgerRecord] = [
            _entry(f"A/{outcome.name}", outcome)
            for outcome in sl.Outcome
            if outcome is not sl.Outcome.DONE
        ]
        self.assertEqual(self._done(records), frozenset())

    def test_an_offline_visit_is_never_done(self) -> None:
        # An offline run asked no provider, so a later sweep online must visit it.
        records: list[sl.LedgerRecord] = [_entry("A/B", sl.Outcome.OFFLINE)]
        self.assertEqual(self._done(records), frozenset())

    def test_the_newest_entry_for_an_album_wins(self) -> None:
        records: list[sl.LedgerRecord] = [
            _entry("A/B", sl.Outcome.DONE),
            _entry("A/B", sl.Outcome.REFUSED),
        ]
        self.assertEqual(self._done(records), frozenset())
        records.append(_entry("A/B", sl.Outcome.DONE))
        self.assertEqual(self._done(records), {"A/B"})

    def test_an_album_done_in_one_language_is_not_done_in_another(self) -> None:
        records: list[sl.LedgerRecord] = [
            _entry("A/B", sl.Outcome.DONE, lang="deu"),
            _entry("A/C", sl.Outcome.DONE),
            _entry("A/C", sl.Outcome.REFUSED, lang="deu"),
        ]
        self.assertEqual(sl.done_albums(records, "deu", sl.SweepPass.NORMAL), {"A/B"})
        self.assertEqual(self._done(records), {"A/C"})

    def test_a_restart_mark_forgets_everything_before_it(self) -> None:
        records: list[sl.LedgerRecord] = [
            _entry("A/Old", sl.Outcome.DONE),
            sl.RestartMark(_TIME),
            _entry("A/New", sl.Outcome.DONE),
        ]
        self.assertEqual(self._done(records), {"A/New"})


_OUTCOMES = st.sampled_from(list(sl.Outcome))
_TALLY = st.integers(0, 999)
#: Counts, every field drawn: the ten whole counts, then the two breakdowns.
_COUNTS_DRAWN = st.builds(
    Counts,
    *[_TALLY] * 10,
    written_by=st.dictionaries(st.sampled_from(list(lc.WrittenCategory)), _TALLY),
    removed_by=st.dictionaries(st.sampled_from(list(lc.RemovedReason)), _TALLY),
)
#: A count per provider and reason, as the retries are kept.
_BY_REASON = st.dictionaries(
    st.sampled_from(list(lm.Source)),
    st.dictionaries(st.sampled_from(list(lrf.Refusal)), _TALLY, min_size=1),
    max_size=3,
)
#: The refusals' counts: a reason, or a lookup an open breaker left unsent.
_REFUSAL_KEYS = st.sampled_from([*lrf.Refusal, *lrt.Unsent])
_BY_KEY = st.dictionaries(
    st.sampled_from(list(lm.Source)),
    st.dictionaries(_REFUSAL_KEYS, _TALLY, min_size=1),
    max_size=3,
)
_NAMES = st.text(
    alphabet=st.characters(blacklist_characters="/\\"),
    min_size=1,
    max_size=12,
).filter(lambda name: name not in {".", ".."})
_ENTRIES = st.builds(
    sl.LedgerEntry,
    album=st.builds(lambda a, b: f"{a}/{b}", _NAMES, _NAMES),
    run_id=st.none() | st.text(max_size=24),
    exit_code=st.none() | st.integers(min_value=-64, max_value=255),
    outcome=_OUTCOMES,
    time=st.text(max_size=32),
    counts=st.none() | _COUNTS_DRAWN,
    refusing_tracks=st.integers(0, 999),
    refusing_providers=st.lists(st.text(max_size=12), max_size=3).map(tuple),
    lang=st.text(max_size=8),
    pass_=st.sampled_from(list(sl.SweepPass)),
    owed_providers=st.lists(st.sampled_from(PROVIDERS), unique=True).map(tuple),
    challenged_providers=st.lists(st.sampled_from(PROVIDERS), unique=True).map(tuple),
    refusals=_BY_KEY,
    retried=_BY_REASON,
    records_digest=st.none() | st.text("0123456789abcdef", min_size=64, max_size=64),
)
#: Any JSON value, plausible or not, for a field the parser validates.
_JSON_VALUES = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False)
    | st.text(max_size=16)
    | _OUTCOMES.map(str),
    lambda inner: (
        st.lists(inner, max_size=4)
        | st.dictionaries(st.text(max_size=12), inner, max_size=4)
    ),
    max_leaves=12,
)
#: The fields an entry or a restart mark holds, beside one no line holds.
_FIELDS = st.sampled_from(
    [
        "kind",
        "album",
        "run_id",
        "exit_code",
        "outcome",
        "time",
        "counts",
        "refusing_tracks",
        "refusing_providers",
        "lang",
        "pass",
        "owed_providers",
        "challenged_providers",
        "refusals",
        "retried",
        "records_digest",
        "elsewhere",
    ]
)


def _reshaped(
    entry: sl.LedgerEntry,
    kind: str,
    changes: dict[str, object],
    dropped: set[str],
) -> dict[str, object]:
    """*entry*'s line as an object, its kind set, *changes* made and *dropped* removed."""
    document = {**json.loads(sl.encode_record(entry)), "kind": kind, **changes}
    return {key: value for key, value in document.items() if key not in dropped}


#: A JSON object shaped like a ledger line: a real line with fields replaced, dropped
#: or added, so the parser's field checks are reached, not only its JSON decoding.
_OBJECTS = st.builds(
    _reshaped,
    _ENTRIES,
    st.sampled_from(["album", "restart", "other"]),
    st.dictionaries(_FIELDS, _JSON_VALUES, max_size=4),
    st.sets(_FIELDS, max_size=3),
)


class LedgerPropertyTest(sf.IsolatedCase):
    """The parser never raises anything but its own error, and round-trips its writes."""

    @given(st.text(max_size=400) | _OBJECTS.map(json.dumps))
    def test_fuzz_parse_record(self, line: str) -> None:
        # Any text at all, and JSON objects shaped like a ledger line, so the field
        # checks are reached as well as the JSON decoding.
        try:
            sl.parse_record(line)
        except sl.LedgerLineError:
            pass

    @given(_ENTRIES)
    def test_every_entry_round_trips(self, entry: sl.LedgerEntry) -> None:
        line = sl.encode_record(entry)
        self.assertNotIn("\n", line)
        self.assertEqual(sl.parse_record(line), entry)
