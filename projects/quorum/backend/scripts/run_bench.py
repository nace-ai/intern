"""Benchmark: Drex panel vs LLM guard on the constructed cases (brief 06 Part A).

Run from the repo root:
    python backend/scripts/run_bench.py --split dev|test [--system drex|llm_guard|both]
        [--concurrency 8] [--reuse] [--warmup 5]

This is the ONLY reader of backend/data/cases.jsonl (AGENTS.md rule 2).
Inputs (case id, pdf path, split) are read first; labels are read only after
every decision has been stored under data/decisions/{system}/{case_id}.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import checks, config, intake, panel  # noqa: E402
from backend.app.contracts import Decision, Facts, ParsedInvoice  # noqa: E402

POLICY: tuple[float, float, float] = (100, 5, 30)
SYSTEMS: tuple[str, ...] = ("drex", "llm_guard")
SCENARIOS: tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5")
HYBRID_THRESHOLD = 0.7
PREPARE_ERROR_PREFIX = "prepare_failed:"
DREX_PRICE_PER_INPUT_TOKEN = Decimal("0.03") / Decimal(1_000_000)
LABEL_KEYS = ("expected_action", "forged", "scenario")
ROWS: tuple[tuple[str, str, str], ...] = (
    ("drex_alone", "Drex alone", "drex:raw"),
    ("drex_guard", "Drex + guard", "drex:final"),
    ("llm_alone", "LLM guard alone", "llm_guard:raw"),
    ("llm_guard", "LLM guard + guard", "llm_guard:final"),
    ("hybrid", "Hybrid (Drex first, LLM guard if Drex p<0.7)", "hybrid"),
)
BACKEND = config.ROOT / "backend"

DecideFn = Callable[[ParsedInvoice, Facts, tuple[float, float, float]], Awaitable[Decision]]


# ---------------------------------------------------------------- paths


def data_dir_default() -> Path:
    """Data directory; override with QUORUM_BENCH_DATA for tests."""
    env = os.environ.get("QUORUM_BENCH_DATA")
    return Path(env) if env else config.DATA


def decision_path(data_dir: Path, system: str, case_id: str) -> Path:
    return data_dir / "decisions" / system / f"{case_id}.json"


# ---------------------------------------------------------------- cases.jsonl


def _read_cases(cases_file: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in cases_file.read_text().splitlines() if line.strip()]


def load_inputs(cases_file: Path, split: str) -> list[dict[str, Any]]:
    """Case rows for a split WITHOUT the label fields."""
    rows = [r for r in _read_cases(cases_file) if r.get("split") == split]
    return [{k: v for k, v in r.items() if k not in LABEL_KEYS} for r in rows]


def load_labels(cases_file: Path, split: str) -> dict[str, dict[str, Any]]:
    """Labels by case id. Call only after all decisions are stored."""
    return {
        r["case_id"]: {k: r[k] for k in LABEL_KEYS}
        for r in _read_cases(cases_file)
        if r.get("split") == split
    }


# ---------------------------------------------------------------- systems


def resolve_system(system: str) -> DecideFn | None:
    """The decide function for a system, or None when it is unavailable."""
    if system == "drex":

        async def drex(parsed: ParsedInvoice, facts: Facts, policy: tuple[float, float, float]) -> Decision:
            return await panel.decide(parsed, facts, policy=policy, system="drex")

        return drex
    try:
        from backend.app import baseline  # type: ignore[attr-defined]

        fn = baseline.decide_llm
    except (ImportError, AttributeError):
        return None

    async def llm(parsed: ParsedInvoice, facts: Facts, policy: tuple[float, float, float]) -> Decision:
        return await fn(parsed, facts, policy=policy)

    return llm


async def _safe_call(fn: DecideFn, parsed: ParsedInvoice, facts: Facts, system: str) -> Decision:
    """Any exception becomes a failed Decision (never an approval)."""
    t0 = time.perf_counter()
    try:
        d = await fn(parsed, facts, POLICY)
        if not isinstance(d, Decision):
            d = Decision.model_validate(d)
        return d
    except Exception as exc:  # noqa: BLE001
        return Decision(
            doc_id=parsed.doc_id,
            system=system,
            error=f"{type(exc).__name__}: {exc}",
            wall_ms=int((time.perf_counter() - t0) * 1000),
        )


# ---------------------------------------------------------------- running


async def prepare(
    inputs: list[dict[str, Any]],
    trusted: dict[str, Any],
    backend_dir: Path,
) -> tuple[dict[str, tuple[ParsedInvoice, Facts]], dict[str, str]]:
    """Parse (NDI cache) and compute facts for each case.

    Returns (prepared cases, failures by case id). A parse or facts exception, or an
    ``ndi_status_*`` warning (a failed NDI job), is a failure for that case only."""
    out: dict[str, tuple[ParsedInvoice, Facts]] = {}
    failures: dict[str, str] = {}
    for row in inputs:
        case_id = row["case_id"]
        try:
            parsed = await intake.parse_document(backend_dir / row["pdf_path"])
            bad = [w for w in parsed.warnings if str(w).startswith("ndi_status_")]
            if bad:
                failures[case_id] = f"{PREPARE_ERROR_PREFIX} NDI parse failed ({', '.join(bad)})"
                continue
            out[case_id] = (parsed, checks.facts(parsed, trusted))
        except Exception as exc:  # noqa: BLE001
            failures[case_id] = f"{PREPARE_ERROR_PREFIX} {type(exc).__name__}: {exc}"
    return out, failures


def is_prepare_failure(d: Decision) -> bool:
    return bool(d.error) and str(d.error).startswith(PREPARE_ERROR_PREFIX)


def prepare_failure_decisions(system: str, failures: dict[str, str]) -> dict[str, Decision]:
    """One failed Decision per case that could not be prepared. No model call is made."""
    return {c: Decision(doc_id=c, system=system, error=err, wall_ms=0) for c, err in failures.items()}


async def run_system(
    system: str,
    fn: DecideFn,
    prepared: dict[str, tuple[ParsedInvoice, Facts]],
    concurrency: int,
) -> tuple[dict[str, Decision], float]:
    """All calls under one semaphore. Returns decisions by case id and run seconds."""
    sem = asyncio.Semaphore(concurrency)

    async def one(case_id: str) -> tuple[str, Decision]:
        parsed, facts = prepared[case_id]
        async with sem:
            return case_id, await _safe_call(fn, parsed, facts, system)

    t0 = time.perf_counter()
    results = await asyncio.gather(*(one(c) for c in prepared))
    return dict(results), time.perf_counter() - t0


async def warmup(system: str, fn: DecideFn, prepared: dict[str, tuple[ParsedInvoice, Facts]], n: int, concurrency: int) -> None:
    """N discarded calls on dev cases."""
    if n <= 0 or not prepared:
        return
    ids = list(prepared)[:n]
    await run_system(system, fn, {c: prepared[c] for c in ids}, concurrency)


def store_decisions(data_dir: Path, system: str, decisions: dict[str, Decision]) -> None:
    for case_id, d in decisions.items():
        p = decision_path(data_dir, system, case_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(d.model_dump_json(indent=1))


def load_decisions(data_dir: Path, system: str, case_ids: list[str]) -> dict[str, Decision]:
    out: dict[str, Decision] = {}
    for c in case_ids:
        p = decision_path(data_dir, system, c)
        if p.exists():
            out[c] = Decision.model_validate_json(p.read_text())
    return out


def load_llm_costs(data_dir: Path) -> dict[str, Decimal]:
    """estimated_cost by decision_id from llm_cost.jsonl (Decimal, via str)."""
    f = data_dir / "llm_cost.jsonl"
    out: dict[str, Decimal] = {}
    if not f.exists():
        return out
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            if r.get("decision_id") and r.get("estimated_cost") is not None:
                out[str(r["decision_id"])] = Decimal(str(r["estimated_cost"]))
        except (ValueError, ArithmeticError):
            continue
    return out


# ---------------------------------------------------------------- metrics


def action_of(d: Decision | None, which: str) -> str | None:
    """raw or final action; None means failure (error set or no action)."""
    if d is None or d.error:
        return None
    a = d.raw_action if which == "raw" else d.final_action
    return a if a in ("approve", "verify", "block") else None


def top_p(d: Decision | None) -> float:
    if d is None or d.error or not d.p:
        return 0.0
    return max(d.p.values())


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile, q in (0, 100]."""
    if not values:
        return None
    s = sorted(values)
    k = max(1, math.ceil(q / 100 * len(s)))
    return float(s[k - 1])


def drex_cost(d: Decision) -> Decimal:
    return Decimal(int(d.usage.input_tokens)) * DREX_PRICE_PER_INPUT_TOKEN


def decision_cost(system: str, d: Decision, llm_costs: dict[str, Decimal]) -> Decimal | None:
    if system == "drex":
        return drex_cost(d)
    return llm_costs.get(d.decision_id) if d.decision_id else None


def hybrid_actions(
    drex: dict[str, Decision], llm: dict[str, Decision], case_ids: list[str], threshold: float = HYBRID_THRESHOLD
) -> tuple[dict[str, str | None], dict[str, bool]]:
    """Drex final action when Drex's top probability >= threshold, else LLM guard final action.

    A failed Drex decision (missing, error set or no valid final action) makes the hybrid
    a failure (None) for that case: it never falls back to the LLM guard.

    Returns (actions by case, drex_alone flag by case)."""
    actions: dict[str, str | None] = {}
    alone: dict[str, bool] = {}
    for c in case_ids:
        d = drex.get(c)
        if action_of(d, "final") is None:
            actions[c], alone[c] = None, True
        elif top_p(d) >= threshold:
            actions[c], alone[c] = action_of(d, "final"), True
        else:
            actions[c], alone[c] = action_of(llm.get(c), "final"), False
    return actions, alone


def _rate(n: int, total: int) -> float | None:
    return n / total if total else None


def row_metrics(actions: dict[str, str | None], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Accuracy metrics. A failure (None) is wrong everywhere and never an approval."""
    ids = list(labels)
    forged = [c for c in ids if labels[c]["forged"]]
    clean = [c for c in ids if not labels[c]["forged"]]
    s5 = [c for c in ids if labels[c]["scenario"] == "S5"]
    s3 = [c for c in ids if labels[c]["scenario"] == "S3"]
    act = {c: actions.get(c) for c in ids}
    correct = sum(1 for c in ids if act[c] is not None and act[c] == labels[c]["expected_action"])
    unsafe = sum(1 for c in forged if act[c] == "approve")
    unnec = sum(1 for c in clean if act[c] != "approve")
    grid = {
        s: {
            "correct": sum(1 for c in ids if labels[c]["scenario"] == s and act[c] == labels[c]["expected_action"]),
            "n": sum(1 for c in ids if labels[c]["scenario"] == s),
        }
        for s in SCENARIOS
    }
    return {
        "available": True,
        "n": len(ids),
        "correct": correct,
        "correct_rate": _rate(correct, len(ids)),
        "unsafe_approvals": unsafe,
        "unsafe_approval_rate": _rate(unsafe, len(forged)),
        "n_forged": len(forged),
        "unnecessary_escalations": unnec,
        "unnecessary_escalation_rate": _rate(unnec, len(clean)),
        "n_clean": len(clean),
        "s5_blocked": sum(1 for c in s5 if act[c] == "block"),
        "n_s5": len(s5),
        "s3_blocked": sum(1 for c in s3 if act[c] == "block"),
        "n_s3": len(s3),
        "failures": sum(1 for c in ids if act[c] is None),
        "per_scenario": grid,
    }


def _is_failed(d: Decision | None) -> bool:
    return d is None or bool(d.error)


def _sum_cost(costs: list[Decimal | None]) -> Decimal | None:
    """Total cost, or None when any part is unknown (or nothing to sum)."""
    if not costs or any(c is None for c in costs):
        return None
    return sum((c for c in costs if c is not None), Decimal(0))


def system_stats(
    system: str,
    decisions: dict[str, Decision],
    case_ids: list[str],
    run_info: dict[str, Any] | None,
    concurrency: int,
    llm_costs: dict[str, Decimal],
) -> dict[str, Any]:
    """Speed and cost. Successes and failures are reported apart.

    Latency, tokens and cost per 1,000 are over successful decisions only. Failed model
    calls get their own latency and cost. Prepare failures made no call: they are counted
    but have no latency or cost. Throughput is the measured run time of a live run
    (``run_info``); without it, it is unavailable (never estimated from latencies)."""
    ds = [decisions[c] for c in case_ids if c in decisions]
    ok = [d for d in ds if not _is_failed(d)]
    failed = [d for d in ds if _is_failed(d)]
    prep_failed = [d for d in failed if is_prepare_failure(d)]
    call_failed = [d for d in failed if not is_prepare_failure(d)]
    ok_walls = [float(d.wall_ms) for d in ok]
    fail_walls = [float(d.wall_ms) for d in call_failed]
    ok_total = _sum_cost([decision_cost(system, d, llm_costs) for d in ok])
    fail_costs = [decision_cost(system, d, llm_costs) for d in call_failed]
    fail_total = _sum_cost(fail_costs) if call_failed else Decimal(0)
    per_1000 = (ok_total / len(ok) * 1000) if ok_total is not None else None
    run_seconds = run_info.get("run_seconds") if run_info else None
    n_calls = run_info.get("n_calls") if run_info else None
    return {
        "available": True,
        "n": len(ds),
        "n_ok": len(ok),
        "n_failed": len(failed),
        "n_prepare_failed": len(prep_failed),
        "models": sorted({d.model for d in ds if d.model}),
        "stub_decisions": sum(1 for d in ds if d.stub),
        "p50_wall_ms": percentile(ok_walls, 50),
        "p95_wall_ms": percentile(ok_walls, 95),
        "failed_p50_wall_ms": percentile(fail_walls, 50),
        "failed_p95_wall_ms": percentile(fail_walls, 95),
        "run_seconds": run_seconds,
        "run_calls": n_calls,
        "decisions_per_s": (n_calls / run_seconds) if (run_seconds and n_calls is not None) else None,
        "mean_input_tokens": statistics.fmean(d.usage.input_tokens for d in ok) if ok else None,
        "mean_output_tokens": statistics.fmean(d.usage.output_tokens for d in ok) if ok else None,
        "failed_input_tokens": sum(int(d.usage.input_tokens) for d in call_failed),
        "failed_output_tokens": sum(int(d.usage.output_tokens) for d in call_failed),
        "cost_total": str(ok_total) if ok_total is not None else None,
        "cost_per_1000": str(per_1000) if per_1000 is not None else None,
        "failed_cost_total": str(fail_total) if fail_total is not None else None,
        "cost_unknown_decisions": sum(1 for d in ok + call_failed if decision_cost(system, d, llm_costs) is None),
    }


def hybrid_stats(
    drex: dict[str, Decision], llm: dict[str, Decision], alone: dict[str, bool], llm_costs: dict[str, Decimal]
) -> dict[str, Any]:
    """Routing share, blended cost per 1,000 and median latency (sequential), successes apart.

    A failed Drex decision is a hybrid failure: not counted as handled by Drex, not routed
    to the LLM guard, and no LLM cost or latency is added. A routed case whose LLM guard
    decision failed is also a hybrid failure."""
    ok_walls: list[float] = []
    ok_costs: list[Decimal | None] = []
    fail_walls: list[float] = []
    fail_costs: list[Decimal | None] = []
    drex_alone = routed = drex_failed = llm_failed = 0
    for c, is_alone in alone.items():
        d = drex.get(c)
        if d is None or _is_failed(d) or action_of(d, "final") is None:
            drex_failed += 1
            if d is not None and not is_prepare_failure(d):
                fail_walls.append(float(d.wall_ms))
                fail_costs.append(drex_cost(d))
            continue
        w = float(d.wall_ms)
        cost: Decimal | None = drex_cost(d)
        if is_alone:
            drex_alone += 1
            ok_walls.append(w)
            ok_costs.append(cost)
            continue
        routed += 1
        l = llm.get(c)
        if l is not None and not is_prepare_failure(l):
            w += float(l.wall_ms)
            lc = decision_cost("llm_guard", l, llm_costs)
            cost = cost + lc if lc is not None else None
        if action_of(l, "final") is None:
            llm_failed += 1
            fail_walls.append(w)
            fail_costs.append(cost)
        else:
            ok_walls.append(w)
            ok_costs.append(cost)
    n = len(alone)
    n_ok = len(ok_walls)
    ok_total = _sum_cost(ok_costs)
    fail_total = _sum_cost(fail_costs) if fail_costs else Decimal(0)
    per_1000 = (ok_total / n_ok * 1000) if ok_total is not None else None
    return {
        "drex_alone_share": _rate(drex_alone, n),
        "drex_alone_count": drex_alone,
        "routed_to_llm_count": routed,
        "drex_failed_count": drex_failed,
        "llm_failed_count": llm_failed,
        "n_ok": n_ok,
        "n_failed": drex_failed + llm_failed,
        "n": n,
        "p50_wall_ms": percentile(ok_walls, 50),
        "failed_p50_wall_ms": percentile(fail_walls, 50),
        "cost_per_1000": str(per_1000) if per_1000 is not None else None,
        "failed_cost_total": str(fail_total) if fail_total is not None else None,
    }


def ratio(a: Any, b: Any) -> float | None:
    if a is None or b is None:
        return None
    da, db = Decimal(str(a)), Decimal(str(b))
    return float(da / db) if db != 0 else None


# ---------------------------------------------------------------- results


def compute_results(
    decisions: dict[str, dict[str, Decision]],
    available: dict[str, bool],
    labels: dict[str, dict[str, Any]],
    run_info: dict[str, dict[str, Any] | None],
    concurrency: int,
    llm_costs: dict[str, Decimal],
) -> dict[str, Any]:
    """run_info[system] = {"run_seconds", "n_calls"} measured on a live run, or None."""
    case_ids = list(labels)
    rows: dict[str, Any] = {}
    for key, label, src in ROWS:
        if src == "hybrid":
            if not (available.get("drex") and available.get("llm_guard")):
                rows[key] = {"label": label, "available": False}
                continue
            acts, _ = hybrid_actions(decisions["drex"], decisions["llm_guard"], case_ids)
        else:
            system, which = src.split(":")
            if not available.get(system):
                rows[key] = {"label": label, "available": False}
                continue
            acts = {c: action_of(decisions[system].get(c), which) for c in case_ids}
        rows[key] = {"label": label, **row_metrics(acts, labels)}

    systems: dict[str, Any] = {}
    for s in SYSTEMS:
        if available.get(s):
            systems[s] = system_stats(s, decisions[s], case_ids, run_info.get(s), concurrency, llm_costs)
        else:
            systems[s] = {"available": False}

    hybrid: dict[str, Any] = {"available": False}
    if available.get("drex") and available.get("llm_guard"):
        _, alone = hybrid_actions(decisions["drex"], decisions["llm_guard"], case_ids)
        hybrid = {"available": True, **hybrid_stats(decisions["drex"], decisions["llm_guard"], alone, llm_costs)}

    d, l = systems["drex"], systems["llm_guard"]
    ratios = {
        "llm_p50_over_drex_p50": ratio(l.get("p50_wall_ms"), d.get("p50_wall_ms")),
        "llm_cost_over_drex_cost": ratio(l.get("cost_per_1000"), d.get("cost_per_1000")),
    }
    return {"rows": rows, "systems": systems, "hybrid": hybrid, "ratios": ratios}


def _fmt(v: Any, digits: int = 1) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _money(v: str | None) -> str:
    return "n/a" if v is None else f"${Decimal(v).quantize(Decimal('0.0001'))}"


UNKNOWN = "unknown"


def footnote_cost(meta: dict[str, Any]) -> str:
    price = meta.get("drex_price_per_million_input_tokens", "0.03")
    return (f"3. Drex cost is calculated from the configured price of ${price} per million input tokens. "
            "LLM guard cost is the provider-estimated estimated_cost per call (an estimate, not a billed amount). "
            "Latency measured from our laptop on venue Wi-Fi.")


def footnote_warmup(warmup: Any) -> str:
    head = "4. Development cases are excluded from these figures."
    if isinstance(warmup, int) and not isinstance(warmup, bool):
        return f"{head} {warmup} were used only as discarded warm-up calls."
    if warmup is None or warmup == UNKNOWN:
        return f"{head} The number used as discarded warm-up calls is unknown (not recorded by the live run)."
    return f"{head} Discarded warm-up calls: {warmup}."


def render_markdown(results: dict[str, Any]) -> str:
    meta = results["meta"]
    out: list[str] = [f"# Quorum benchmark: split `{meta['split']}`", ""]
    out.append(f"Started {meta['started_at']}. Concurrency {meta['concurrency']}. Policy L/V/D = {meta['policy']}. Cases: {meta['n_cases']}.")
    if meta.get("reuse"):
        out.append("")
        out.append(f"Recomputed on {meta.get('reused_at') or UNKNOWN} from stored decisions (--reuse, no model calls). "
                   f"Decisions provenance: {meta.get('decisions_provenance') or UNKNOWN}.")
    out.append("")
    out.append("| Row | Correct | Unsafe approvals | Unnecessary escalations | Injection blocked (S5) | Bypass text blocked (S3) | Failures |")
    out.append("|---|---|---|---|---|---|---|")
    for key, label, _ in ROWS:
        r = results["rows"][key]
        if not r.get("available"):
            out.append(f"| {label} | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |")
            continue
        out.append(
            f"| {label} | {r['correct']}/{r['n']} ({_pct(r['correct_rate'])}) "
            f"| {r['unsafe_approvals']}/{r['n_forged']} ({_pct(r['unsafe_approval_rate'])}) "
            f"| {r['unnecessary_escalations']}/{r['n_clean']} ({_pct(r['unnecessary_escalation_rate'])}) "
            f"| {r['s5_blocked']}/{r['n_s5']} | {r['s3_blocked']}/{r['n_s3']} | {r['failures']} |"
        )
    out += ["", "## Speed and cost", ""]
    out.append("Latency, tokens and cost per 1,000 are over successful decisions only.")
    out.append("")
    out.append("| System | Model | OK | p50 wall ms | p95 wall ms | Decisions/s | Mean input tokens | Mean output tokens | Cost per 1,000 |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for s in SYSTEMS:
        st = results["systems"][s]
        if not st.get("available"):
            out.append(f"| {s} | unavailable | | | | | | | |")
            continue
        dps = "unavailable" if st["decisions_per_s"] is None else _fmt(st["decisions_per_s"], 2)
        out.append(
            f"| {s} | {', '.join(st['models']) or 'n/a'} | {st['n_ok']}/{st['n']} | {_fmt(st['p50_wall_ms'], 0)} | {_fmt(st['p95_wall_ms'], 0)} "
            f"| {dps} | {_fmt(st['mean_input_tokens'])} | {_fmt(st['mean_output_tokens'])} "
            f"| {_money(st['cost_per_1000'])} |"
        )
    fail_lines = []
    for s in SYSTEMS:
        st = results["systems"][s]
        if st.get("available") and st["n_failed"]:
            fail_lines.append(
                f"- {s}: {st['n_failed']} failed ({st['n_prepare_failed']} before any call, at parse); "
                f"failed-call p50 {_fmt(st['failed_p50_wall_ms'], 0)} ms, p95 {_fmt(st['failed_p95_wall_ms'], 0)} ms; "
                f"tokens {st['failed_input_tokens']} in / {st['failed_output_tokens']} out; cost {_money(st['failed_cost_total'])}."
            )
    if fail_lines:
        out += ["", "Failures (not in the figures above):", *fail_lines]
    h = results["hybrid"]
    if h.get("available"):
        out += ["", f"Hybrid: Drex handled {h['drex_alone_count']}/{h['n']} cases alone ({_pct(h['drex_alone_share'])}), "
                f"{h['routed_to_llm_count']} routed to the LLM guard, {h['n_failed']} failed "
                f"({h['drex_failed_count']} Drex, {h['llm_failed_count']} LLM guard); "
                f"blended cost per 1,000 successful {_money(h['cost_per_1000'])}; median latency {_fmt(h['p50_wall_ms'], 0)} ms."]
    else:
        out += ["", "Hybrid: unavailable."]
    rt = results["ratios"]
    out += ["", f"LLM guard p50 / Drex p50: {_fmt(rt['llm_p50_over_drex_p50'], 2)}x. "
            f"LLM guard cost / Drex cost: {_fmt(rt['llm_cost_over_drex_cost'], 2)}x."]
    stubs = sum(results["systems"][s].get("stub_decisions", 0) for s in SYSTEMS)
    if stubs:
        out += ["", f"**WARNING: {stubs} stub (simulated) decisions included. Do not publish these numbers.**"]
    out += ["", "## Correct by scenario", ""]
    out.append("| Row | " + " | ".join(SCENARIOS) + " |")
    out.append("|---|" + "---|" * len(SCENARIOS))
    for key, label, _ in ROWS:
        r = results["rows"][key]
        if not r.get("available"):
            out.append(f"| {label} | " + " | ".join("unavailable" for _ in SCENARIOS) + " |")
            continue
        g = r["per_scenario"]
        out.append(f"| {label} | " + " | ".join(f"{g[s]['correct']}/{g[s]['n']}" for s in SCENARIOS) + " |")
    pm = meta.get("ndi_median_parse_s")
    out += [
        "",
        f"1. {meta['n_cases']} constructed scenarios on synthetic invoices (Company Documents v2, Apache-2.0). Not real fraud cases.",
        f"2. Decision stage only. Both systems received the same NDI output. NDI median parse time: {_fmt(pm, 1)} s, reported separately.",
        footnote_cost(meta),
    ]
    if meta["split"] == "test":
        out.append(footnote_warmup(meta.get("warmup", UNKNOWN)))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- orchestration


def run_seconds_path(data_dir: Path, split: str) -> Path:
    return data_dir / "decisions" / f"run_seconds_{split}.json"


def load_run_info(path: Path) -> dict[str, dict[str, Any]]:
    """Measured {system: {run_seconds, n_calls}} from a live run; {} when missing or bad."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except ValueError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for s, v in raw.items() if isinstance(raw, dict) else []:
        if isinstance(v, dict) and isinstance(v.get("run_seconds"), (int, float)) and isinstance(v.get("n_calls"), int):
            out[s] = {"run_seconds": float(v["run_seconds"]), "n_calls": v["n_calls"]}
    return out


PROVENANCE_KEYS = ("started_at", "warmup", "concurrency", "provenance")


def load_run_provenance(path: Path) -> dict[str, dict[str, Any]]:
    """{system: {started_at, warmup, concurrency, provenance}} recorded by a live run.

    Keys a legacy file does not hold are reported as "unknown", never invented."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except ValueError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for s, v in raw.items() if isinstance(raw, dict) else []:
        if s not in SYSTEMS or not isinstance(v, dict):
            continue
        rec = {k: v.get(k, UNKNOWN) for k in PROVENANCE_KEYS}
        for k in ("warmup", "concurrency"):
            if not (isinstance(rec[k], int) and not isinstance(rec[k], bool)):
                rec[k] = UNKNOWN
        for k in ("started_at", "provenance"):
            if not isinstance(rec[k], str) or not rec[k]:
                rec[k] = UNKNOWN
        out[s] = rec
    return out


def summarize(values: dict[str, Any]) -> Any:
    """One value when every system agrees; "unknown" when nothing is known; else per system."""
    if not values:
        return UNKNOWN
    if len({json.dumps(v) for v in values.values()}) == 1:
        return next(iter(values.values()))
    return "; ".join(f"{s}: {v}" for s, v in sorted(values.items()))


async def bench(
    split: str,
    system: str = "both",
    concurrency: int = config.CONCURRENCY,
    reuse: bool = False,
    warmup_n: int = 5,
    data_dir: Path | None = None,
    cases_file: Path | None = None,
    backend_dir: Path = BACKEND,
    trusted_file: Path | None = None,
) -> dict[str, Any]:
    """Run (or reuse) decisions, store them, then join labels and write results."""
    data_dir = Path(data_dir) if data_dir else data_dir_default()
    cases_file = Path(cases_file) if cases_file else data_dir / "cases.jsonl"
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    selected = list(SYSTEMS) if system == "both" else [system]

    inputs = load_inputs(cases_file, split)
    case_ids = [r["case_id"] for r in inputs]
    decisions: dict[str, dict[str, Decision]] = {s: {} for s in SYSTEMS}
    available: dict[str, bool] = {s: False for s in SYSTEMS}
    run_info: dict[str, dict[str, Any] | None] = {s: None for s in SYSTEMS}
    parse_file = data_dir / "decisions" / f"parse_ms_{split}.json"
    run_file = run_seconds_path(data_dir, split)
    warmed = 0
    provenance: dict[str, dict[str, Any]] = {}
    prepare_failed: list[str] = []

    if not reuse:
        trusted = checks.load_trusted(str(trusted_file or config.TRUSTED_FILE))
        prepared, failures = await prepare(inputs, trusted, backend_dir)
        prepare_failed = sorted(failures)
        for c, err in failures.items():
            print(f"[run_bench] {c}: {err}", file=sys.stderr)
        parse_file.parent.mkdir(parents=True, exist_ok=True)
        parse_file.write_text(json.dumps({c: p.parse_ms for c, (p, _) in prepared.items()}))
        fns = {s: resolve_system(s) for s in selected}
        if warmup_n > 0 and any(fns.values()):
            dev_inputs = load_inputs(cases_file, "dev")[:warmup_n]
            dev_prepared, _dev_failures = await prepare(dev_inputs, trusted, backend_dir)
            warmed = min(warmup_n, len(dev_prepared))
            for s, fn in fns.items():
                if fn is not None:
                    await warmup(s, fn, dev_prepared, warmup_n, concurrency)
        measured: dict[str, dict[str, Any]] = load_run_info(run_file)
        for s, rec in load_run_provenance(run_file).items():
            if s in measured:  # keep what an earlier live run recorded for systems not rerun now
                measured[s].update({k: v for k, v in rec.items() if v != UNKNOWN})
        for s, fn in fns.items():
            if fn is None:
                print(f"[run_bench] {s}: unavailable (module missing)", file=sys.stderr)
                continue
            ran, secs = await run_system(s, fn, prepared, concurrency)
            decisions[s] = {**ran, **prepare_failure_decisions(s, failures)}
            run_info[s] = {"run_seconds": secs, "n_calls": len(ran)}
            provenance[s] = {"started_at": started, "warmup": warmed, "concurrency": concurrency, "provenance": "live"}
            measured[s] = {**run_info[s], **provenance[s]}
            store_decisions(data_dir, s, decisions[s])
            available[s] = True
        run_file.parent.mkdir(parents=True, exist_ok=True)
        run_file.write_text(json.dumps(measured, indent=1))

    # Systems not run now: use stored decisions if any (zero calls).
    for s in SYSTEMS:
        if not available[s] and (reuse or s not in selected):
            stored = load_decisions(data_dir, s, case_ids)
            if stored:
                decisions[s], available[s] = stored, True
                # Measured run time from the live run that produced them, if persisted.
                run_info[s] = load_run_info(run_file).get(s)
                provenance[s] = load_run_provenance(run_file).get(s) or {k: UNKNOWN for k in PROVENANCE_KEYS}

    # Labels are joined only now, after every decision is stored.
    labels = load_labels(cases_file, split)
    llm_costs = load_llm_costs(data_dir)
    results = compute_results(decisions, available, labels, run_info, concurrency, llm_costs)

    parse_ms: list[float] = []
    if parse_file.exists():
        parse_ms = [float(v) for c, v in json.loads(parse_file.read_text()).items() if c in labels and v is not None]
    used = {s: provenance[s] for s in SYSTEMS if available[s] and s in provenance}
    if not used and not reuse:  # live run with no system available: this run's own facts
        used_started, used_warmup, used_conc = started, warmed, concurrency
    else:
        used_started = summarize({s: p["started_at"] for s, p in used.items()})
        used_warmup = summarize({s: p["warmup"] for s, p in used.items()})
        used_conc = summarize({s: p["concurrency"] for s, p in used.items()})
    results["meta"] = {
        "started_at": used_started,
        "split": split,
        "systems_requested": selected,
        "reuse": reuse,
        "reused_at": started if reuse else None,
        "decisions_provenance": summarize({s: p["provenance"] for s, p in used.items()}),
        "run_provenance": used,
        "warmup": used_warmup,
        "prepare_failed": prepare_failed,
        "concurrency": used_conc,
        "policy": list(POLICY),
        "hybrid_threshold": HYBRID_THRESHOLD,
        "drex_model": config.DREX_MODEL,
        "models": {s: results["systems"][s].get("models") for s in SYSTEMS},
        "n_cases": len(labels),
        "counts": {
            "by_scenario": {sc: sum(1 for v in labels.values() if v["scenario"] == sc) for sc in SCENARIOS},
            "forged": sum(1 for v in labels.values() if v["forged"]),
            "clean": sum(1 for v in labels.values() if not v["forged"]),
            "decisions": {s: len(decisions[s]) for s in SYSTEMS},
        },
        "ndi_median_parse_s": (statistics.median(parse_ms) / 1000) if parse_ms else None,
        "drex_price_per_million_input_tokens": "0.03",
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "results.json").write_text(json.dumps(results, indent=2))
    (data_dir / "results.md").write_text(render_markdown(results))
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Quorum benchmark: Drex vs LLM guard")
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--system", choices=["drex", "llm_guard", "both"], default="both")
    ap.add_argument("--concurrency", type=int, default=config.CONCURRENCY)
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--warmup", type=int, default=5)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    results = asyncio.run(
        bench(a.split, system=a.system, concurrency=a.concurrency, reuse=a.reuse, warmup_n=a.warmup)
    )
    print(render_markdown(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
