"""The steward a closing session starts, in a client that stays open and closes many sessions."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from intern import config
from intern.config import Bootstrap, Options
from intern.free_session import FreePlan, FreeSession


def test_sessions_closed_together_start_their_stewards_in_turn_and_leave_no_zombie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    made: list[subprocess.Popen[bytes]] = []
    still_running: list[int] = []

    def popen(argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        # A process is collected once `returncode` is set: only waiting for it sets that.
        still_running.append(sum(process.returncode is None for process in made))
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.1)"], stdout=kwargs["stdout"])
        made.append(process)
        return process

    monkeypatch.setattr(config, "subprocess", SimpleNamespace(Popen=popen, DEVNULL=subprocess.DEVNULL))
    journal = tmp_path / "session.jsonl"
    journal.write_text("")
    plan = FreePlan(use=False)
    bootstrap = Bootstrap(Options(cwd=tmp_path), {"INTERN_HOME": str(tmp_path / "home")})
    on_close = bootstrap._steward_on_close(journal, tmp_path / "drive", "fake/model", FreeSession(plan, plan.sources()))
    closed: Any = SimpleNamespace(last_outcome=object())
    for _ in range(4):
        on_close(closed)
    assert len(made) == 1  # the first is started before the close returns, the rest wait their turn

    deadline = time.monotonic() + 20
    while (len(made) < 4 or any(process.returncode is None for process in made)) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(made) == 4 and still_running == [0, 0, 0, 0]
    for process in made:
        with pytest.raises(ChildProcessError):  # already collected, so nothing is left in the process table
            os.waitpid(process.pid, os.WNOHANG)
