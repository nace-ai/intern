"""Changing a file in the harness home (`config.toml`, `.env`) while other Intern processes change it too.

Setup, `intern free`, `intern secrets` and the terminal UI each read the file, change one part and write it back.
`editing` holds a lock for that whole read-change-write, so one writer's change is not lost to another's, and
`write_atomic` puts the new text in place in one step, so a reader never sees half a file.
"""

from __future__ import annotations

import contextlib
import os
import stat
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import tomlkit

if sys.platform != "win32":
    import fcntl

OWNER_ONLY = 0o600


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold the lock for `path` (a `.<name>.lock` file beside it) until the block ends. Where there is no `flock`
    the block runs without one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(f".{path.name.lstrip('.')}.lock").open("a") as handle:
        if sys.platform != "win32":
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield  # closing the handle gives the lock up


def write_atomic(path: Path, text: str, mode: int | None = None) -> None:
    """Replace `path` with `text` in one step: written to a file beside it, then renamed over it. The file keeps its
    permissions, or gets `mode` when given; a new one is readable by its owner only. A link stays a link, and the
    file it points to is the one replaced."""
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        try:
            mode = stat.S_IMODE(target.stat().st_mode)
        except OSError:
            mode = OWNER_ONLY
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        with contextlib.suppress(OSError):
            temporary.chmod(mode)
        temporary.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


@contextlib.contextmanager
def editing(path: Path) -> Iterator[tomlkit.TOMLDocument]:
    """The TOML file at `path` to change in the block (empty when there is none), saved when the block ends without
    an error. No other `editing` of the file runs in between, here or in another process."""
    with locked(path):
        document = tomlkit.parse(path.read_text()) if path.is_file() else tomlkit.document()
        yield document
        write_atomic(path, tomlkit.dumps(document))
