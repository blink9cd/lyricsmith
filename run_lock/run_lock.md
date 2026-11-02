# run_lock.py

An exclusive lock on a file, held for a process's lifetime and released by the kernel when the
process dies, **SIGKILL included**, plus a probe that asks whether it is held without taking it.

## Why it exists

A job that must never run twice, and a reader that must tell a RUNNING job from a DEAD one, need
the same primitive. With a kernel-released lock, "the record says in progress and the lock is
free" means the holder died, with no process-ID guesswork. A recorded process ID proves little: IDs
recycle fast on a busy machine.

## Public surface

| Name | What it does |
| --- | --- |
| `acquire(path) -> Lock \| None` | Takes the lock without waiting. Returns `None` when another holder has it. Creates `path` and its parents. Any other failure raises `OSError`. |
| `Lock.release()` | Drops the lock if still held; a second call is a no-op. |
| `with lock:` | Releases on exit, whether or not the block raised. |
| `is_held(path) -> bool` | Probes without waiting and without creating the file. An absent file is free. An existing file that cannot be opened raises `OSError`. |

## Behavior worth knowing

- **Per open file, not per process.** `flock(2)` on POSIX and `msvcrt.locking` on Windows both
  lock an open file. A second `acquire` of the same path is refused even inside the process that
  holds the first, which is what lets the tests exercise contention in one process.
- **The lock file is never deleted.** Unlinking it races a concurrent opener: the second process
  can lock a file the first has just removed, and two holders then each own "the" lock.
- **Windows frees a dead holder's lock a moment late.** It releases a terminated process's
  byte-range locks after the process exits rather than as it exits. "Free" still proves the holder
  died; only "held" can briefly outlive it, so a reader can see a just-killed job as running once.
- **The probe can briefly contend.** `is_held` takes the lock and lets it go at once. A real
  `acquire` landing in that instant returns `None`, the same answer as losing to any other holder.
  A caller that retries on a schedule loses one turn, never a lock.

## Using it

It is a sibling library, imported from a fixed relative path:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "run_lock"))
import run_lock

lock = run_lock.acquire(state_dir / "run.lock")
if lock is None:
    raise SystemExit(0)  # another run holds it
with lock:
    ...
```

## Tests

`test_run_lock.py` covers contention, release, probing held and free, the descriptor closed both
on a refused acquire and on a non-contention error, and a live boundary test: a second process
takes the lock, is SIGKILLed, and the probe then reads the lock free (on Windows, polled for
until it does, for the late release above).
