"""Starting stewards from a client that stays open, one at a time, and collecting each when it ends.

A client such as the desktop app closes many sessions over its life, sometimes dozens at once. Each close starts a
steward, a Python process of its own. Stewards on one drive take turns anyway, so starting them all at once only
costs memory; and a child nobody waits for stays in the process table as a zombie until the client exits.
"""

from __future__ import annotations

import atexit
import subprocess
import threading
from collections import deque
from collections.abc import Callable

Launch = Callable[[], "subprocess.Popen[bytes] | None"]
"""Starts one process; None when it could not start."""


class Launches:
    """Runs each launch given to `start`, the next one when the process before it has ended."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiting: deque[Launch] = deque()
        self._busy = False
        self._registered = False

    def start(self, launch: Launch) -> None:
        """Run `launch` now when no earlier process is still running, else after those before it. The first runs
        before this returns, so a client that exits straight after a close has still started its steward."""
        with self._lock:
            if not self._registered:
                atexit.register(self.flush)
                self._registered = True
            if self._busy:
                self._waiting.append(launch)
                return
            self._busy = True
        self._run(launch)

    def flush(self) -> None:
        """Start every launch still waiting, at once: the client is exiting and will not be here to start them."""
        with self._lock:
            waiting = list(self._waiting)
            self._waiting.clear()
        for launch in waiting:
            launch()

    def _run(self, launch: Launch | None) -> None:
        while launch is not None:
            process = launch()
            if process is not None:
                threading.Thread(target=self._collect, args=(process,), daemon=True, name="steward-wait").start()
                return
            launch = self._next()

    def _collect(self, process: subprocess.Popen[bytes]) -> None:
        process.wait()  # takes its exit status, so it does not stay behind as a zombie
        self._run(self._next())

    def _next(self) -> Launch | None:
        with self._lock:
            if self._waiting:
                return self._waiting.popleft()
            self._busy = False
            return None
