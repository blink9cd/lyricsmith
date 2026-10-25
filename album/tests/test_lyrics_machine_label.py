"""The machine-transcript label: read from a .lrc's header and carried on its state."""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory as li  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar as lsc  # noqa: E402 - import follows the sys.path bootstrap above

# A registered profile, never an @settings decorator: a fuzzing campaign that re-applies
# its own settings to each property is refused by Hypothesis on a decorated one. The
# name and settings are test_lyrics_model's, so whichever suite loads first, the other's
# properties run under the same profile. deadline=None: a per-example wall-clock
# deadline measures machine load, not correctness. too_slow is suppressed because a
# cold cache's first text draw builds Hypothesis's unicode tables inside that health
# check's window.
settings.register_profile(
    "album_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("album_properties")

MT = lm.Source.MACHINE_TRANSCRIPT
LABEL = "[by:machine transcript]"
BODY = "[00:01.000]A gull on the railing\n"


OTHER = fixtures.synced_lrc("[00:02.000]A lamp swings on the landing\n")


class MachineLabelTest(fixtures.IsolatedCase):
    """machine_label reads one exact line, in the header only."""

    def test_the_exact_by_line_in_the_header_is_the_label(self) -> None:
        self.assertIs(lsc.machine_label(f"[re:x]\n{LABEL}\n{BODY}"), MT)

    def test_surrounding_whitespace_and_crlf_still_match(self) -> None:
        self.assertIs(lsc.machine_label(f"  {LABEL}\t\r\n{BODY}"), MT)

    def test_a_tag_parse_lrc_skips_does_not_end_the_header(self) -> None:
        self.assertIs(lsc.machine_label(f"[length:03:45]\n\n{LABEL}\n{BODY}"), MT)

    def test_an_edited_by_line_is_no_label(self) -> None:
        self.assertIsNone(lsc.machine_label(f"[by:machine transcript, edited]\n{BODY}"))

    def test_the_line_after_the_first_timed_line_is_no_label(self) -> None:
        self.assertIsNone(lsc.machine_label(f"{BODY}{LABEL}\n"))

    def test_no_header_is_no_label(self) -> None:
        self.assertIsNone(lsc.machine_label(BODY))

    def test_a_text_with_no_timed_line_is_no_label(self) -> None:
        self.assertIsNone(lsc.machine_label(f"{LABEL}\nwords with no stamp\n"))

    def test_the_source_is_never_a_chain_step(self) -> None:
        self.assertEqual((MT.value, MT.label), ("machine transcript",) * 2)
        self.assertNotIn(MT, {key.source for key in lf.CHAIN})


#: Header-shaped text: the label, noise, and stamped lines, in any order.
_HEADERISH = st.lists(
    st.one_of(
        st.just(LABEL),
        st.text(max_size=20),
        st.from_regex(r"\[\d{1,2}:\d{2}\.\d{3}\][a-z ]{0,10}", fullmatch=True),
    ),
    max_size=8,
).map("\n".join)


class MachineLabelPropertyTest(fixtures.IsolatedCase):
    """machine_label never raises, and names a label only on a file with timed lines."""

    @given(st.one_of(st.text(max_size=200), _HEADERISH))
    def test_fuzz_machine_label(self, text: str) -> None:
        label = lsc.machine_label(text)
        self.assertIn(label, (None, *lsc.MACHINE_LABELS.values()))
        if label is not None:
            self.assertIsInstance(lm.parse_lrc(text), lm.SyncedLyrics)


class SidecarLabelTest(fixtures.IsolatedCase):
    """The sidecar plan carries the label of a timed .lrc, and of nothing else."""

    def _plan(
        self, *, lrc: str | None = None, txt: str | None = None
    ) -> lm.SidecarState:
        track = fixtures.TrackSpec("01 Track.mp3", seconds=1.0, lrc=lrc, txt=txt)
        fixtures.build_album(self.tmp, (track,))
        return lsc.plan_sidecars(self.tmp / "01 Track.mp3", self.tmp)

    def test_a_labeled_lrc_sets_the_label_and_still_reads_as_synced(self) -> None:
        state = self._plan(lrc=fixtures.MACHINE_LRC, txt=fixtures.LIGHTHOUSE_PLAIN)
        self.assertIs(state.label, MT)
        self.assertEqual(state.lyrics, lm.parse_lrc(fixtures.MACHINE_LRC))
        self.assertEqual(state.removed, ("01 Track.txt",))

    def test_an_unlabeled_lrc_sets_no_label(self) -> None:
        self.assertIsNone(self._plan(lrc=fixtures.LIGHTHOUSE_LRC).label)

    def test_a_txt_sets_no_label(self) -> None:
        self.assertIsNone(self._plan(txt=fixtures.MACHINE_LRC).label)


class SyncedBesidesTest(fixtures.IsolatedCase):
    """Synced frames outrank a transcript only when they hold other lyrics."""

    TRANSCRIPT = fixtures.synced_lrc(fixtures.MACHINE_LRC)

    def test_no_synced_frames_never_outrank(self) -> None:
        self.assertFalse(li.EmbeddedLyrics(None, None).synced_besides(self.TRANSCRIPT))

    def test_frames_holding_the_transcript_do_not_outrank_it(self) -> None:
        frames = li.EmbeddedLyrics(self.TRANSCRIPT, None)
        self.assertFalse(frames.synced_besides(self.TRANSCRIPT))

    def test_frames_holding_other_synced_lyrics_outrank_it(self) -> None:
        frames = li.EmbeddedLyrics(OTHER, None)
        self.assertTrue(frames.synced_besides(self.TRANSCRIPT))
