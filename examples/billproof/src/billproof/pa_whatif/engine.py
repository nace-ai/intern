"""What-if engine: re-decide a recorded PA showcase case after a reviewer edits it.

Inputs are the recorded run only — Drex's per-fact answers and confidences from
evidence.json — plus the reviewer's edits. The decision comes from the same
deterministic code the benchmark scores (bench.pa.logic.decide/compute_scope);
the letter from the same clause library (pa_demo.letters.build_letter). No model
calls here; `ask_drex_live` is the one opt-in live call.

Edits (all optional):
  records    {record_id: "met" | "not_met" | "missing" | "removed"}
  connective "AND" | "OR"           (rule edit on the policy page)
  off        [fact, ...]            (criteria switched off on the policy page)
  payer      "flip" | "request"     (flip the request payer: policy payer <-> "Other payer")

A record's state sets the fact(s) it was linked to by Drex Search:
met -> true, not_met -> false, missing/removed -> null (not documented).
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from typing import Any

from billproof.bench.pa.logic import compute_scope, decide
from billproof.pa_demo.letters import build_letter
from billproof.pa_demo.showcase import PA_DEMO_DIR, SHOWCASE, load_row

RECORD_STATES = ("met", "not_met", "missing", "removed")
_FACT_OF_STATE = {"met": True, "not_met": False, "missing": None, "removed": None}
_STATE_OF_FACT = {True: "met", False: "not_met", None: "missing"}
_UI_STATE = {True: "met", False: "unmet", None: "missing"}
OR_NAME = "any_listed_alternative"
OTHER_PAYER = "Other payer"


@lru_cache(maxsize=8)
def _load(slug: str) -> tuple[Any, dict, dict]:
    if slug not in SHOWCASE:
        raise KeyError(slug)
    ev = json.loads((PA_DEMO_DIR / slug / "evidence.json").read_text())
    return load_row(SHOWCASE[slug]["row_id"]), SHOWCASE[slug], ev


def _recorded_facts(ev: dict) -> dict[str, Any]:
    return dict(ev.get("facts") or {})


def _links(ev: dict) -> dict[str, list[str]]:
    """record id -> facts it documents (Drex Search link)."""
    out: dict[str, list[str]] = {}
    for c in ev.get("criteria") or []:
        rid = (c.get("link") or {}).get("record")
        if rid:
            out.setdefault(rid, []).append(c["criterion"])
    return out


def _deps(case, facts_order: list[str], connective: str, off: set[str]) -> dict[str, list[str]]:
    on = [f for f in facts_order if f not in off]
    if connective == case.connective:
        deps = {}
        for name, fs in case.criterion_deps.items():
            keep = [f for f in fs if f not in off]
            if keep:
                deps[name] = keep
        return deps
    if connective == "OR":
        return {OR_NAME: on}
    return {f: [f] for f in on}


def _record_states(ev: dict, facts: dict[str, Any], edits: dict[str, str]) -> dict[str, str]:
    links = _links(ev)
    out = {}
    for r in ev.get("records") or []:
        if r.get("kind") != "record":
            continue
        rid = r["record"]
        fs = links.get(rid) or []
        base = _STATE_OF_FACT.get(facts.get(fs[0])) if fs else "missing"
        out[rid] = edits.get(rid) or base
    return out


def _apply(ev: dict, record_states: dict[str, str]) -> dict[str, Any]:
    facts = _recorded_facts(ev)
    for rid, fs in _links(ev).items():
        st = record_states.get(rid)
        if st in _FACT_OF_STATE:
            for f in fs:
                facts[f] = _FACT_OF_STATE[st]
    return facts


def _run(case, ev, facts, deps, request) -> dict[str, Any]:
    return decide(facts, deps, compute_scope(case.policy, request))


def _tags(ev: dict) -> dict[str, str]:
    return {c["criterion"]: f"C{i + 1}" for i, c in enumerate(ev.get("criteria") or [])}


def _why(dec: dict, facts: dict, deps: dict, connective: str, tags: dict, request: dict, policy: dict) -> str:
    on = [f for fs in deps.values() for f in fs]
    by = lambda v: [tags.get(f, f) for f in on if facts.get(f) is v]  # noqa: E731
    join = lambda xs: ", ".join(xs[:-1]) + (" and " if len(xs) > 1 else "") + xs[-1] if xs else ""  # noqa: E731
    d = dec["decision"]
    if d == "not_applicable":
        if dec["scope"] == "wrong_payer":
            return (f"The request names payer “{request.get('payer')}”, but this policy is "
                    f"{policy.get('payer')}'s: the policy does not apply, so its criteria are not checked.")
        return "The service date is outside the policy's window: the policy does not apply."
    if d == "criteria_unmet":
        if connective == "OR":
            return "Every listed alternative is documented as not met, so none can satisfy the rule (OR)."
        return f"{join(by(False))} documented as not met, and every criterion is required (AND)."
    if d == "incomplete":
        if connective == "OR":
            return f"No alternative is documented as met yet; {join(by(None))} not documented: ask for records."
        return f"{join(by(None))} not documented and nothing is documented as not met: ask for the missing record."
    if connective == "OR":
        return f"{join(by(True))} documented as met; one alternative is enough (OR)."
    return "Every required criterion is documented as met (AND)."


def _changes(ev: dict, edits: dict, case, tags: dict) -> list[str]:
    out = []
    names = {"met": "met", "not_met": "not met", "missing": "missing", "removed": "removed"}
    links = _links(ev)
    for rid, st in (edits.get("records") or {}).items():
        fs = links.get(rid) or []
        out.append(f"{rid} ({', '.join(tags.get(f, f) for f in fs) or 'no criterion'}) → {names.get(st, st)}")
    if edits.get("connective") and edits["connective"] != case.connective:
        out.append(f"rule {case.connective} → {edits['connective']}")
    for f in edits.get("off") or []:
        out.append(f"{tags.get(f, f)} switched off")
    if edits.get("payer") == "flip":
        out.append("request payer → " + (OTHER_PAYER if case.request.get("payer") == case.policy.get("payer")
                                         else str(case.policy.get("payer"))))
    return out


def _clean(slug: str, edits: dict) -> tuple[dict, list[str]]:
    case, _, ev = _load(slug)
    rec_ids = {r["record"] for r in ev.get("records") or [] if r.get("kind") == "record"}
    facts = list(_recorded_facts(ev))
    recs = {k: v for k, v in (edits.get("records") or {}).items() if k in rec_ids and v in RECORD_STATES}
    conn = edits.get("connective") if edits.get("connective") in ("AND", "OR") else case.connective
    off = [f for f in (edits.get("off") or []) if f in facts]
    if len(off) >= len(facts):  # at least one criterion stays on
        off = off[: len(facts) - 1]
    payer = "flip" if edits.get("payer") == "flip" else "request"
    return {"records": recs, "connective": conn, "off": off, "payer": payer}, facts


def whatif(slug: str, edits: dict | None = None) -> dict[str, Any]:
    case, spec, ev = _load(slug)
    edits, facts_order = _clean(slug, edits or {})
    tags = _tags(ev)
    recorded = _recorded_facts(ev)
    rstates = _record_states(ev, recorded, edits["records"])
    facts = _apply(ev, rstates)
    off = set(edits["off"])
    deps = _deps(case, facts_order, edits["connective"], off)
    request = dict(case.request)
    if edits["payer"] == "flip":
        request["payer"] = (OTHER_PAYER if case.request.get("payer") == case.policy.get("payer")
                            else case.policy.get("payer"))
    dec = _run(case, ev, facts, deps, request)

    # baseline = recorded answers, no edits: must equal the recorded decision
    base = decide(recorded, case.criterion_deps, compute_scope(case.policy, case.request))

    # deciding record(s): drop each present record in turn, re-run the same code
    deciding, probes = [], []
    for rid, st in rstates.items():
        if st == "removed":
            continue
        dropped = dict(rstates, **{rid: "removed"})
        alt = _run(case, ev, _apply(ev, dropped), deps, request)
        changed = alt["decision"] != dec["decision"]
        probes.append({"dropped": rid, "decision": alt["decision"], "changed": changed})
        if changed:
            deciding.append(rid)

    # Drex's recorded ablation re-asked the model with one record dropped: when the
    # only edit is that removal, show whether code-on-recorded-answers agrees.
    drex_check = None
    only = edits["records"]
    if (len(only) == 1 and next(iter(only.values())) == "removed" and edits["connective"] == case.connective
            and not off and edits["payer"] == "request"):
        rid = next(iter(only))
        rec = next((a for a in (ev.get("ablation") or []) if a.get("dropped") == rid), None)
        if rec:
            drex_check = {"record": rid, "drex_decision": rec.get("decision"),
                          "agrees": rec.get("decision") == dec["decision"]}

    qa = (ev.get("drex") or {}).get("qa") or {}
    edited_facts = {f for rid, st in edits["records"].items() for f in _links(ev).get(rid, [])}
    crit_rows, criteria = [], []
    for c in ev.get("criteria") or []:
        f = c["criterion"]
        a = qa.get(f"fact::{f}") or {}
        rid = (c.get("link") or {}).get("record")
        state = "off" if f in off else _UI_STATE[facts.get(f)]
        criteria.append({
            "criterion": f, "tag": tags[f], "state": state, "on": f not in off,
            "record": rid, "record_state": rstates.get(rid) if rid else None,
            "source": "edit" if f in edited_facts else "drex",
            "drex_choice": a.get("choice"), "drex_confidence": a.get("confidence"),
            "changed": facts.get(f) != recorded.get(f) or f in off,
        })
        if f in off:
            continue
        row = copy.deepcopy(c)
        row["state"] = "unmet" if facts.get(f) is False else "met" if facts.get(f) else "missing"
        if f in edited_facts and row.get("link"):
            row["link"]["quote"] = f"[what-if: {rid} marked {rstates.get(rid, '').replace('_', ' ')} by the reviewer; no recorded wording]"
        crit_rows.append(row)

    # letters read better per fact when the rule is one OR group
    letter_dec = dec
    if OR_NAME in dec["criteria"] or (edits["connective"] == "OR" and len(dec["criteria"]) == 1):
        on = [f for fs in deps.values() for f in fs]
        st = {f: ("met" if facts.get(f) is True else "unmet" if facts.get(f) is False else "unknown") for f in on}
        letter_dec = dict(dec, criteria=st, missing_info=[f for f, s in st.items() if s == "unknown"])
    letter = build_letter(case, spec, letter_dec, crit_rows)

    return {
        "case": slug,
        "edits": edits,
        "changes": _changes(ev, edits, case, tags),
        "decision": dec["decision"], "action": dec["action"], "scope": dec["scope"],
        "connective": edits["connective"], "recorded_connective": case.connective,
        "group_states": dec["criteria"], "missing_info": dec["missing_info"],
        "why": _why(dec, facts, deps, edits["connective"], tags, request, case.policy),
        "criteria": criteria,
        "records": [{"record": rid, "state": st, "facts": _links(ev).get(rid, []),
                     "deciding": rid in deciding} for rid, st in rstates.items()],
        "deciding": deciding, "probes": probes, "drex_check": drex_check,
        "request_payer": request.get("payer"), "policy_payer": case.policy.get("payer"),
        "baseline_decision": base["decision"], "recorded_decision": ev.get("decision"),
        "letter": letter,
        "method": "Drex's recorded per-criterion answers + reviewer edits → bench.pa.logic.decide (same code as the benchmark); no model call",
    }


def ask_drex_live(slug: str, criterion: str, text: str) -> dict[str, Any]:
    """One live Drex question: does `text` (the edited record) document `criterion`?"""
    from billproof.adapters.drex import DrexAdapter
    from billproof.bench.pa.questions import build_questions
    from billproof.config import settings

    case, _, ev = _load(slug)
    if criterion not in _recorded_facts(ev):
        raise KeyError(criterion)
    text = (text or "").strip()[:2000]
    if not text:
        raise ValueError("empty record text")
    cfg = settings()
    if not cfg.use_real("drex"):
        return {"live": False, "error": "Drex key not configured on this server"}
    rid = next((c["link"]["record"] for c in ev.get("criteria") or []
                if c["criterion"] == criterion and c.get("link")), None)
    state = case.state()
    records = [dict(r) for r in state["records"]]
    hit = next((r for r in records if r.get("id") == rid), None)
    if hit is not None:
        hit["text"] = text
    else:
        records.append({"id": rid or "R_new", "text": text})
    state["records"] = records
    q = [x for x in build_questions(case, include_scope=False) if x.name == f"fact::{criterion}"]
    model = (ev.get("drex") or {}).get("model") or "drex-max-v1.1"
    adapter = DrexAdapter(cfg.nace_api_key, cfg.nace_base_url, model, cfg.drex_price_per_mtok or 0.03)
    res = adapter.decide(state, q, case_id=f"{case.case_id}#whatif")
    a = res.answers.get(q[0].name)
    if a is None:
        return {"live": True, "error": res.error or "no answer", "model": res.model}
    return {"live": True, "model": res.model, "record": rid, "criterion": criterion,
            "choice": a.choice, "confidence": a.confidence, "probabilities": a.probabilities,
            "record_state": {"documented_satisfied": "met", "documented_not_satisfied": "not_met",
                             "not_documented": "missing"}.get(a.choice),
            "latency_ms": round(res.latency_ms, 1), "cost_usd": res.cost_usd, "error": res.error}
