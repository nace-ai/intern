#!/usr/bin/env python3
"""Start criteria-gym after a model's Hard wrapper exits. Persius is last.

Does not kill a running Hard process. Prints a line only when a bench starts,
when Persius is skipped, or when the chain is finished.
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "out" / "logs"
RUNS = ROOT / "data" / "runs"
MODELS = (
    "openai/gpt-5.4-mini",
    "anthropic/claude-haiku-5.5",
    "google/gemini-3.5-flash-lite",
    "deepseek/deepseek-v4.1-flash",
)


def slug(model: str) -> str:
    return model.replace("/", "_")


def log_path(bench: str, model: str) -> Path:
    return LOGS / f"ladder_{bench}_{slug(model)}.log"


def has_marker(bench: str, model: str, marker: str) -> bool:
    path = log_path(bench, model)
    if not path.exists():
        return False
    return marker in path.read_text()


def call_count() -> int:
    path = RUNS / "ladder_router_calls.json"
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text()).get("calls") or 0)
    except (json.JSONDecodeError, ValueError, OSError):
        return 0


def launch(bench: str, model: str) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = log_path(bench, model)
    script = f'''
set -u
cd {ROOT}
model={model!r}
log={str(log)!r}
bench={bench!r}
while true; do
  .venv/bin/python -u scripts/ladder_run.py --model "$model" --test "$bench" >> "$log" 2>&1
  code=$?
  if [ "$code" -eq 0 ]; then echo WRAP_DONE >> "$log"; exit 0; fi
  if [ "$code" -eq 75 ]; then echo WRAP_RETRY >> "$log"; sleep 20; continue; fi
  echo WRAP_FAIL $code >> "$log"
  sleep 30
done
'''
    proc = subprocess.Popen(["bash", "-c", script], start_new_session=True)
    pidfile = LOGS / f"ladder_{bench}_pids.txt"
    with pidfile.open("a") as fh:
        fh.write(f"{proc.pid} {model}\n")
    print(f"STARTED {bench} {model} pid={proc.pid}", flush=True)
    return proc.pid


def main() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    started_pa: set[str] = set()
    started_persius: set[str] = set()
    persius_decided = False
    while True:
        for model in MODELS:
            if model in started_pa:
                continue
            if has_marker("hard", model, "WRAP_DONE") and not has_marker("pa", model, "start pa"):
                launch("pa", model)
                started_pa.add(model)
        hard_closed = all(has_marker("hard", model, "WRAP_DONE") for model in MODELS)
        if hard_closed and not persius_decided:
            calls = call_count()
            if calls >= 1000:
                print(
                    f"SKIP persius calls={calls}",
                    flush=True,
                )
                persius_decided = True
            else:
                pending_pa = [model for model in MODELS if not has_marker("pa", model, "WRAP_DONE")]
                if not pending_pa:
                    for model in MODELS:
                        if model not in started_persius:
                            launch("persius", model)
                            started_persius.add(model)
                    persius_decided = True
        pa_closed = all(has_marker("pa", model, "WRAP_DONE") for model in MODELS)
        persius_closed = (not started_persius) or all(
            has_marker("persius", model, "WRAP_DONE") for model in MODELS
        )
        if persius_decided and pa_closed and persius_closed:
            break
        time.sleep(20)
    print("CHAIN_DONE", flush=True)


if __name__ == "__main__":
    main()
