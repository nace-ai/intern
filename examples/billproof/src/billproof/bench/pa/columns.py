"""The four benchmark columns for the PA benchmark.

col1  gpt_structured : GPT gets the dataset's own messages, returns the full
                       structured answer (facts, criteria+evidence, decision...).
col2  drex           : Drex answers per-fact 3-way questions + payer/date nouls;
                       code computes criteria, missing_info, decision, action.
col3  drex_then_gpt  : Drex first; only answers with P(choice) < cutoff go to a
                       FRESH GPT call (no Drex draft shown); merged; code decides.
col4  gpt_checked    : col1's GPT answer checked claim-by-claim against Drex;
                       disagreements resolved by code when Drex is confident
                       (P >= cutoff), else the claim is flagged for a person and
                       GPT's claim kept. No extra GPT calls.

All columns share one case state (case.state()) — same information for everyone.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from billproof.adapters.frontier import (
    FrontierAdapter,
    _billed_cost,
    _clamp_conf,
    _extract_payload,
    _model_name,
    _usage,
)
from billproof.bench.pa.cases import PaCase
from billproof.bench.pa.logic import ACTION_FOR_DECISION, compute_scope, decide, decide_from_states
from billproof.bench.pa.questions import DATE_Q, PAYER_Q, build_questions
from billproof.bench.pa.score import INVALID, _norm_fact, facts_from_answers
from billproof.config import settings
from billproof.schema import Answer, DecisionResult, Question

PA_ANSWERS_SYSTEM = (
    "You review one synthetic prior-authorization packet against one verbatim criteria list. "
    "Answer each question from the supplied records only; never infer clinical facts. "
    'Return JSON {"answers": {"<name>": {"choice": "...", "confidence": 0.0}}}. '
    "confidence is the probability your choice is correct given the records."
)

_CRIT_STATE_TO_FACT = {"met": True, "unmet": False, "unknown": None}


# ---------------------------------------------------------------- col1: GPT structured


def gpt_answer_schema(case: PaCase) -> dict[str, Any]:
    fact_props = {f: {"type": ["boolean", "null"]} for f in case.requested_facts}
    crit_props = {
        c: {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "state": {"type": "string", "enum": ["met", "unmet", "unknown"]},
                "evidence": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["state", "evidence"],
        }
        for c in case.criterion_deps
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "policy_id": {"type": "string"},
            "scope": {"type": "string", "enum": ["applicable", "wrong_payer", "wrong_date"]},
            "branch": {"type": "string", "enum": ["preferred", "nonpreferred", "unspecified"]},
            "facts": {
                "type": "object",
                "additionalProperties": False,
                "properties": fact_props,
                "required": list(case.requested_facts),
            },
            "criteria": {
                "type": "object",
                "additionalProperties": False,
                "properties": crit_props,
                "required": list(case.criterion_deps),
            },
            "missing_info": {"type": "array", "items": {"type": "string"}},
            "decision": {
                "type": "string",
                "enum": ["criteria_met", "criteria_unmet", "incomplete", "not_applicable"],
            },
            "action": {
                "type": "string",
                "enum": [
                    "continue_full_review",
                    "review_unmet_criteria",
                    "request_evidence",
                    "route_to_applicable_policy",
                ],
            },
        },
        "required": ["policy_id", "scope", "branch", "facts", "criteria",
                     "missing_info", "decision", "action"],
    }


def _frontier() -> FrontierAdapter:
    return FrontierAdapter()


def gpt_structured(case: PaCase) -> dict[str, Any]:
    """Column 1: send the dataset's own messages to the frontier router."""
    cfg = settings()
    adapter = _frontier()
    messages = [
        {"role": "system", "content": case.system},
        {"role": "user", "content": case.raw_user},
    ]
    t0 = time.perf_counter()
    data, error = adapter._post(messages, "pa_structured_answer", gpt_answer_schema(case))
    latency_ms = (time.perf_counter() - t0) * 1000.0
    out: dict[str, Any] = {"latency_ms": latency_ms, "calls": 1}
    if error or data is None:
        out.update(error=error or "frontier request failed", prediction=None)
        return out
    reported = _model_name(data, cfg.frontier_model)
    input_tokens, output_tokens = _usage(data)
    out.update(model=reported, input_tokens=input_tokens, output_tokens=output_tokens,
               cost_usd=_billed_cost(input_tokens, output_tokens, reported),
               request_id=data.get("id"))
    try:
        payload = _extract_payload(data)
    except (ValueError, json.JSONDecodeError) as exc:
        out.update(error=f"unparseable model output: {exc}", prediction=None)
        return out
    out["prediction"] = payload
    return out


# ------------------------------------------------------- fact-question answering (GPT)


def _questions_schema(questions: list[Question]) -> dict[str, Any]:
    props = {
        q.name: {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "choice": {"type": "string", "enum": q.options()},
                "confidence": {"type": "number"},
            },
            "required": ["choice", "confidence"],
        }
        for q in questions
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answers": {"type": "object", "additionalProperties": False,
                        "properties": props, "required": [q.name for q in questions]},
        },
        "required": ["answers"],
    }


def gpt_questions(case: PaCase, questions: list[Question], *, state: dict | None = None) -> dict[str, Any]:
    """Fresh GPT call over a subset of typed questions (column-3 escalations)."""
    cfg = settings()
    adapter = _frontier()
    payload = {
        "case_id": case.case_id,
        "state": state if state is not None else case.state(),
        "questions": [
            {"name": q.name, "type": q.type, "instructions": q.instructions,
             **({"criteria": q.criteria} if q.criteria is not None else {})}
            for q in questions
        ],
    }
    messages = [
        {"role": "system", "content": PA_ANSWERS_SYSTEM},
        {"role": "user", "content": json.dumps(payload, default=str, ensure_ascii=False,
                                               separators=(",", ":"))},
    ]
    t0 = time.perf_counter()
    data, error = adapter._post(messages, "pa_fact_answers", _questions_schema(questions))
    latency_ms = (time.perf_counter() - t0) * 1000.0
    out: dict[str, Any] = {"latency_ms": latency_ms, "calls": 1}
    if error or data is None:
        out.update(error=error or "frontier request failed", answers={})
        return out
    reported = _model_name(data, cfg.frontier_model)
    input_tokens, output_tokens = _usage(data)
    out.update(model=reported, input_tokens=input_tokens, output_tokens=output_tokens,
               cost_usd=_billed_cost(input_tokens, output_tokens, reported),
               request_id=data.get("id"))
    try:
        payload = _extract_payload(data)
    except (ValueError, json.JSONDecodeError) as exc:
        out.update(error=f"unparseable model output: {exc}", answers={})
        return out
    answer_map = payload.get("answers") if isinstance(payload.get("answers"), dict) else payload
    answers: dict[str, dict[str, Any]] = {}
    for q in questions:
        item = answer_map.get(q.name) if isinstance(answer_map, dict) else None
        body = item if isinstance(item, dict) else {}
        choice = body.get("choice")
        answers[q.name] = {
            "choice": choice if isinstance(choice, str) and choice in q.options() else q.options()[0],
            "confidence": _clamp_conf(body.get("confidence")),
            "error": None if isinstance(choice, str) and choice in q.options() else "invalid choice",
        }
    out["answers"] = answers
    return out


# ---------------------------------------------------------------- col2/3/4: Drex-based


def _drex_decide(case: PaCase, questions: list[Question], *, model: str | None = None,
                 state: dict | None = None) -> DecisionResult:
    from billproof.adapters.drex import DrexAdapter

    cfg = settings()
    if cfg.use_real("drex"):
        adapter = DrexAdapter(cfg.nace_api_key, cfg.nace_base_url,
                              model or cfg.drex_model,
                              cfg.drex_price_per_mtok or 0.03)
    else:  # pragma: no cover - real key configured for the event
        from billproof.adapters.drex import SimulatedDrexAdapter
        adapter = SimulatedDrexAdapter(model or cfg.drex_model)
    return adapter.decide(state if state is not None else case.state(), questions,
                          case_id=case.case_id)


def _prediction_from_facts(case: PaCase, facts: dict[str, Any], scope: str) -> dict[str, Any]:
    derived = decide(facts, case.criterion_deps, scope)
    criteria = {name: {"state": s, "evidence": []} for name, s in derived["criteria"].items()}
    return {
        "policy_id": case.policy.get("id"),
        "scope": scope,
        "branch": case.policy.get("branch", "unspecified"),
        "facts": facts,
        "criteria": criteria,
        "missing_info": derived["missing_info"],
        "decision": derived["decision"],
        "action": derived["action"],
    }


def drex_column(case: PaCase, *, model: str | None = None,
                state: dict | None = None) -> dict[str, Any]:
    """Column 2 (and the Drex half of 3/4): typed questions -> code decision."""
    questions = build_questions(case)
    result = _drex_decide(case, questions, model=model, state=state)
    bad = [q.name for q in questions
           if q.name not in result.answers
           or result.answers[q.name].choice not in q.options()]
    answers_raw = {q.name: result.answers[q.name].choice for q in questions
                   if q.name in result.answers}
    facts = facts_from_answers(answers_raw, case.requested_facts)
    scope = _scope_from_qanswers(result.answers)
    out: dict[str, Any] = {
        "latency_ms": result.latency_ms, "calls": result.calls,
        "model": result.model, "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens, "cost_usd": result.cost_usd,
        "error": result.error or (f"invalid/missing answers: {bad}" if bad else None),
        "qa": {
            q.name: {
                "choice": result.answers[q.name].choice,
                "confidence": result.answers[q.name].confidence,
                "probabilities": result.answers[q.name].probabilities,
            }
            for q in questions
            if q.name in result.answers
        },
    }
    out["prediction"] = _prediction_from_facts(case, facts, scope)
    out["facts"] = facts
    return out


def _scope_from_qanswers(answers: dict[str, Answer]) -> str:
    payer = answers.get(PAYER_Q)
    date = answers.get(DATE_Q)
    if payer is not None and payer.choice == "no":
        return "wrong_payer"
    if date is not None and date.choice == "no":
        return "wrong_date"
    return "applicable"


def drex_then_gpt_column(case: PaCase, drex_out: dict[str, Any], *, cutoff: float) -> dict[str, Any]:
    """Column 3: reuse col2's Drex answers; escalate sub-cutoff questions to a fresh GPT call."""
    questions = build_questions(case)
    low = [q for q in questions
           if float((drex_out["qa"].get(q.name) or {}).get("confidence") or 0.0) < cutoff]
    merged_facts = dict(drex_out["facts"])
    scope = _scope_from_qanswers_names(drex_out["qa"])
    out: dict[str, Any] = {
        "latency_ms": drex_out["latency_ms"], "calls": drex_out["calls"],
        "cost_usd": drex_out["cost_usd"], "input_tokens": drex_out["input_tokens"],
        "output_tokens": drex_out["output_tokens"],
        "escalated": [q.name for q in low],
        "qa": dict(drex_out["qa"]),
    }
    if low:
        gpt = gpt_questions(case, low)
        out["latency_ms"] += gpt["latency_ms"]
        out["calls"] += gpt.get("calls", 1)
        out["cost_usd"] += gpt.get("cost_usd", 0.0)
        out["input_tokens"] = (out["input_tokens"] or 0) + gpt.get("input_tokens", 0)
        out["output_tokens"] = (out["output_tokens"] or 0) + gpt.get("output_tokens", 0)
        if gpt.get("error"):
            out["error"] = gpt["error"]
        for q in low:
            ga = (gpt.get("answers") or {}).get(q.name)
            if ga is None or ga.get("error"):
                continue
            out["qa"][q.name] = {"choice": ga["choice"], "confidence": ga["confidence"],
                                 "probabilities": None, "via": "gpt"}
        answers_raw = {q.name: out["qa"][q.name]["choice"] for q in questions}
        merged_facts = facts_from_answers(answers_raw, case.requested_facts)
        scope = _scope_from_qanswers_names({k: v for k, v in out["qa"].items()})
    out["facts"] = merged_facts
    out["prediction"] = _prediction_from_facts(case, merged_facts, scope)
    return out


def _scope_from_qanswers_names(qa: dict[str, dict]) -> str:
    payer = (qa.get(PAYER_Q) or {}).get("choice")
    date = (qa.get(DATE_Q) or {}).get("choice")
    if payer == "no":
        return "wrong_payer"
    if date == "no":
        return "wrong_date"
    return "applicable"


def gpt_checked_column(case: PaCase, gpt_out: dict[str, Any],
                       drex_out: dict[str, Any], *, cutoff: float) -> dict[str, Any]:
    """Column 4: check GPT's structured answer claim-by-claim against Drex.

    Resolution rule (deterministic): on a fact disagreement where Drex's
    P(choice) >= cutoff, Drex's value wins; otherwise the claim is flagged for a
    person and GPT's value kept. Decision/action/criteria are then recomputed by
    code from resolved facts — a disagreeing GPT decision can never stand on its
    own.
    """
    pred = gpt_out.get("prediction")
    out: dict[str, Any] = {
        "latency_ms": (gpt_out.get("latency_ms") or 0) + drex_out["latency_ms"],
        "calls": (gpt_out.get("calls") or 1) + drex_out["calls"],
        "cost_usd": (gpt_out.get("cost_usd") or 0) + drex_out["cost_usd"],
        "input_tokens": (gpt_out.get("input_tokens") or 0) + (drex_out["input_tokens"] or 0),
        "output_tokens": (gpt_out.get("output_tokens") or 0) + (drex_out["output_tokens"] or 0),
        "flagged": [],
        "overrides": [],
    }
    if pred is None:
        out["error"] = gpt_out.get("error") or "no gpt prediction"
        out["prediction"] = None
        return out
    gpt_facts = {f: _norm_fact(v) for f, v in (pred.get("facts") or {}).items()}
    resolved: dict[str, Any] = {}
    for fact in case.requested_facts:
        g = gpt_facts.get(fact)
        dq = drex_out["qa"].get(f"fact::{fact}") or {}
        d = _norm_fact(dq.get("choice"))
        if d is INVALID:
            d = None  # invalid/missing Drex answer abstains — never overrides
        conf = float(dq.get("confidence") or 0.0)
        if d == g:
            resolved[fact] = g
        elif conf >= cutoff:
            resolved[fact] = d
            out["overrides"].append({"fact": fact, "gpt": g, "drex": d, "conf": conf})
        else:
            resolved[fact] = g
            out["flagged"].append({"fact": fact, "gpt": g, "drex": d, "conf": conf})
    # scope: GPT's scope checked against Drex's payer/date answers.
    # The override must be licensed by the questions the two scopes actually
    # disagree on — not by the max confidence of either scope question.
    gpt_scope = pred.get("scope")
    drex_scope = _scope_from_qanswers_names(drex_out["qa"])
    scope = gpt_scope
    if drex_scope != gpt_scope:
        implied = {"wrong_payer": {PAYER_Q: "no"}, "wrong_date": {DATE_Q: "no"},
                   "applicable": {PAYER_Q: "yes", DATE_Q: "yes"}}.get(gpt_scope) or {}
        disagree_confs = [
            float((drex_out["qa"].get(q) or {}).get("confidence") or 0.0)
            for q, want in implied.items()
            if (drex_out["qa"].get(q) or {}).get("choice") != want
        ]
        if disagree_confs and min(disagree_confs) >= cutoff:
            scope = drex_scope
            out["overrides"].append({"fact": "scope", "gpt": gpt_scope, "drex": drex_scope})
        else:
            out["flagged"].append({"fact": "scope", "gpt": gpt_scope, "drex": drex_scope})
    derived = decide(resolved, case.criterion_deps, scope)
    criteria = {}
    for name, s in derived["criteria"].items():
        gpt_item = (pred.get("criteria") or {}).get(name) or {}
        criteria[name] = {"state": s, "evidence": gpt_item.get("evidence") or []}
    out["prediction"] = {
        "policy_id": case.policy.get("id") or pred.get("policy_id"),
        "scope": scope,
        "branch": pred.get("branch", case.policy.get("branch", "unspecified")),
        "facts": resolved,
        "criteria": criteria,
        "missing_info": derived["missing_info"],
        "decision": derived["decision"],
        "action": derived["action"],
    }
    out["facts"] = resolved
    return out
