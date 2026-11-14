"""Ask your documents: one question over a case workspace, structured answer + checked quotes.

Answers are cached in `recorded/ask_<case_id>.json`, keyed by (question, mode, schema),
so the stage replays them with no network. `live=True` asks Drex Search on a miss.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

from billproof.search import corpus
from billproof.search.chain import RECORDED_DIR, SEARCH_USD_PER_MTOK, workspace_name

DEFAULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "description": "one short sentence"},
        "value": {"type": ["string", "number", "boolean", "null"], "description": "the bare value, if one"},
    },
    "required": ["answer"],
}

# Demo questions with known answers (from the synthetic documents), used for metrics.
DEMO_QUESTIONS: dict[str, list[dict]] = {
    "SYN-D08": [
        {"id": "appeal_days", "question": "How many days does the member have to file an internal appeal?",
         "schema": {"type": "object", "properties": {"days": {"type": "integer"}}, "required": ["days"]},
         "expect": {"days": 180}},
        {"id": "er_prior_auth", "question": "Is prior authorization required for a CT done during an emergency department visit?",
         "schema": {"type": "object", "properties": {"required": {"type": "boolean"}}, "required": ["required"]},
         "expect": {"required": False}},
        {"id": "final_dx_denial", "question": "Can the plan deny an emergency claim only because the final diagnosis was not an emergency?",
         "schema": {"type": "object", "properties": {"allowed": {"type": "boolean"}}, "required": ["allowed"]},
         "expect": {"allowed": False}},
        {"id": "vitals", "question": "What were the patient's temperature (Celsius) and white blood cell count in the ED?",
         "schema": {"type": "object", "properties": {"temperature_c": {"type": "number"}, "wbc": {"type": "number"}},
                    "required": ["temperature_c", "wbc"]},
         "expect": {"temperature_c": 38.6, "wbc": 15.2}},
        {"id": "final_dx", "question": "What was the final diagnosis after the CT?",
         "schema": {"type": "object", "properties": {"diagnosis": {"type": "string"}}, "required": ["diagnosis"]},
         "expect": {"diagnosis": "mesenteric adenitis"}},
        {"id": "visit_owed", "question": "How much does the patient owe for the emergency department visit according to the EOB?",
         "schema": {"type": "object", "properties": {"amount_usd": {"type": "number"}}, "required": ["amount_usd"]},
         "expect": {"amount_usd": 13.89}},
        {"id": "guideline_study", "question": "Which imaging study does the published imaging guideline list for lower quadrant or generalized abdominal pain?",
         "schema": {"type": "object", "properties": {"study": {"type": "string"}}, "required": ["study"]},
         "expect": {"study": "CT abdomen and/or pelvis"}},
    ],
}


def _key(question: str, mode: str, schema: dict) -> str:
    raw = json.dumps([question.strip().lower(), mode, schema], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_path(case_id: str):
    return RECORDED_DIR / f"ask_{case_id}.json"


def _load(case_id: str) -> dict:
    p = _cache_path(case_id)
    return json.loads(p.read_text()) if p.exists() else {}


def demo_question(case_id: str, qid: str) -> dict | None:
    return next((q for q in DEMO_QUESTIONS.get(case_id, []) if q["id"] == qid), None)


def grade(expect: dict, got: dict | None) -> bool:
    if not isinstance(got, dict):
        return False
    for k, v in expect.items():
        g = got.get(k)
        if isinstance(v, bool) or v is None:
            if g is not v:
                return False
        elif isinstance(v, (int, float)):
            if not isinstance(g, (int, float)) or abs(g - v) > 0.01:
                return False
        elif v.lower() not in str(g or "").lower():
            return False
    return True


def ask(case_id: str, question: str, *, mode: str = "fact", schema: dict | None = None,
        live: bool = False) -> dict:
    """{question, structured_answer, answer, confidence, evidences[{document, page, quote, verbatim}], ...}.

    Replays a recorded answer when there is one. Otherwise asks live only if `live`.
    """
    schema = schema or DEFAULT_SCHEMA
    key = _key(question, mode, schema)
    cache = _load(case_id)
    if key in cache:
        return {**cache[key], "replayed": True}
    if not live:
        return {"question": question, "status": "not_recorded", "replayed": False,
                "detail": "no recorded answer; ask with live=true"}
    from billproof.search.client import WorkspaceClient, answer_of

    c = WorkspaceClient()
    ws = c.workspace(workspace_name(case_id))
    t0 = time.monotonic()
    job = c.search(ws, question, mode=mode, path_prefix=f"{case_id}/", output_schema=schema, top_k=4)
    secs = round(time.monotonic() - t0, 1)
    a = answer_of(job)
    out = (job.get("result") or {}).get("output") or {}
    usage = (out.get("answer") or {}).get("usage") if isinstance(out.get("answer"), dict) else out.get("usage")
    cost = (round(usage.get("input_tokens", 0) * SEARCH_USD_PER_MTOK[0] / 1e6
                  + usage.get("output_tokens", 0) * SEARCH_USD_PER_MTOK[1] / 1e6, 5) if usage else None)
    evid = []
    for e in a.get("evidences") or []:
        loc = corpus.locate_quote(case_id, e.get("source_path") or "", e.get("quote") or "")
        kind = corpus.kind_of(e.get("source_path") or "")
        evid.append({"document": e.get("source_path"), "doc_label": corpus.LABELS.get(kind, kind),
                     "page": e.get("page"), "quote": e.get("quote"), "verbatim": loc["verbatim"]})
    rec = {
        "question": question, "mode": mode, "route": a.get("route"), "status": job.get("status"),
        "structured_answer": a.get("structured_answer"), "answer": a.get("answer"),
        "confidence": a.get("confidence"), "evidences": evid, "seconds": secs, "cost_usd": cost,
        "job_id": job.get("job_id"), "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    cache[key] = rec
    RECORDED_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(case_id).write_text(json.dumps(cache, indent=1))
    return {**rec, "replayed": False}
