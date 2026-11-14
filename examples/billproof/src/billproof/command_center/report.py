"""Summaries, flat metric files, and COMMAND_CENTER.md.

Safe to call on partial JSONL. Projected GPT figures are marked
``projected: true``. The OR→AND rule change is marked ``simulated: true``.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from billproof.command_center.common import (
    DECIDE_PATH,
    DECIDE_SUMMARY_PATH,
    DOC_PATH,
    GPT_PATH,
    NACE_CAP_USD,
    QUEUE_PATH,
    QUEUE_SUMMARY_PATH,
    REPLAY_PATH,
    ROUTER_CAP_USD,
    RULE_PATH,
    RULE_SUMMARY_PATH,
    atomic_write,
    latest_success,
    load_jsonl,
    load_spend,
    now_iso,
    read_json,
    write_json,
    write_metric,
)
from billproof.command_center.rows import single_document_fixes

def _batch_seconds(rows: list[dict]) -> float:
    seen: dict[str, float] = {}
    for i, row in enumerate(rows):
        if row.get("error"):
            continue
        key = str(row.get("batch_id") or f"row-{i}")
        seen[key] = float(row.get("batch_latency_ms") or row.get("latency_ms") or 0.0)
    return sum(seen.values()) / 1000.0


def queue_source() -> Path:
    """Full rows when the run is on disk. Otherwise the committed replay."""
    if QUEUE_PATH.is_file():
        return QUEUE_PATH
    return REPLAY_PATH


def write_replay(rows: list[dict]) -> None:
    """Compact stream file. Full criterion text stays in the gitignored JSONL."""
    latest = _latest([r for r in rows if not r.get("error")])
    lines = []
    for row in latest:
        lines.append(json.dumps({
            "case_id": row.get("case_id"),
            "decision": row.get("decision"),
            "colour": row.get("colour"),
            "latency_ms": row.get("latency_ms"),
            "cost_usd": row.get("cost_usd"),
            "correct": bool(row.get("correct")),
        }, separators=(",", ":")))
    REPLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(REPLAY_PATH, ("\n".join(lines) + "\n") if lines else "")


def _latest(rows: list[dict]) -> list[dict]:
    """Last row per case. A retry appends; the earlier miss must not count twice."""
    order: list[str] = []
    seen: dict[str, dict] = {}
    for row in rows:
        cid = row.get("case_id")
        if not cid:
            continue
        if cid not in seen:
            order.append(cid)
        seen[cid] = row
    return [seen[cid] for cid in order]


def _rate(rows: list[dict]) -> dict[str, Any]:
    rows = _latest(rows)
    ok = [r for r in rows if not r.get("error")]
    n = len(ok)
    correct = sum(1 for r in ok if r.get("correct"))
    seconds = _batch_seconds(ok)
    cost = sum(float(r.get("cost_usd") or 0.0) for r in ok)
    labels = Counter(r.get("label") for r in ok)
    colours = Counter(r.get("colour") for r in ok)
    simulated = any(bool(r.get("simulated")) for r in ok)
    return {
        "n": n,
        "n_error": sum(1 for r in rows if r.get("error")),
        "n_correct": correct,
        "accuracy": (correct / n) if n else None,
        "criteria_accuracy": (
            sum(1 for r in ok if r.get("criteria_all_correct")) / n if n else None
        ),
        "facts_accuracy": (
            sum(1 for r in ok if r.get("facts_all_correct")) / n if n else None
        ),
        "by_label": dict(labels),
        "by_colour": dict(colours),
        "total_cost_usd": round(cost, 8),
        "cost_per_1000": (cost / n * 1000.0) if n else None,
        "service_seconds": round(seconds, 3),
        "cases_per_minute": (n / seconds * 60.0) if seconds > 0 else None,
        "simulated": simulated,
        "model": next((r.get("model") for r in ok if r.get("model")), None),
    }


def stuck_reasons(rows: list[dict], *, limit: int = 15) -> dict[str, Any]:
    """Criterion text → share of deny + incomplete cases that include it."""
    stuck = [r for r in rows if not r.get("error") and r.get("label") in ("deny", "incomplete")]
    denom = len(stuck)
    counts: Counter[tuple[str, str]] = Counter()
    for row in stuck:
        seen: set[tuple[str, str]] = set()
        for crit in row.get("criteria") or []:
            state = crit.get("state")
            if row.get("label") == "deny" and state != "unmet":
                continue
            if row.get("label") == "incomplete" and state != "unknown":
                continue
            text = (crit.get("text") or crit.get("name") or "").strip()
            if not text:
                continue
            seen.add((text, "unmet" if state == "unmet" else "unknown"))
        counts.update(seen)
    ranked = []
    for (text, role), n in counts.most_common(limit):
        ranked.append({
            "criterion": text,
            "role": role,
            "n": n,
            "pct_of_stuck": (n / denom) if denom else None,
        })
    return {"n_stuck": denom, "reasons": ranked}


def document_bundle(rows: list[dict], *, limit: int = 8) -> dict[str, Any]:
    """Counterfactual: if one not-satisfied fact were documented satisfied,
    how many additional patients would the code approve?

    ``case_lookup`` is optional. Rows already carry facts, scope, and the
    published dependency is recovered from criteria[].facts when the row is
    a baseline (one published criterion structure)."""
    saved: dict[str, set[str]] = defaultdict(set)  # text -> case ids
    text_for: dict[str, str] = {}
    n_considered = 0
    n_no_single = 0
    for row in rows:
        if row.get("error") or row.get("label") not in ("deny", "incomplete"):
            continue
        facts = row.get("facts") or {}
        scope = row.get("scope") or "applicable"
        deps: dict[str, list[str]] = {}
        texts: dict[str, str] = dict(row.get("clauses") or {})
        for crit in row.get("criteria") or []:
            fact_names = list(crit.get("facts") or [])
            if not fact_names:
                continue
            deps[str(crit.get("name"))] = fact_names
            if len(fact_names) == 1 and fact_names[0] not in texts:
                texts[fact_names[0]] = crit.get("text") or fact_names[0]
        if not deps or not facts:
            continue
        n_considered += 1
        fixes = single_document_fixes(facts, deps, scope)
        if not fixes:
            n_no_single += 1
            continue
        for fact in fixes:
            text = texts.get(fact) or fact
            text_for[text] = text
            saved[text].add(row["case_id"])
    ranked = sorted(saved.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    # Greedy union.
    remaining: set[str] = set()
    for cases in saved.values():
        remaining |= cases
    left = set(remaining)
    chosen: list[dict[str, Any]] = []
    covered: set[str] = set()
    while left:
        best_text = max(saved, key=lambda t: (len(saved[t] & left), t))
        gain = saved[best_text] & left
        if not gain:
            break
        covered |= gain
        left -= gain
        chosen.append({
            "criterion": best_text,
            "additional_patients": len(gain),
            "cumulative_patients": len(covered),
        })
        if len(chosen) >= 30:
            break
    prefixes = []
    for n in (1, 3, 5):
        if not chosen:
            break
        take = chosen[:n]
        prefixes.append({
            "n_documents": len(take),
            "patients": take[-1]["cumulative_patients"],
            "criteria": [c["criterion"] for c in take],
            "label": "counterfactual",
        })
    return {
        "method": "counterfactual_fact_true",
        "counterfactual": True,
        "simulated": False,
        "note": (
            "Each figure is the code decision if that one recorded fact were "
            "true and every other recorded fact stayed as Drex answered it. "
            "It is not a second model call."
        ),
        "n_stuck_considered": n_considered,
        "n_need_more_than_one_document": n_no_single,
        "n_patients_one_document_would_approve": len(remaining),
        "ranked": [
            {"criterion": text, "patients": len(cases)}
            for text, cases in ranked[:limit]
        ],
        "bundles": prefixes,
        "greedy": chosen[:limit],
    }


def gpt_projection(gpt_rows: list[dict], n_queue: int) -> dict[str, Any]:
    ok = [r for r in gpt_rows if not r.get("error")]
    n = len(ok)
    if not n:
        return {"n_measured": 0, "projected": None, "simulated": False}
    correct = sum(1 for r in ok if r.get("correct"))
    mean_cost = sum(float(r.get("cost_usd") or 0.0) for r in ok) / n
    mean_ms = sum(float(r.get("latency_ms") or 0.0) for r in ok) / n
    projected = {
        "label": "projected",
        "projected": True,
        "simulated": False,
        "n_queue": n_queue,
        "n_measured": n,
        "time_s": mean_ms * n_queue / 1000.0,
        "cost_usd": mean_cost * n_queue,
        "cases_per_minute": (60000.0 / mean_ms) if mean_ms else None,
        "cost_per_1000": mean_cost * 1000.0,
        "note": (
            "Time and cost scale the measured per-case mean to the full queue. "
            "GPT latency includes shared router rate-limit wait. "
            "Accuracy is the measured sample only; it is not projected."
        ),
    }
    return {
        "n_measured": n,
        "n_error": sum(1 for r in gpt_rows if r.get("error")),
        "accuracy": correct / n,
        "n_correct": correct,
        "mean_cost_usd": mean_cost,
        "mean_latency_ms": mean_ms,
        "total_cost_usd": mean_cost * n,
        "simulated": any(bool(r.get("simulated")) for r in ok),
        "model": ok[0].get("model"),
        "projected": projected,
    }


def write_queue_outputs(*, n_planned: int, gpt_planned: int, status: str,
                        note: str = "") -> dict[str, Any]:
    drex_rows = load_jsonl(QUEUE_PATH)
    gpt_rows = load_jsonl(GPT_PATH)
    write_replay(drex_rows)
    drex = _rate(drex_rows)
    # Planned queue size is the case list, not the rows written so far.
    proj = gpt_projection(gpt_rows, n_planned)
    spend = load_spend()
    summary = {
        "status": status,
        "note": note,
        "n_planned": n_planned,
        "computed_at": now_iso(),
        "drex": drex,
        "stuck": stuck_reasons(_latest([r for r in drex_rows if not r.get("error")])),
        "gpt_planned": gpt_planned,
        "gpt": proj,
        "spend": {**spend, "nace_cap": NACE_CAP_USD, "router_cap": ROUTER_CAP_USD},
    }
    write_json(QUEUE_SUMMARY_PATH, summary)
    sim = bool(drex["simulated"])
    src = "command_center/queue_replay.jsonl"
    if drex["n"]:
        write_metric("cc_drex_cases_per_minute.json", drex["cases_per_minute"],
                     drex["n"], src, simulated=sim)
        write_metric("cc_drex_cost_per_1000.json", drex["cost_per_1000"],
                     drex["n"], src, simulated=sim)
        write_metric("cc_drex_decision_accuracy.json", drex["accuracy"],
                     drex["n"], src, simulated=sim)
    if proj.get("n_measured"):
        gsrc = "command_center/queue_gpt.jsonl"
        gsim = bool(proj["simulated"])
        write_metric("cc_gpt_sample_decision_accuracy.json", proj["accuracy"],
                     proj["n_measured"], gsrc, simulated=gsim)
        p = proj["projected"]
        write_metric("cc_gpt_projected_full_queue_cost_usd.json", p["cost_usd"],
                     p["n_measured"], gsrc, simulated=False, projected=True,
                     extra={"n_queue": n_planned})
        write_metric("cc_gpt_projected_full_queue_seconds.json", p["time_s"],
                     p["n_measured"], gsrc, simulated=False, projected=True,
                     extra={"n_queue": n_planned})
        write_metric("cc_gpt_projected_cases_per_minute.json", p["cases_per_minute"],
                     p["n_measured"], gsrc, simulated=False, projected=True)
        write_metric("cc_gpt_projected_cost_per_1000.json", p["cost_per_1000"],
                     p["n_measured"], gsrc, simulated=False, projected=True)
    write_metric("cc_nace_spend_usd.json", spend["nace_usd"], drex["n"], src,
                 simulated=False)
    write_metric("cc_router_spend_usd.json", spend["router_usd"], proj.get("n_measured") or 0,
                 "command_center/queue_gpt.jsonl", simulated=False)
    write_doc()
    return summary


def write_doc() -> None:
    q = read_json(QUEUE_SUMMARY_PATH, {})
    rule = read_json(RULE_SUMMARY_PATH, {})
    decide = read_json(DECIDE_SUMMARY_PATH, {})
    spend = load_spend()
    drex = q.get("drex") or {}
    gpt = q.get("gpt") or {}
    proj = gpt.get("projected") or {}
    lines = [
        "# Prior-auth command center",
        "",
        ". Live Drex (`drex-max-v1.1`) decides each criterion; code applies",
        "the published AND/OR rules. GPT (`openai/gpt-6.1-sol`) is a fixed hash",
        "sample. Criteria-gym `policy_checklist` (Hammer Labs), hackathon demo use.",
        "Validation + development `initial_packet` rows.",
        "",
        "Replay routes live on `billproof.server.cc_routes:router`. `app.py` already",
        "imports it with `importlib.import_module(\"billproof.server.cc_routes\").router`.",
        "",
        "## How to re-run",
        "",
        "From `billproof/`, resumable (skips successful rows in the full JSONL).",
        "Logs: `out/logs/cc_queue.log`, `cc_rule_change.log`, `cc_what_decides.log`.",
        "",
        "```bash",
        "mkdir -p out/logs out/command_center data/command_center",
        "PYTHONUNBUFFERED=1 PYTHONPATH=src nohup .venv/bin/python scripts/cc_queue.py \\",
        "  >> out/logs/cc_queue.log 2>&1 &",
        "PYTHONUNBUFFERED=1 PYTHONPATH=src nohup .venv/bin/python scripts/cc_rule_change.py \\",
        "  >> out/logs/cc_rule_change.log 2>&1 &",
        "PYTHONUNBUFFERED=1 PYTHONPATH=src nohup .venv/bin/python scripts/cc_what_decides.py \\",
        "  >> out/logs/cc_what_decides.log 2>&1 &",
        "```",
        "",
        "The full queue, including criterion text and fact answers, is",
        "`out/command_center/queue.jsonl` (gitignored). The scripts regenerate it.",
        "Git holds `data/command_center/queue_replay.jsonl` (case id, decision,",
        "colour, latency, cost, correct) and `queue_summary.json`.",
        "Caps inside the scripts: Nace $0.90, router $0.45 (shared spend file).",
        "A stopped run is partial and safe to start again. Do not delete the",
        "full JSONL unless you intend to pay for those cases twice.",
        "",
        "## Queue (Drex, live)",
        "",
        f"- Status: `{q.get('status', 'not started')}`. Planned cases: {q.get('n_planned', '—')}.",
        f"- Decided: {drex.get('n', 0)}. Decision accuracy: {_fmt(drex.get('accuracy'))} "
        f"(n={drex.get('n', 0)}). Criteria accuracy: {_fmt(drex.get('criteria_accuracy'))}. "
        f"Fact accuracy (every fact right): {_fmt(drex.get('facts_accuracy'))}.",
        f"- Cases per minute (summed call latency): {_fmt(drex.get('cases_per_minute'))}.",
        f"- Cost per 1,000: {_fmt_usd(drex.get('cost_per_1000'))}. "
        f"Spent on recorded rows: {_fmt_usd(drex.get('total_cost_usd'))}.",
        f"- Colours: {drex.get('by_colour') or '—'}. Labels: {drex.get('by_label') or '—'}.",
        f"- Model: `{drex.get('model') or 'drex-max-v1.1'}`. Simulated: {drex.get('simulated', False)}.",
        "",
        *_full_queue_md(),
        "",
        "## GPT (measured sample, projected onto the earlier initial queue)",
        "",
        f"- Measured n: {gpt.get('n_measured', 0)} of {q.get('gpt_planned', 40)} "
        f"(hash seed 20261114, not picked by outcome).",
        f"- Sample decision accuracy: {_fmt(gpt.get('accuracy'))} "
        "(this accuracy is measured, not projected).",
        f"- Projected time for that earlier queue: {_fmt(proj.get('time_s'))} s. "
        f"Projected cost for that earlier queue: {_fmt_usd(proj.get('cost_usd'))}.",
        f"- Projected cases per minute: {_fmt(proj.get('cases_per_minute'))}. "
        f"Projected cost per 1,000: {_fmt_usd(proj.get('cost_per_1000'))}.",
        "- Those four GPT figures are **projected** from the sample mean onto the",
        "  earlier initial queue only (the `n_queue` inside the projection).",
        "  They are not scaled to the 9,686-case run.",
        "",
        "## Rule change",
        "",
        _rule_md(rule),
        "",
        "## What decides",
        "",
        _decide_md(decide),
        "",
        "## Spend",
        "",
        f"- Nace (Drex) recorded by these scripts: {_fmt_usd(spend.get('nace_usd'))} "
        f"(cap ${NACE_CAP_USD:.2f}, budget $1.00).",
        f"- Router (GPT) recorded by these scripts: {_fmt_usd(spend.get('router_usd'))} "
        f"(cap ${ROUTER_CAP_USD:.2f}, budget $0.50).",
        f"- Drex cost sitting on queue rows: {_fmt_usd(drex.get('total_cost_usd'))}. "
        f"Drex cost sitting on what-decides rows: {_fmt_usd(decide.get('total_cost_usd'))}. "
        "The Nace total is higher because a batch under 95% fact-perfect is billed "
        "and then discarded.",
        "- The caps are local. Other threads share the same provider accounts.",
        "",
        "## What is real, simulated, or projected",
        "",
        "- **Real:** Drex answers on the queue and on record-removal probes; the",
        "  code decision; accuracy against `target`; GPT answers on the hash sample;",
        "  the search that found no same-drug old/new policy pair.",
        "- **Batch size:** Drex calls are capped at 200 questions. A measured",
        "  507-question pack kept the decision (~97%) and got every fact right on",
        "  only ~61% of cases. Packs near 200 questions stayed near 98% facts.",
        "  A batch under 95% fact-perfect is re-asked in halves. Misses inside a",
        "  batch that clears 95% are kept. The replay serves the re-asked call,",
        "  and that row is marked `resampled`. The first call stays in the Nace total.",
        "- **Failures:** A split keeps the half that already returned. The other half is an",
        "  error for those cases only. A what-decides row with any failed record",
        "  probe is an error and is not counted as agreement. An invalid GPT",
        "  choice is an error, not the first listed option.",
        "- **Simulated:** the 189 flips are a simulated update, not a real old/new",
        "  policy pair. Each published OR list is split so every alternative",
        "  clause is required. Fact answers are the live Drex answers.",
        "- **Projected:** GPT time and cost if the sample mean were applied to",
        "  every case in the earlier initial queue. GPT was not run on the",
        "  9,686-case set, and those figures are not rescaled to it.",
        "- **Counterfactual (not a model call):** \"send this document\" counts how",
        "  many stuck cases the code would approve if one recorded fact flipped to true.",
        "",
        f"_Updated {now_iso()}._",
        "",
    ]
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text("\n".join(lines))


def _full_queue_md() -> list[str]:
    """Full criteria-gym queue and the live what-if. Reads the scale summary."""
    from billproof.command_center.common import FULL_SUMMARY_PATH, PRESETS_PATH
    full = read_json(FULL_SUMMARY_PATH, {})
    presets = read_json(PRESETS_PATH, {})
    if not isinstance(full, dict):
        full = {}
    drex = full.get("drex") or {}
    spend = full.get("spend") or {}
    lines = [
        "## Full queue and live what-if ",
        "",
        "Every `policy_checklist` row in train, validation, and development",
        "(initial requests and follow-up stages). Drex (`drex-max-v1.1`) answers",
        "each criterion; code applies the published rule. Edits below are",
        "**hypothetical what-ifs**: the same cached facts, a different rule.",
        "They are not a published policy change and they are not a second model call,",
        "except when a new criterion text is asked.",
        "",
        "```bash",
        "cd billproof && mkdir -p out/logs out/command_center data/command_center",
        "PYTHONUNBUFFERED=1 PYTHONPATH=src nohup .venv/bin/python scripts/cc_scale.py \\",
        "  >> out/logs/cc_scale.log 2>&1 &",
        "PYTHONPATH=src .venv/bin/python scripts/cc_presets.py",
        "```",
        "",
        "Fact cache (gitignored): `out/command_center/facts.jsonl`.",
        "Compact replay: `data/command_center/full_replay.jsonl`.",
        "Policy list: `data/command_center/policies.json`.",
        "New Drex spend for this run is capped at $1.00 in `out/command_center/full_spend.json`.",
        "Answers already on the 2,211-case queue are reused and not billed again.",
        "A new criterion is a separate cap of $0.05 and at most 500 cases.",
        "",
        f"- Status: `{full.get('status', 'not started')}`. "
        f"Planned cases: {full.get('n_planned', '—')}. "
        f"Policies: {full.get('n_policies', '—')}. "
        f"Initial: {full.get('n_initial', '—')}. Follow-up: {full.get('n_followup', '—')}.",
        f"- Cached Drex decisions: {drex.get('n', 0)}. "
        f"Decision accuracy: {_fmt(drex.get('accuracy'))} (n={drex.get('n', 0)}). "
        f"Criteria accuracy: {_fmt(drex.get('criteria_accuracy'))}. "
        f"Fact accuracy: {_fmt(drex.get('facts_accuracy'))}.",
        f"- Reused from the earlier queue: {drex.get('reused_n', 0)} "
        f"(row cost {_fmt_usd(drex.get('reused_cost_usd'))}, not charged again).",
        f"- Cases per minute (summed call latency): {_fmt(drex.get('cases_per_minute'))}. "
        f"Cost per 1,000 on the rows: {_fmt_usd(drex.get('cost_per_1000'))}. "
        f"Row cost: {_fmt_usd(drex.get('total_cost_usd'))}.",
        f"- New Nace spend this run: {_fmt_usd(spend.get('nace_usd'))} "
        f"(cap {_fmt_usd(spend.get('nace_cap'))}). "
        f"Of which new-criterion calls: {_fmt_usd(spend.get('criterion_usd'))}.",
        f"- Colours: {drex.get('by_colour') or '—'}. Labels: {drex.get('by_label') or '—'}.",
        f"- Rederive mismatches (cached decision vs `logic.decide` on the same facts): "
        f"{drex.get('n_rederive_mismatch', '—')}.",
        f"- Model: `{drex.get('model') or 'drex-max-v1.1'}`. Simulated: {drex.get('simulated', False)}.",
        "",
        "Endpoints (existing routes are unchanged):",
        "`GET /api/cc/policies`, `GET /api/cc/policy/{id}`, `POST /api/cc/what_if`,",
        "`POST /api/cc/what_if/new_criterion` (SSE), `GET /api/cc/presets`.",
        "`GET /api/cc/summary` prefers this full run when `full_summary.json` exists",
        "and keeps the 2,211-case run under `legacy_initial_queue`.",
        "`GET /api/cc/queue/stream` stays on that earlier replay;",
        "`?full=1` replays `full_replay.jsonl`.",
        "",
        "### Preset what-ifs",
        "",
    ]
    scenarios = []
    if isinstance(presets, dict):
        scenarios = list(presets.get("scenarios") or [])
    if not scenarios:
        lines.append("- Not computed yet. They appear after the fact cache has rows.")
    for item in scenarios:
        if item.get("error"):
            lines.append(f"- **{item.get('title')}** — {item.get('error')}.")
            continue
        policy = item.get("policy") or {}
        who = ""
        if policy:
            who = (
                f" Policy `{policy.get('id')}`"
                f" ({policy.get('drug') or policy.get('title')}, {policy.get('state')})."
            )
        lines.append(
            f"- **{item.get('title')}** (hypothetical). "
            f"Affected {item.get('n_affected', 0)}, decided {item.get('n_decided', 0)}, "
            f"flips {item.get('n_flips', 0)}. Counts: {item.get('flip_counts') or '—'}.{who}"
        )
    lines += [
        "",
        "Partial caches are labelled `partial`. A flip count is only over cases",
        "whose Drex facts are already cached.",
    ]
    return lines


def _rule_md(rule: dict) -> str:
    if not rule:
        return "- Not started."
    counts = rule.get("flip_counts") or {}
    search = rule.get("search") or {}
    n_flips = rule.get("n_flips", 0)
    if rule.get("simulated", True):
        pair_line = (
            f"- The {n_flips} flips are a simulated update. "
            "The run did not use a real old/new policy pair."
        )
    else:
        pair_line = f"- The {n_flips} flips used a real old/new policy pair."
    return "\n".join([
        f"- Status: `{rule.get('status', '—')}`. Simulated: **{rule.get('simulated', True)}**.",
        pair_line,
        f"- Pair search: found={search.get('found')}. {search.get('reason', '')}",
        f"- Update: {rule.get('description', '')}",
        f"- Affected cases decided: {rule.get('n_done', 0)} / {rule.get('n_affected', '—')}.",
        f"- Flips: {rule.get('n_flips', 0)}. Counts: {counts or '—'}.",
    ])


def _decide_md(decide: dict) -> str:
    if not decide:
        return "- Not started."
    reasons = (decide.get("stuck") or {}).get("reasons") or []
    top = reasons[:5]
    bits = [f"- Status: `{decide.get('status', '—')}`. Probed: {decide.get('n_done', 0)}."]
    agr = decide.get("agreement")
    bits.append(
        f"- Deciding-record agreement with gold construction: {_fmt(agr)} "
        f"(n={decide.get('n_scored', 0)})."
    )
    by_kind = decide.get("agreement_by_kind") or {}
    if by_kind:
        parts = []
        for kind, slot in sorted(by_kind.items(), key=lambda kv: -kv[1].get("n", 0)):
            n = slot.get("n") or 0
            a = slot.get("n_agree") or 0
            parts.append(f"{kind} {a}/{n}")
        bits.append(
            "- Agreement by gold kind: " + "; ".join(parts) + ". "
            "Scope and absent cases are mostly 'no single record'. "
            "criteria_met and criteria_unmet are the hinge check."
        )
    if top:
        bits.append("- Top stuck clauses (share of deny + incomplete):")
        for item in top:
            pct = item.get("pct_of_stuck")
            bits.append(
                f"  - {item.get('role')}: {item.get('criterion')} — "
                f"{item.get('n')} ({_fmt(pct)})"
            )
    bundle = decide.get("documents") or {}
    bundles = bundle.get("bundles") or []
    if bundles:
        first = bundles[0]
        bits.append(
            f"- Send these {first.get('n_documents')} document(s) and "
            f"{first.get('patients')} more patients get approved "
            "(counterfactual, one clause set to documented-satisfied)."
        )
    return "\n".join(bits)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if 0.0 <= value <= 1.0:
            return f"{value * 100:.1f}%"
        return f"{value:.2f}"
    return str(value)


def _fmt_usd(value: Any) -> str:
    if value is None:
        return "—"
    return f"${float(value):.4f}"


def write_rule_summary(payload: dict) -> None:
    payload = {**payload, "computed_at": now_iso()}
    write_json(RULE_SUMMARY_PATH, payload)
    write_metric(
        "cc_rule_change_flips.json",
        payload.get("n_flips"),
        int(payload.get("n_done") or 0),
        "command_center/rule_change.jsonl",
        simulated=True,
        extra={"flip_counts": payload.get("flip_counts"), "pair_kind": payload.get("pair_kind")},
    )
    write_doc()


def write_decide_summary(*, status: str, note: str = "") -> dict[str, Any]:
    rows = _latest([r for r in load_jsonl(DECIDE_PATH) if not r.get("error")])
    scored = [r for r in rows if r.get("agree") is not None]
    n_agree = sum(1 for r in scored if r.get("agree"))
    by_kind: dict[str, dict[str, int]] = {}
    for row in scored:
        kind = str(row.get("gold_kind") or "unknown")
        slot = by_kind.setdefault(kind, {"n": 0, "n_agree": 0})
        slot["n"] += 1
        slot["n_agree"] += int(bool(row.get("agree")))
    queue_rows = _latest([r for r in load_jsonl(queue_source()) if not r.get("error")])
    stuck_src = queue_rows or rows
    summary = {
        "status": status,
        "note": note,
        "computed_at": now_iso(),
        "n_done": len(rows),
        "n_scored": len(scored),
        "n_agree": n_agree,
        "agreement": (n_agree / len(scored)) if scored else None,
        "agreement_by_kind": by_kind,
        "simulated": any(bool(r.get("simulated")) for r in rows),
        "total_cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 8),
        "stuck": stuck_reasons(stuck_src),
        "documents": document_bundle(queue_rows),
        "stuck_source": "queue" if queue_rows else "what_decides",
    }
    write_json(DECIDE_SUMMARY_PATH, summary)
    if scored:
        write_metric(
            "cc_what_decides_agreement.json",
            summary["agreement"],
            len(scored),
            "command_center/what_decides.jsonl",
            simulated=bool(summary["simulated"]),
        )
    write_doc()
    return summary


def cases_index() -> dict[str, dict]:
    """Merge the latest successful queue, rule, and what-decides rows."""
    out: dict[str, dict] = {}
    for row in latest_success(queue_source()).values():
        out[row["case_id"]] = {"queue": row}
    for row in latest_success(RULE_PATH).values():
        out.setdefault(row["case_id"], {})["rule_change"] = row
    for row in latest_success(DECIDE_PATH).values():
        out.setdefault(row["case_id"], {})["what_decides"] = row
    return out


def replay_rows() -> list[dict]:
    """Latest non-error queue row per case, in first-seen order.

    Uses the full gitignored JSONL when this machine has it, so a live run
    can stream detail. A checkout without that file replays
    ``queue_replay.jsonl``.
    """
    return _latest([r for r in load_jsonl(queue_source()) if not r.get("error")])
