#!/usr/bin/env python3
"""DeepInfra lanes on the frozen sets that already have Drex and GPT-6.1 Sol.

Same prompts as the GPT lane. Does not call the token router or Nace.
Stops when recorded DeepInfra spend would pass $5.

    cd billproof && PYTHONPATH=src .venv/bin/python scripts/di_lanes.py run --bench hard --model deepseek-ai/DeepSeek-V4.1-Flash
    cd billproof && PYTHONPATH=src .venv/bin/python scripts/di_lanes.py score
"""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from billproof.adapters.frontier import (  # noqa: E402
    FrontierAdapter,
    _deepinfra_list,
    _extract_deepinfra,
    _model_name,
    _resolve,
    _usage,
    price_usage,
)
from billproof.bench.metrics import wilson_ci  # noqa: E402
from billproof.config import DATA_DIR, RUNS_DIR  # noqa: E402
from billproof.schema import Answer, DecisionResult  # noqa: E402

CAP_USD = float(os.environ.get("DI_CAP_USD", "5.0"))
RUNS = RUNS_DIR
METRICS = DATA_DIR / "metrics"
BUDGET_PATH = RUNS / "di_budget.json"
# Qwen3.8-Max is the frontier lane. Qwen3.8-Flash is the cheap lane. Both via DeepInfra.
MODELS = (
    "Qwen/Qwen3.8-Max",
    "Qwen/Qwen3.8-Flash",
    "Qwen/Qwen3.8-27B",
    "Qwen/Qwen3.7-Max",
    "deepseek-ai/DeepSeek-V4.1-Flash",
)
VIA = "via DeepInfra"
# Upper bound used only to refuse a call that would cross the cap.
# Real cost is usage.estimated_cost, else the published list rate.
_ESTIMATE = {
    "hard": (25_000, 16_000),
    "pa": (12_000, 4_000),
    "persius": (6_000, 800),
}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(model: str) -> str:
    return model.replace("/", "__").replace(" ", "_")


def run_path(bench: str, model: str) -> Path:
    return RUNS / f"di_{bench}_{slug(model)}.jsonl"


def metric_path(bench: str, model: str) -> Path:
    return METRICS / f"di_{bench}_{slug(model)}.json"


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def _last_ok(path: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row in _iter_jsonl(path):
        case_id = row.get("case_id")
        if not case_id or row.get("simulated"):
            continue
        if row.get("error"):
            continue
        found[case_id] = row
    return found


def _recorded_spend() -> float:
    total = 0.0
    for path in sorted(RUNS.glob("di_*.jsonl")):
        for row in _iter_jsonl(path):
            total += float(row.get("cost_usd") or 0.0)
    return total


def _estimate(bench: str, model: str) -> float:
    listed = _deepinfra_list(model) or {"input_usd_per_mtok": 0.20, "output_usd_per_mtok": 2.50}
    inn, out = _ESTIMATE[bench]
    return (inn * listed["input_usd_per_mtok"] + out * listed["output_usd_per_mtok"]) / 1_000_000


class Budget:
    """Cross-process cap. Holds live in di_holds.json and drop when the pid is gone."""

    def __init__(self) -> None:
        self._lock_path = RUNS / "di_budget.lock"
        self._holds_path = RUNS / "di_holds.json"

    def _read_holds(self) -> dict:
        if not self._holds_path.exists():
            return {}
        try:
            payload = json.loads(self._holds_path.read_text() or "{}")
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        now_s = time.time()
        kept = {}
        for key, item in payload.items():
            if not isinstance(item, dict):
                continue
            if now_s - float(item.get("ts") or 0) > 900:
                continue
            pid = item.get("pid")
            if pid and not Path(f"/proc/{pid}").exists():
                continue
            kept[key] = item
        return kept

    def begin(self, estimate: float) -> str | None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}-{time.time_ns()}"
        with self._lock_path.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            holds = self._read_holds()
            reserved = sum(float(item.get("estimate") or 0) for item in holds.values())
            if _recorded_spend() + reserved + estimate > CAP_USD:
                return None
            holds[token] = {"estimate": estimate, "ts": time.time(), "pid": os.getpid()}
            self._holds_path.write_text(json.dumps(holds))
            return token

    def end(self, token: str | None) -> float:
        with self._lock_path.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            holds = self._read_holds()
            if token:
                holds.pop(token, None)
            self._holds_path.write_text(json.dumps(holds))
            return _recorded_spend()


def _client() -> httpx.Client:
    return httpx.Client(timeout=240.0)


def _priced_row(data: dict, model: str) -> tuple[str, int, int, float, dict]:
    reported = _model_name(data, model)
    inn, out = _usage(data)
    cost, rates = price_usage("deepinfra", data, inn, out, reported)
    return reported, inn, out, cost, rates


# ---------------------------------------------------------------- hard


def _hard_jobs(limit: int | None) -> list[dict]:
    from billproof.bench.hard.runner import install_gpt_system, load_cases

    install_gpt_system()
    payload = json.loads((RUNS / "ladder_hard_ids.json").read_text())
    if not payload.get("chosen_before_any_ladder_call"):
        raise SystemExit("ladder_hard_ids.json is not marked frozen before results")
    order = list(payload["run_order"])
    if len(order) != 100:
        raise SystemExit(f"expected 100 frozen hard ids, found {len(order)}")
    manifest = json.loads((DATA_DIR / "synthetic" / "hard" / "manifest.json").read_text())
    if not manifest.get("frozen"):
        raise SystemExit("hard split is not frozen")
    found = {
        row["case_id"]: row
        for row in load_cases(DATA_DIR / "synthetic" / "hard" / "test.jsonl")
        if row["case_id"] in set(order)
    }
    missing = [case_id for case_id in order if case_id not in found]
    if missing:
        raise SystemExit(f"hard ids missing: {missing[:3]}")
    rows = [found[case_id] for case_id in order]
    return rows[:limit] if limit else rows


def _hard_one(record: dict, adapter: FrontierAdapter, model: str) -> dict:
    from billproof.bench.hard.runner import _base_row, grade, questions_for, visible_state

    questions = questions_for(record)
    state = visible_state(record)
    if "gold" in state:
        raise SystemExit("gold leaked into the hard state")
    result = adapter.decide(state, questions, case_id=record["case_id"])
    row = _base_row(record, "deepinfra", result.model, bool(result.simulated))
    row["run_id"] = f"di_hard_{slug(model)}"
    row.update(grade(record, questions, result))
    basis = None
    source = None
    for answer in result.answers.values():
        cost = (answer.raw or {}).get("cost") or {}
        basis = cost.get("kind")
        source = cost.get("source")
        break
    row.update({
        "provider": "deepinfra",
        "via": VIA,
        "model_requested": model,
        "latency_ms": result.latency_ms,
        "cost_usd": result.cost_usd,
        "cost_basis": basis,
        "cost_source": source,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "error": result.error,
        "request_id": result.request_id,
        "prompt": "hard GPT_SYSTEM + visible_state + questions_for",
    })
    return row


# ---------------------------------------------------------------- prior auth


def _pa_ids() -> list[str]:
    ids = []
    seen = set()
    for row in _iter_jsonl(RUNS / "pa_hybrid_development.jsonl"):
        case_id = row.get("case_id")
        if case_id and case_id not in seen:
            seen.add(case_id)
            ids.append(case_id)
    if len(ids) != 370:
        raise SystemExit(f"expected 370 PA hybrid ids, found {len(ids)}")
    return ids


def _pa_jobs(limit: int | None):
    from billproof.bench.pa.cases import load_cases

    wanted = _pa_ids()
    by_id = {case.case_id: case for case in load_cases("development")}
    missing = [case_id for case_id in wanted if case_id not in by_id]
    if missing:
        raise SystemExit(f"PA ids missing from development parquet: {missing[:3]}")
    rows = [by_id[case_id] for case_id in wanted]
    return rows[:limit] if limit else rows


def _pa_one(case, adapter: FrontierAdapter, model: str) -> dict:
    from billproof.bench.pa.columns import gpt_answer_schema
    from billproof.bench.pa.score import score_prediction

    messages = [
        {"role": "system", "content": case.system},
        {"role": "user", "content": case.raw_user},
    ]
    started = time.perf_counter()
    data, error = adapter._post(messages, "pa_structured_answer", gpt_answer_schema(case))
    latency_ms = (time.perf_counter() - started) * 1000.0
    row = {
        "case_id": case.case_id,
        "split": "development",
        "bench": "pa",
        "lane": "deepinfra",
        "provider": "deepinfra",
        "via": VIA,
        "model_requested": model,
        "prompt": "pa gpt_structured: case.system + case.raw_user",
        "simulated": False,
        "latency_ms": latency_ms,
        "ts": now(),
    }
    if error or data is None:
        row.update(error=error or "deepinfra request failed", prediction=None, cost_usd=0.0)
        return row
    reported, inn, out, cost, rates = _priced_row(data, model)
    row.update(
        model=reported,
        input_tokens=inn,
        output_tokens=out,
        cost_usd=cost,
        cost_basis=rates.get("kind"),
        cost_source=rates.get("source"),
        request_id=data.get("id") if isinstance(data.get("id"), str) else None,
    )
    try:
        payload = _extract_deepinfra(data)
    except (ValueError, json.JSONDecodeError) as exc:
        row.update(error=f"unparseable model output: {exc}", prediction=None)
        return row
    if isinstance(payload.get("answers"), dict) and "decision" not in payload:
        payload = payload["answers"]
    row["prediction"] = payload
    row["score"] = score_prediction(case, payload, check_evidence=True)
    row["error"] = None
    return row


# ---------------------------------------------------------------- persius


def _fair():
    path = ROOT / "scripts" / "c_persius_fair.py"
    spec = importlib.util.spec_from_file_location("c_persius_fair_di", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _persius_jobs(limit: int | None) -> tuple[object, list[dict], dict[int, str]]:
    fair = _fair()
    document = fair._load_test_ids()
    items = list(document["items"])
    if len(items) != 400:
        raise SystemExit(f"expected 400 Persius test rows, found {len(items)}")
    if limit:
        items = items[:limit]
    texts = fair._texts(fair.FROZEN.TEST_PATH, {item["row_index"] for item in items})
    return fair, items, texts


def _persius_one(fair, item: dict, text: str, adapter: FrontierAdapter, model: str) -> dict:
    fair._verify_body(item, text)
    state = fair.FROZEN._state(text)
    messages = fair._gpt_messages(state)
    question = fair.QUESTION
    started = time.perf_counter()
    data, error = adapter._post(messages, "persius_overturn", fair._gpt_schema())
    latency_ms = (time.perf_counter() - started) * 1000.0
    if error or data is None:
        failed = DecisionResult(
            lane="deepinfra",
            model=model,
            answers={},
            latency_ms=latency_ms,
            simulated=False,
            error=error or "deepinfra request failed",
        )
        row = fair._row_from_result(item, "deepinfra", failed, text)
        row["provider"] = "deepinfra"
        row["model_requested"] = model
        row["prompt"] = "persius fair GPT_SYSTEM + c_persius._state"
        return row
    reported, inn, out, cost, rates = _priced_row(data, model)
    try:
        payload = _extract_deepinfra(data)
        body = fair._payload_item(payload, question.name)
        choice, confidence, rationale, raw = _resolve(question, body, draft=None, verify=False)
    except (ValueError, json.JSONDecodeError) as exc:
        failed = DecisionResult(
            lane="deepinfra",
            model=reported,
            answers={},
            latency_ms=latency_ms,
            input_tokens=inn,
            output_tokens=out,
            cost_usd=cost,
            simulated=False,
            error=f"unparseable model output: {exc}",
            request_id=data.get("id") if isinstance(data.get("id"), str) else None,
        )
        row = fair._row_from_result(item, "deepinfra", failed, text)
        row["provider"] = "deepinfra"
        row["cost_basis"] = rates.get("kind")
        row["model_requested"] = model
        return row
    raw = dict(raw)
    raw["cost"] = rates
    answer = Answer(
        name=question.name,
        choice=choice,
        probabilities=None,
        confidence=confidence,
        confidence_kind="self_reported",
        rationale=rationale,
        raw=raw,
    )
    result = DecisionResult(
        lane="deepinfra",
        model=reported,
        answers={question.name: answer},
        latency_ms=latency_ms,
        input_tokens=inn,
        output_tokens=out,
        cost_usd=cost,
        simulated=False,
        request_id=data.get("id") if isinstance(data.get("id"), str) else None,
    )
    row = fair._row_from_result(item, "deepinfra", result, text)
    row["provider"] = "deepinfra"
    row["model_requested"] = model
    row["cost_basis"] = rates.get("kind")
    row["cost_source"] = rates.get("source")
    row["prompt"] = "persius fair GPT_SYSTEM + c_persius._state"
    return row


# ---------------------------------------------------------------- run


def _case_id(job) -> str:
    if isinstance(job, dict):
        return job["case_id"]
    return job.case_id


def _run_shared(bench: str, model: str, jobs: list, one) -> None:
    """one(job, adapter) -> row. One client, the DeepInfra slot cap does the concurrency."""
    path = run_path(bench, model)
    done = _last_ok(path)
    pending = [job for job in jobs if _case_id(job) not in done]
    print(
        f"[di] {bench} {model} todo={len(pending)} done={len(done)} "
        f"spent=${_recorded_spend():.4f} {now()}",
        flush=True,
    )
    if not pending:
        return
    budget = Budget()
    stop = threading.Event()
    adapter = FrontierAdapter(provider="deepinfra", model=model, client=_client())
    estimate = _estimate(bench, model)
    workers = int(os_workers())

    def work(job):
        if stop.is_set():
            return None
        token = budget.begin(estimate)
        if token is None:
            stop.set()
            print(f"[di] stop before call, spent=${_recorded_spend():.4f} estimate=${estimate:.4f}", flush=True)
            return None
        try:
            row = one(job, adapter)
        except Exception as exc:
            row = {
                "case_id": _case_id(job),
                "bench": bench,
                "lane": "deepinfra",
                "provider": "deepinfra",
                "model_requested": model,
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "cost_usd": 0.0,
                "simulated": False,
                "ts": now(),
            }
        finally:
            spent = budget.end(token)
        tokens = int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0)
        priced = row.get("cost_basis") not in (None, "unpriced", "missing")
        if not row.get("error") and (not priced or (float(row.get("cost_usd") or 0) <= 0 and tokens > 0)):
            row["error"] = f"unpriced success basis={row.get('cost_basis')} cost={row.get('cost_usd')}"
            stop.set()
            print(f"[di] stopping: {row['error']}", flush=True)
        _append(path, row)
        if spent >= CAP_USD:
            stop.set()
            print(f"[di] cap reached ${spent:.4f}", flush=True)
        return row

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, job) for job in pending]
            finished = 0
            for future in as_completed(futures):
                row = future.result()
                finished += 1
                if row is None:
                    continue
                if finished <= 3 or finished % 10 == 0 or row.get("error"):
                    print(
                        f"[di] {bench} {finished}/{len(pending)} "
                        f"in={row.get('input_tokens')} out={row.get('output_tokens')} "
                        f"cost={row.get('cost_usd')} basis={row.get('cost_basis')} "
                        f"err={row.get('error')} spent=${_recorded_spend():.4f}",
                        flush=True,
                    )
    finally:
        adapter._client.close()


_WORKERS = 4


def os_workers() -> int:
    return _WORKERS


def cmd_run(args: argparse.Namespace) -> None:
    global _WORKERS
    if args.model not in MODELS:
        raise SystemExit(f"model must be one of {MODELS}")
    _WORKERS = args.workers
    if args.bench == "hard":
        jobs = _hard_jobs(args.limit)
        _run_shared("hard", args.model, jobs, lambda record, adapter: _hard_one(record, adapter, args.model))
    elif args.bench == "pa":
        jobs = _pa_jobs(args.limit)
        _run_shared("pa", args.model, jobs, lambda case, adapter: _pa_one(case, adapter, args.model))
    elif args.bench == "persius":
        fair, items, texts = _persius_jobs(args.limit)
        _run_shared(
            "persius",
            args.model,
            items,
            lambda item, adapter: _persius_one(fair, item, texts[item["row_index"]], adapter, args.model),
        )
    else:
        raise SystemExit(args.bench)
    cmd_score(argparse.Namespace(bench=args.bench, model=args.model))


# ---------------------------------------------------------------- score


def _median(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return float(statistics.median(clean)) if clean else None


def _mean(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return float(statistics.fmean(clean)) if clean else None


def _rate_block(k: int, n: int, source: str) -> dict:
    lo, hi = wilson_ci(k, n) if n else (None, None)
    return {
        "value": (k / n) if n else None,
        "k": k,
        "n": n,
        "ci95": [lo, hi] if n else None,
        "ci_method": "wilson" if n else None,
        "source_run": source,
        "simulated": False,
        "computed_at": now(),
    }


def _score_hard(model: str) -> dict:
    from billproof.bench.hard.outcome_score import _lane_payload, score_lane
    from billproof.bench.hard.runner import load_cases

    path = run_path("hard", model)
    order = list(json.loads((RUNS / "ladder_hard_ids.json").read_text())["run_order"])
    rows_by = _last_ok(path)
    rows = [rows_by[case_id] for case_id in order if case_id in rows_by]
    records = {
        row["case_id"]: row
        for row in load_cases(DATA_DIR / "synthetic" / "hard" / "test.jsonl")
        if row["case_id"] in set(order)
    }
    source = f"data/runs/{path.name}"
    packed = _lane_payload("deepinfra", source, score_lane(rows, records)) if rows else {}
    line = packed.get("line_verdict") or {}
    body = {
        "bench": "hard",
        "model": model,
        "provider": "deepinfra",
        "n_cases": len(rows),
        "n_expected": 100,
        "complete": len(rows) == 100,
        "line_verdict": line,
        "question_accuracy": packed.get("question_accuracy"),
        "bill_verdict": packed.get("bill_verdict"),
        "strict_case": packed.get("strict_case"),
        "latency_p50_ms": _median([row.get("latency_ms") for row in rows]),
        "cost_usd_sum": sum(float(row.get("cost_usd") or 0) for row in rows),
        "cost_per_1000_usd": (_mean([row.get("cost_usd") for row in rows]) or 0) * 1000,
        "cost_basis_counts": _basis_counts(rows),
        "input_tokens_sum": sum(int(row.get("input_tokens") or 0) for row in rows),
        "output_tokens_sum": sum(int(row.get("output_tokens") or 0) for row in rows),
        "source_run": source,
        "computed_at": now(),
    }
    if line.get("k") is not None:
        body["line_verdict_wilson"] = _rate_block(int(line["k"]), int(line["n"]), source)
    return body


def _basis_counts(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        key = row.get("cost_basis") or "missing"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _score_pa(model: str) -> dict:
    path = run_path("pa", model)
    ids = _pa_ids()
    rows_by = _last_ok(path)
    rows = [rows_by[case_id] for case_id in ids if case_id in rows_by]
    whole = sum(1 for row in rows if (row.get("score") or {}).get("whole_case"))
    decision = sum(1 for row in rows if (row.get("score") or {}).get("decision"))
    source = f"data/runs/{path.name}"
    n = len(rows)
    return {
        "bench": "pa_criteria_gym",
        "split": "development",
        "model": model,
        "provider": "deepinfra",
        "n_cases": n,
        "n_expected": 370,
        "complete": n == 370,
        "whole_case": _rate_block(whole, n, source),
        "decision": _rate_block(decision, n, source),
        "latency_p50_ms": _median([row.get("latency_ms") for row in rows]),
        "cost_usd_sum": sum(float(row.get("cost_usd") or 0) for row in rows),
        "cost_per_1000_usd": (_mean([row.get("cost_usd") for row in rows]) or 0) * 1000,
        "cost_basis_counts": _basis_counts(rows),
        "input_tokens_sum": sum(int(row.get("input_tokens") or 0) for row in rows),
        "output_tokens_sum": sum(int(row.get("output_tokens") or 0) for row in rows),
        "source_run": source,
        "prompt": "same as pa gpt_structured",
        "computed_at": now(),
    }


def _score_persius(model: str) -> dict:
    path = run_path("persius", model)
    rows = list(_last_ok(path).values())
    # The frozen 400, not a prefix of whatever finished.
    fair_ids = {item["case_id"] for item in json.loads((RUNS / "c_persius_test_ids.json").read_text())["items"]}
    rows = [row for row in rows if row.get("case_id") in fair_ids]
    k = sum(1 for row in rows if row.get("correct") and not row.get("error"))
    n = len(rows)
    source = f"data/runs/{path.name}"
    return {
        "bench": "persius",
        "split": "test",
        "model": model,
        "provider": "deepinfra",
        "n_cases": n,
        "n_expected": 400,
        "complete": n == 400,
        "accuracy": _rate_block(k, n, source),
        "latency_p50_ms": _median([row.get("latency_ms") for row in rows]),
        "cost_usd_sum": sum(float(row.get("cost_usd") or 0) for row in rows),
        "cost_per_1000_usd": (_mean([row.get("cost_usd") for row in rows]) or 0) * 1000,
        "cost_basis_counts": _basis_counts(rows),
        "input_tokens_sum": sum(int(row.get("input_tokens") or 0) for row in rows),
        "output_tokens_sum": sum(int(row.get("output_tokens") or 0) for row in rows),
        "source_run": source,
        "prompt": "same as c_persius_fair GPT_SYSTEM",
        "computed_at": now(),
    }


def _score_one(bench: str, model: str) -> dict:
    if bench == "hard":
        body = _score_hard(model)
    elif bench == "pa":
        body = _score_pa(model)
    else:
        body = _score_persius(model)
    body["via"] = VIA
    path = metric_path(bench, model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n")
    print(f"[di] wrote {path} n={body.get('n_cases')} complete={body.get('complete')}", flush=True)
    return body


def cmd_score(args: argparse.Namespace) -> None:
    benches = [args.bench] if getattr(args, "bench", None) else ["hard", "pa", "persius"]
    models = [args.model] if getattr(args, "model", None) else list(MODELS)
    lock_path = RUNS / "di_summary.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        for bench in benches:
            for model in models:
                if run_path(bench, model).exists():
                    _score_one(bench, model)
        lanes = _existing_metrics()
        summary = {
            "generated_at": now(),
            "generator": "scripts/di_lanes.py score",
            "via": VIA,
            "primary_model": "Qwen/Qwen3.8-Max",
            "cheap_model": "Qwen/Qwen3.8-Flash",
            "note": (
                "Every lane is via DeepInfra. Qwen3.8-Max is the frontier lane and the dial "
                "escalation model. Qwen3.8-Flash is the cheap lane. "
                "g_build_results.py builds fixed lanes and does not take an extra-lane list."
            ),
            "spend_usd": _recorded_spend(),
            "cap_usd": CAP_USD,
            "lanes": lanes,
        }
        out = METRICS / "di_summary.json"
        out.write_text(json.dumps(summary, indent=2) + "\n")
        print(f"[di] wrote {out} lanes={len(summary['lanes'])} spend={summary['spend_usd']:.4f}", flush=True)


def _existing_metrics() -> list[dict]:
    rows = []
    for path in sorted(METRICS.glob("di_*.json")):
        if path.name == "di_summary.json":
            continue
        rows.append(json.loads(path.read_text()))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--bench", required=True, choices=("hard", "pa", "persius"))
    run.add_argument("--model", required=True)
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--workers", type=int, default=4)
    run.set_defaults(func=cmd_run)
    score = sub.add_parser("score")
    score.add_argument("--bench", choices=("hard", "pa", "persius"))
    score.add_argument("--model")
    score.set_defaults(func=cmd_score)
    args = parser.parse_args()
    if args.cmd == "run" and not 1 <= args.workers <= 16:
        raise SystemExit("--workers must be from 1 to 16")
    args.func(args)


if __name__ == "__main__":
    main()
