"""A machine-made timed ``.lrc`` beside untimed frames owes an embed, and is visited.

``sync`` writes a ``.lrc`` labeled ``[by:machine timed]`` beside a track and leaves its
frames as they were; a sweep run is what embeds it. The survey reads the frames, the
state that matters: a track whose sidecar a machine labeled, and whose frames hold no
timed lyrics, is named unembedded and makes its album one to visit, however synced its
sidecar is. Frames already timed, from that sidecar or otherwise, owe nothing. An
unlabeled timed ``.lrc`` owes an embed only beside frames holding no timed lyrics, and is
named ``unlabeled``; beside timed frames of any lines or times it owes nothing. The end
to end cases run the real album tool offline as the child, so the embed it makes is the
one the survey reads after it. Every name, title and lyric here is invented.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path
from unittest import mock

from mutagen.id3 import ID3, SYLT, USLT

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_survey  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard_store import (  # noqa: E402 - follows the sys.path bootstrap
    HeardRecord,
    write_record,
)

# The album tool's test builders, beside its entry point: the pyproject's mypy_path
# names the same folder, so the type checker resolves this import as the runtime does.
sys.path.append(str(sweep.ALBUM_ENTRY.parent / "tests"))

import fixtures as album_fixtures  # noqa: E402 - import follows the sys.path insert above

#: The invented words `album_fixtures.MACHINE_TIMED_LRC` times, as untimed frames.
_UNTIMED_WORDS = "A gull on the railing\ncounts the boats coming in\n"
#: Invented synced lines other than the machine-timed file's, in milliseconds.
_OTHER_SYNCED = [("The ferry horn at noon", 1000), ("and nobody aboard", 4000)]
#: A machine-timed ``.lrc`` whose one line is an instrumental placeholder, a note glyph.
_PLACEHOLDER_TIMED_LRC = "[by:machine timed]\n[00:01.000]♪\n"
#: A SYLT frame's placeholder line, the same note glyph, in milliseconds.
_PLACEHOLDER_SYNCED = [("♪", 1000)]
#: An unlabeled timed ``.lrc`` holding `_OTHER_SYNCED` exactly, as a ``.lrc`` holds it.
_UNLABELED_LRC = "[00:01.000]The ferry horn at noon\n[00:04.000]and nobody aboard\n"
#: An unlabeled timed ``.lrc`` whose one line is the placeholder note glyph.
_UNLABELED_PLACEHOLDER_LRC = "[00:01.000]♪\n"
#: The SYLT timestamp format in milliseconds, as ID3 numbers it.
_MILLISECONDS = 2
#: The SYLT content type for lyrics, as ID3 numbers it.
_LYRICS_TYPE = 1


def _track(album: Path, name: str, *, lrc: str | None = None) -> Path:
    """A silent invented MP3 named *name* under *album*, with any ``.lrc`` beside it."""
    wanted = album_fixtures.TrackSpec(name, title=name.removesuffix(".mp3"), lrc=lrc)
    (mp3,) = album_fixtures.build_album(album, (wanted,))
    return mp3


def _add_frame(mp3: Path, frame: USLT | SYLT) -> None:
    """Add *frame* to *mp3*'s tag."""
    tags = ID3(mp3)
    tags.add(frame)
    tags.save(mp3)


def _untimed_frames(mp3: Path) -> None:
    """Give *mp3* a USLT of the words its machine-timed file times."""
    _add_frame(mp3, USLT(encoding=3, lang="eng", desc="", text=_UNTIMED_WORDS))


def _synced_frames(mp3: Path, lines: list[tuple[str, int]]) -> None:
    """Give *mp3* a millisecond SYLT holding *lines*."""
    sylt = SYLT(
        encoding=3,
        lang="eng",
        format=_MILLISECONDS,
        type=_LYRICS_TYPE,
        desc="",
        text=lines,
    )
    _add_frame(mp3, sylt)


def _run_sweep(case: sf.IsolatedCase, *args: str) -> tuple[int, str]:
    """Run the sweep's entry point on *case*'s library, with the real album tool child.

    Returns its exit code and what it printed.
    """
    # The child inherits this environment: keep the host's ffmpeg out of it.
    case.enterContext(mock.patch.dict(os.environ, sf.env_without_ffmpeg(case.tmp)))
    stdout = io.StringIO()
    with (
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        code = sweep.main([*args, "--library", str(case.library)])
    return code, stdout.getvalue()


class UnembeddedSurveyTest(sf.IsolatedCase):
    """`survey_album` names a machine-timed sidecar its frames have not taken."""

    def setUp(self) -> None:
        """An album folder to build tracks in."""
        super().setUp()
        self.album = self.library / "Invented Artist" / "Timed By Machine"

    def _survey(self) -> sweep_survey.AlbumSurvey:
        return sweep_survey.survey_album(self.album, lang="eng")

    def test_machine_timed_beside_untimed_frames_needs_a_visit(self) -> None:
        mp3 = _track(self.album, "01 Gull.mp3", lrc=album_fixtures.MACHINE_TIMED_LRC)
        _untimed_frames(mp3)
        survey = self._survey()
        self.assertEqual(survey.unembedded, ("01 Gull.mp3 (machine timed)",))
        self.assertEqual((survey.synced, survey.untimed, survey.none), (1, 0, 0))
        self.assertTrue(survey.needs_visit)

    def test_machine_timed_beside_no_frames_needs_a_visit(self) -> None:
        _track(self.album, "01 Gull.mp3", lrc=album_fixtures.MACHINE_TIMED_LRC)
        self.assertEqual(self._survey().unembedded, ("01 Gull.mp3 (machine timed)",))

    def test_a_machine_transcript_is_named_by_its_own_label(self) -> None:
        _track(self.album, "01 Gull.mp3", lrc=album_fixtures.MACHINE_LRC)
        self.assertEqual(
            self._survey().unembedded, ("01 Gull.mp3 (machine transcript)",)
        )

    def test_frames_timed_from_the_sidecar_owe_nothing(self) -> None:
        mp3 = _track(self.album, "01 Gull.mp3", lrc=album_fixtures.MACHINE_TIMED_LRC)
        _synced_frames(
            mp3, [("A gull on the railing", 1000), ("counts the boats coming in", 4000)]
        )
        survey = self._survey()
        self.assertEqual(survey.unembedded, ())
        self.assertFalse(survey.needs_visit)

    def test_frames_holding_its_lines_at_other_times_need_a_visit(self) -> None:
        # The file was timed again since its embed: the album tool embeds it again.
        mp3 = _track(self.album, "01 Gull.mp3", lrc=album_fixtures.MACHINE_TIMED_LRC)
        _synced_frames(
            mp3, [("A gull on the railing", 1500), ("counts the boats coming in", 4500)]
        )
        self.assertEqual(self._survey().unembedded, ("01 Gull.mp3 (machine timed)",))

    def test_frames_holding_other_synced_lyrics_owe_nothing(self) -> None:
        # The album tool serves such a track from its frames and embeds nothing.
        mp3 = _track(self.album, "01 Gull.mp3", lrc=album_fixtures.MACHINE_TIMED_LRC)
        _synced_frames(mp3, _OTHER_SYNCED)
        self.assertFalse(self._survey().needs_visit)

    def test_a_placeholder_machine_timed_sidecar_owes_nothing(self) -> None:
        # The album tool's fetch sets a placeholder sidecar aside and embeds nothing.
        mp3 = _track(self.album, "01 Gull.mp3", lrc=_PLACEHOLDER_TIMED_LRC)
        _untimed_frames(mp3)
        self.assertEqual(self._survey().unembedded, ())

    def test_a_placeholder_synced_frame_does_not_hold_the_sidecar(self) -> None:
        # A placeholder frame serves nothing, so the album tool embeds the file over it.
        mp3 = _track(self.album, "01 Gull.mp3", lrc=album_fixtures.MACHINE_TIMED_LRC)
        _synced_frames(mp3, _PLACEHOLDER_SYNCED)
        self.assertEqual(self._survey().unembedded, ("01 Gull.mp3 (machine timed)",))


class UnlabeledSurveyTest(sf.IsolatedCase):
    """An unlabeled timed sidecar is owed only beside frames holding no timed lyrics.

    The survey's narrow rule: frames holding timed lyrics keep serving, whatever
    lines or times they hold, and such a track is not visited.
    """

    def setUp(self) -> None:
        """An album folder to build tracks in."""
        super().setUp()
        self.album = self.library / "Invented Artist" / "Timed By Hand"

    def _survey(self) -> sweep_survey.AlbumSurvey:
        return sweep_survey.survey_album(self.album, lang="eng")

    def test_beside_untimed_frames_it_needs_a_visit(self) -> None:
        mp3 = _track(self.album, "01 Ferry.mp3", lrc=_UNLABELED_LRC)
        _untimed_frames(mp3)
        survey = self._survey()
        self.assertEqual(survey.unembedded, ("01 Ferry.mp3 (unlabeled)",))
        self.assertEqual((survey.synced, survey.untimed, survey.none), (1, 0, 0))
        self.assertTrue(survey.needs_visit)

    def test_beside_no_frames_it_needs_a_visit(self) -> None:
        _track(self.album, "01 Keeper.mp3", lrc=album_fixtures.LIGHTHOUSE_LRC)
        self.assertEqual(self._survey().unembedded, ("01 Keeper.mp3 (unlabeled)",))

    def test_beside_a_placeholder_timed_frame_only_it_needs_a_visit(self) -> None:
        mp3 = _track(self.album, "01 Ferry.mp3", lrc=_UNLABELED_LRC)
        _synced_frames(mp3, _PLACEHOLDER_SYNCED)
        self.assertEqual(self._survey().unembedded, ("01 Ferry.mp3 (unlabeled)",))

    def test_beside_other_timed_frames_it_owes_nothing(self) -> None:
        # The frames keep serving: the survey leaves them, by the narrow rule.
        mp3 = _track(self.album, "01 Keeper.mp3", lrc=album_fixtures.LIGHTHOUSE_LRC)
        _synced_frames(mp3, _OTHER_SYNCED)
        survey = self._survey()
        self.assertEqual((survey.synced, survey.unembedded), (1, ()))
        self.assertFalse(survey.needs_visit)

    def test_beside_its_lines_at_other_times_it_owes_nothing(self) -> None:
        mp3 = _track(self.album, "01 Ferry.mp3", lrc=_UNLABELED_LRC)
        _synced_frames(mp3, [(text, ms + 500) for text, ms in _OTHER_SYNCED])
        self.assertFalse(self._survey().needs_visit)

    def test_beside_frames_holding_it_it_owes_nothing(self) -> None:
        mp3 = _track(self.album, "01 Ferry.mp3", lrc=_UNLABELED_LRC)
        _synced_frames(mp3, _OTHER_SYNCED)
        self.assertFalse(self._survey().needs_visit)

    def test_an_unlabeled_placeholder_sidecar_owes_nothing(self) -> None:
        mp3 = _track(self.album, "01 Ferry.mp3", lrc=_UNLABELED_PLACEHOLDER_LRC)
        _untimed_frames(mp3)
        self.assertEqual(self._survey().unembedded, ())


class UnembeddedRunTest(sf.IsolatedCase):
    """An offline run visits the album, the real album tool embeds, and it is settled."""

    def setUp(self) -> None:
        """An album whose one owed track is machine-timed over untimed frames.

        Its other track's frames are already synced, so it owes nothing.
        """
        super().setUp()
        self.album = self.library / "Invented Artist" / "Timed By Machine"
        _synced_frames(_track(self.album, "01 Ferry.mp3"), _OTHER_SYNCED)
        self.owed = _track(
            self.album, "02 Gull.mp3", lrc=album_fixtures.MACHINE_TIMED_LRC
        )
        _untimed_frames(self.owed)

    def _main(self, *args: str) -> tuple[int, str]:
        return _run_sweep(self, *args)

    def test_the_survey_lists_it_and_names_the_track(self) -> None:
        code, survey = self._main("--mode", "survey")
        self.assertEqual(code, 0)
        self.assertIn(
            "unembedded: Invented Artist/Timed By Machine/02 Gull.mp3 (machine timed)",
            survey,
        )
        self.assertIn("albums a run would visit: 1", survey)

    def test_an_offline_run_embeds_it_and_then_it_owes_nothing(self) -> None:
        listed = self.tmp / "changed.txt"
        code, _ = self._main(
            "--mode", "run", "--offline", "--changed-list", str(listed)
        )
        self.assertEqual(code, 0)
        self.assertEqual(listed.read_text("utf-8").splitlines(), [str(self.owed)])
        survey = sweep_survey.survey_album(self.album, lang="eng")
        self.assertEqual(survey.unembedded, ())
        self.assertFalse(survey.needs_visit)
        code, report = self._main("--mode", "survey")
        self.assertIn("albums a run would visit: 0", report)


class UnlabeledRunTest(sf.IsolatedCase):
    """An unlabeled timed sidecar beside untimed frames: a run embeds it, and it settles."""

    def setUp(self) -> None:
        """An album whose one track holds an unlabeled timed sidecar and untimed frames."""
        super().setUp()
        self.album = self.library / "Invented Artist" / "Timed By Hand"
        self.owed = _track(self.album, "01 Ferry.mp3", lrc=_UNLABELED_LRC)
        _untimed_frames(self.owed)

    def test_the_survey_names_it_unlabeled(self) -> None:
        code, survey = _run_sweep(self, "--mode", "survey")
        self.assertEqual(code, 0)
        self.assertIn(
            "unembedded: Invented Artist/Timed By Hand/01 Ferry.mp3 (unlabeled)",
            survey,
        )
        self.assertIn("albums a run would visit: 1", survey)

    def test_an_offline_run_embeds_it_and_then_it_owes_nothing(self) -> None:
        listed = self.tmp / "changed.txt"
        code, _ = _run_sweep(
            self, "--mode", "run", "--offline", "--changed-list", str(listed)
        )
        self.assertEqual(code, 0)
        self.assertEqual(listed.read_text("utf-8").splitlines(), [str(self.owed)])
        code, report = _run_sweep(self, "--mode", "survey")
        self.assertIn("albums a run would visit: 0", report)


class UnembeddedFixPassTest(sf.IsolatedCase):
    """The fix pass owes no embed: an unembedded track alone visits nothing there.

    ``album --mode fix-text`` passes over a track holding machine-timed lyrics and
    embeds nothing, so a visit for the embed belongs to the run pass alone.
    """

    def test_a_record_beside_only_an_unembedded_track_visits_nothing(self) -> None:
        album = self.library / "Invented Artist" / "Timed By Machine"
        track = sf.add_track(album, "01 Gull.mp3", lrc=album_fixtures.MACHINE_TIMED_LRC)
        heard = self.tmp / "heard"
        heard.mkdir()
        write_record(heard, HeardRecord(track, "ab" * 32, "an invented transcript"))
        fake = sf.FakeAlbumTool()
        argv = ["--mode", "fix-text", "--library", str(self.library)]
        argv += ["--heard-dir", str(heard)]
        wiring = sweep.Wiring(fake, sf.FakeSleep(), sf.fixed_now)
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = sweep.main(argv, wiring=wiring)
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls, [])
