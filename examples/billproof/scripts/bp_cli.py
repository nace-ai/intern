#!/usr/bin/env python3
"""Agent-facing prior-auth CLI for Intern.

  list                         three small open requests (no gold labels)
  audit-case <id>              live Drex decision plus the deciding record
  explain <id>                 the same audit, as a short brief
  what-if <preset>             Command Center hypothetical (cached Drex facts)
  presets                      preset ids

Run from billproof/:  .venv/bin/python scripts/bp_cli.py <command>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.pa.cases import PaCase, load_cases
from billproof.bench.pa.columns import _drex_decide, _prediction_from_facts, drex_column
from billproof.bench.pa.questions import build_questions
from billproof.bench.pa.score import facts_from_answers
from billproof.command_center.rows import colour_of
from billproof.config import DATA_DIR, settings

RECOMMENDATION = {
    "criteria_met": "approve",
    "criteria_unmet": "deny",
    "incomplete": "request_documents",
    "not_applicable": "not_applicable",
}
CACHE_DIR = Path(__file__).resolve().parents[1] / "out" / "intern" / "audits"


def _emit(payload: dict[str, Any], *, code: int = 0) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    raise SystemExit(code)


def _title(case: PaCase) -> str:
    policy = case.policy
    return str(policy.get("document_title") or policy.get("title") or case.list_id)[:160]


def _clause(case: PaCase, fact: str) -> str:
    for item in case.policy.get("criteria_list") or []:
        if item.get("fact") == fact:
            return str(item.get("text") or fact)
    return str(case.fact_definitions.get(fact) or fact)


def _criterion_text(case: PaCase, name: str) -> str:
    deps = case.criterion_deps.get(name) or []
    parts = [_clause(case, fact) for fact in deps] or [name]
    return "; ".join(parts)[:400]


def _excerpt(record: dict[str, Any]) -> str:
    for key in ("text", "content", "body", "note", "summary", "value"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:280]
    bits: list[str] = []
    for key, value in record.items():
        if key in {"id", "fact"} or not isinstance(value, str) or not value.strip():
            continue
        bits.append(f"{key}: {' '.join(value.split())[:120]}")
    return "; ".join(bits)[:280]


def _load_split(split: str) -> list[PaCase]:
    return load_cases(split, stages=("initial_packet",))


def find_case(case_id: str) -> PaCase:
    needle = case_id.strip()
    for split in ("validation", "development"):
        for case in _load_split(split):
            if case.case_id == needle:
                return case
    _emit({"error": f"unknown case {needle}", "hint": "run: bp_cli.py list"}, code=2)


def _readable(case: PaCase) -> bool:
    title = _title(case)
    if title.lower().endswith(".pdf") or title[:1].isdigit():
        return False
    noisy = ("opposite", "nothing_documented", "all_required_information")
    return not any(token in case.case_id for token in noisy)


def queue() -> list[PaCase]:
    """One small validation request from each decision class. Stable, no gold in the print."""
    cases = _load_split("validation")
    picked: list[PaCase] = []
    for decision in ("criteria_met", "criteria_unmet", "incomplete"):
        pool = [
            case for case in cases
            if case.target.get("decision") == decision and 1 <= case.n_records <= 2 and case.n_facts <= 4
        ]
        readable = [case for case in pool if _readable(case)]
        pool = readable or pool
        if not pool:
            pool = [case for case in cases if case.target.get("decision") == decision and case.n_records >= 1]
        if not pool:
            continue
        pool.sort(key=lambda case: (case.n_records, case.n_facts, case.case_id))
        picked.append(pool[0])
    return picked


def _cache_path(case_id: str) -> Path:
    safe = case_id.replace("/", "_")
    return CACHE_DIR / f"{safe}.json"


def _public_audit(audit: dict[str, Any]) -> dict[str, Any]:
    hidden = {"_gold_decision"}
    return {key: value for key, value in audit.items() if key not in hidden}


def audit_case(case: PaCase) -> dict[str, Any]:
    """One live Drex pass, then one pass per record removed, to name the deciding record."""
    cfg = settings()
    if not cfg.use_real("drex"):
        _emit({"error": "Drex is not live (no NACE_API_KEY)", "case_id": case.case_id}, code=3)
    started = time.perf_counter()
    base = drex_column(case)
    if base.get("error"):
        _emit({"error": base["error"], "case_id": case.case_id, "model": base.get("model")}, code=1)
    prediction = base["prediction"]
    decision = str(prediction.get("decision") or "")
    label, colour = colour_of(decision)
    scope = str(prediction.get("scope") or "applicable")
    questions = [q for q in build_questions(case, include_scope=False)]
    base_facts = base["facts"]
    calls = int(base.get("calls") or 0)
    cost = float(base.get("cost_usd") or 0.0)
    tokens_in = int(base.get("input_tokens") or 0)
    tokens_out = int(base.get("output_tokens") or 0)
    flips: list[dict[str, Any]] = []
    by_id = {str(record.get("id")): record for record in case.records}
    for record in case.records:
        rid = str(record.get("id"))
        removed = _drex_decide(case, questions, state=case.state(drop_records={rid}))
        calls += int(removed.calls or 0)
        cost += float(removed.cost_usd or 0.0)
        tokens_in += int(removed.input_tokens or 0)
        tokens_out += int(removed.output_tokens or 0)
        if removed.error:
            continue
        answers = {name: answer.choice for name, answer in removed.answers.items()}
        facts = facts_from_answers(answers, case.requested_facts)
        alt = _prediction_from_facts(case, facts, scope)
        if alt["decision"] != decision:
            flips.append({
                "id": rid,
                "fact": record.get("fact"),
                "excerpt": _excerpt(record),
                "decision_without_it": RECOMMENDATION.get(alt["decision"], alt["decision"]),
            })
    deciding = flips[0] if flips else {
        "id": "none",
        "fact": None,
        "excerpt": "",
        "decision_without_it": None,
    }
    criteria = []
    for name, state in (prediction.get("criteria") or {}).items():
        row_state = state.get("state") if isinstance(state, dict) else state
        criteria.append({"name": name, "state": row_state, "text": _criterion_text(case, name)})
    missing = []
    for name in prediction.get("missing_info") or []:
        missing.append({"name": name, "text": _criterion_text(case, name)})
    unmet = [row for row in criteria if row["state"] == "unmet"]
    audit = {
        "case_id": case.case_id,
        "split": case.split,
        "policy": _title(case),
        "payer": case.request.get("payer"),
        "service_date": case.request.get("service_date"),
        "n_records": case.n_records,
        "recommendation": RECOMMENDATION.get(decision, decision),
        "label": label,
        "colour": colour,
        "decision": decision,
        "action": prediction.get("action"),
        "scope": scope,
        "deciding_record": deciding,
        "other_flips": flips[1:],
        "missing": missing,
        "unmet": [{"name": row["name"], "text": row["text"]} for row in unmet],
        "criteria": criteria,
        "model": base.get("model") or cfg.drex_model,
        "live": True,
        "calls": calls,
        "cost_usd": round(cost, 6),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "elapsed_s": round(time.perf_counter() - started, 2),
        "_gold_decision": case.target.get("decision"),
    }
    # Keep the gold label off stdout. The cache is gitignored and only feeds `explain`.
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(case.case_id).write_text(json.dumps(audit, default=str))
    return _public_audit(audit)


def explain(case: PaCase) -> dict[str, Any]:
    path = _cache_path(case.case_id)
    if path.is_file():
        audit = json.loads(path.read_text())
        source = "cache"
    else:
        audit = audit_case(case)
        source = "live"
    rec = audit.get("recommendation")
    deciding = audit.get("deciding_record") or {}
    lines = [
        f"{audit['case_id']}: {rec} ({audit.get('decision')}).",
        f"Policy: {audit.get('policy')}.",
    ]
    if deciding.get("id") and deciding.get("id") != "none":
        lines.append(
            f"Deciding record {deciding['id']}"
            + (f" ({deciding.get('fact')})" if deciding.get("fact") else "")
            + (f": {deciding.get('excerpt')}" if deciding.get("excerpt") else ".")
        )
    else:
        lines.append("No single record flips the decision when removed.")
    if rec == "request_documents":
        lines.append("Missing documents:")
        for item in audit.get("missing") or []:
            lines.append(f"- {item['text']}")
    elif rec == "deny":
        lines.append("Unmet criteria:")
        for item in audit.get("unmet") or []:
            lines.append(f"- {item['text']}")
    elif rec == "approve":
        lines.append("Every listed criterion is documented as met, and the request is in scope.")
    elif rec == "not_applicable":
        lines.append(f"Out of scope ({audit.get('scope')}). Route it to the applicable policy.")
    return {
        "case_id": case.case_id,
        "source": source,
        "recommendation": rec,
        "brief": "\n".join(lines),
        "missing": audit.get("missing") or [],
        "deciding_record": deciding,
        "cost_usd": 0.0 if source == "cache" else audit.get("cost_usd"),
    }


def what_if(preset_id: str) -> dict[str, Any]:
    from billproof.command_center.store import get_store
    from billproof.command_center.whatif import preset_specs, run_preset

    spec = next((item for item in preset_specs() if item["id"] == preset_id), None)
    if spec is None:
        known = [item["id"] for item in preset_specs()]
        _emit({"error": f"unknown preset {preset_id}", "presets": known}, code=2)
    store = get_store()
    summary = run_preset(spec, store.policies, store.cards, example_limit=5)
    examples = []
    for flip in summary.get("example_flips") or []:
        examples.append({
            "case_id": flip.get("case_id"),
            "from_label": flip.get("from_label"),
            "to_label": flip.get("to_label"),
            "title": flip.get("title"),
        })
    return {
        "preset": summary.get("id"),
        "title": summary.get("title"),
        "hypothetical": True,
        "simulated": False,
        "partial": summary.get("partial"),
        "n_affected": summary.get("n_affected"),
        "n_decided": summary.get("n_decided"),
        "n_flips": summary.get("n_flips"),
        "flip_counts": summary.get("flip_counts"),
        "policy": summary.get("policy"),
        "examples": examples,
        "elapsed_ms": summary.get("elapsed_ms"),
        "note": summary.get("note"),
        "model_calls": 0,
    }


def main(argv: list[str]) -> None:
    if not argv or argv[0] in {"-h", "--help"}:
        _emit({
            "commands": ["list", "audit-case <id>", "explain <id>", "what-if <preset>", "presets"],
            "note": "audit-case calls live Drex. what-if replays cached Drex facts under a hypothetical rule.",
        })
    cmd = argv[0]
    if cmd == "list":
        rows = []
        for case in queue():
            first = next(iter(case.criterion_deps), "")
            rows.append({
                "case_id": case.case_id,
                "policy": _title(case),
                "payer": case.request.get("payer"),
                "service_date": case.request.get("service_date"),
                "n_records": case.n_records,
                "n_criteria": case.n_criteria,
                "clause": _criterion_text(case, first)[:180] if first else "",
            })
        _emit({"requests": rows, "note": "Audit each id. Recommendations come from audit-case, not from this list."})
    if cmd == "presets":
        from billproof.command_center.whatif import preset_specs
        _emit({"presets": [{"id": item["id"], "title": item["title"]} for item in preset_specs()]})
    if cmd == "audit-case":
        if len(argv) != 2:
            _emit({"error": "usage: audit-case <id>"}, code=2)
        _emit(audit_case(find_case(argv[1])))
    if cmd == "explain":
        if len(argv) != 2:
            _emit({"error": "usage: explain <id>"}, code=2)
        _emit(explain(find_case(argv[1])))
    if cmd == "what-if":
        if len(argv) != 2:
            _emit({"error": "usage: what-if <preset>"}, code=2)
        _emit(what_if(argv[1]))
    _emit({"error": f"unknown command {cmd}"}, code=2)


if __name__ == "__main__":
    main(sys.argv[1:])
