#!/usr/bin/env python3
"""Resumable Hard calls on the frozen split.

Lanes: gpt, family-hybrid, billproof-system. The old single-tau hybrid and the
rules-engine lane remain so a finished file can be read, and they are not the
lanes to start.

--model sets FRONTIER_MODEL before settings is cached. cheaper
models can use this script or scripts/ladder_run.py --model. A model other
than gpt-6.1-sol writes its own JSONL and does not reuse the packed GPT rows.

The id list for test is data/runs/ladder_hard_ids.json. Family-hybrid dev is
all 60 dev cases. Family-hybrid test waits until the dev report file exists.
Does not edit the caps in policy.py. Night ceiling is $30 list price
($2 / $10 per 1M). Packed GPT on this script also stays under $28 list.
Does not stop on a clock.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Share the router window with the ladder. F's processes use the default 13.
# 15 is the router cap, so this process can take a slot when 13 are already stamped.
os.environ.setdefault("FRONTIER_RPM", "15")


def _parse():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lane",
        choices=("gpt", "hybrid", "system", "billproof-system", "family-hybrid"),
        required=True,
    )
    parser.add_argument(
        "--model", default="",
        help="Frontier model id. Default is FRONTIER_MODEL from the environment.",
    )
    parser.add_argument("--split", choices=("dev", "test"), default="")
    parser.add_argument("--workers", type=int, default=None, help="Parallel calls. Capped at 4.")
    return parser.parse_args()


ARGS = _parse()
if ARGS.model:
    os.environ["FRONTIER_MODEL"] = ARGS.model
if ARGS.workers is not None and not 1 <= ARGS.workers <= 4:
    raise SystemExit("--workers must be from 1 to 4")

from billproof.adapters.drex import ModelMismatch  # noqa: E402
from billproof.bench.hard.family_tau import load_taus, tau_path  # noqa: E402
from billproof.bench.hard.list_price import (  # noqa: E402
    NIGHT_CAP_USD, ROUTER_FILES, SUBSET_GPT_CAP_USD, SUBSET_GPT_FILE, list_cost, summed,
)
from billproof.bench.hard.policy import DREX_MODELS, NACE_CAP_USD  # noqa: E402
from billproof.bench.hard.runner import (  # noqa: E402
    BILLPROOF_SYSTEM_MODEL, _load_lane_map, append_row, call_billproof_system,
    call_family_hybrid, call_hybrid, call_lane, call_system, done_ids, drex_adapter,
    gpt_adapter, jsonl_path, load_cases, now,
)
from billproof.config import DATA_DIR, RUNS_DIR, settings  # noqa: E402

settings.cache_clear()

STORED_LANE = {
    "gpt": "gpt",
    "hybrid": "hybrid",
    "system": "system",
    "billproof-system": "billproof-system",
    "family-hybrid": "family_hybrid",
}


def model_ok(returned: str, requested: str) -> bool:
    returned = returned.lower()
    requested = requested.lower()
    tail = requested.rsplit("/", 1)[-1]
    return bool(requested) and (requested in returned or tail in returned)


def foreign_model() -> bool:
    return bool(ARGS.model) and "gpt-6.1-sol" not in ARGS.model


def resolve_split() -> str:
    if ARGS.lane == "family-hybrid":
        if ARGS.split not in ("dev", "test"):
            raise SystemExit("family-hybrid requires --split dev or --split test")
        return ARGS.split
    if ARGS.lane == "billproof-system":
        if ARGS.split not in ("", "test"):
            raise SystemExit("billproof-system runs on the frozen 100 test ids")
        return "test"
    if ARGS.split == "dev" and ARGS.lane == "gpt" and not ARGS.model:
        raise SystemExit("the dev GPT prefix is already scored; pass --model for another model")
    return ARGS.split or "test"


def workers_for() -> int:
    if ARGS.workers is not None:
        return ARGS.workers
    if ARGS.lane == "family-hybrid":
        return 4
    if ARGS.lane == "billproof-system":
        return 2
    return 2


def ids() -> list[str]:
    payload = json.loads((RUNS_DIR / "ladder_hard_ids.json").read_text())
    if not payload.get("chosen_before_any_ladder_call"):
        raise SystemExit("ladder_hard_ids.json is not marked frozen before results")
    order = list(payload["run_order"])
    if len(order) != 100:
        raise SystemExit(f"expected 100 frozen ids, found {len(order)}")
    return order


def case_order(split: str) -> list[str]:
    if split == "dev" and ARGS.lane == "family-hybrid":
        rows = load_cases(DATA_DIR / "synthetic" / "hard" / "dev.jsonl")
        order = [row["case_id"] for row in rows]
        if len(order) != 60:
            raise SystemExit(f"expected 60 dev cases, found {len(order)}")
        return order
    return ids()


def cases_for(split: str, order: list[str]) -> dict[str, dict]:
    found = {}
    for record in load_cases(DATA_DIR / "synthetic" / "hard" / f"{split}.jsonl"):
        if record["case_id"] in set(order):
            found[record["case_id"]] = record
    missing = [case_id for case_id in order if case_id not in found]
    if missing:
        raise SystemExit(f"ids missing from {split}.jsonl: {missing[:3]}")
    manifest = json.loads((DATA_DIR / "synthetic" / "hard" / "manifest.json").read_text())
    if not manifest.get("frozen"):
        raise SystemExit("split is not frozen")
    return found


def out_path(split: str) -> Path:
    slug = ARGS.model.replace("/", "__").replace(" ", "_") if foreign_model() else ""
    if ARGS.lane == "gpt":
        if slug:
            return jsonl_path(split, "gpt", f"hard_{split}_gpt__{slug}")
        if split == "test":
            return jsonl_path("test", "gpt", "hard_test_gpt_subset")
        return jsonl_path(split, "gpt")
    if ARGS.lane == "hybrid":
        if split == "test":
            return jsonl_path("test", "hybrid", "hard_test_hybrid_subset")
        return jsonl_path(split, "hybrid")
    if ARGS.lane == "system":
        return jsonl_path(split, "system")
    if ARGS.lane == "billproof-system":
        return jsonl_path("test", "billproof-system")
    suffix = f"__{slug}" if slug else ""
    return jsonl_path(split, "family_hybrid", f"hard_{split}_family_hybrid{suffix}")


def reused_gpt(order: list[str]) -> set[str]:
    """Full packed rows already bought for this prompt. Do not pay twice."""
    done = set()
    path = jsonl_path("test", "gpt")
    if not path.exists():
        return done
    last: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("case_id"):
                last[row["case_id"]] = row
    wanted = set(order)
    for case_id, row in last.items():
        if case_id not in wanted:
            continue
        unpaid = bool(row.get("error")) and not float(row.get("cost_usd") or 0)
        if unpaid or row.get("simulated"):
            continue
        if int(row.get("n_questions") or 0) < 2:
            continue
        done.add(case_id)
    return done


def router_spent(extra: Path | None = None) -> float:
    names = list(ROUTER_FILES)
    if extra is not None and extra.name not in names:
        names.append(extra.name)
    total, missing = summed(tuple(names))
    if missing:
        raise SystemExit("router row missing tokens: " + ", ".join(missing[:5]))
    return total


def subset_spent() -> float:
    total, missing = summed((SUBSET_GPT_FILE,))
    if missing:
        raise SystemExit("subset row missing tokens: " + ", ".join(missing[:5]))
    return total


def nace_spent() -> float:
    total = 0.0
    for path in sorted(RUNS_DIR.glob("hard_*.jsonl")):
        name = path.name
        if "drex" not in name and "system" not in name:
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("lane") in ("drex-v1", "drex-finance", "system", "billproof-system"):
                total += float(row.get("cost_usd") or 0)
    return total


def past_deadline() -> bool:
    return False


def eta_misses(remaining: int, workers: int, samples: list[float]) -> bool:
    return False


def reserve_usd(observed: list[float]) -> float:
    if ARGS.lane == "billproof-system":
        peak = max(observed) if observed else 0.004
        return max(0.004, 1.5 * peak)
    peak = max(observed) if observed else 0.0
    return max(0.40, 1.25 * peak)


def requested_model() -> str:
    return ARGS.model or settings().frontier_model or "gpt-6.1-sol"


def run() -> None:
    split = resolve_split()
    if ARGS.lane == "family-hybrid" and split == "test":
        report = RUNS_DIR / "hard_dev_family_hybrid_report.json"
        if not report.exists():
            raise SystemExit("dev family-hybrid report is not frozen; run --split dev and record it first")
        body = json.loads(report.read_text())
        if not body.get("allow_test"):
            raise SystemExit("dev family-hybrid report does not allow the test call")
    if ARGS.lane == "family-hybrid":
        if not tau_path().exists():
            raise SystemExit("hard_family_tau.json is missing; freeze cutoffs before any call")
        frozen = json.loads(tau_path().read_text())
        if not frozen.get("frozen_before_family_hybrid_calls"):
            raise SystemExit("family taus are not frozen")
        taus = load_taus()
        drex_map = _load_lane_map(split, "drex-v1")
        print(f"family taus {json.dumps(taus)} frozen", flush=True)
    else:
        taus = None
        drex_map = None

    order = case_order(split)
    by_id = cases_for(split, order)
    path = out_path(split)
    finished = done_ids(path)
    if ARGS.lane == "gpt" and split == "test" and not foreign_model():
        already = reused_gpt(order)
        if already:
            print(f"reuse {len(already)} packed rows from hard_test_gpt.jsonl", flush=True)
        finished |= already
    pending = [case_id for case_id in order if case_id not in finished]
    workers = workers_for()
    mark = requested_model()
    print(
        f"start subset {ARGS.lane} split={split} model={mark} n={len(order)} "
        f"done={len(finished)} pending={len(pending)} workers={workers} "
        f"rpm={os.environ.get('FRONTIER_RPM')} out={path.name} {now()}",
        flush=True,
    )
    if not pending:
        print(f"DONE subset {ARGS.lane} n={len(finished)} {now()}", flush=True)
        return

    tau = None
    if ARGS.lane == "hybrid":
        manifest = json.loads((DATA_DIR / "synthetic" / "hard" / "manifest.json").read_text())
        if "hybrid_tau" not in manifest:
            raise SystemExit("manifest has no hybrid_tau")
        tau = float(manifest["hybrid_tau"])
        print(f"hybrid tau={tau} frozen", flush=True)

    state = {"stop": False, "reason": "", "inflight": 0}
    samples: list[float] = []
    observed: list[float] = []
    lock = threading.Lock()
    work: Queue[str] = Queue()
    for case_id in pending:
        work.put(case_id)
    router_lane = ARGS.lane in ("gpt", "hybrid", "family-hybrid")
    extra = path if router_lane and path.name not in ROUTER_FILES else None

    def acquire() -> bool:
        with lock:
            if state["stop"] or past_deadline():
                state["stop"] = True
                state["reason"] = state["reason"] or "stopped"
                return False
            remaining = work.qsize() + 1
            if eta_misses(remaining, workers, samples):
                state["stop"] = True
                state["reason"] = "stopped"
                return False
            hold = reserve_usd(observed)
            if ARGS.lane == "billproof-system" or ARGS.lane == "system":
                spent = nace_spent()
                if spent + (state["inflight"] + 1) * hold > NACE_CAP_USD:
                    state["stop"] = True
                    state["reason"] = f"nace ${spent:.4f} + reserve would pass ${NACE_CAP_USD:.2f}"
                    return False
            else:
                night = router_spent(extra)
                subset = subset_spent() if ARGS.lane == "gpt" and not foreign_model() else 0.0
                need = (state["inflight"] + 1) * hold
                if ARGS.lane == "gpt" and not foreign_model() and subset + need > SUBSET_GPT_CAP_USD:
                    state["stop"] = True
                    state["reason"] = f"subset list ${subset:.4f} + reserve would pass ${SUBSET_GPT_CAP_USD:.2f}"
                    return False
                if night + need > NIGHT_CAP_USD:
                    state["stop"] = True
                    state["reason"] = f"night list ${night:.4f} + reserve would pass ${NIGHT_CAP_USD:.2f}"
                    return False
            state["inflight"] += 1
            return True

    def release() -> None:
        with lock:
            state["inflight"] = max(0, state["inflight"] - 1)

    def one(adapter, case_id: str) -> dict:
        record = by_id[case_id]
        if ARGS.lane == "gpt":
            return call_lane(record, "gpt", adapter)
        if ARGS.lane == "hybrid":
            row = call_hybrid(record, adapter, tau)
            if row is None:
                raise RuntimeError("drex-v1 row missing")
            row["run_id"] = path.stem
            return row
        if ARGS.lane == "family-hybrid":
            row = call_family_hybrid(record, adapter, taus, drex_map.get(case_id))
            if row is None:
                raise RuntimeError("drex-v1 row missing")
            row["run_id"] = path.stem
            return row
        if ARGS.lane == "billproof-system":
            return call_billproof_system(record, adapter)
        return call_system(record, adapter)

    def loop(adapter) -> None:
        while True:
            try:
                case_id = work.get_nowait()
            except Empty:
                return
            if not acquire():
                return
            try:
                try:
                    row = one(adapter, case_id)
                except ModelMismatch as exc:
                    with lock:
                        state["stop"] = True
                        state["reason"] = str(exc)
                    print(f"FAILED subset {ARGS.lane}: {exc}", flush=True)
                    return
                except Exception as exc:  # noqa: BLE001 — unpaid row, resume retries it
                    record = by_id[case_id]
                    row = {
                        "run_id": path.stem, "bench": "hard", "case_id": case_id,
                        "split": split, "lane": STORED_LANE[ARGS.lane], "model": ARGS.lane,
                        "simulated": False, "error": f"{type(exc).__name__}: {exc}",
                        "cost_usd": 0, "latency_ms": 0, "input_tokens": 0, "output_tokens": 0,
                        "gold": record["gold"], "answers": {},
                        "correct": {name: False for name in record["gold"]},
                        "invalid": {name: True for name in record["gold"]},
                        "case_correct": False, "n_questions": len(record["gold"]),
                        "n_correct": 0, "n_invalid": len(record["gold"]), "ts": now(),
                        "trace": traceback.format_exc(limit=4),
                    }
                if router_lane:
                    row["list_cost_usd"] = list_cost(
                        int(row.get("input_tokens") or 0), int(row.get("output_tokens") or 0),
                    )
                if row.get("simulated"):
                    if float(row.get("cost_usd") or 0) > 0 or int(row.get("input_tokens") or 0) > 0:
                        append_row(path, row)
                    with lock:
                        state["stop"] = True
                        state["reason"] = f"simulated response from {row.get('model')}"
                    print(f"FAILED subset {ARGS.lane}: {state['reason']}", flush=True)
                    return
                if router_lane and not row.get("error") and not row.get("gpt_error"):
                    returned = str(row.get("gpt_model") or row.get("model") or "")
                    skipped = ARGS.lane == "family-hybrid" and not row.get("n_escalated")
                    if not skipped and (row.get("simulated") or not model_ok(returned, mark)):
                        if float(row.get("cost_usd") or 0) > 0 or int(row.get("input_tokens") or 0) > 0:
                            append_row(path, row)
                        with lock:
                            state["stop"] = True
                            state["reason"] = (
                                f"expected live {mark}, got {returned} simulated={row.get('simulated')}"
                            )
                        print(f"FAILED subset {ARGS.lane}: {state['reason']}", flush=True)
                        return
                if ARGS.lane == "billproof-system" and not row.get("error"):
                    if row.get("simulated") or row.get("model") != BILLPROOF_SYSTEM_MODEL:
                        if float(row.get("cost_usd") or 0) > 0:
                            append_row(path, row)
                        with lock:
                            state["stop"] = True
                            state["reason"] = (
                                f"expected live {BILLPROOF_SYSTEM_MODEL}, got {row.get('model')} "
                                f"simulated={row.get('simulated')}"
                            )
                        print(f"FAILED subset {ARGS.lane}: {state['reason']}", flush=True)
                        return
                append_row(path, row)
                with lock:
                    if row.get("latency_ms") and not row.get("error"):
                        samples.append(float(row["latency_ms"]))
                    if router_lane and not row.get("error"):
                        observed.append(float(row.get("list_cost_usd") or 0))
                    elif ARGS.lane in ("system", "billproof-system") and not row.get("error"):
                        observed.append(float(row.get("cost_usd") or 0))
                acc = (row.get("n_correct") or 0) / row["n_questions"] if row.get("n_questions") else 0
                print(
                    f"{ARGS.lane} {case_id} acc={acc:.3f} invalid={row.get('n_invalid')} "
                    f"cost={row.get('list_cost_usd', row.get('cost_usd'))} "
                    f"latency_ms={row.get('latency_ms')} night={router_spent(extra):.4f} "
                    f"nace={nace_spent():.4f}",
                    flush=True,
                )
                if row.get("error") or row.get("gpt_error"):
                    print(f"ERROR {case_id} {row.get('error') or row.get('gpt_error')}", flush=True)
            finally:
                release()

    if ARGS.lane in ("gpt", "hybrid", "family-hybrid"):
        adapters = [gpt_adapter() for _ in range(workers)]
    else:
        adapters = [drex_adapter(DREX_MODELS["drex-v1"]) for _ in range(workers)]
    threads = [threading.Thread(target=loop, args=(adapter,), daemon=True) for adapter in adapters]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    done_n = len(done_ids(path))
    if ARGS.lane == "gpt" and split == "test" and not foreign_model():
        done_n = len(done_ids(path) | reused_gpt(order))
    if state["stop"]:
        print(f"STOP subset {ARGS.lane}: {state['reason']} n={done_n} {now()}", flush=True)
        return
    print(f"DONE subset {ARGS.lane} n={done_n} {now()}", flush=True)


if __name__ == "__main__":
    run()
