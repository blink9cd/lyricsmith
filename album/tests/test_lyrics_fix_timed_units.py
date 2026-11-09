"""The timed branches of a fix's reading and drawing, one function at a time.

`lyrics_fix_text.text_of` reads the untimed text alone first, as the untimed fix does,
else a timed ``.lrc``'s, keeping its machine label so `fix_skip_reason` can leave a
machine-made one alone. `sibling_texts` keeps an album's timed texts apart, as their words
only, and `SiblingTexts.every_besides` gives a timed track both kinds.
`lyrics_compare.timed_answers_in_turn` draws the whole chain, its synced steps first.
Every lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_compare as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fix_text as lft  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard_store as store  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_provenance  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    HARBOR,
    MEADOW,
    QUARRY,
    song_track,
    words,
)
from timed_fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    LANG,
    MACHINE_HEADER,
    embed,
    lrc_of,
    timed_track,
)

#: The source no run recorded, as `sibling_texts` reads an album with no runs.
_NO_RUNS = lyrics_provenance.SourceRecords({})


class _AlbumCase(fixtures.IsolatedCase):
    """An album under the test's folder, read with the real stages."""

    def setUp(self) -> None:
        """The real stages, and the album folder."""
        super().setUp()
        self.stages = album.real_wiring().stages
        self.album = self.tmp / "album"

    def text_of(self, path: Path) -> lft.StoredText:
        """What a fix reads *path* as."""
        tags = self.stages.read_tags(path)
        return lft.text_of(self.stages, self.album, path, tags, LANG)


class TextOfTest(_AlbumCase):
    """The untimed text alone first, else a timed ``.lrc``'s with its label."""

    def test_an_untimed_text_is_read_as_entry_84_reads_it(self) -> None:
        (path,) = fixtures.build_album(self.album, (song_track(1, HARBOR, txt=QUARRY),))
        stored = self.text_of(path)
        self.assertEqual(stored.lyrics, lm.PlainLyrics(QUARRY))
        self.assertFalse(stored.timed)

    def test_a_provider_timed_lrc_is_read_with_no_label(self) -> None:
        (path,) = fixtures.build_album(self.album, (timed_track(1, HARBOR, QUARRY),))
        embed(path)
        stored = self.text_of(path)
        self.assertTrue(stored.timed)
        self.assertIsNone(stored.label)
        assert stored.lyrics is not None
        self.assertEqual(words(stored.lyrics.plain_text()), words(QUARRY))

    def test_a_machine_timed_lrc_keeps_its_label(self) -> None:
        machine = timed_track(1, HARBOR, QUARRY, label=MACHINE_HEADER)
        (path,) = fixtures.build_album(self.album, (machine,))
        stored = self.text_of(path)
        self.assertIs(stored.label, lm.Source.MACHINE_TIMED)

    def test_a_track_with_no_lyrics_holds_none(self) -> None:
        (path,) = fixtures.build_album(self.album, (song_track(1, HARBOR, txt=None),))
        self.assertEqual(self.text_of(path), lft.StoredText(None))


class FixSkipReasonTest(fixtures.IsolatedCase):
    """A machine-made timed text is never the fix's; the rest as `skip_reason` says."""

    def record(self, held: str) -> store.HeardRecord:
        """A record of the harbor song, judged against *held*."""
        fingerprint = lyrics_heard.text_fingerprint(held)
        return store.HeardRecord(Path("/invented/01.mp3"), fingerprint, HARBOR)

    def test_a_machine_label_is_skipped_whatever_the_text(self) -> None:
        stored = lft.StoredText(lm.parse_lrc(lrc_of(QUARRY)), lm.Source.MACHINE_TIMED)
        reason = lft.fix_skip_reason(stored, self.record(QUARRY))
        self.assertEqual(reason, lft.SKIP_MACHINE_TIMED)

    def test_a_provider_timed_wrong_text_is_taken(self) -> None:
        stored = lft.StoredText(lm.parse_lrc(lrc_of(QUARRY)))
        self.assertIsNone(lft.fix_skip_reason(stored, self.record(QUARRY)))

    def test_no_text_is_not_untimed_only(self) -> None:
        reason = lft.fix_skip_reason(lft.StoredText(None), self.record(QUARRY))
        self.assertEqual(reason, lft.SKIP_NOT_UNTIMED)


class SiblingTextsTest(_AlbumCase):
    """Timed texts are kept apart, as their words; a timed track draws both kinds."""

    def test_a_timed_sibling_gives_its_words_untimed(self) -> None:
        paths = fixtures.build_album(
            self.album,
            (song_track(1, HARBOR, txt=QUARRY), timed_track(2, MEADOW, HARBOR)),
        )
        siblings = lft.sibling_texts(self.stages, self.album, paths, LANG, _NO_RUNS)
        self.assertEqual([c.origin for c in siblings.texts], [paths[0].name])
        (timed,) = siblings.timed
        self.assertIsInstance(timed.lyrics, lm.PlainLyrics)
        self.assertEqual(words(timed.lyrics.plain_text()), words(HARBOR))
        # The blank stamped line stays a blank line in the words written.
        self.assertIn("\n\n", timed.lyrics.plain_text())
        self.assertIs(timed.source, lft.UNRECORDED_SIBLING)
        self.assertEqual(siblings.besides(paths[1].name), siblings.texts)
        every = siblings.every_besides(paths[0].name)
        self.assertEqual([c.origin for c in every], [paths[1].name])
        both = siblings.every_besides("elsewhere.mp3")
        self.assertEqual([c.origin for c in both], [p.name for p in paths])


class TimedDrawTest(fixtures.IsolatedCase):
    """A timed text's draw asks the chain's synced steps, then its untimed ones."""

    def test_the_whole_chain_is_drawn_synced_steps_first(self) -> None:
        drawn: list[lm.ProviderKey] = []

        def step(key: lm.ProviderKey) -> lm.Provider:
            def ask(query: lm.TrackQuery) -> str | None:
                del query
                drawn.append(key)
                return None

            return ask

        table = {key: step(key) for key in lyrics_fetch.CHAIN}
        query = lm.TrackQuery("Invented Artist", "Harbor Song")
        asked = list(lc.timed_answers_in_turn(query, table, run_fakes.DEADLINE_S))
        self.assertEqual(len(asked), len(lyrics_fetch.CHAIN))
        self.assertEqual(set(drawn), set(lyrics_fetch.CHAIN))
        kinds = [key.kind for key in dict.fromkeys(drawn)]
        synced = kinds.count(lm.LyricsKind.SYNCED)
        self.assertEqual(set(kinds[:synced]), {lm.LyricsKind.SYNCED})
        self.assertEqual(set(kinds[synced:]), {lm.LyricsKind.UNSYNCED})
