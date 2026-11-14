"""PA letters: approved / request for missing documents / appeal / routing.

Clause bodies are PA-specific (this module owns them); slot filling reuses
letters.assembler._fill and the shared `closing` clause — generated_words
stays 0: every fact comes from the case, every quote is document text.
"""

from __future__ import annotations

from typing import Any

from billproof.bench.pa.cases import PaCase
from billproof.letters.assembler import _fill, load_clauses

LETTER_TYPES = ("approval", "records_request", "appeal", "routing")

_PA_CLAUSES: dict[str, dict[str, str]] = {
    "approval": {
        "title": "Prior authorization approved",
        "body": ("The request for {drug} under {payer} policy {policy_id} meets the published "
                 "criteria. Each required clause is documented in the enclosed packet: {met_lines} "
                 "Authorization may proceed to the next review step.{deadline? The review window on file ends {deadline}.}"),
    },
    "records_request": {
        "title": "Request for missing documentation",
        "body": ("The request for {drug} under {payer} policy {policy_id} cannot be completed: "
                 "the criteria list is not fully documented. Documented so far: {met_lines} "
                 "Still needed: {missing_lines} Please send records that address each missing "
                 "criterion so the review can continue."),
    },
    "appeal": {
        "title": "Appeal of an unmet-criteria review",
        "body": ("Please reconsider the review of {drug} under {payer} policy {policy_id}. "
                 "The published criterion reads: \"{criterion_quote}\" ({doc_label}, page {page}). "
                 "The enclosed packet, record {record_id} page {record_page}, states: "
                 "\"{record_quote}\" We ask for a written decision addressing whether the "
                 "documented record satisfies the criterion."),
    },
    "routing": {
        "title": "Route to the applicable policy",
        "body": ("The request for {drug} names payer \"{request_payer}\" with service date "
                 "{service_date}. {payer} policy {policy_id} applies only to {payer} and to "
                 "service dates {window}. This review is not applicable to that request and is "
                 "returned for routing to the applicable policy."),
    },
}

_TYPE_FOR_DECISION = {
    "criteria_met": "approval",
    "incomplete": "records_request",
    "criteria_unmet": "appeal",
    "not_applicable": "routing",
}


def _crit_text(case: PaCase, name: str) -> str:
    for item in case.policy.get("criteria_list") or []:
        if item.get("fact") == name:
            return item.get("text") or name
    return name


def build_letter(case: PaCase, spec: dict[str, Any], decision: dict[str, Any],
                 criteria: list[dict[str, Any]]) -> dict[str, Any]:
    """decision: output of bench.pa.logic.decide(); criteria: evidence rows with links."""
    policy, request = case.policy, case.request
    ltype = _TYPE_FOR_DECISION[decision["decision"]]
    states = decision["criteria"]
    met = [f"{name}: \"{_crit_text(case, name)}\"" for name, s in states.items() if s == "met"]
    missing = [f"{name}: \"{_crit_text(case, name)}\"" for name in decision["missing_info"]]
    window = policy.get("exercise_applicability") or {}
    base = {
        "drug": spec.get("drug") or "the requested service",
        "payer": str(policy.get("payer") or ""),
        "policy_id": str(policy.get("id") or ""),
        "request_payer": str(request.get("payer") or ""),
        "service_date": str(request.get("service_date") or ""),
        "window": f"{window.get('from')} through {window.get('through')}",
        "met_lines": "; ".join(met) if met else "none yet",
        "missing_lines": "; ".join(missing) if missing else "none",
        "deadline": str(window.get("through") or ""),
    }
    if ltype == "appeal":
        link = next((c for c in criteria if c.get("state") == "unmet" and c.get("link")),
                    next((c for c in criteria if c.get("link")), {}))
        crit = link or {}
        base.update({
            "criterion_quote": crit.get("text") or "",
            "doc_label": "published criteria",
            "page": str(crit.get("page") or ""),
            "record_id": str(((crit.get("link") or {}).get("record") or "")),
            "record_page": str((crit.get("link") or {}).get("record_page") or ""),
            "record_quote": (crit.get("link") or {}).get("quote") or "",
        })
    body = _fill(_PA_CLAUSES[ltype]["body"], base)
    clauses = [{"clause_id": ltype, "title": _PA_CLAUSES[ltype]["title"],
                "text": body, "picked_by": "decision"}]
    clauses.append({"clause_id": "closing", "title": load_clauses()["closing"].title,
                    "text": _fill(load_clauses()["closing"].body, base), "picked_by": "decision"})
    return {"type": ltype, "recipient": "insurer" if ltype != "records_request" else "requesting_provider",
            "subject": f"{_PA_CLAUSES[ltype]['title']} - {base['drug']}",
            "clauses": clauses, "generated_words": 0,
            "decision": decision["decision"], "action": decision["action"]}
