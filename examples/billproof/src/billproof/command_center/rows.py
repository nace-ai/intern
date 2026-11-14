"""Decision labels, simulated OR→AND, and queue-row builders.

The combining rules are ``logic.decide``. This module only
reshapes dependencies and formats rows.
"""

from __future__ import annotations

import hashlib
from typing import Any

from billproof.bench.pa.cases import PaCase
from billproof.bench.pa.logic import decide
from billproof.bench.pa.questions import scope_from_answers
from billproof.bench.pa.score import facts_from_answers, score_prediction

# Dataset decision -> (card label, colour). not_applicable shares amber:
# the plan's three colours are approve / deny / missing-or-person.
LABELS: dict[str, tuple[str, str]] = {
    "criteria_met": ("approve", "green"),
    "criteria_unmet": ("deny", "red"),
    "incomplete": ("incomplete", "amber"),
    "not_applicable": ("not applicable", "amber"),
}


def colour_of(decision: str | None) -> tuple[str, str]:
    return LABELS.get(decision or "", ("incomplete", "amber"))


def seeded_sample_ids(case_ids: list[str], n: int, seed: int) -> list[str]:
    """Fixed hash sample. Does not look at the gold decision."""
    ranked = sorted(
        case_ids,
        key=lambda i: hashlib.md5(f"{seed}:{i}".encode()).hexdigest(),
    )
    return ranked[:n]


def clause_map(case: PaCase) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in case.policy.get("criteria_list") or []:
        fact = item.get("fact")
        if fact:
            out[str(fact)] = (item.get("text") or str(fact)).strip()
    return out


def criterion_text(deps: list[str], clauses: dict[str, str]) -> str:
    parts = [clauses.get(f, f) for f in deps]
    if len(parts) <= 1:
        return (parts[0] if parts else "")[:500]
    return " OR ".join(parts)[:500]


def and_deps(criterion_deps: dict[str, list[str]]) -> dict[str, list[str]]:
    """Simulated tightening: one OR criterion over alternatives becomes one
    required criterion per fact. ``logic.decide`` ignores the connective
    string and reads this dependency map, so the string alone cannot flip
    a case."""
    facts: list[str] = []
    for deps in criterion_deps.values():
        for fact in deps:
            if fact not in facts:
                facts.append(fact)
    return {f"required::{fact}": [fact] for fact in facts}


def derive(case: PaCase, choices: dict[str, str], *,
           scope: str | None = None,
           deps: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """choices: original question name -> choice string."""
    facts = facts_from_answers(choices, case.requested_facts)
    used_scope = scope if scope is not None else scope_from_answers(choices)
    used_deps = deps if deps is not None else case.criterion_deps
    derived = decide(facts, used_deps, used_scope)
    return {"facts": facts, "scope": used_scope, "deps": used_deps, **derived}


def prediction(case: PaCase, derived: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": case.policy.get("id"),
        "scope": derived["scope"],
        "branch": case.policy.get("branch", "unspecified"),
        "facts": derived["facts"],
        "criteria": {name: {"state": state, "evidence": []}
                     for name, state in derived["criteria"].items()},
        "missing_info": derived["missing_info"],
        "decision": derived["decision"],
        "action": derived["action"],
    }


def qa_from_choices(choices: dict[str, str], confidence: dict[str, float]) -> dict[str, dict]:
    return {
        name: {"choice": choice, "confidence": confidence.get(name)}
        for name, choice in choices.items()
    }


def criteria_rows(case: PaCase, derived: dict[str, Any],
                  confidence: dict[str, float]) -> list[dict[str, Any]]:
    clauses = clause_map(case)
    rows = []
    for name, deps in derived["deps"].items():
        confs = [confidence[f"fact::{fact}"] for fact in deps
                 if f"fact::{fact}" in confidence]
        text = criterion_text(deps, clauses)
        # Simulated per-fact criteria are not in the published list; use the clause.
        if name.startswith("required::") and name[len("required::"):] in clauses:
            text = clauses[name[len("required::"):]][:500]
        rows.append({
            "name": name,
            "text": text,
            "state": derived["criteria"].get(name),
            "confidence": min(confs) if confs else None,
            "facts": list(deps),
        })
    return rows


def provenance_bits(case: PaCase) -> dict[str, Any]:
    prov = case.provenance or {}
    return {
        "publisher": case.publisher,
        "family": case.list_id.split(":", 1)[0],
        "list_id": case.list_id,
        "document_title": prov.get("document_title"),
        "source_url": prov.get("url"),
        "source": prov.get("source"),
        "state": prov.get("state"),
        "vintage": prov.get("vintage"),
        "policy_id": case.policy.get("id"),
        "connective": case.connective,
    }


def queue_row(case: PaCase, derived: dict[str, Any], *,
              choices: dict[str, str], confidence: dict[str, float],
              lane: str, model: str, simulated: bool,
              latency_ms: float, batch_latency_ms: float, batch_id: str,
              batch_questions: int, cost_usd: float, batch_cost_usd: float,
              input_tokens: int, output_tokens: int, calls: int,
              error: str | None = None, projected: bool = False) -> dict[str, Any]:
    label, colour = colour_of(derived["decision"])
    pred = prediction(case, derived)
    score = score_prediction(case, pred, check_evidence=False)
    return {
        "case_id": case.case_id,
        "parent_id": case.parent_id,
        "split": case.split,
        "scenario": case.scenario,
        "lane": lane,
        "model": model,
        "simulated": simulated,
        "projected": projected,
        "decision": derived["decision"],
        "label": label,
        "colour": colour,
        "action": derived["action"],
        "scope": derived["scope"],
        "missing_info": derived["missing_info"],
        "criteria": criteria_rows(case, derived, confidence),
        "clauses": clause_map(case),
        "qa": qa_from_choices(choices, confidence),
        "facts": derived["facts"],
        "correct": bool(score["decision"]),
        "criteria_all_correct": bool(score["criteria_all"]),
        "facts_all_correct": bool(score["facts_all"]),
        "gold_decision": case.target.get("decision"),
        "gold_label": colour_of(case.target.get("decision"))[0],
        "latency_ms": round(latency_ms, 3),
        "batch_latency_ms": round(batch_latency_ms, 3),
        "batch_id": batch_id,
        "batch_questions": batch_questions,
        "cost_usd": cost_usd,
        "batch_cost_usd": batch_cost_usd,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "calls": calls,
        "error": error,
        **provenance_bits(case),
    }


def single_document_fixes(facts: dict[str, Any], deps: dict[str, list[str]],
                          scope: str) -> list[str]:
    """Facts which, set to true one at a time, flip the code decision to
    criteria_met. Counterfactual on recorded facts, not a new model call.
    Wrong-scope cases are not document fixes."""
    if scope != "applicable":
        return []
    base = decide(facts, deps, scope)["decision"]
    if base == "criteria_met":
        return []
    fixes: list[str] = []
    for fact, value in facts.items():
        if value is True:
            continue
        trial = dict(facts)
        trial[fact] = True
        if decide(trial, deps, scope)["decision"] == "criteria_met":
            fixes.append(fact)
    return fixes


def gold_deciding(case: PaCase) -> dict[str, Any]:
    """Same rule as scripts/pa_decide.py. Agreement is only meaningful when
    construction.ids names the designed record for a fact."""
    target = case.target
    ids = case.construction.get("ids") or {}
    cons_facts = case.construction.get("facts") or {}
    decision = target.get("decision")
    if decision == "not_applicable":
        return {"kind": "scope", "facts": [], "records": [], "none_ok": True}
    if decision == "incomplete":
        gold_facts = case.gold_facts()
        facts = sorted(f for f, v in gold_facts.items() if v is None)
        return {"kind": "absent", "facts": facts, "records": [], "none_ok": True}
    if decision == "criteria_met":
        facts_s = {f for f, v in cons_facts.items() if v is True}
    elif decision == "criteria_unmet":
        facts_s = {f for f, v in cons_facts.items() if v is False}
    else:
        return {"kind": "unknown", "facts": [], "records": [], "none_ok": True}
    records = sorted({ids[f] for f in facts_s if f in ids})
    if decision == "criteria_unmet" and len(facts_s) > 1:
        return {"kind": "joint_unmet", "facts": sorted(facts_s),
                "records": records, "none_ok": True}
    return {"kind": decision, "facts": sorted(facts_s), "records": records,
            "none_ok": False}


def agrees(predicted: str, case: PaCase) -> tuple[bool, dict[str, Any]]:
    gold = gold_deciding(case)
    ids_rev = {v: k for k, v in (case.construction.get("ids") or {}).items()}
    pred_fact = None if predicted == "none" else ids_rev.get(predicted)
    if predicted == "none":
        ok = bool(gold["none_ok"])
    else:
        ok = predicted in gold["records"] or (pred_fact is not None and pred_fact in gold["facts"])
    return ok, {**gold, "predicted_fact": pred_fact}


def flip_name(old_label: str, new_label: str) -> str | None:
    if old_label == new_label:
        return None
    return f"{old_label.replace(' ', '_')}_to_{new_label.replace(' ', '_')}"


def blocking_clauses(case: PaCase, facts: dict[str, Any],
                     new_derived: dict[str, Any]) -> list[dict[str, Any]]:
    """Clauses that are not satisfied, which the simulated AND rule now requires."""
    clauses = clause_map(case)
    out = []
    for name, deps in new_derived["deps"].items():
        state = new_derived["criteria"].get(name)
        if state == "met":
            continue
        fact = deps[0] if deps else name
        out.append({
            "criterion": name,
            "fact": fact,
            "text": clauses.get(fact, fact)[:500],
            "fact_value": facts.get(fact),
            "new_state": state,
        })
    return out


def needs_accuracy_split(flags: list[bool], n_items: int, *, floor: float = 0.95) -> bool:
    """Re-ask halves when too many fact-level answers miss gold.

    A 500-question pack on this dataset kept the decision but missed facts
    (about 61% of cases fully right). Packs of ~200 questions stayed near
    98%. One-question groups cannot split.
    """
    if n_items <= 1 or len(flags) < 2:
        return False
    return (sum(1 for f in flags if f) / len(flags)) < floor


def invalid_gpt_answers(questions, answers: dict | None) -> list[str]:
    """Names whose GPT choice is missing, outside the options, or flagged.

    The frontier helper stores option 0 and sets ``error`` when the model
    omits the enum. That option is ``documented_satisfied`` or ``yes``, so
    it must not be stored as a measured success.
    """
    bad: list[str] = []
    payload = answers or {}
    for question in questions:
        item = payload.get(question.name)
        choice = item.get("choice") if isinstance(item, dict) else None
        flagged = isinstance(item, dict) and bool(item.get("error"))
        allowed = set(question.options())
        if flagged or not isinstance(choice, str) or choice not in allowed:
            bad.append(question.name)
    return bad


def probe_failure(removals: list[dict], n_expected: int) -> str | None:
    """Why a what-decides row must not be stored as a success.

    One failed record probe is enough. Gold often expects ``none`` on
    not-applicable, incomplete, and joint-unmet cases, so a total probe
    failure would otherwise count as agreement.
    """
    if n_expected <= 0:
        return "no records to ablate"
    n_ok = sum(1 for row in removals if not row.get("error"))
    n_err = sum(1 for row in removals if row.get("error"))
    if n_err or n_ok != n_expected:
        return f"ablation incomplete {n_ok}/{n_expected}"
    return None
