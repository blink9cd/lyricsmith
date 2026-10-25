# stdio_utf8

Force UTF-8 on a tool's own text streams, name the encoding policy for child-process
captures, and escape a message that quotes argv or a file name. A pure library (no CLI, no
`__main__`) imported by this project's tools — it owns the one decision each of them needs to
make identically, so the policy lives in one place instead of a copy per tool that drifts.

## Why this exists

Python derives a text stream's encoding from the **locale** whenever that stream is not a
terminal. On Windows that is the legacy ANSI code page (cp1252 on a Western install), which cannot
encode most non-ASCII characters. Two consequences, in opposite directions, from the same cause.

**Write side.** A tool that prints a path it discovered on disk works when run by hand in a UTF-8
terminal and raises `UnicodeEncodeError` the moment stdout is a pipe — which is every scheduled
run, every CI run, and every run whose parent captures it. The bug class is invisible
interactively and certain in automation.

**Read side.** `subprocess.run(..., text=True)` with no explicit `encoding` decodes the child
using the same locale code page. A child emitting UTF-8 therefore decodes to mojibake — or, for a
byte undefined in cp1252 (`0x81`, `0x8D`, `0x8F`, `0x90`, `0x9D`, reachable from Cyrillic, Greek,
and CJK names), raises `UnicodeDecodeError` **on subprocess's reader thread**, where it is
swallowed: `run()` returns with `stdout=None` and the caller fails later with a confusing
`TypeError` that names neither encoding nor the child.

**The two halves are coupled and must be fixed together.** A cp1252 decode followed by a cp1252
encode restores the original bytes *by accident*, which is why a capture-and-reprint tool can look
correct today. Forcing only the output stream to UTF-8 turns that working accident into
double-encoded corruption:

| Stage | Bytes |
| --- | --- |
| Child emits (UTF-8 `→`) | `e2 86 92` |
| Parent captures, cp1252 decode | `U+00E2 U+2020 U+2019` (mojibake) |
| Reprinted to a cp1252 stream | `e2 86 92` — correct, by luck |
| Reprinted to a **UTF-8** stream | `c3 a2 e2 80 a0 e2 80 99` — **corrupted** |

So a tool that captures a child and reprints it takes **both** changes, in the same commit, or
neither.

## API

```python
from stdio_utf8 import (
    CAPTURE_ERRORS, ENCODING, ESCAPE_ERRORS, EscapingArgumentParser,
    force_utf8_streams, print_escaped, printable,
)

force_utf8_streams(sys.stdout, sys.stderr)   # -> the stream names actually changed
print_escaped(f"tool: {line}", sys.stderr)    # one message line; never raises on a surrogate
EscapingArgumentParser(prog=...)             # argparse, every message escaped
printable(text)                               # the escape alone, for a message printed elsewhere
ENCODING                                      # "utf-8" -- both directions
CAPTURE_ERRORS                                # "replace" -- child captures only
ESCAPE_ERRORS                                 # "backslashreplace" -- stderr and messages
```

`force_utf8_streams` reconfigures each argument to UTF-8 and returns the names it changed. The
process's **stderr keeps `backslashreplace`**, the handler Python opened it with; **every other
stream, stdout above all, is strict**. The role is read by identity at the call: the stream that
*is* `sys.stderr` keeps the escape, so a tool hands over `sys.stdout, sys.stderr` as they stand,
and any other stream it passes is forced strict. One stream serving as both — `sys.stderr`
pointed at `sys.stdout` — is strict, because data is on it. It forces only the streams it is handed, so it requires at least one, positionally: a
call passing none would force nothing while looking, at the call site, as if it forced the
process's own streams. That call is a type-check error and a `TypeError` rather than a no-op.
Pass `sys.stdout, sys.stderr` explicitly.

Streams it cannot reconfigure are skipped rather than treated as an error, so adopting it can never
destabilize an existing test harness:

- `io.StringIO` — what a test substitutes for stdout — has no `reconfigure` method.
- A closed or detached stream raises `ValueError`.

### Why stdout is strict, stderr escapes, and captures `replace`

Three streams, three policies, each for its own reason.

- **stdout: strict.** It carries a tool's data — reports, paths, JSON — and a report that silently
  mangles a path is worse than one that fails loudly: a PII finding whose path is corrupted is
  actively misleading. A report never prints a path that is not the real path; one it cannot
  print exactly is a loud failure, on purpose.
- **stderr: `backslashreplace`.** It carries diagnostics, and the one that matters most here
  quotes what the operator typed. A refusal of a mistyped argument must print, and exit with the
  tool's own code, rather than become a traceback and exit 1 — which, for a tool whose exit 1
  means "found something", reads as a verdict. An escaped byte in a diagnostic misleads no one:
  it names the byte that could not be decoded. `reconfigure(encoding=...)` alone would reset the
  handler to strict, so `force_utf8_streams` names it. This replaced a strict-on-both rule under
  which every diagnostic quoting argv or a file name — argparse's errors among them — crashed.
- **Child captures: `replace`.** A child's bytes are *not* ours to guarantee, and one stray byte
  from a third-party linter must not abort a whole gate.

Accepted trade-off: a child that genuinely emits cp1252 loses fidelity to `U+FFFD` where it
previously decoded. UTF-8 is what this tool family and the node-based linters it drives actually
emit, so this is net-positive for path fidelity.

### A message that quotes argv or a file name

Python decodes argv and file names with `surrogateescape`, so a byte that is not UTF-8 — a
mistyped argument, a name from a filesystem that accepts any bytes — arrives as a **lone
surrogate**, which a strict stream refuses. The tool's own message then becomes a
`UnicodeEncodeError` traceback and exit 1.

**On stderr the stream handles it**, with no change at the call site: a plain
`print(..., file=sys.stderr)`, a logging handler writing there, and argparse's usage and errors
all print the escape once `force_utf8_streams` has run.

**On stdout the MESSAGE must be escaped**, because the stream stays strict for data. That is
argparse's help and `--version`, which go to stdout, and any human-facing line a tool prints
there. One boundary covers it:

- **`print_escaped(line, stream)`** for a human-facing line: a refusal, a warning, progress. The
  stream is always named, as `force_utf8_streams` takes its streams. On stderr it is harmless and
  redundant; on stdout it is what keeps the line from raising.
- **`EscapingArgumentParser`** in place of `argparse.ArgumentParser`. argparse writes usage,
  help, errors and `--version` through one method, `_print_message`, which it overrides.
  `add_subparsers` builds each sub-parser from the parent's class, so sub-parsers inherit it.
  Its errors already escape on stderr; what it adds is help and `--version` on stdout — under a
  `prog` taken from an undecodable `argv[0]`, say.
- **`printable(text)`**, the escape alone, for a message built in one place and printed in
  another. It changes only a lone surrogate, spelled as a backslash, `u` and four hex digits,
  and escaping its result again changes nothing.

A tool's **data** does not go through any of these: a path in a report that is not the real path
is the silent corruption the strict stdout exists to refuse. Escaping once around a single call,
or catching the `UnicodeEncodeError` there, fixes one route and leaves the next one open.

**Test with a strict stream, never `io.StringIO`.** `StringIO` stores text without encoding it,
so it accepts the surrogate a real stream refuses, and a test written over it passes with the
defect present. Use `io.TextIOWrapper(io.BytesIO(), encoding="utf-8")`, as
`tests/test_escaping.py` does, and pair it with a guard proving the raw write raises. To test
stderr's half, redirect that stream INTO `sys.stderr` before forcing, as
`tests/test_streams.py` does: the role is read by identity, so a stream handed over on its own
is forced strict.

## How this project's tools import it

Every tool in this project, and the dispatcher's own test, puts each shared library on
`sys.path` from **one fixed place**: the folder of that name directly under the project
root, derived from the tool's own `__file__` at the depth the tool sits. A tool one folder
below the root, such as `album/album.py`, appends `parents[1]`:

```python
sys.path += [
    str(Path(__file__).resolve().parents[1] / name)
    for name in ("stdio_utf8", "help_formatter")
]
```

**One fixed candidate per name, not a search up the ancestors.** A search would also match
a same-named folder above the checkout, in a shared temporary folder or the home folder,
and import it in place of the real one. A fixed candidate can name only the copy this
repository ships, so nothing outside the tree is ever imported, and a missing copy fails at
once with Python's `No module named '<name>'`. The entries join at the END of `sys.path`,
so none of them can shadow the standard library or the tool's own modules. The upward
search described below is the portable form for a tree whose layout is not fixed; this
project's layout is.

## Importing it from a sibling tool

`stdio_utf8` lives in its own per-tool folder, so a sibling tool adds it to `sys.path` (derived
from `__file__`, location-independent) before importing — and the project's `pyproject.toml`
lists `stdio_utf8` on `[tool.mypy] mypy_path` so the import resolves when mypy type-checks a
tool's folder in isolation:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stdio_utf8"))
import stdio_utf8
```

No `# noqa: E402` on the import: ruff reads a `sys.path` call or augmented assignment as a
path modification rather than the end of the import block, so E402 does not fire after either
form on this page.

### Importing it from a tree with no sibling — the portable form

The fixed relative path above resolves **only** for a tool in a folder beside this library. A
tool deeper in a tree has no sibling `stdio_utf8/`, so it searches **upward** from its own file
instead. One expression accepts both layouts the library can be found in: a `utilities/` folder
of shared libraries, or a copy vendored beside the tools, as in this repository:

```python
# Resolve the shared UTF-8 stdio policy library by searching upward from this file: it lives at
# <repo>/utilities/<name>/ in a tree that keeps its shared libraries there, or at
# <root>/stdio_utf8/ in one that vendors it beside its tools. See stdio_utf8/stdio_utf8.md.
sys.path += [
    str(folder)
    for ancestor in Path(__file__).resolve().parents
    for folder in (ancestor / "utilities" / "stdio_utf8", ancestor / "stdio_utf8")
    if (folder / "stdio_utf8.py").is_file()
][:1]

import stdio_utf8
```

Three deliberate details:

- **Only the nearest match joins, and it joins at the END.** The search walks every ancestor up
  to the filesystem root, so it also matches a same-named folder *above* the tree — a shared
  temporary directory, or the primary checkout that every task worktree sits inside.
  `Path.parents` is ordered nearest-first, so `[:1]` keeps the copy closest to the tool and drops
  every farther one. Appending rather than prepending covers the case the slice cannot: when a
  planted folder is the *only* match, it can supply the library the tool asked for, but the
  standard library and the tool's own modules stay ahead of it, so nothing else in it is ever
  imported. The earlier form prepended every match, and a `json.py` planted beside a farther copy
  ran in place of the standard library's. `TestPortableBootstrapResolution` in
  `tests/test_bootstrap.py` pins the two halves separately, because either one alone passes a test
  that plants a decoy above a real copy, and `reliability_lint`'s RL006 refuses any new search
  that drops either half.
- **A comprehension, not a loop with a `break`.** The loop form leaves a branch that only ever
  resolves one way per run — a partial branch the coverage gate would flag at every adopting site.
  The comprehension runs at import and naturally exercises both outcomes (most ancestors lack the
  library, one has it), and the `[:1]` that keeps the nearest match is a slice, not a branch, so no
  adopting site needs a pragma.
- **An empty result is not silently tolerated.** Nothing joins `sys.path`, and the following
  `import` raises a plain `ImportError` naming the module. A tool that cannot establish its output
  encoding should fail loudly, not quietly reintroduce the bug this library exists to remove.

**The same search reaches a project's own sibling folders.** A tool that also imports a library
from its own project lists every name, and there the bare `ancestor / name` arm does real work: it
finds the project's sibling folder, while the `utilities/` arm finds this library. The `[:1]`
moves inside, one slice per name, because a single slice over the whole list would keep only the
first library found:

```python
sys.path += [
    nearest
    for name in ("stdio_utf8", "feed_read")
    for nearest in [
        str(folder)
        for ancestor in Path(__file__).resolve().parents
        for folder in (ancestor / "utilities" / name, ancestor / name)
        if (folder / f"{name}.py").is_file()
    ][:1]
]
```

**For this library, the second candidate resolves only where a copy was vendored.** A tree shipped
without `utilities/` must carry `stdio_utf8/` at its root itself. Leaving the copy out
still builds, then fails at the recipient's first run with the `ImportError` described above. So a
packaging step that ships a tool out of this repository carries the folder and pins it with a test.

Then, as the first statement in `main()` — before any logging setup, so a warning emitted during
startup is also safe:

```python
stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
```

That call is also what makes stderr escape a lone surrogate, so a diagnostic there needs nothing
more. A tool that prints a message to **stdout** quoting argv or a file name also prints that
line through `stdio_utf8.print_escaped`, and builds its parser from
`stdio_utf8.EscapingArgumentParser` for help and `--version` (see "A message that quotes argv or
a file name" above).

And at every subprocess seam that decodes text, replacing `text=True` (which is redundant once
`encoding` is passed):

```python
proc = subprocess.run(
    argv,
    capture_output=True,
    encoding=stdio_utf8.ENCODING,
    errors=stdio_utf8.CAPTURE_ERRORS,
)
```

## Tests

```text
python stdio_utf8/test_stdio_utf8.py
```
