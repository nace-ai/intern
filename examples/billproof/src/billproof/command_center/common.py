"""Paths, spend caps, JSONL, and metric envelopes for the command center."""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from billproof.config import DATA_DIR, PKG_ROOT

CC_DIR = DATA_DIR / "command_center"
METRICS_DIR = DATA_DIR / "metrics"
LOG_DIR = PKG_ROOT / "out" / "logs"
LOCK_PATH = PKG_ROOT / "out" / "cc_spend.lock"
DREX_LOCK_PATH = PKG_ROOT / "out" / "cc_drex.lock"
# Full per-case rows. gitignored with billproof/out/. Scripts regenerate it.
QUEUE_PATH = PKG_ROOT / "out" / "command_center" / "queue.jsonl"
# Committed stream file: case id, decision, colour, time, cost, correct.
REPLAY_PATH = CC_DIR / "queue_replay.jsonl"
GPT_PATH = CC_DIR / "queue_gpt.jsonl"
GPT_IDS_PATH = CC_DIR / "gpt_sample_ids.json"
QUEUE_SUMMARY_PATH = CC_DIR / "queue_summary.json"
RULE_PATH = CC_DIR / "rule_change.jsonl"
RULE_SUMMARY_PATH = CC_DIR / "rule_change_summary.json"
DECIDE_PATH = CC_DIR / "what_decides.jsonl"
DECIDE_SUMMARY_PATH = CC_DIR / "what_decides_summary.json"
SPEND_PATH = CC_DIR / "spend.json"
DOC_PATH = PKG_ROOT / "docs" / "COMMAND_CENTER.md"
# Full-scale queue . Fact rows stay under out/, which is gitignored.
# The committed replay is the compact file under data/command_center/.
FULL_QUEUE_PATH = PKG_ROOT / "out" / "command_center" / "full_queue.jsonl"
FACTS_PATH = PKG_ROOT / "out" / "command_center" / "facts.jsonl"
NEW_CRITERION_PATH = PKG_ROOT / "out" / "command_center" / "new_criteria.jsonl"
FULL_SPEND_PATH = PKG_ROOT / "out" / "command_center" / "full_spend.json"
FULL_LOCK_PATH = PKG_ROOT / "out" / "cc_full_spend.lock"
POLICIES_PATH = CC_DIR / "policies.json"
CASES_PATH = CC_DIR / "cases_index.jsonl"
FULL_SUMMARY_PATH = CC_DIR / "full_summary.json"
FULL_REPLAY_PATH = CC_DIR / "full_replay.jsonl"
PRESETS_PATH = CC_DIR / "presets.json"
FULL_NACE_CAP_USD = 1.00
NEW_CRITERION_CAP_USD = 0.05

# budget. Caps sit under the ceiling so one in-flight call can finish.
NACE_CAP_USD = 0.90
ROUTER_CAP_USD = 0.45
DREX_MODEL = "drex-max-v1.1"
GPT_MODEL = "openai/gpt-6.1-sol"
GPT_SEED = 20261114
GPT_N = 40
MAX_QUESTIONS = 200

_SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}|nace_sk_[A-Za-z0-9_-]+|ndi_sk_[A-Za-z0-9_-]+|"
    r"Bearer [A-Za-z0-9._\-]+"
)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def scrub(text: str, limit: int = 400) -> str:
    return _SECRET.sub("[redacted]", text or "")[:limit]


def ensure_dirs() -> None:
    CC_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    atomic_write(path, json.dumps(payload, indent=2, default=str) + "\n")


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
        fh.flush()


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def error_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in load_jsonl(path):
        cid = row.get("case_id")
        if cid and row.get("error"):
            counts[cid] = counts.get(cid, 0) + 1
    return counts


def success_ids(path: Path) -> set[str]:
    done: set[str] = set()
    for row in load_jsonl(path):
        if row.get("case_id") and not row.get("error"):
            done.add(row["case_id"])
    return done


def latest_success(path: Path) -> dict[str, dict]:
    """Last successful row per case_id. Error rows do not evict a success."""
    out: dict[str, dict] = {}
    for row in load_jsonl(path):
        cid = row.get("case_id")
        if cid and not row.get("error"):
            out[cid] = row
    return out


def load_spend() -> dict[str, float]:
    raw = read_json(SPEND_PATH, {})
    return {
        "nace_usd": float(raw.get("nace_usd") or 0.0),
        "router_usd": float(raw.get("router_usd") or 0.0),
    }


def add_spend(lane: str, amount: float) -> dict[str, float]:
    """lane is 'nace' or 'router'. Amount is USD, may be 0."""
    key = "nace_usd" if lane == "nace" else "router_usd"
    with file_lock(LOCK_PATH):
        spend = load_spend()
        spend[key] = round(spend[key] + float(amount or 0.0), 8)
        write_json(SPEND_PATH, {**spend, "updated": now_iso(),
                                "nace_cap": NACE_CAP_USD, "router_cap": ROUTER_CAP_USD})
        return spend


def can_spend(lane: str, estimate: float) -> bool:
    cap = NACE_CAP_USD if lane == "nace" else ROUTER_CAP_USD
    with file_lock(LOCK_PATH):
        spend = load_spend()
        key = "nace_usd" if lane == "nace" else "router_usd"
        return spend[key] + float(estimate) <= cap


def estimate_nace_usd(state: dict, n_questions: int) -> float:
    chars = len(json.dumps(state, default=str)) + n_questions * 800
    return max(0.00002, chars / 4.0 * 0.03 / 1_000_000 * 1.35)


def load_full_spend() -> dict[str, float]:
    raw = read_json(FULL_SPEND_PATH, {})
    scale = float(raw.get("scale_usd") or 0.0)
    criterion = float(raw.get("criterion_usd") or 0.0)
    new = float(raw.get("new_usd") or 0.0)
    if not new and (scale or criterion):
        new = scale + criterion
    return {"scale_usd": scale, "criterion_usd": criterion, "new_usd": new}


def add_full_spend(amount: float, *, lane: str) -> dict[str, float]:
    """lane is 'scale' (the full queue) or 'criterion' (one new question)."""
    with file_lock(FULL_LOCK_PATH):
        spend = load_full_spend()
        value = float(amount or 0.0)
        spend["new_usd"] = round(spend["new_usd"] + value, 8)
        key = "criterion_usd" if lane == "criterion" else "scale_usd"
        spend[key] = round(spend[key] + value, 8)
        write_json(FULL_SPEND_PATH, {
            **spend,
            "updated": now_iso(),
            "cap_usd": FULL_NACE_CAP_USD,
            "criterion_cap_usd": NEW_CRITERION_CAP_USD,
        })
        return spend


def full_spend_ok(amount: float, *, lane: str) -> bool:
    with file_lock(FULL_LOCK_PATH):
        spend = load_full_spend()
        if spend["new_usd"] + float(amount) > FULL_NACE_CAP_USD + 1e-9:
            return False
        if lane == "criterion" and spend["criterion_usd"] + float(amount) > NEW_CRITERION_CAP_USD + 1e-9:
            return False
        return True


def write_metric(name: str, value: Any, n: int, source_run: str, *,
                 simulated: bool, projected: bool = False, extra: dict | None = None) -> Path:
    payload: dict[str, Any] = {
        "value": value,
        "n": n,
        "source_run": source_run,
        "simulated": simulated,
        "computed_at": now_iso(),
    }
    if projected:
        payload["projected"] = True
    if extra:
        payload.update(extra)
    path = METRICS_DIR / name
    write_json(path, payload)
    return path
