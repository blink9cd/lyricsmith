"""The entry point's options, its exit codes, and how it binds the real stages.

Options are parsed through `build_parser` and refused through `main`, so a refusal is
seen as the exit code an operator gets. Exit codes are driven end to end over built
albums with the run folder and providers faked. The binding test checks each stage is
the real function itself, every one of them, so a stage bound to the wrong function, or
to none, fails here as well as under the type checker.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import help_formatter  # noqa: E402 - reachable through the entry point's bootstrap
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_check  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_frames  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_help  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_options  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_silence  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_strip  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_undo  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import run_lock  # noqa: E402 - reachable through the entry point's bootstrap

TrackSpec = fixtures.TrackSpec


class ParserTest(fixtures.IsolatedCase):
    """Every option is named and parses, with its defaults and help text."""

    def test_every_run_option_parses(self) -> None:
        args = album.build_parser().parse_args(
            [
                "--album",
                "a",
                "--lang",
                "deu",
                "--config",
                "c.toml",
                "--dry-run",
                "--offline",
                "--no-genius",
                "--fresh",
            ]
        )
        self.assertEqual(
            (
                args.album,
                args.mode,
                args.lang,
                args.config,
                args.dry_run,
                args.offline,
                args.no_genius,
                args.fresh,
            ),
            (Path("a"), "run", "deu", Path("c.toml"), True, True, True, True),
        )

    def test_the_undo_and_check_options_parse(self) -> None:
        args = album.build_parser().parse_args(
            ["--album", "a", "--mode", "undo", "--track", "t.mp3", "--run-id", "r"]
        )
        self.assertEqual(
            (args.mode, args.track, args.run_id), ("undo", Path("t.mp3"), "r")
        )

    def test_the_defaults(self) -> None:
        args = album.build_parser().parse_args(["--album", "a"])
        self.assertEqual(
            (args.mode, args.track, args.run_id, args.lang, args.config),
            ("run", None, None, None, None),
        )
        self.assertEqual(
            (args.dry_run, args.offline, args.no_genius, args.fresh),
            (False, False, False, False),
        )

    def test_the_help_states_the_effective_language_and_no_none_default(self) -> None:
        text = " ".join(album.build_parser().format_help().split())
        self.assertIn(f"{album.DEFAULT_LANG} when omitted", text)
        self.assertNotIn("(default: None)", text)

    def test_the_help_shows_defaults_prerequisites_and_exit_codes(self) -> None:
        text = album.build_parser().format_help()
        for part in (
            "(default: run)",
            "Prerequisites:",
            "Exit codes:",
            "GENIUS_ACCESS_TOKEN",
        ):
            self.assertIn(part, text)

    def test_the_help_says_the_config_file_holds_the_genre_list(self) -> None:
        text = " ".join(album.build_parser().format_help().split())
        for part in (
            "a config file holding the Genius key, the [no_lyrics] genre list and the "
            "[musicbrainz] contact, the only place any is read, under --offline "
            "and --no-genius too",
            "The same file's [no_lyrics] genres array replaces the built-in "
            "no-lyrics genre list.",
        ):
            self.assertIn(part, text)

    def test_the_epilog_names_prune_beside_undo(self) -> None:
        text = " ".join(album.build_parser().format_help().split())
        self.assertIn(
            "--mode prune removes the run folders nothing needs: a run whose every "
            "track is undone, with nothing set aside, and a backup that never "
            "finished; --dry-run first shows which.",
            text,
        )

    def test_the_exit_codes_name_a_failed_read_back_and_an_interrupt(self) -> None:
        text = " ".join(album.build_parser().format_help().split())
        for part in (
            "a manifest write that did not read back as written",
            "ends a run or an undo after its report)",
            "130 interrupted (Ctrl-C); once the backup is done, the manifest "
            "records the tracks that finished",
            "or, in a prune, a run folder whose rename or removal failed",
            "a prune or an accept on a Python whose shutil.rmtree does not protect "
            "against links",
            "or, in an accept, the removal of .lyricsmith",
            "an accept refused (an unfinished run, an unreadable manifest or run "
            "folder, an entry "
            "lyricsmith did not write, a link), nothing removed, or whose records "
            "could not be written, nothing removed but an earlier accept's leftover",
            "or, once a run, a strip, an undo, a prune or an accept began, its report",
        ):
            self.assertIn(part, text)

    def test_the_epilog_lists_exactly_the_exit_constants(self) -> None:
        listed = set(re.findall(r"^  (\d+) ", lyrics_help.EPILOG, flags=re.MULTILINE))
        constants = {
            str(value)
            for name, value in vars(album).items()
            if name.startswith("EXIT_") and isinstance(value, int)
        }
        self.assertEqual(listed, constants)

    def test_the_exit_codes_name_a_record_or_list_not_written(self) -> None:
        text = " ".join(album.build_parser().format_help().split())
        self.assertIn(
            f"{album.EXIT_RECORD_NOT_WRITTEN} the work finished, or stopped "
            "part-way, but the run's record (its manifest) or the --changed-list "
            "file could not be written",
            text,
        )

    def test_the_parser_uses_the_shared_formatter(self) -> None:
        # A binding, not a rendering: argparse on Python 3.13+ already drops the bogus
        # required-option default note, so only the class shows the shim. The help
        # module keeps no formatter of its own.
        self.assertIs(
            album.build_parser().formatter_class, help_formatter.RawNoneHidingFormatter
        )
        self.assertFalse(hasattr(lyrics_help, "HelpFormatter"))


class RefusalTest(cli_fakes.CliCase):
    """Options a mode cannot use, and malformed values, exit 2 before anything runs."""

    def test_each_refused_combination_exits_2(self) -> None:
        cases = {
            "--run-id in run": ("--run-id", "r"),
            "--dry-run in check": ("--mode", "check", "--dry-run"),
            "--offline in undo": ("--mode", "undo", "--offline"),
            "--config in check": ("--mode", "check", "--config", "c.toml"),
            "--no-genius in undo": ("--mode", "undo", "--no-genius"),
            "--lang in check": ("--mode", "check", "--lang", "deu"),
            "--lang in undo": ("--mode", "undo", "--lang", "eng"),
            "a malformed --lang": ("--lang", "english"),
            "an unknown --mode": ("--mode", "all"),
        }
        for name, extra in cases.items():
            with self.subTest(name):
                result = self.main("--album", str(self.tmp), *extra)
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertEqual(result.stdout, "")
                # Refused by the parser, not by a mode that found no run to read.
                self.assertIn("error:", result.stderr)
        self.assertEqual(self.folder.backed_up, [])

    def test_track_is_accepted_by_run_and_strip(self) -> None:
        for mode in ("run", "strip"):
            with self.subTest(mode):
                args = album.build_parser().parse_args(
                    ["--album", "a", "--mode", mode, "--track", "t.mp3"]
                )
                # Returns, rather than exiting through the parser.
                lyrics_options.refuse_misplaced(album.build_parser(), args)
                self.assertEqual(args.track, Path("t.mp3"))

    def test_a_missing_album_option_exits_2(self) -> None:
        self.assertEqual(self.main().code, album.EXIT_CANNOT_RUN)


class ExitCodeTest(cli_fakes.CliCase):
    """0 for a clean run, 1 for a track error, 2 when the run cannot start, 130 on Ctrl-C."""

    def build(self) -> list[Path]:
        """A two-track album: one track finds lyrics, one does not."""
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        return fixtures.build_album(
            self.album,
            (
                TrackSpec("01 A.mp3", title="The Keeper"),
                TrackSpec("02 B.mp3", title="The Mill"),
            ),
        )

    def test_a_clean_run_exits_0(self) -> None:
        self.build()
        self.assertEqual(self.run_album().code, album.EXIT_OK)

    def test_a_track_error_exits_1(self) -> None:
        self.build()

        def embed(path: Path, lyrics: lm.Lyrics, lang: str) -> bool:
            raise lm.TagVersionError("cannot write an ID3v2.2 tag")

        self.overrides["write_lyrics"] = embed
        self.assertEqual(self.run_album().code, album.EXIT_TRACK_ERRORS)

    def test_a_run_with_no_lang_writes_the_default_language(self) -> None:
        self.build()
        self.run_album()
        self.assertEqual(self.folder.manifests[0].lang, album.DEFAULT_LANG)

    def test_a_run_with_a_lang_writes_that_language(self) -> None:
        self.build()
        self.run_album("--lang", "deu")
        self.assertEqual(self.folder.manifests[0].lang, "deu")

    def test_each_skipped_link_gets_one_notice(self) -> None:
        self.build()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (self.album / "03 Link.mp3").symlink_to(elsewhere / "x.mp3")
        (self.album / "Disc 2").symlink_to(elsewhere, target_is_directory=True)
        for extra in ((), ("--dry-run",)):
            with self.subTest(extra=extra):
                result = self.run_album(*extra)
                notices = [
                    line for line in result.stderr.splitlines() if "link" in line
                ]
                self.assertEqual(len(notices), 2)
                self.assertIn("03 Link.mp3", notices[0])
                self.assertIn("Disc 2", notices[1])
                self.assertNotIn("Link", result.stdout)

    def test_a_missing_album_exits_2(self) -> None:
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertIn("not a directory", result.stderr)

    def test_an_album_with_no_mp3_exits_2(self) -> None:
        self.album.mkdir()
        self.assertEqual(self.run_album().code, album.EXIT_CANNOT_RUN)

    def test_a_lock_held_by_a_live_run_exits_2(self) -> None:
        self.build()
        held = run_fakes.acquire(run_fakes.lock_path(self.album))
        assert held is not None
        self.addCleanup(held.release)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertEqual(self.folder.backed_up, [])

    def test_an_unreadable_config_exits_2(self) -> None:
        self.build()
        result = self.run_album("--config", str(self.tmp / "missing.toml"))
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertEqual(self.folder.backed_up, [])

    def test_check_with_no_run_exits_2(self) -> None:
        self.build()
        self.assertEqual(self.run_album("--mode", "check").code, album.EXIT_CANNOT_RUN)

    def test_undo_of_a_track_the_run_does_not_list_exits_2(self) -> None:
        (first, _second) = self.build()
        self.run_album()

        def undo(
            album: Path,
            run_dir: Path,
            manifest: lr.Manifest,
            *,
            newest: bool,
            track: str | None,
        ) -> lr.UndoReport:
            raise lm.TrackNotInRunError(f"{track} is not in run {run_dir.name}")

        self.overrides["undo"] = undo
        result = self.run_album("--mode", "undo", "--track", str(first))
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)

    def test_a_track_outside_the_album_exits_2(self) -> None:
        self.build()
        outside = self.tmp / "elsewhere.mp3"
        result = self.run_album("--mode", "check", "--track", str(outside))
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)

    def test_an_undo_track_outside_the_album_exits_2(self) -> None:
        self.build()
        outside = self.tmp / "elsewhere.mp3"
        result = self.run_album("--mode", "undo", "--track", str(outside))
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertIn("album.py:", result.stderr)
        self.assertEqual((result.stdout, self.folder.undo_calls), ("", []))

    def test_an_interrupted_run_exits_130(self) -> None:
        self.build()

        def fetch(*args: object, **kwargs: object) -> lm.FetchResult:
            raise KeyboardInterrupt

        self.overrides["fetch"] = fetch
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertIn("interrupted", result.stderr)

    def test_an_interrupt_before_any_track_exits_130(self) -> None:
        self.build()

        def back_up(*args: object, **kwargs: object) -> lr.Manifest:
            raise KeyboardInterrupt

        self.overrides["back_up"] = back_up
        result = self.run_album()
        self.assertEqual((result.code, result.stdout), (album.EXIT_INTERRUPTED, ""))


class RealWiringTest(fixtures.IsolatedCase):
    """The entry point binds every stage to the real function, imported statically."""

    def test_each_stage_is_bound_to_its_function(self) -> None:
        wiring = album.real_wiring()
        stages = wiring.stages
        expected = {
            "read_tags": lyrics_tags.read_tags,
            "write_lyrics": lyrics_tags.write_lyrics,
            "normalize_sidecars": lyrics_sidecar.normalize_sidecars,
            "write_sidecar": lyrics_sidecar.write_sidecar,
            "fetch": lyrics_fetch.fetch_lyrics,
            "check": lyrics_check.check_track,
            "back_up": lyrics_backup.back_up_album,
            "write_manifest": lyrics_manifest.write_manifest,
            "select_run": lyrics_backup.select_run,
            "read_manifest": lyrics_manifest.read_manifest,
            "load_before": lyrics_backup.load_before_frames,
            "load_before_sidecar": lyrics_backup.load_before_sidecar,
            "undo": lyrics_undo.undo_run,
            "strip_track": lyrics_strip.strip_track,
            "check_writable": lyrics_tags.check_writable,
            "check_encodable": lyrics_frames.check_encodable,
            "measure_level": lyrics_silence.measure_level,
        }
        self.assertEqual(
            sorted(expected), sorted(f.name for f in dataclasses.fields(stages))
        )
        for name, function in expected.items():
            with self.subTest(name):
                self.assertIs(getattr(stages, name), function)
        self.assertIs(wiring.acquire_lock, run_lock.acquire)
        self.assertEqual(wiring.now().utcoffset(), dt.timedelta(0))

    def test_the_providers_take_the_runs_deadline(self) -> None:
        wiring = album.real_wiring()
        keyless = wiring.build_providers(None, 3.0)
        genius_key = lm.ProviderKey(lm.Source.GENIUS, lm.LyricsKind.UNSYNCED)
        self.assertNotIn(genius_key, keyless)
        keyed = wiring.build_providers(lr.Secret("not-a-real-key"), 3.0)
        genius = keyed[genius_key]
        assert isinstance(genius, lyrics_genius.GeniusProvider)
        self.assertEqual(genius.deadline_s, 3.0)
