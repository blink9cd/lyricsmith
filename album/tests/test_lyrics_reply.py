"""Reading a provider reply's fields: a number, a flag, or the provider's own error.

Every field here is invented; nothing reaches the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_reply as lrep  # noqa: E402 - import follows the sys.path bootstrap above

SOURCE = lm.Source.NETEASE


class UnexpectedReplyTest(fixtures.IsolatedCase):
    """The error names the provider and what was out of shape."""

    def test_the_error_names_the_provider_and_the_field(self) -> None:
        error = lrep.unexpected_reply(SOURCE, "songs is not a list")
        self.assertIsInstance(error, lp.ProviderError)
        self.assertEqual(
            str(error), f"{SOURCE} answered an unexpected reply: songs is not a list"
        )


class NumberFieldTest(fixtures.IsolatedCase):
    """A number reads as a float; absent or null is None; any other shape raises."""

    def test_a_whole_or_fractional_number_reads_as_a_float(self) -> None:
        for value, expected in ((200, 200.0), (200.5, 200.5)):
            with self.subTest(value=value):
                read = lrep.number_field({"duration": value}, "duration", SOURCE)
                self.assertEqual(read, expected)
                self.assertIsInstance(read, float)

    def test_absent_or_null_is_none(self) -> None:
        for reply in ({}, {"duration": None}):
            with self.subTest(reply=reply):
                self.assertIsNone(lrep.number_field(reply, "duration", SOURCE))

    def test_text_or_a_flag_is_the_providers_error(self) -> None:
        for value in ("long", True, False, [1]):
            with (
                self.subTest(value=value),
                self.assertRaises(lp.ProviderError) as raised,
            ):
                lrep.number_field({"duration": value}, "duration", SOURCE)
            self.assertIn("duration is not a number", str(raised.exception))
            self.assertIn(str(SOURCE), str(raised.exception))

    def test_a_whole_number_too_large_for_a_float_is_the_providers_error(self) -> None:
        with self.assertRaises(lp.ProviderError) as raised:
            lrep.number_field({"duration": 10**400}, "duration", SOURCE)
        self.assertIn("duration is out of range", str(raised.exception))


class FlagFieldTest(fixtures.IsolatedCase):
    """A flag reads as given; absent is false; anything but true or false raises."""

    def test_a_flag_reads_as_given_and_absent_is_false(self) -> None:
        self.assertTrue(lrep.flag_field({"nolyric": True}, "nolyric", SOURCE))
        self.assertFalse(lrep.flag_field({"nolyric": False}, "nolyric", SOURCE))
        self.assertFalse(lrep.flag_field({}, "nolyric", SOURCE))

    def test_any_other_value_is_the_providers_error(self) -> None:
        for value in ("yes", 1, None):
            with (
                self.subTest(value=value),
                self.assertRaises(lp.ProviderError) as raised,
            ):
                lrep.flag_field({"nolyric": value}, "nolyric", SOURCE)
            self.assertIn("nolyric is not true or false", str(raised.exception))
