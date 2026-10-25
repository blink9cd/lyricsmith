"""Every environment variable this project reads by name is declared to the gate cache.

`LYRICSMITH_OFFLINE` skips album's live provider tests, so it changes a gate verdict
without changing one file. The gate's verdict cache folds in only the variables
that `[tool.gate_cache] env` in this project's pyproject.toml declares. An undeclared one
lets a verdict recorded with it unset be replayed into a run with it set, and the
reverse -- an online sweep replaying an offline PASS never runs the live tests at all.

So this test fails on a read nothing declares, or one whose name it cannot resolve, which
stops a new switch landing without its declaration, and on a declaration nothing reads,
which stops a renamed switch leaving a stale name behind. It sits under album only because
a test must live under one tool; it scans the whole project, sweep and transcribe too.

One narrow class is held apart: a CREDENTIAL. Folding a token's value into a persisted
cache key would write a secret's fingerprint to disk, and a credential never changes which
tests run, since every suite that could reach one clears it first. So `_CREDENTIAL_READS`
names each such variable with its reason. The read stays visible to this scan rather than
hidden from it: an exempted name must still be read somewhere OUTSIDE the tests, so a
stale exemption fails just as a stale declaration does, and a test's own read cannot keep
one alive. An exempted name may never be declared as well.

A second narrow class is held apart for the same two checks: a variable every suite that
could read it REDIRECTS (a config, state or model directory pointed under a temporary
HOME), so its value never reaches a verdict, while declaring it would only split the cache
by machine layout.

A third class is the inverse of the first two: a variable only a TEST reads, to confirm
the isolation it set itself. Its value is the test's own, so it reaches no verdict, and
`PATH` is one the gate cache's own documentation rejects declaring. So `_ISOLATION_READS`
names each with its reason, and holds it to the opposite check: it must be read in the
tests and never by shipped code, which would need a reason of its own, and it may never
be declared.
"""

from __future__ import annotations

import ast
import tomllib
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
# How a read is spelled here, compared as unparsed source so `os.getenv` and a bare
# `getenv` imported from os are both seen.
# `env.get` too: transcribe's model folder is read from an environment mapping handed
# in as `env`, as album's config folder is read from one handed in as `environ`.
_GETTERS = frozenset(
    {"os.environ.get", "os.getenv", "environ.get", "env.get", "getenv"}
)
_MAPPINGS = frozenset({"os.environ", "environ"})
# A switch the scan must find, so a scanner that silently stopped matching cannot pass
# by finding nothing on either side.
_KNOWN_SWITCH = "LYRICSMITH_OFFLINE"
# The vendored shared libraries, skipped by the scan below. Their environment use is
# test-fixture plumbing -- a pid-file path a test exports for one child and reads back --
# not a switch that changes which tests run, so declaring it would say something untrue.
# Excluded by name rather than by listing the tool folders, so a tool folder added later
# is scanned by default -- the safe direction.
_VENDORED = frozenset(
    {
        "stdio_utf8",
        "help_formatter",
        "run_lock",
        "bounded_run",
        "library_walk",
        "live_pid_guard",
    }
)
# Credentials the project reads but must never declare, each with its reason. A mapping,
# not a list, so an entry cannot be added without saying why.
_CREDENTIAL_READS: dict[str, str] = {
    "GENIUS_ACCESS_TOKEN": (
        "album's Genius key, read when no config file names one. A credential: its "
        "value must never reach a persisted cache key, and every album suite unsets it, "
        "so it cannot change which tests run"
    ),
}
# Variables the code reads but every suite that could reach the read redirects, each with
# its reason; held to the same two checks as the credentials above.
_REDIRECTED_READS: dict[str, str] = {
    "XDG_CONFIG_HOME": (
        "album's config directory, where it looks for the Genius key file. Every album "
        "suite points it under a temporary HOME, so it cannot change which tests run, "
        "and folding it in would only split the cache by machine layout"
    ),
    "HOME": (
        "album's fallback config and cooldown directories, under HOME when the XDG "
        "variables name none, and transcribe's model folder. Every album and "
        "transcribe suite points HOME at a temporary directory, so it cannot change "
        "which tests run, and folding it in would only split the cache by machine layout"
    ),
    "XDG_STATE_HOME": (
        "album's cooldown state and sweep's ledger directory. Every album and sweep "
        "suite points it under a temporary HOME, so it cannot change which tests run, "
        "and folding it in would only split the cache by machine layout"
    ),
    "XDG_DATA_HOME": (
        "transcribe's model folder off macOS and Windows. Every transcribe suite points "
        "it under a temporary HOME, so it cannot change which tests run, and folding it "
        "in would only split the cache by machine layout"
    ),
    "LOCALAPPDATA": (
        "transcribe's model folder on Windows. Every transcribe suite points it under a "
        "temporary HOME, so it cannot change which tests run, and folding it in would "
        "only split the cache by machine layout"
    ),
}
_EXEMPT_READS = {**_CREDENTIAL_READS, **_REDIRECTED_READS}
# Variables only a test reads, to confirm the isolation it set; each with its reason.
_ISOLATION_READS: dict[str, str] = {
    "PATH": (
        "read by transcribe's isolation test to confirm its base class pointed PATH at "
        "the scratch folder's fake whisper-cli; the value is the test's own, and "
        "gate_cache.md rejects declaring PATH"
    ),
}


def _read_key(node: ast.AST) -> ast.AST | None:
    """The key expression *node* reads the environment with, else None."""
    if isinstance(node, ast.Call) and node.args and ast.unparse(node.func) in _GETTERS:
        return node.args[0]
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.ctx, ast.Load)
        and ast.unparse(node.value) in _MAPPINGS
    ):
        return node.slice
    if (
        isinstance(node, ast.Compare)
        and isinstance(node.ops[0], (ast.In, ast.NotIn))
        and ast.unparse(node.comparators[0]) in _MAPPINGS
    ):
        return node.left
    return None


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` bindings, plain or annotated.

    The ``USERNAME_ENV = "..."`` style reads a variable through a named constant, and a
    scan that saw only literals would miss every such read.
    """
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.update(
                (t.id, value.value) for t in targets if isinstance(t, ast.Name)
            )
    return found


def _names_read(source: str) -> tuple[set[str], list[str]]:
    """Every name *source* reads from the environment, and each read it cannot name.

    A key resolves when it is a string literal or a module-level constant bound to one.
    Anything else comes back as ``"<line>: <read>"`` so the test FAILS on it: skipping a
    read the scan cannot name would reopen the gap it exists to close.
    """
    tree = ast.parse(source)
    constants = _string_constants(tree)
    names: set[str] = set()
    unresolved: list[str] = []
    for node in ast.walk(tree):
        key = _read_key(node)
        if key is None:
            continue
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            names.add(key.value)
        elif isinstance(key, ast.Name) and key.id in constants:
            names.add(constants[key.id])
        else:
            unresolved.append(f"{getattr(node, 'lineno', 0)}: {ast.unparse(node)}")
    return names, unresolved


def _is_test_file(relative: Path) -> bool:
    """True for a suite or a fixture: a ``test_*`` file, or anything under ``tests/``."""
    return relative.name.startswith("test_") or "tests" in relative.parts[:-1]


def _project_reads(*, tests: bool = True) -> tuple[set[str], list[str]]:
    """Every environment read in this project's Python, caches skipped, as above.

    With *tests* False, test files are left out, which is how an exemption is shown to
    be held up by a read the shipped code makes rather than by a test of it.
    """
    names: set[str] = set()
    unresolved: list[str] = []
    for path in sorted(_PROJECT_ROOT.rglob("*.py")):
        relative = path.relative_to(_PROJECT_ROOT)
        if relative.parts[0] in _VENDORED or any(
            part.startswith(".") or part == "__pycache__" for part in relative.parts
        ):
            continue
        if not tests and _is_test_file(relative):
            continue
        found, unnamed = _names_read(path.read_text(encoding="utf-8"))
        names |= found
        unresolved += [f"{relative.as_posix()}:{entry}" for entry in unnamed]
    return names, unresolved


def _declared() -> set[str]:
    """The names this project's pyproject.toml declares under [tool.gate_cache] env."""
    text = (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return set(tomllib.loads(text)["tool"]["gate_cache"]["env"])


class TestEnvDeclared(fixtures.IsolatedCase):
    """The declaration and the reads are the same set, in both directions."""

    def test_the_scanner_sees_each_read_form_and_no_write(self) -> None:
        source = (
            'NAMED_ENV = "E"\n'
            'os.environ.get("A")\n'
            'os.getenv("B")\n'
            'os.environ["C"]\n'
            'if "D" in os.environ: pass\n'
            "os.environ.get(NAMED_ENV)\n"
            'os.environ["W"] = "1"\n'
            'patch.dict(os.environ, {"P": "1"})\n'
            'os.environ.pop("Q", None)\n'
            'env.get("F")\n'
        )
        self.assertEqual(_names_read(source), ({"A", "B", "C", "D", "E", "F"}, []))

    def test_a_read_the_scanner_cannot_name_is_reported(self) -> None:
        """Skipping it would reopen the gap: the name could be a switch nobody declared."""
        _, unresolved = _names_read("def f(name):\n    return os.environ.get(name)\n")
        self.assertEqual(unresolved, ["2: os.environ.get(name)"])

    def test_a_test_file_is_a_suite_or_anything_under_tests(self) -> None:
        cases = {
            "album/tests/fixtures.py": True,
            "album/tests/test_env_declared.py": True,
            "sweep/test_sweep.py": True,
            "album/src/lyrics_config.py": False,
            "transcribe/transcribe.py": False,
            "tests.py": False,
        }
        for relative, expected in cases.items():
            with self.subTest(relative=relative):
                self.assertIs(_is_test_file(Path(relative)), expected)

    def test_every_environment_read_is_declared_and_nothing_else(self) -> None:
        read, unresolved = _project_reads()
        self.assertEqual(
            unresolved,
            [],
            "an environment read whose name this scan cannot resolve: spell the name "
            "as a string literal or a module-level constant so it can be checked",
        )
        self.assertIn(
            _KNOWN_SWITCH, read, "the scan found no switch; it is not looking"
        )
        shipped, _ = _project_reads(tests=False)
        for name in sorted(_EXEMPT_READS):
            with self.subTest(exempt=name):
                self.assertIn(
                    name,
                    shipped,
                    "an exemption no shipped code reads: remove it",
                )
                self.assertNotIn(
                    name,
                    _declared(),
                    "an exempted variable must never be folded into the cache key",
                )
        for name in sorted(_ISOLATION_READS):
            with self.subTest(isolation=name):
                self.assertIn(name, read, "an isolation read no test makes: remove it")
                self.assertNotIn(
                    name,
                    shipped,
                    "shipped code reads an isolation-only variable: rule on it afresh",
                )
                self.assertNotIn(
                    name,
                    _declared(),
                    "an isolation read must never be folded into the cache key",
                )
        self.assertEqual(
            sorted(read - _EXEMPT_READS.keys() - _ISOLATION_READS.keys()),
            sorted(_declared()),
            "[tool.gate_cache] env in this project's pyproject.toml must list exactly "
            "the variables its code reads by name, or the gate cache replays a verdict "
            "across a change in one of them",
        )
