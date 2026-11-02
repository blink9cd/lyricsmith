# lyricsmith_project

The project's own manifest reader, shared by every tool: the project's version, and the
folders of the shared libraries the tools import. It is a library with no command line,
and imports nothing outside the standard library, because it runs before any shared
library can be imported.

## Where the shared libraries come from

The project's `pyproject.toml` names each shared library folder under
`[tool.lyricsmith] shared_libraries`, in the order they go on `sys.path`, each path
relative to that `pyproject.toml`'s own folder, never to the working folder. The same code
then runs in any layout the list describes: in a tree that keeps its shared libraries in
a folder beside the project, the paths climb out to it; in the published repository, each
library is a folder directly under the root, beside the tools.

Every entry file (`lyricsmith.py`, each tool's `<tool>.py`, `album/samples`'s builder
and sync's child process) begins the same way: it puts this folder first on `sys.path`,
from its own `__file__` at one fixed depth, moving it there when it is already on the
path behind another folder, so no same-named module can stand in for this one, then
imports this module and calls `add_shared_libraries()`, exiting 2 on a
failure when it runs as a script:

```python
_PROJECT_DIR = Path(__file__).resolve().parents[1]
_READER_DIR = str(_PROJECT_DIR / "lyricsmith_project")
sys.path[:] = [_READER_DIR, *(entry for entry in sys.path if entry != _READER_DIR)]
import lyricsmith_project

lyricsmith_project.add_shared_libraries(exit_on_failure=__name__ == "__main__")
```

This folder holds only the module and its suite, so putting it first shadows nothing.
`sweep.py` and `sync.py` also put their sibling tools' folders on the path, at the end,
and call `confirm_from()` for each tool's entry module before importing it, so a planted
`album.py` earlier on the path is refused rather than run.

**The private modules.** A module under a tool's `src/` that imports a shared library,
or a module of another tool, first imports that tool's located module:
`album/src/lyrics_located.py`, `transcribe/src/at_located.py`, `sweep/src/sweep_located.py`
(which adds the album tool's folder and its `src/` too) and `sync/src/sy_located.py`
(which adds the album, sweep and transcribe tools and their `src/`). Each holds the
bootstrap above once, raising rather than exiting, so every private module imports alone,
in an interpreter whose path holds only its own `src/`, whatever was imported before it.
Each tool's suite holds this two ways: a read of every `src/` module's top-level imports,
which must name the located module before any shared library, and a fresh interpreter
per module. `transcribe/tests/__init__.py` also makes the call itself, so every
transcribe suite can import a private module before anything has imported the tool.

**Why a list, not a search.** A search up a file's ancestors would also match a
same-named folder above the checkout, in a shared temporary folder or the home folder,
and import it in place of the real one; and where it stops depends on markers (`.git`,
a `pyproject.toml`) that a copy of the tree may lack. Nothing searches here: only the
folders the list names are added. They join at the end of `sys.path`, once each, so none
can shadow the standard library or a tool's own modules, and calling it again adds
nothing. Then each library's module must load from its named folder, checked for every
library before any is imported: a same-named module earlier on `sys.path` (`PYTHONPATH`,
site-packages, the script's own folder) would be found first, so it fails the run,
naming both, rather than being run in its place. The folder compared is the one the
module's file sits in, so a module file that is itself a link to a file elsewhere still
loads from its named folder.

**How it fails.** Every entry is checked before any is added, so a run gets all its
libraries or none. A `SharedLibraryError`, an `ImportError`, names the problem, the key
and the `pyproject.toml`:

| Problem | Message |
| --- | --- |
| The file is missing, unreadable, malformed or not UTF-8 | `cannot read [tool.lyricsmith] shared_libraries from <file>: …` |
| No such key, or not a list | `<file> names no [tool.lyricsmith] shared_libraries list` |
| An entry that is not text, or is blank | `… <entry> is not a folder path` |
| An absolute entry (a root, a drive, a share) | `… <entry> is absolute; name each folder relative to the pyproject.toml's own folder` |
| A named folder that does not hold its same-named module | `shared library '<entry>' is missing: no <name>.py in <folder> (…)` |
| A same-named module earlier on `sys.path`, or one already imported from elsewhere | `shared library '<name>' would load from <file>, not from <folder> (…): a same-named module earlier on sys.path shadows it` |
| A module already imported, or found, with no file: a built-in or a namespace package | `shared library '<name>' would load with no file, as a built-in or namespace module, …` |
| A sibling tool's entry module shadowed the same way (`confirm_from`) | `sibling tool module '<name>' would load from <file>, not from <folder> (…)` |

An empty list is valid and adds nothing. With `exit_on_failure`, the error is printed on
stderr after the script's name, each unprintable character spelled as its escape, and the
process exits `2`, the code `lyricsmith.md` gives for it; without it, the error is raised.
The dispatcher imports the tool it runs, so the tool raises; it catches the error and
exits through `exit_with()` the same way.

## The version

`project_version()` returns `[project].version` from the same `pyproject.toml`, or
`unknown` when the file is missing, malformed, not UTF-8, has no `[project]` table, or
holds a version that is not text. It is cached, because every provider request's
User-Agent carries it and the file does not change during a run. The dispatcher's
`--version`, the album tool's User-Agent, and the labels transcribe and sync write all
read it here.

## API

| Name | What it is |
| --- | --- |
| `PROJECT_ROOT`, `PROJECT_CONFIG` | The project root, this folder's parent, and its `pyproject.toml`: every reader's default. |
| `UNKNOWN_VERSION` | `unknown`. |
| `CONFIG_KEY` | The key, as every error spells it. |
| `project_version(config=PROJECT_CONFIG)` | The version, or `UNKNOWN_VERSION`. |
| `shared_library_folders(config=PROJECT_CONFIG)` | Each named folder, resolved, in order; raises `SharedLibraryError`. |
| `add_shared_libraries(config=PROJECT_CONFIG, search_path=None, *, exit_on_failure=False)` | Appends the folders not already on `search_path` (`sys.path` when None), checks each library loads from its own, and returns them. |
| `confirm_from(name, folder, *, search_path=None, exit_on_failure=False)` | Fails unless module `name` loads from `folder`: a sibling tool's, checked before it is imported. |
| `exit_with(error)` | Prints *error* on stderr after the script's name, escaped, and exits `EXIT_CANNOT_LOCATE`: what `exit_on_failure` does. |
| `EXIT_CANNOT_LOCATE` | `2`, the exit code of `exit_on_failure`. |
| `SharedLibraryError` | The error every locator failure raises. |

## Tests

`test_lyricsmith_project.py` reads invented `pyproject.toml` files beside invented
library folders in a scratch folder: the version's fallbacks, each folder resolved
relative to the file and not the working folder, a same-named folder above the project
never found, every shape the key is refused in, nothing added when any entry fails,
each folder added once, a same-named module earlier on the path or already imported from
elsewhere refused before anything is imported, and the exit 2 an entry file takes. Its last case holds this project's own list equal, in order, to
the folders its export vendors (`[tool.project_export].vendor`), deriving the repository
root from the two lists rather than naming it; a tree without that table skips it. The
dispatcher's suite, `test_lyricsmith.py`, runs every entry file's `--help`, and imports
each private module that runs the locator itself, in a copy of the project whose list
names each library in a folder below the copy's root, with a decoy of each library at
that root and above the copy: with any one library missing, each fails naming it, an
entry file exiting 2; with all in place, each loads every library from its named folder
and never a decoy; and with a folder of planted modules just after the script's own, as
`PYTHONPATH` would put it, a planted library or sibling tool is refused and a planted
reader never imported. It finds the files it runs by reading every file for a call of the
locator, so a new one is covered without being listed.

Run the reader's suite as a script, from anywhere:

```bash
python lyricsmith_project/test_lyricsmith_project.py
```

Not as `python -m unittest lyricsmith_project/test_lyricsmith_project.py` from the
project folder: that imports the folder as a namespace package of the module's own name,
which then shadows the module the suite imports.
