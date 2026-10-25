"""The option layer: parsing, validation and the live defaults."""

from __future__ import annotations

import contextlib
import io

import at_models
import transcribe
import at_types
from tests._isolation import IsolatedCase


class OptionsTest(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.model_dir = self.root / "models"
        self.audio = self.root / "a.mp3"
        self.audio.write_bytes(b"")

    def resolve(self, *args: str) -> transcribe.Options:
        parser = transcribe.build_parser(self.model_dir)
        return transcribe.resolve_options(parser.parse_args(list(args)))

    def test_defaults(self) -> None:
        options = self.resolve("--path", str(self.audio))
        self.assertEqual(options.mode, "transcribe")
        self.assertEqual(options.extensions, frozenset({"mp3"}))
        self.assertEqual(options.language, "en")
        self.assertEqual(options.model, self.model_dir / at_models.WHISPER_MODEL_NAME)
        self.assertEqual(options.vad_model, self.model_dir / at_models.VAD_MODEL_NAME)
        self.assertIsNone(options.output_dir)
        self.assertIsNone(options.whisper_cli)
        self.assertFalse(options.overwrite or options.dry_run)

    def test_model_dir_moves_both_model_defaults(self) -> None:
        other = self.root / "elsewhere"
        options = self.resolve("--path", str(self.audio), "--model-dir", str(other))
        self.assertEqual(options.model.parent, other)
        self.assertEqual(options.vad_model.parent, other)

    def test_explicit_model_wins(self) -> None:
        model = self.root / "mine.bin"
        self.assertEqual(
            self.resolve("--path", str(self.audio), "--model", str(model)).model, model
        )

    def test_extensions_are_lowered_and_dot_stripped(self) -> None:
        options = self.resolve("--path", str(self.audio), "--extensions", "MP3, .m4a")
        self.assertEqual(options.extensions, frozenset({"mp3", "m4a"}))

    def test_setup_errors(self) -> None:
        cases = {
            "--path": [],
            "does not exist": ["--path", str(self.root / "missing.mp3")],
            "--extensions": ["--path", str(self.audio), "--extensions", " , "],
            "--language": ["--path", str(self.audio), "--language", "english"],
        }
        for needle, args in cases.items():
            with self.subTest(needle=needle):
                with self.assertRaises(at_types.SetupError) as caught:
                    self.resolve(*args)
                self.assertIn(needle, str(caught.exception))

    def test_language_auto_and_three_letters(self) -> None:
        self.assertEqual(
            self.resolve("--path", str(self.audio), "--language", "auto").language,
            "auto",
        )
        self.assertEqual(
            self.resolve("--path", str(self.audio), "--language", "yue").language, "yue"
        )

    def test_fetch_models_needs_no_path_and_ignores_transcribe_options(self) -> None:
        options = self.resolve(
            "--mode", "fetch-models", "--language", "english", "--extensions", ","
        )
        self.assertIsNone(options.path)
        self.assertEqual(options.extensions, frozenset())

    def test_help_shows_the_live_model_dir_and_the_modes(self) -> None:
        text = transcribe.build_parser(self.model_dir).format_help()
        # argparse wraps a long default across lines, so compare with whitespace removed.
        self.assertIn("".join(str(self.model_dir).split()), "".join(text.split()))
        self.assertIn("fetch-models", text)
        self.assertIn("Exit codes", text)

    def test_help_names_the_label_the_lrc_is_written_with(self) -> None:
        text = transcribe.build_parser(self.model_dir).format_help()
        self.assertIn(f"[re:{at_types.TOOL_LABEL} <version>;", text)

    def test_help_names_a_claimed_output_pair_under_exit_1(self) -> None:
        text = " ".join(transcribe.build_parser(self.model_dir).format_help().split())
        self.assertIn(
            "1 any file failed (an output pair another file already claims among them)",
            text,
        )

    def test_main_reports_a_setup_error_with_exit_2(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = transcribe.main(["--path", str(self.root / "missing.mp3")])
        self.assertEqual(code, at_types.EXIT_SETUP)
        self.assertIn("does not exist", err.getvalue())

    def test_a_sidecar_as_the_path_is_refused_in_any_case(self) -> None:
        # A .lrc or .txt named by --path would be its own output.
        for name in ("a.lrc", "a.txt", "A.LRC", "b.Txt"):
            with self.subTest(name=name):
                sidecar = self.root / name
                sidecar.write_text("", encoding="utf-8", newline="\n")
                with self.assertRaises(at_types.SetupError) as caught:
                    self.resolve("--path", str(sidecar))
                self.assertIn("is a .lrc or .txt", str(caught.exception))

    def test_a_folder_named_like_a_sidecar_is_walked(self) -> None:
        folder = self.root / "odd.lrc"
        folder.mkdir()
        self.assertEqual(self.resolve("--path", str(folder)).path, folder)

    def test_the_outputs_suffixes_are_refused_as_extensions(self) -> None:
        # A walk would map each sidecar onto itself.
        for value in ("lrc", ".TXT", "mp3, Lrc"):
            with self.subTest(value=value):
                with self.assertRaises(at_types.SetupError) as caught:
                    self.resolve("--path", str(self.audio), "--extensions", value)
                self.assertIn("this tool's outputs", str(caught.exception))


class ToolVersionTest(IsolatedCase):
    def test_the_manifest_version(self) -> None:
        (self.root / "pyproject.toml").write_text(
            '[project]\nversion = "0.1.0"\n', encoding="utf-8", newline="\n"
        )
        self.assertEqual(transcribe.read_tool_version(self.root), "0.1.0")

    def test_fallbacks_give_unknown(self) -> None:
        cases = {
            "no file": None,
            "bad toml": "[project\n",
            "not a string": "[project]\nversion = 1\n",
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                folder = self.root / name.replace(" ", "-")
                folder.mkdir()
                if text is not None:
                    (folder / "pyproject.toml").write_text(
                        text, encoding="utf-8", newline="\n"
                    )
                self.assertEqual(transcribe.read_tool_version(folder), "unknown")

    def test_a_project_that_is_not_a_table_is_unknown(self) -> None:
        (self.root / "pyproject.toml").write_text(
            'project = "x"\n', encoding="utf-8", newline="\n"
        )
        self.assertEqual(transcribe.read_tool_version(self.root), "unknown")

    def test_a_manifest_that_is_not_utf8_is_unknown(self) -> None:
        (self.root / "pyproject.toml").write_bytes(b'[project]\nversion = "\xff"\n')
        self.assertEqual(transcribe.read_tool_version(self.root), "unknown")
