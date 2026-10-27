"""Every album property suite runs under the one Hypothesis profile `album_profile` loads.

``@given`` keeps the default settings of the moment it decorates a test, and Hypothesis
keeps one default per process, which the gate shares among every suite in this folder
(`test_album.load_tests`). A profile one suite loaded would therefore also govern each
suite imported after it that loads none of its own, and the settings would have as many
sources as suites. So `album_profile` is the one module that registers and loads a
profile, every property suite imports it before it defines its tests, and this suite
checks each property test's own settings against it.
"""

from __future__ import annotations

import ast
import sys
import unittest
from collections.abc import Callable
from pathlib import Path

import hypothesis
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above

#: The Hypothesis classmethods that write and choose the process's profile.
_PROFILE_CALLS = frozenset({"register_profile", "load_profile"})
#: The one module allowed to call them.
_PROFILE_MODULE = Path(album_profile.__file__).resolve()
#: The module name a property suite imports to load the shared profile.
_PROFILE_IMPORT = "album_profile"


def _profile_calls(path: Path) -> list[int]:
    """The line of each call *path* makes to `register_profile` or `load_profile`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in _PROFILE_CALLS:
            lines.append(node.lineno)
    return lines


def _decorator_name(node: ast.expr) -> str:
    """The bare name a decorator calls or names: ``given`` for ``@given(...)``."""
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    return getattr(target, "id", "")


def _hypothesis_names(tree: ast.Module, name: str) -> frozenset[str]:
    """*name*, and each local name *tree* binds it to with ``from hypothesis import``.

    So ``from hypothesis import given as g`` makes ``g`` count as ``given``.
    """
    aliases = {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "hypothesis"
        for alias in node.names
        if alias.name == name and alias.asname
    }
    return frozenset({name, *aliases})


def _decorated(tree: ast.Module, name: str) -> list[int]:
    """The line of each decorator in *tree* that calls or names *name*, or an alias."""
    names = _hypothesis_names(tree, name)
    return [
        decorator.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for decorator in node.decorator_list
        if _decorator_name(decorator) in names
    ]


def _loads_profile(node: ast.stmt) -> bool:
    """Whether *node* imports `album_profile`, whole or a name from it, loading it."""
    if isinstance(node, ast.ImportFrom):
        return node.level == 0 and node.module == _PROFILE_IMPORT
    return isinstance(node, ast.Import) and any(
        alias.name == _PROFILE_IMPORT for alias in node.names
    )


def _profile_import_line(tree: ast.Module) -> int | None:
    """The line of *tree*'s first top-level import of `album_profile`, if it has one."""
    for node in tree.body:
        if _loads_profile(node):
            return node.lineno
    return None


def _late_profile_import(path: Path) -> bool:
    """Whether *path* defines a ``@given`` test before, or without, loading the profile.

    ``@given`` keeps the default settings of the moment it decorates, so a suite's
    tests run under the shared profile only when the profile is loaded first.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    givens = _decorated(tree, "given")
    if not givens:
        return False
    loaded = _profile_import_line(tree)
    return loaded is None or loaded > min(givens)


def _settings_decorators(path: Path) -> list[int]:
    """The line of each ``@settings`` decorator in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _decorated(tree, "settings")


def _grouped_suite() -> unittest.TestSuite:
    """Every grouped suite in tests/, loaded as the aggregator loads them."""
    return unittest.TestLoader().discover(
        str(_HERE), pattern="test_*.py", top_level_dir=str(_HERE)
    )


def _property_tests(suite: unittest.TestSuite) -> list[Callable[..., object]]:
    """Each ``@given`` test method in *suite*, as its case binds it."""
    methods = (getattr(case, case._testMethodName) for case in fixtures.tests_in(suite))
    return [method for method in methods if hypothesis.is_hypothesis_test(method)]


def _off_profile(methods: list[Callable[..., object]], profile: settings) -> list[str]:
    """The name of each method in *methods* not decorated under *profile*."""
    return [
        method.__qualname__
        for method in methods
        if getattr(method, "_hypothesis_internal_use_settings", None) is not profile
    ]


def _album_sources() -> list[Path]:
    """Every Python file the album gate's process imports tests from."""
    tool = _HERE.parent
    return sorted([*_HERE.glob("*.py"), *tool.glob("test_*.py")])


class OneProfileTest(fixtures.IsolatedCase):
    """One module sets the profile, and the profile is the one it sets."""

    def test_no_other_module_registers_or_loads_a_profile(self) -> None:
        offenders = [
            f"{path.name}:{line}"
            for path in _album_sources()
            if path.resolve() != _PROFILE_MODULE
            for line in _profile_calls(path)
        ]
        self.assertEqual(offenders, [])

    def test_the_scan_finds_the_profile_module_s_own_calls(self) -> None:
        # Without this, a scan that matched nothing would pass the check above.
        self.assertEqual(len(_profile_calls(_PROFILE_MODULE)), 2)

    def test_the_shared_profile_holds_every_suite_s_settings(self) -> None:
        shared = settings.get_profile(album_profile.PROFILE)
        self.assertIsNone(shared.deadline)
        self.assertLessEqual(
            {HealthCheck.too_slow, HealthCheck.filter_too_much},
            set(shared.suppress_health_check),
        )

    def test_every_property_suite_loads_the_profile_before_its_tests(self) -> None:
        offenders = [
            path.name for path in _album_sources() if _late_profile_import(path)
        ]
        self.assertEqual(offenders, [])

    def test_the_import_scan_finds_a_suite_that_loads_it_too_late(self) -> None:
        late = self.tmp / "test_late.py"
        late.write_text(
            "from hypothesis import given, strategies as st\n"
            "@given(st.integers())\n"
            "def test_x(x): pass\n"
            "import album_profile\n",
            encoding="utf-8",
            newline="\n",
        )
        on_time = self.tmp / "test_on_time.py"
        on_time.write_text(
            "import album_profile\n" + late.read_text(encoding="utf-8"),
            encoding="utf-8",
            newline="\n",
        )
        self.assertTrue(_late_profile_import(late))
        self.assertFalse(_late_profile_import(on_time))

    def test_the_import_scan_follows_an_alias_of_given(self) -> None:
        aliased = self.tmp / "test_aliased.py"
        aliased.write_text(
            "from hypothesis import given as g, strategies as st\n"
            "@g(st.integers())\n"
            "def test_x(x): pass\n",
            encoding="utf-8",
            newline="\n",
        )
        self.assertTrue(_late_profile_import(aliased))

    def test_the_import_scan_accepts_a_name_imported_from_the_profile(self) -> None:
        named = self.tmp / "test_named.py"
        named.write_text(
            "from album_profile import PROFILE\n"
            "from hypothesis import given, strategies as st\n"
            "@given(st.integers())\n"
            "def test_x(x): pass\n",
            encoding="utf-8",
            newline="\n",
        )
        self.assertFalse(_late_profile_import(named))

    def test_no_suite_decorates_a_test_with_its_own_settings(self) -> None:
        # The fuzz_gate convention forbids @settings outright, since its campaign
        # refuses a decorated property, so no form of it is allowed here.
        offenders = [
            f"{path.name}:{line}"
            for path in _album_sources()
            for line in _settings_decorators(path)
        ]
        self.assertEqual(offenders, [])

    def test_every_property_test_is_decorated_under_the_shared_profile(self) -> None:
        methods = _property_tests(_grouped_suite())
        self.assertGreater(len(methods), 0)
        shared = settings.get_profile(album_profile.PROFILE)
        self.assertEqual(_off_profile(methods, shared), [])

    def test_the_walk_names_a_test_decorated_under_other_settings(self) -> None:
        # Without this, a walk that compared nothing would pass the check above.
        methods = _property_tests(_grouped_suite())
        other = settings(deadline=1)
        self.assertEqual(len(_off_profile(methods, other)), len(methods))
        # A test decorated under other settings, built at run time, so the source
        # scan for @settings above never sees it: the walk must name it.
        stray = settings(deadline=1)(given(st.integers())(lambda _x: None))
        shared = settings.get_profile(album_profile.PROFILE)
        self.assertEqual(_off_profile([stray], shared), [stray.__qualname__])
