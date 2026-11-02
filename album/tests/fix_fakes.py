"""Invented songs and the case `--mode fix-text`'s end-to-end suites share.

Each song holds well over `lyrics_heard.MIN_HEARD_PAIRS` distinct word pairs, so a
transcript of it can judge a text, and no two share a pair, so each is another song's
wrong text. A transcript here is the song's own words, as whisper-cli would hear them
over a clean vocal. `FixCase` drives the entry point with its real wiring; only the clock,
the provider table and the transcript store's folder are the test's, and every title and
lyric is invented.

Not discovered as a suite (no ``test_`` prefix).
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard_store as store  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

MODE = ("--mode", "fix-text")
PLAIN_KEY = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED)
SYNCED_KEY = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.SYNCED)

#: Four invented songs, each its own words.
HARBOR = """Down by the harbor where the gray boats sleep
the rope man whistles and the gulls dive deep,
he counts the barrels and he folds his maps
and waits for evening with his woolen caps.
"""
MEADOW = """Over the meadow runs a crooked fence
built by a farmer with uncommon sense,
it leans to windward and it bends to rain
and never once has it been mended again.
"""
FOUNDRY = """In the old foundry where the hammers ring
the iron glows and the young smiths sing,
they pour the morning into molds of sand
and shape the river with a steady hand.
"""
QUARRY = """Up at the quarry on a chalky shelf
a stonecutter mutters to himself,
he splits the marble with a single blow
and sends it rolling to the town below.
"""
#: A transcript too short to judge anything: under twenty distinct pairs.
LITTLE = "the rope man whistles\n"

#: The titles the tracks carry, each song's own.
TITLES = {
    HARBOR: "Harbor Song",
    MEADOW: "Meadow Fence",
    FOUNDRY: "Foundry Morning",
    QUARRY: "Quarry Shelf",
}


def words(text: str) -> list[str]:
    """*text*'s words, as the judge and the fingerprint read them."""
    return lyrics_heard.words_of_text(text)


def song_track(
    index: int, song: str, *, txt: str | None, lrc: str | None = None
) -> TrackSpec:
    """Track *index* titled for *song*, holding *txt* (and *lrc*) beside it."""
    name = f"0{index} {TITLES[song].split()[0]}.mp3"
    return TrackSpec(name, title=TITLES[song], txt=txt, lrc=lrc)


def sung_neighbor() -> TrackSpec:
    """A second track holding its own words, with no record.

    Beside one track heard not sung it makes that track half the album, no majority,
    so the album does not look spoken (`lyrics_spoken`) and a removal goes ahead.
    """
    return song_track(9, MEADOW, txt=MEADOW)


class FixCase(cli_fakes.RealWiringCliCase):
    """The real wiring, a transcript store under the test's folder, a settable clock."""

    def setUp(self) -> None:
        """No album yet, an empty store, and a table that answers nothing."""
        super().setUp()
        self.heard = self.tmp / "heard"
        self.started = run_fakes.STARTED
        self.answer({})

    def fixed_now(self) -> dt.datetime:
        """The clock as this test last set it, so a second run gets its own id."""
        return self.started

    def answer(self, plain: Mapping[str, str | Exception]) -> None:
        """Answer each title in *plain* untimed, from LRCLIB; nothing timed."""
        self.titles = {
            SYNCED_KEY: run_fakes.TitleProvider({}),
            PLAIN_KEY: run_fakes.TitleProvider(plain),
        }
        self.providers = self.titles

    def asked(self) -> list[str]:
        """Every title the untimed provider was asked for, in order."""
        return [query.title for query in self.titles[PLAIN_KEY].calls]

    def build(self, *tracks: TrackSpec) -> list[Path]:
        """Build an album of *tracks*."""
        return fixtures.build_album(self.album, tracks)

    def hear(self, path: Path, sung: str, *, held: str | None = None) -> None:
        """Keep *path*'s record: the transcript *sung*, judged against *held*.

        *held* is the text the track holds now, read from its ``.txt`` when None.
        """
        text = held if held is not None else path.with_suffix(".txt").read_text("utf-8")
        record = store.HeardRecord(path, lyrics_heard.text_fingerprint(text), sung)
        store.write_record(self.heard, record)

    def record_of(self, path: Path) -> store.HeardRecord | None:
        """*path*'s record in the store, None when there is none."""
        return store.read_record(self.heard, path)

    def fix(self, *extra: str) -> cli_fakes.Result:
        """Run the fix over this test's album and store, with *extra* options."""
        return self.run_album(*MODE, "--heard-dir", str(self.heard), *extra)

    def newest(self) -> list[lr.ManifestTrack]:
        """The newest run's tracks, as its manifest on disk records them."""
        run_dir = lyrics_backup.runs_newest_first(self.album)[0]
        return list(lyrics_manifest.read_manifest(run_dir).tracks)

    def outcome(self, name: str) -> lr.TrackOutcome:
        """The newest run's outcome for the track named *name*."""
        (track,) = [t for t in self.newest() if t.path == name]
        assert track.after is not None
        return track.after

    def text_of(self, path: Path) -> list[str]:
        """The words of *path*'s ``.txt`` now."""
        return words(path.with_suffix(".txt").read_text("utf-8"))

    def later(self, minutes: int) -> None:
        """Move the clock on *minutes*, so the next run gets a run id of its own."""
        self.started = run_fakes.STARTED + dt.timedelta(minutes=minutes)
