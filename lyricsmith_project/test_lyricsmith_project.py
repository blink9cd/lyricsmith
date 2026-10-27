"""The project's manifest reader: its version, and the shared libraries it locates.

Every case reads an invented ``pyproject.toml`` in a scratch folder, beside invented
library folders, so nothing depends on where this tree sits, except those that read
this project's own: the defaults, and the list held equal to the export's.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import os
import sys
import tempfile
import tomllib
import types
import unittest
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path += [] if str(_HERE) in sys.path else [str(_HERE)]

import lyricsmith_project as lp  # noqa: E402 - import follows the sys.path bootstrap above


class ScratchCase(unittest.TestCase):
    """A scratch folder holding the stand-in pyproject.toml and library folders."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.top = Path(scratch.name).resolve()
        self.project = self.top / "project"
        self.project.mkdir()
        self.config = self.project / "pyproject.toml"

    def write_config(self, text: str) -> None:
        """The stand-in pyproject.toml's text."""
        self.config.write_text(text, encoding="utf-8", newline="\n")

    def name_libraries(self, entries: Sequence[str]) -> None:
        """A pyproject.toml whose shared_libraries list is *entries*."""
        listed = ", ".join(f'"{entry}"' for entry in entries)
        self.write_config(f"[tool.lyricsmith]\nshared_libraries = [{listed}]\n")

    def make_library(self, folder: Path) -> Path:
        """A library folder holding its own same-named module; the folder."""
        folder.mkdir(parents=True)
        (folder / f"{folder.name}.py").write_text("", encoding="utf-8", newline="\n")
        return folder


class VersionTest(ScratchCase):
    """The version is the pyproject's, and 'unknown' whenever it cannot be read."""

    def test_the_project_files_version(self) -> None:
        self.write_config('[project]\nversion = "9.8.7"\n')
        self.assertEqual(lp.project_version(self.config), "9.8.7")

    def test_a_missing_file_is_unknown(self) -> None:
        self.assertEqual(lp.project_version(self.config), lp.UNKNOWN_VERSION)

    def test_a_malformed_file_is_unknown(self) -> None:
        self.write_config("[project\n")
        self.assertEqual(lp.project_version(self.config), "unknown")

    def test_a_version_that_is_not_text_is_unknown(self) -> None:
        self.write_config("[project]\nversion = 1\n")
        self.assertEqual(lp.project_version(self.config), "unknown")

    def test_a_project_that_is_not_a_table_is_unknown(self) -> None:
        self.write_config('project = "x"\n')
        self.assertEqual(lp.project_version(self.config), "unknown")

    def test_a_file_that_is_not_utf8_is_unknown(self) -> None:
        self.config.write_bytes(b'[project]\nversion = "\xff"\n')
        self.assertEqual(lp.project_version(self.config), "unknown")

    def test_the_default_is_this_projects_own_file(self) -> None:
        with lp.PROJECT_CONFIG.open("rb") as stream:
            declared = tomllib.load(stream)["project"]["version"]
        self.assertEqual(lp.project_version(), declared)


class LocateTest(ScratchCase):
    """Each named folder, relative to the pyproject.toml, or an error naming it."""

    def test_each_folder_is_resolved_relative_to_the_config_in_order(self) -> None:
        second = self.make_library(self.top / "shared" / "second")
        first = self.make_library(self.project / "first")
        self.name_libraries(["first", "../shared/second"])
        self.assertEqual(lp.shared_library_folders(self.config), (first, second))

    def test_the_folders_are_read_relative_to_the_config_not_the_cwd(self) -> None:
        library = self.make_library(self.project / "one")
        self.name_libraries(["one"])
        elsewhere = self.top / "elsewhere"
        self.make_library(elsewhere / "one")
        before = Path.cwd()
        os.chdir(elsewhere)
        self.addCleanup(os.chdir, before)
        self.assertEqual(lp.shared_library_folders(self.config), (library,))

    def test_a_missing_folder_fails_naming_it_and_the_key(self) -> None:
        self.make_library(self.project / "present")
        self.name_libraries(["present", "absent"])
        with self.assertRaises(lp.SharedLibraryError) as caught:
            lp.shared_library_folders(self.config)
        message = str(caught.exception)
        self.assertIn("'absent'", message)
        self.assertIn("absent.py", message)
        self.assertIn(lp.CONFIG_KEY, message)
        self.assertIn(str(self.config), message)

    def test_a_folder_without_its_module_is_missing(self) -> None:
        (self.project / "hollow").mkdir()
        self.name_libraries(["hollow"])
        with self.assertRaisesRegex(lp.SharedLibraryError, "'hollow'.*hollow.py"):
            lp.shared_library_folders(self.config)

    def test_the_error_is_an_import_error(self) -> None:
        self.assertTrue(issubclass(lp.SharedLibraryError, ImportError))

    def test_a_same_named_folder_above_the_project_is_never_found(self) -> None:
        self.make_library(self.top / "decoy")
        self.name_libraries(["decoy"])
        with self.assertRaisesRegex(lp.SharedLibraryError, "'decoy'"):
            lp.shared_library_folders(self.config)

    def test_an_empty_list_names_nothing(self) -> None:
        self.name_libraries([])
        self.assertEqual(lp.shared_library_folders(self.config), ())

    def test_the_default_config_is_this_projects_own(self) -> None:
        self.assertEqual(
            lp.shared_library_folders(), lp.shared_library_folders(lp.PROJECT_CONFIG)
        )
        self.assertEqual(lp.PROJECT_CONFIG, _HERE.parent / "pyproject.toml")


class ShapeTest(ScratchCase):
    """A manifest that is unreadable or misshapen fails, naming the key and the file."""

    def assert_refused(self, text: str, fragment: str) -> None:
        """Locating under a config holding *text* fails with *fragment* in the message."""
        self.write_config(text)
        with self.assertRaises(lp.SharedLibraryError) as caught:
            lp.shared_library_folders(self.config)
        self.assertIn(fragment, str(caught.exception))
        self.assertIn(lp.CONFIG_KEY, str(caught.exception))
        self.assertIn(str(self.config), str(caught.exception))

    def test_a_missing_config_fails(self) -> None:
        with self.assertRaisesRegex(lp.SharedLibraryError, "cannot read"):
            lp.shared_library_folders(self.config)

    def test_a_malformed_config_fails(self) -> None:
        self.assert_refused("[tool.lyricsmith\n", "cannot read")

    def test_a_config_that_is_not_utf8_fails(self) -> None:
        self.config.write_bytes(b'[tool.lyricsmith]\nshared_libraries = ["\xff"]\n')
        with self.assertRaisesRegex(lp.SharedLibraryError, "cannot read"):
            lp.shared_library_folders(self.config)

    def test_no_key_fails(self) -> None:
        for text in (
            "",
            'tool = "x"\n',
            '[tool]\nlyricsmith = "x"\n',
            "[tool.lyricsmith]\n",
        ):
            with self.subTest(text=text):
                self.assert_refused(text, "names no")

    def test_a_key_that_is_not_a_list_fails(self) -> None:
        self.assert_refused('[tool.lyricsmith]\nshared_libraries = "one"\n', "names no")

    def test_an_entry_that_is_not_text_fails(self) -> None:
        self.assert_refused(
            "[tool.lyricsmith]\nshared_libraries = [1]\n", "1 is not a folder path"
        )

    def test_an_empty_entry_fails(self) -> None:
        for entry in ("", "  "):
            with self.subTest(entry=entry):
                self.assert_refused(
                    f'[tool.lyricsmith]\nshared_libraries = ["{entry}"]\n',
                    "is not a folder path",
                )

    def test_an_absolute_entry_fails(self) -> None:
        library = self.make_library(self.project / "real")
        for entry in (library.as_posix(), "/x", "C:/x", "C:x", "//server/share/x"):
            with self.subTest(entry=entry):
                self.assert_refused(
                    f"[tool.lyricsmith]\nshared_libraries = ['{entry}']\n",
                    "is absolute",
                )

    def test_nothing_is_added_when_any_entry_fails(self) -> None:
        self.make_library(self.project / "good")
        self.name_libraries(["good", "absent"])
        search: list[str] = []
        with self.assertRaises(lp.SharedLibraryError):
            lp.add_shared_libraries(self.config, search)
        self.assertEqual(search, [])


class AddTest(ScratchCase):
    """The folders go on the search path once each, after what is there."""

    def test_each_folder_is_appended_once(self) -> None:
        one = self.make_library(self.project / "one")
        two = self.make_library(self.project / "two")
        self.name_libraries(["one", "two", "one"])
        search = ["first", str(two)]
        self.assertEqual(lp.add_shared_libraries(self.config, search), (one, two, one))
        self.assertEqual(search, ["first", str(two), str(one)])
        lp.add_shared_libraries(self.config, search)
        self.assertEqual(search, ["first", str(two), str(one)])

    def test_the_default_search_path_is_sys_path(self) -> None:
        self.make_library(self.project / "solo")
        self.name_libraries(["solo"])
        before = list(sys.path)
        self.addCleanup(sys.path.__setitem__, slice(None), before)
        (folder,) = lp.add_shared_libraries(self.config)
        self.assertEqual(sys.path, [*before, str(folder)])


class OriginCase(ScratchCase):
    """A library ``alpha`` in its named folder, and a decoy of it in another folder."""

    def setUp(self) -> None:
        super().setUp()
        self.folder = self.make_library(self.project / "alpha")
        self.name_libraries(["alpha"])
        self.decoys = self.top / "decoys"
        self.decoys.mkdir()
        self.decoy = self.decoys / "alpha.py"
        self.decoy.write_text("", encoding="utf-8", newline="\n")
        # A finder caches each folder's listing; the scratch files are new to it.
        importlib.invalidate_caches()


class OriginTest(OriginCase):
    """Each library must load from its named folder, checked before any is imported."""

    def test_a_same_named_module_earlier_on_the_path_fails_by_name(self) -> None:
        with self.assertRaises(lp.SharedLibraryError) as caught:
            lp.add_shared_libraries(self.config, [str(self.decoys)])
        message = str(caught.exception)
        self.assertIn("'alpha'", message)
        self.assertIn(str(self.decoy), message)
        self.assertIn(str(self.folder), message)
        self.assertIn(lp.CONFIG_KEY, message)
        self.assertIn(str(self.config), message)

    def test_a_module_already_loaded_from_elsewhere_fails_by_name(self) -> None:
        loaded = types.ModuleType("alpha")
        loaded.__file__ = str(self.decoy)
        with (
            mock.patch.dict(sys.modules, {"alpha": loaded}),
            self.assertRaises(lp.SharedLibraryError) as caught,
        ):
            lp.add_shared_libraries(self.config, [])
        self.assertIn(str(self.decoy), str(caught.exception))

    def test_a_module_already_loaded_with_no_file_fails_naming_it_so(self) -> None:
        with (
            mock.patch.dict(sys.modules, {"alpha": types.ModuleType("alpha")}),
            self.assertRaises(lp.SharedLibraryError) as caught,
        ):
            lp.add_shared_libraries(self.config, [])
        self.assertIn("'alpha'", str(caught.exception))
        self.assertIn("built-in or namespace", str(caught.exception))

    def test_a_module_already_loaded_from_its_folder_passes(self) -> None:
        loaded = types.ModuleType("alpha")
        loaded.__file__ = str(self.folder / "alpha.py")
        with mock.patch.dict(sys.modules, {"alpha": loaded}):
            self.assertEqual(lp.add_shared_libraries(self.config, []), (self.folder,))

    def test_the_folder_alone_on_the_path_passes(self) -> None:
        search = ["elsewhere"]
        self.assertEqual(lp.add_shared_libraries(self.config, search), (self.folder,))

    def test_a_module_file_linked_to_a_file_elsewhere_passes(self) -> None:
        # The folder is what the manifest names, not where the module's file points.
        module = self.folder / "alpha.py"
        module.unlink()
        module.symlink_to(self.decoy)
        search = [str(self.folder)]
        self.assertEqual(lp.add_shared_libraries(self.config, search), (self.folder,))

    def test_the_default_search_path_is_checked_too(self) -> None:
        before = list(sys.path)
        self.addCleanup(sys.path.__setitem__, slice(None), before)
        sys.path.insert(0, str(self.decoys))
        with self.assertRaisesRegex(lp.SharedLibraryError, "'alpha'"):
            lp.add_shared_libraries(self.config)

    def test_the_check_imports_no_library(self) -> None:
        with self.assertRaises(lp.SharedLibraryError):
            lp.add_shared_libraries(self.config, [str(self.decoys)])
        self.assertNotIn("alpha", sys.modules)


class ExitTest(OriginCase):
    """An entry file run as a script exits 2 with the error, never a traceback."""

    def run_exiting(self, search: list[str]) -> tuple[object, str]:
        """The exit code and stderr of a failing call made with exit_on_failure."""
        err = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["/somewhere/tool.py"]),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as stop,
        ):
            lp.add_shared_libraries(self.config, search, exit_on_failure=True)
        return stop.exception.code, err.getvalue()

    def test_a_failure_exits_2_naming_the_script_and_the_error(self) -> None:
        code, err = self.run_exiting([str(self.decoys)])
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("tool.py: SharedLibraryError: "), err)
        self.assertIn("'alpha'", err)
        self.assertEqual(err.count("\n"), 1)

    def test_a_control_character_in_the_error_is_escaped(self) -> None:
        self.name_libraries(["missing\\u001b"])
        code, err = self.run_exiting([])
        self.assertEqual(code, 2)
        self.assertNotIn("\x1b", err)
        self.assertIn("\\x1b", err)

    def test_without_it_the_error_is_raised(self) -> None:
        with self.assertRaises(lp.SharedLibraryError):
            lp.add_shared_libraries(self.config, [str(self.decoys)])


class SiblingTest(OriginCase):
    """A sibling tool's module loads from its own folder, or fails by name."""

    def test_a_sibling_found_in_its_folder_passes(self) -> None:
        lp.confirm_from("alpha", self.folder, search_path=[str(self.folder)])

    def test_a_sibling_shadowed_earlier_on_the_path_fails_by_name(self) -> None:
        search = [str(self.decoys), str(self.folder)]
        with self.assertRaises(lp.SharedLibraryError) as caught:
            lp.confirm_from("alpha", self.folder, search_path=search)
        message = str(caught.exception)
        self.assertIn("'alpha'", message)
        self.assertIn(str(self.decoy), message)
        self.assertIn(str(self.folder), message)

    def test_a_shadowed_sibling_exits_2_when_asked(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["sweep.py"]),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as stop,
        ):
            lp.confirm_from(
                "alpha",
                self.folder,
                search_path=[str(self.decoys)],
                exit_on_failure=True,
            )
        self.assertEqual(stop.exception.code, 2)
        self.assertTrue(err.getvalue().startswith("sweep.py: SharedLibraryError: "))


class ManifestTest(unittest.TestCase):
    """This project's own list names exactly the folders its export vendors.

    The vendor list is relative to the repository root, the shared list to this
    project's folder, so the root is derived from the two lists themselves: each shared
    entry is the vendor entry under the same number of steps up from the project.
    """

    def setUp(self) -> None:
        with lp.PROJECT_CONFIG.open("rb") as stream:
            tool = tomllib.load(stream)["tool"]
        if "project_export" not in tool:  # an exported copy carries no export policy
            self.skipTest("this tree declares no export vendor list")
        self.vendor = [
            PurePosixPath(entry) for entry in tool["project_export"]["vendor"]
        ]
        self.shared = [
            PurePosixPath(entry) for entry in tool["lyricsmith"]["shared_libraries"]
        ]

    def test_the_shared_libraries_are_the_vendored_folders_in_order(self) -> None:
        self.assertEqual(len(self.shared), len(self.vendor))
        steps = {
            len(shared.parts) - len(vendor.parts)
            for shared, vendor in zip(self.shared, self.vendor, strict=True)
        }
        self.assertEqual(len(steps), 1, steps)
        project = _HERE.parent
        root = (project / Path(*[".."] * steps.pop())).resolve()
        self.assertTrue(project.is_relative_to(root))
        self.assertEqual(
            list(lp.shared_library_folders()),
            [(root / vendor).resolve() for vendor in self.vendor],
        )


if __name__ == "__main__":  # pragma: no cover - the suite's own entry point
    unittest.main()
