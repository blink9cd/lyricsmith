"""A run id is read back by the pattern its own format derives, never a second copy.

`lyrics_backup.run_id_base` writes a run id with `RUN_ID_FORMAT`, and
`lyrics_run_folders.run_order` reads one back with a pattern derived from that same
format, so a change to the format changes what a listing takes for a run. These cases
check the derivation itself, that every id the backup writes reads back with each
collision suffix `make_exclusive_dir` gives, and that a name one step off an id does
not. Every name here is invented.
"""

from __future__ import annotations

import re
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run_folders as rf  # noqa: E402 - import follows the sys.path bootstrap above

#: The earliest start a run id's four-digit year can spell.
_FIRST = datetime(1000, 1, 1)
#: The latest start a run id's four-digit year can spell.
_LAST = datetime(9999, 12, 31, 23, 59, 59)
#: Any start a run id can be made from, in UTC.
_STARTS = st.datetimes(min_value=_FIRST, max_value=_LAST, timezones=st.just(UTC))
#: The collision suffixes `make_exclusive_dir` gives: the second run of a second on.
_SUFFIXES = st.integers(min_value=rf.NO_SUFFIX + 1, max_value=10**6)
#: A character no run id holds anywhere: another script's digits included, as
#: strftime writes ASCII digits alone.
_NOT_IN_AN_ID = st.characters(exclude_characters="0123456789TZ")


class RunIdPatternTest(fixtures.IsolatedCase):
    """The derivation: each directive a fixed run of digits, every literal escaped."""

    def test_the_format_lives_with_the_pattern_and_backup_re_exports_it(self) -> None:
        self.assertEqual(lyrics_backup.RUN_ID_FORMAT, rf.RUN_ID_FORMAT)

    def test_each_directive_is_its_fixed_width_of_digits(self) -> None:
        self.assertEqual(
            rf.run_id_pattern("%Y%m%dT%H%M%SZ"),
            r"[0-9]{4}[0-9]{2}[0-9]{2}T[0-9]{2}[0-9]{2}[0-9]{2}Z",
        )

    def test_a_literal_is_escaped(self) -> None:
        self.assertEqual(rf.run_id_pattern("%Y.%m+"), r"[0-9]{4}\.[0-9]{2}\+")

    def test_a_doubled_percent_is_a_literal_percent(self) -> None:
        self.assertEqual(rf.run_id_pattern("%%Y%Y"), r"%Y[0-9]{4}")

    def test_an_unsupported_directive_is_refused(self) -> None:
        for form in ("%Y%j", "%B", "%Y%"):
            with (
                self.subTest(form=form),
                self.assertRaisesRegex(rf.RunIdFormatError, "run id format"),
            ):
                rf.run_id_pattern(form)

    def test_a_changed_format_changes_what_reads_as_a_run(self) -> None:
        daily = rf.run_id_regex("%Y-%m-%d")
        self.assertIsNotNone(daily.fullmatch("2026-10-01"))
        self.assertIsNotNone(daily.fullmatch("2026-10-01-2"))
        self.assertIsNone(daily.fullmatch("20261001T000000Z"))
        self.assertNotEqual(daily.pattern, rf.run_id_regex(rf.RUN_ID_FORMAT).pattern)

    def test_run_order_reads_with_the_pattern_the_format_derives(self) -> None:
        derived = rf.run_id_regex(rf.RUN_ID_FORMAT).pattern
        self.assertEqual(rf.RUN_ID_PATTERN.pattern, derived)


class RunIdRoundTripTest(fixtures.IsolatedCase):
    """Every id the backup writes reads back; a name one step off one does not."""

    @given(_STARTS)
    def test_every_written_id_reads_back_with_no_suffix(
        self, started: datetime
    ) -> None:
        base = lyrics_backup.run_id_base(started)
        self.assertEqual(rf.run_order(base), (base, rf.NO_SUFFIX))

    @given(_STARTS, _SUFFIXES)
    def test_every_collision_suffix_reads_back_as_its_number(
        self, started: datetime, suffix: int
    ) -> None:
        base = lyrics_backup.run_id_base(started)
        self.assertEqual(rf.run_order(f"{base}-{suffix}"), (base, suffix))

    @given(_STARTS, st.data())
    def test_one_character_changed_is_no_run_id(
        self, started: datetime, data: st.DataObject
    ) -> None:
        base = lyrics_backup.run_id_base(started)
        index = data.draw(st.integers(min_value=0, max_value=len(base) - 1))
        changed = base[:index] + data.draw(_NOT_IN_AN_ID) + base[index + 1 :]
        self.assertIsNone(rf.run_order(changed))

    @given(_STARTS)
    def test_a_digit_dropped_or_added_is_no_run_id(self, started: datetime) -> None:
        base = lyrics_backup.run_id_base(started)
        for name in (base[1:], f"0{base}", base.replace("T", "0T", 1)):
            self.assertIsNone(rf.run_order(name), name)

    def test_a_suffix_make_exclusive_dir_never_gives_is_no_run_id(self) -> None:
        base = "20261001T000000Z"
        names = ("-0", "-1", "-02", "-", "-x", "-\N{FULLWIDTH DIGIT TWO}")
        for name in (f"{base}{suffix}" for suffix in names):
            with self.subTest(name=name):
                self.assertIsNone(rf.run_order(name))

    def test_another_scripts_digits_are_no_run_id(self) -> None:
        # strftime and `make_exclusive_dir` write ASCII digits alone; `\d` would take
        # any script's, so a name no run wrote would list as a run.
        for name in (
            "\N{FULLWIDTH DIGIT TWO}0261001T000000Z",
            "20261001T000000Z-1\N{ARABIC-INDIC DIGIT TWO}",
        ):
            with self.subTest(name=name):
                self.assertIsNone(rf.run_order(name))

    def test_each_folder_make_exclusive_dir_creates_reads_back_in_order(self) -> None:
        base = "20261001T000000Z"
        made = [rf.make_exclusive_dir(self.tmp, base) for _ in range(11)]
        orders = [rf.run_order(path.name) for path in made]
        self.assertEqual(orders, [(base, n) for n in range(1, 12)])


class RunOrderFuzzTest(fixtures.IsolatedCase):
    """`run_order` takes any folder name a listing meets without raising."""

    @given(st.text(max_size=64))
    def test_fuzz_run_order(self, name: str) -> None:
        order = rf.run_order(name)
        if order is not None:
            base, suffix = order
            self.assertTrue(re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", base))
            self.assertGreaterEqual(suffix, rf.NO_SUFFIX)


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
