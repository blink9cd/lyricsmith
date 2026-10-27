"""lyrics_silence: volumedetect's answer parsed, the threshold, and the runner's failures."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_config as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_silence as ls  # noqa: E402 - import follows the sys.path bootstrap above

#: The real lookup, captured before any IsolatedCase patches the module attribute.
_REAL_FIND = ls.find_ffmpeg

#: A fake run seam, and the (argv, timeout) pairs it was called with.
_FakeRun = tuple[ls.Runner, list[tuple[list[str], float]]]


class ParsePeakTest(fixtures.IsolatedCase):
    """volumedetect's stderr to a peak: the last max_volume line wins."""

    def test_a_finite_level_is_read(self) -> None:
        self.assertEqual(ls.parse_peak("max_volume: -18.5 dB\n"), -18.5)

    def test_minus_inf_is_held_at_the_floor(self) -> None:
        self.assertEqual(ls.parse_peak("max_volume: -inf dB"), ls.FLOOR_DBFS)

    def test_the_last_line_wins_past_an_empty_block(self) -> None:
        text = "[Parsed_volumedetect_0 @ 0x1] n_samples: 0\n" + (
            "[Parsed_volumedetect_0 @ 0x2] max_volume: -91.0 dB\n"
        )
        self.assertEqual(ls.parse_peak(text), -91.0)

    def test_several_levels_take_the_last(self) -> None:
        self.assertEqual(
            ls.parse_peak("max_volume: -3.0 dB\nmax_volume: -70.0 dB"), -70.0
        )

    def test_no_level_is_none(self) -> None:
        self.assertIsNone(ls.parse_peak("n_samples: 0\n"))

    def test_malformed_nan_and_inf_are_none(self) -> None:
        for text in (
            "max_volume: loud dB",
            "max_volume: nan dB",
            "max_volume: inf dB",
            "max_volume: 1e999 dB",
        ):
            with self.subTest(text=text):
                self.assertIsNone(ls.parse_peak(text))

    def test_below_the_floor_is_clamped(self) -> None:
        self.assertEqual(ls.parse_peak("max_volume: -200.0 dB"), ls.FLOOR_DBFS)


#: Tokens ffmpeg never prints but a parser must survive, beside the real ``-inf``.
_ODD_TOKENS = ["-inf", "inf", "nan", "+inf", "1e999", "-1e999", "loud", "--5", "0x10"]
#: The prefix volumedetect writes before each of its lines.
_FILTER_PREFIX = "[Parsed_volumedetect_0 @ 0x1] "


def _has_whitespace(text: str) -> bool:
    """Whether *text* holds a character the parser's regex reads as whitespace."""
    return re.search(r"\s", text) is not None


def _level_tokens() -> st.SearchStrategy[str]:
    """A level line's token: a float as ffmpeg might print it, an odd token, or junk."""
    floats = st.floats(allow_nan=True, allow_infinity=True)
    junk = st.text(min_size=1).filter(
        lambda t: not _has_whitespace(t) and "max_volume" not in t
    )
    return st.one_of(
        floats.map(repr),
        floats.map(lambda x: f"{x:.1f}"),
        st.sampled_from(_ODD_TOKENS),
        junk,
    )


@st.composite
def _stderr_texts(draw: st.DrawFn) -> tuple[str, str | None]:
    """Stderr built of noise and level lines, and the last level line's token or None."""
    noise = st.text().filter(lambda t: "max_volume" not in t)
    lines: list[str] = []
    last: str | None = None
    for _ in range(draw(st.integers(min_value=0, max_value=6))):
        if draw(st.booleans()):
            lines.append(draw(noise))
            continue
        last = draw(_level_tokens())
        prefix = draw(st.sampled_from(["", _FILTER_PREFIX]))
        lines.append(f"{prefix}max_volume: {last} dB")
    return "\n".join(lines), last


def _expected_peak(token: str | None) -> float | None:
    """The documented contract for a last level token: the floor, a clamp, or None."""
    if token is None:
        return None
    if token == "-inf":
        return ls.FLOOR_DBFS
    try:
        value = float(token)
    except ValueError:
        return None
    return max(value, ls.FLOOR_DBFS) if math.isfinite(value) else None


class ParsePeakPropertyTest(fixtures.IsolatedCase):
    """parse_peak reads the last level line: the floor for -inf, a clamp, None otherwise."""

    @given(_stderr_texts())
    def test_fuzz_parse_peak(self, drawn: tuple[str, str | None]) -> None:
        text, last = drawn
        got = ls.parse_peak(text)
        self.assertTrue(got is None or (math.isfinite(got) and got >= ls.FLOOR_DBFS))
        self.assertEqual(got, _expected_peak(last))


class IsSilentTest(fixtures.IsolatedCase):
    """At or below -60 dBFS is silent; anything above is not."""

    def test_the_threshold(self) -> None:
        self.assertTrue(ls.is_silent(ls.Peak(-60.0)))
        self.assertTrue(ls.is_silent(ls.Peak(ls.FLOOR_DBFS)))
        self.assertFalse(ls.is_silent(ls.Peak(-59.9)))


class MeasureLevelTest(fixtures.IsolatedCase):
    """The runner, through a fake run seam: argv, timeout, each failure."""

    def _run(
        self, *, code: int = 0, stderr: str = "", raises: BaseException | None = None
    ) -> _FakeRun:
        """A fake run seam answering *code* and *stderr*, or raising *raises*."""
        calls: list[tuple[list[str], float]] = []

        def run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            calls.append((argv, timeout))
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(argv, code, "", stderr)

        return run, calls

    def test_the_argument_list_and_the_timeout(self) -> None:
        run, calls = self._run(stderr="max_volume: -91.0 dB")
        level = ls.measure_level(Path("a.mp3"), "/x/ffmpeg", run=run)
        self.assertEqual(level, ls.Peak(-91.0))
        argv = [
            "/x/ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", "a.mp3",
            "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-",
        ]  # fmt: skip
        self.assertEqual(calls, [(argv, ls.SILENCE_TIMEOUT_S)])

    def test_each_failure_names_its_reason(self) -> None:
        cases = (
            (self._run(raises=subprocess.TimeoutExpired("ffmpeg", 60.0)), "timed out"),
            (self._run(raises=FileNotFoundError("ffmpeg")), "ffmpeg did not start"),
            (self._run(code=1), "ffmpeg exit 1"),
            (self._run(stderr="n_samples: 0"), "no level in its answer"),
        )
        for (run, _), reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(
                    ls.measure_level(Path("a.mp3"), "ffmpeg", run=run),
                    ls.MeasureFailed(reason),
                )

    def test_check_label(self) -> None:
        self.assertEqual(ls.check_label("/x/ffmpeg"), "on")
        self.assertEqual(ls.check_label(None), "off (no ffmpeg)")

    def test_find_ffmpeg_reads_path(self) -> None:
        # _REAL_FIND is `ls.find_ffmpeg` captured at this suite's import, before the
        # isolation patch replaces the module attribute for every IsolatedCase.
        with mock.patch.object(shutil, "which", return_value="/x/ffmpeg") as which:
            self.assertEqual(_REAL_FIND(), "/x/ffmpeg")
        which.assert_called_once_with(ls.FFMPEG)


class ChildEnvironTest(fixtures.IsolatedCase):
    """ffmpeg runs without the Genius key: it is the one variable taken out."""

    def test_the_key_is_removed_and_everything_else_kept(self) -> None:
        environ = {
            lc.GENIUS_TOKEN_ENV: "not-a-real-key",
            "PATH": "/invented/bin",
            "LANG": "C",
        }
        self.assertEqual(
            ls.child_environ(environ), {"PATH": "/invented/bin", "LANG": "C"}
        )
        self.assertIn(lc.GENIUS_TOKEN_ENV, environ)

    def test_an_environment_without_the_key_is_kept_whole(self) -> None:
        self.assertEqual(ls.child_environ({"HOME": "/invented"}), {"HOME": "/invented"})
