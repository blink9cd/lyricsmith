"""A sidecar is matched against every worded frame of its kind, not the best-ranked one.

`lyrics_inventory.embedded_lyrics` keeps each worded frame's lyrics as a candidate, in
the order `lyrics_from_frames` ranks them, so a sidecar a run wrote under one ``--lang``
still matches its frames when a later run ranks another language first.
`EmbeddedLyrics.serving` sets such a sidecar aside, `EmbeddedLyrics.favoring` puts the
frames it holds first of their kind, and the fetch serves those frames. A sidecar
matching no candidate still serves. Every lyric here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory as li  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above

#: Invented lines.
_ONE, _TWO = "Chalk birds fly off the pavement", "when the street sweeper sings"
#: A query no provider is asked about: the fetches here are served by what is held.
_QUERY = lm.TrackQuery("Invented Artist", "Invented Title")


class CandidatesTest(fixtures.IsolatedCase):
    """Every worded frame is a candidate, ranked as `lyrics_from_frames` ranks it."""

    def test_every_worded_frame_is_kept_best_ranked_first_in_each_kind(self) -> None:
        fra = tb.sylt((1000, _ONE), lang="fra")
        deu = tb.sylt((1000, _TWO), lang="deu")
        mpeg = tb.sylt((1000, _TWO), time_format=tb.MPEG_FRAMES)
        uslt = tb.uslt(_ONE, lang="deu")
        blank = tb.uslt("  ", lang="deu")
        held = li.embedded_lyrics((fra, mpeg, blank, deu, uslt), "deu")
        self.assertEqual(
            held.candidates,
            (
                fixtures.synced_lines((1000, _TWO)),
                fixtures.synced_lines((1000, _ONE)),
                lm.PlainLyrics(_ONE),
                lm.PlainLyrics(_TWO),
            ),
        )
        self.assertEqual(
            (held.synced, held.untimed),
            (fixtures.synced_lines((1000, _TWO)), lm.PlainLyrics(_ONE)),
        )

    def test_an_mpeg_frame_sylt_is_a_candidate_for_a_txt_never_a_lrc(self) -> None:
        held = li.embedded_lyrics(
            (tb.sylt((1000, _ONE), time_format=tb.MPEG_FRAMES),), "eng"
        )
        lrc = fixtures.synced_lines((1000, _ONE))
        self.assertIs(held.serving(lrc), lrc)
        self.assertIsNone(held.serving(lm.PlainLyrics(_ONE)))


class FavoringTest(fixtures.IsolatedCase):
    """A sidecar matching a lower-ranked candidate gives way to that frame."""

    def test_a_lrc_matching_the_second_candidate_is_found(self) -> None:
        deu, fra = (
            fixtures.synced_lines((1000, _TWO)),
            fixtures.synced_lines((1000, f"\n{_ONE}")),
        )
        embedded = li.EmbeddedLyrics(deu, None, (deu, fra))
        lrc = fixtures.synced_lines((1000, _ONE))
        self.assertIsNone(embedded.serving(lrc))
        self.assertEqual(
            embedded.favoring(lrc), li.EmbeddedLyrics(fra, None, (deu, fra))
        )

    def test_a_txt_matching_the_second_candidate_is_found(self) -> None:
        deu, fra = lm.PlainLyrics(_TWO), lm.PlainLyrics(f"{_ONE}\r\n")
        embedded = li.EmbeddedLyrics(None, deu, (deu, fra))
        txt = lm.PlainLyrics(f"{_ONE}\n")
        self.assertIsNone(embedded.serving(txt))
        self.assertEqual(
            embedded.favoring(txt), li.EmbeddedLyrics(None, fra, (deu, fra))
        )

    def test_a_sidecar_matching_no_candidate_still_serves(self) -> None:
        synced = (
            fixtures.synced_lines((1000, _ONE)),
            fixtures.synced_lines((1000, _TWO)),
        )
        plain = (lm.PlainLyrics(_ONE), lm.PlainLyrics(_TWO))
        embedded = li.EmbeddedLyrics(synced[0], plain[0], synced + plain)
        for other in (
            fixtures.synced_lines((2000, _ONE)),
            lm.PlainLyrics("A third invented line"),
        ):
            with self.subTest(kind=other.kind):
                self.assertIs(embedded.serving(other), other)
                self.assertIs(embedded.favoring(other), embedded)
        self.assertIs(embedded.favoring(None), embedded)


class FetchServesTheMatchingFrameTest(fixtures.IsolatedCase):
    """The fetch serves the frame the sidecar holds, not the best-ranked one."""

    def test_a_lrc_of_a_lower_ranked_sylt_serves_that_sylt(self) -> None:
        deu, fra = (
            fixtures.synced_lines((1000, _TWO)),
            fixtures.synced_lines((1000, _ONE)),
        )
        embedded = li.EmbeddedLyrics(deu, None, (deu, fra))
        sidecar = lm.SidecarState(fra)
        result = lf.fetch_lyrics(_QUERY, sidecar, {}, deadline_s=1.0, embedded=embedded)
        self.assertEqual((result.lyrics, result.source), (fra, lm.Source.FRAMES))

    def test_a_txt_of_a_lower_ranked_uslt_serves_that_uslt(self) -> None:
        deu, fra = lm.PlainLyrics(_TWO), lm.PlainLyrics(_ONE)
        embedded = li.EmbeddedLyrics(None, deu, (deu, fra))
        sidecar = lm.SidecarState(fra)
        result = lf.fetch_lyrics(_QUERY, sidecar, {}, deadline_s=1.0, embedded=embedded)
        self.assertEqual((result.lyrics, result.source), (fra, lm.Source.FRAMES))
