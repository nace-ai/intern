"""Notifications for the operator who is doing something else: tell them the run needs them, or
that it finished. Three ways out, all best-effort and never in the way of the work itself:

- a desktop notification (macOS ``osascript``, Linux ``notify-send``; absent → no-op);
- the terminal bell, which most terminals turn into a badge or a bounce;
- a webhook (``INTERN_NOTIFY_URL``): a POST of ``{"text": …}``, the shape Slack, Mattermost,
  Discord and most chat tools accept. The text names the target, so point it only at a place
  that may know it.

On by default; ``INTERN_NOTIFY=0`` silences everything. Delivery is fire-and-forget with a 5 s
timeout and never raises into the caller.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from typing import Protocol

import httpx

SILENT = re.compile(r"^(0|false|off|no)$", re.IGNORECASE)
#: How long a run must have lasted before its end is worth a notification (INTERN_NOTIFY_AFTER).
DEFAULT_AFTER_S = 60.0
WEBHOOK_TIMEOUT_S = 5.0


def _one_line(text: str, max_: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= max_ else flat[: max_ - 1] + "…"


def _applescript(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def desktop_command(title: str, text: str, platform: str, has: Callable[[str], bool]) -> tuple[str, list[str]] | None:
    """The command that shows a desktop notification on this machine, if there is one."""
    t, body = _one_line(title, 80), _one_line(text, 240)
    if platform == "darwin":
        return "osascript", ["-e", f"display notification {_applescript(body)} with title {_applescript(t)}"]
    if platform == "linux" and has("notify-send"):
        return "notify-send", ["--app-name=intern", "--", t, body]
    return None


class RunFn(Protocol):
    def __call__(self, cmd: str, args: list[str]) -> None: ...


class PostFn(Protocol):
    def __call__(self, url: str, payload: dict[str, str]) -> None: ...


def _popen(cmd: str, args: list[str]) -> None:
    with contextlib.suppress(OSError):
        subprocess.Popen([cmd, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _bell() -> None:
    try:
        sys.stderr.write("\a")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass  # no terminal


def _thread_post(url: str, payload: dict[str, str]) -> None:
    def deliver() -> None:
        with contextlib.suppress(Exception):
            httpx.post(url, json=payload, timeout=WEBHOOK_TIMEOUT_S)

    threading.Thread(target=deliver, daemon=True).start()


def duration(seconds: float) -> str:
    s = max(1, round(seconds))
    if s < 90:
        return f"{s} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


class Notifier:
    """Fire-and-forget notices on every channel that is set up. Never raises."""

    def __init__(
        self,
        *,
        platform: str | None = None,
        has: Callable[[str], bool] | None = None,
        run: RunFn | None = None,
        bell: Callable[[], None] | None = None,
        post: PostFn | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.platform = platform or sys.platform
        self.has = has or (lambda cmd: shutil.which(cmd) is not None)
        self.run = run or _popen
        self.bell = bell or _bell
        self.post = post or _thread_post
        self.env = env if env is not None else os.environ
        self.enabled = not SILENT.match(self.env.get("INTERN_NOTIFY", "").strip() or "")

    @property
    def after_seconds(self) -> float:
        raw = self.env.get("INTERN_NOTIFY_AFTER", "").strip()
        try:
            n = float(raw)
        except ValueError:
            return DEFAULT_AFTER_S
        return n if n >= 0 else DEFAULT_AFTER_S

    @property
    def webhook(self) -> str | None:
        url = self.env.get("INTERN_NOTIFY_URL", "").strip()
        return url if re.match(r"^https?://", url, re.IGNORECASE) else None

    def send(self, title: str, text: str, *, force: bool = False) -> list[str]:
        """Send one notice every way that is set up; returns the ways that were used."""
        if not self.enabled and not force:
            return []
        used: list[str] = []
        desktop = desktop_command(title, text, self.platform, self.has)
        if desktop is not None:
            cmd, args = desktop
            try:
                self.run(cmd, args)
                used.append("desktop")
            except Exception:
                pass
        try:
            self.bell()
            used.append("bell")
        except Exception:
            pass  # no terminal
        url = self.webhook
        if url is not None:
            payload = {"text": f"{_one_line(title, 80)}: {_one_line(text, 400)}"}
            try:
                self.post(url, payload)
                used.append("webhook")
            except Exception:
                pass
        return used

    def run_needs_you(self, reason: str, *, title: str = "intern") -> list[str]:
        """A run is blocked on the operator: every channel, immediately."""
        return self.send(title, f"Needs you: {_one_line(reason, 200)}")

    def run_ended(self, target: str, duration_s: float, *, title: str = "intern") -> list[str]:
        """A run ended: worth a notice only when it lasted long enough to look away."""
        if duration_s < self.after_seconds:
            return []
        return self.send(title, f"{target}: run ended after {duration(duration_s)}; it is waiting for you.")
