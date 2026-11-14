"""NDI → Drex Search (+Browser) → Drex → letter, for one case.

1. Search (agent mode, `output_schema`) reads the case workspace and returns plan
   clauses and record facts that support the appeal, each with page and quote.
2. Code checks every quote is verbatim in the cited PDF (`corpus.locate_quote`).
3. Verified quotes become Drex state. Drex answers typed questions twice, without and
   with the quotes, so the stage shows what the documents changed. Drex also picks
   which clause the letter cites.
4. `letters.assemble_letter(..., support=...)` cites that clause and page.
The whole result is recorded under `recorded/<case_id>.json` for stage replay.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from billproof.schema import Case, Question
from billproof.search import corpus
from billproof.search.client import WorkspaceClient, answer_of

RECORDED_DIR = Path(__file__).resolve().parent / "recorded"
SEARCH_USD_PER_MTOK = (0.10, 0.50)  # input, output; docs/drex/reference/pricing#drex-search

SUPPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "supports_coverage": {"type": "boolean",
                              "description": "Does the plan's own language support covering the denied service?"},
        "clauses": {"type": "array", "items": {"type": "object", "properties": {
            "document": {"type": "string"}, "section": {"type": "string"}, "page": {"type": "integer"},
            "quote": {"type": "string", "description": "verbatim sentence from the document"},
            "why_it_helps": {"type": "string"}}, "required": ["document", "page", "quote", "why_it_helps"]}},
        "record_facts": {"type": "array", "items": {"type": "object", "properties": {
            "document": {"type": "string"}, "page": {"type": "integer"}, "quote": {"type": "string"}},
            "required": ["page", "quote"]}},
        "appeal_deadline_days": {"type": ["integer", "null"]},
    },
    "required": ["supports_coverage", "clauses", "record_facts"],
}


def workspace_name(case_id: str) -> str:
    return f"billproof-{case_id}"


def load_case(case_id: str) -> Case:
    from billproof.config import DATA_DIR

    for d in (DATA_DIR / "synthetic" / "cases", DATA_DIR / "showcase" / "cases"):
        p = d / f"{case_id}.json"
        if p.exists():
            return Case.model_validate_json(p.read_text())
    raise FileNotFoundError(case_id)


def denied_lines(case: Case) -> list[dict]:
    bill = {ln.line_no: ln for ln in case.bill.lines}
    out = []
    for ln in (case.eob.lines if case.eob else []):
        if ln.carc and ln.allowed_cents == 0 and ln.line_no in bill:
            b = bill[ln.line_no]
            out.append({"line_no": ln.line_no, "code": b.code, "label": b.label, "dos": b.dos,
                        "charge_cents": b.charge_cents, "carc": list(ln.carc), "remark": ln.remark})
    return out


def support_query(case: Case) -> str:
    lines = denied_lines(case)
    d = case.denial
    what = "; ".join(f"line {x['line_no']}, {x['label']} ({x['code']}) on {x['dos']}" for x in lines) or "the denied service"
    why = f"{d.carc} {d.category.replace('_', ' ').lower()}" if d else "the stated reason"
    return (f"The plan denied {what} ({why}). Find the plan language that supports covering it on appeal "
            "(emergency services, medical necessity, prior authorization), quoting each clause verbatim with its "
            "section and page, and the facts in the medical record that support the service.")


def ensure_workspace(case_id: str, client: WorkspaceClient | None = None) -> dict:
    """Create the case workspace, upload missing files, ingest. Returns timings and outcomes."""
    c = client or WorkspaceClient()
    t0 = time.monotonic()
    ws = c.workspace(workspace_name(case_id))
    have = {f.get("path") for f in c.files(ws)}
    uploaded = []
    for doc in corpus.case_docs(case_id):
        if doc["path"] not in have:
            c.upload(ws, doc["path"], Path(doc["local"]).read_bytes())
            uploaded.append(doc["path"])
    job = c.ingest(ws, f"{case_id}/")
    res = job.get("result") or {}
    return {"workspace_id": ws, "workspace_name": workspace_name(case_id), "uploaded": uploaded,
            "files_ingested": res.get("files_ingested"), "files_skipped": res.get("files_skipped"),
            "ingest_credits": job.get("credits"), "seconds": round(time.monotonic() - t0, 1)}


def _search_cost(job: dict) -> float | None:
    out = ((job.get("result") or {}).get("output") or {})
    usage = (out.get("answer") or {}).get("usage") if isinstance(out.get("answer"), dict) else out.get("usage")
    if not usage:
        return None
    return round(usage.get("input_tokens", 0) * SEARCH_USD_PER_MTOK[0] / 1e6
                 + usage.get("output_tokens", 0) * SEARCH_USD_PER_MTOK[1] / 1e6, 5)


def _verified(case_id: str, items: list[dict], default_doc: str) -> list[dict]:
    out = []
    for i, it in enumerate(items or []):
        doc = it.get("document") or default_doc
        if "/" not in doc:
            doc = f"{case_id}/{doc}"
        loc = corpus.locate_quote(case_id, doc, it.get("quote", ""))
        kind = corpus.kind_of(doc)
        out.append({**it, "id": f"q{i + 1}", "document": doc, "kind": kind,
                    "doc_label": corpus.LABELS.get(kind, kind), "verbatim": loc["verbatim"],
                    "page_checked": loc["page"], "page_ok": loc["page"] == it.get("page")})
    return out


def run_search(case: Case, client: WorkspaceClient | None = None) -> dict:
    c = client or WorkspaceClient()
    ws = c.workspace(workspace_name(case.case_id))
    query = support_query(case)
    t0 = time.monotonic()
    job = c.search(ws, query, mode="agent", path_prefix=f"{case.case_id}/", output_schema=SUPPORT_SCHEMA)
    secs = round(time.monotonic() - t0, 1)
    ans = answer_of(job)
    sa = ans.get("structured_answer") or {}
    return {
        "query": query, "mode": "agent", "job_id": job.get("job_id"), "status": job.get("status"),
        "seconds": secs, "cost_usd": _search_cost(job), "confidence": ans.get("confidence"),
        "supports_coverage": sa.get("supports_coverage"), "appeal_deadline_days": sa.get("appeal_deadline_days"),
        "clauses": _verified(case.case_id, sa.get("clauses"), f"{case.case_id}_coverage.pdf"),
        "record_facts": _verified(case.case_id, sa.get("record_facts"), f"{case.case_id}_ed_record.pdf"),
        "evidences": [{k: e.get(k) for k in ("source_path", "page", "quote")} for e in ans.get("evidences") or []],
    }


# ------------------------------------------------------------------ Drex


def drex_state(case: Case, search: dict | None, policy: dict | None = None) -> dict:
    """What Drex sees. Without `search` it gets only the claim and the denial notice."""
    d = case.denial
    state: dict[str, Any] = {
        "denied_lines": denied_lines(case),
        "denial_notice": {"carc": d.carc, "category": d.category, "reason_text": d.reason_text,
                          "plan_section_cited": d.plan_section} if d else None,
        "service_setting": case.service_setting,
    }
    if search:
        state["plan_quotes"] = [{"id": q["id"], "document": q["doc_label"], "section": q.get("section"),
                                 "page": q["page"], "quote": q["quote"]} for q in search["clauses"] if q["verbatim"]]
        state["record_quotes"] = [{"document": q["doc_label"], "page": q["page"], "quote": q["quote"]}
                                  for q in search["record_facts"] if q["verbatim"]]
    if policy and policy.get("quote"):
        state["published_guideline"] = {k: policy.get(k) for k in ("title", "section", "url", "quote", "study")}
    return state


def drex_questions(search: dict | None, policy: dict | None = None) -> list[Question]:
    qs = [
        Question(name="plan_supports_coverage", type="noul", family="ask_docs",
                 instructions="Based only on the plan language quoted in plan_quotes, does the plan's own wording "
                              "support covering the denied service? If no plan language is quoted, answer no."),
        Question(name="emergency_standard_met", type="noul", family="ask_docs",
                 instructions="Do the record_quotes show symptoms that meet the plan's quoted definition of an "
                              "emergency medical condition? If either is missing, answer no."),
        Question(name="denial_conflicts_with_plan", type="noul", family="ask_docs",
                 instructions="Does the quoted plan language rule out the reason the denial gives (for example a "
                              "denial based on the final diagnosis or on missing prior authorization)? "
                              "If no plan language is quoted, answer no."),
    ]
    if policy:
        qs.append(Question(
            name="guideline_supports_service", type="noul", family="ask_docs",
            instructions="Does the published_guideline (fetched from a public page) list the denied service as "
                         "medically necessary for the symptoms in record_quotes? If either is missing, answer no."))
    clauses = [q for q in (search or {}).get("clauses", []) if q["verbatim"]]
    if clauses:
        qs.append(Question(
            name="cite_clause", type="choice", family="letter_cite",
            instructions="Which quoted plan clause should the appeal letter cite as its main ground?",
            criteria={q["id"]: f"{q.get('section') or q['doc_label']} (page {q['page']}): {q['quote'][:160]}"
                      for q in clauses}))
    return qs


def _answers_json(result) -> dict:
    return {n: {"choice": a.choice, "confidence": round(a.confidence, 4),
                "probabilities": {k: round(v, 4) for k, v in (a.probabilities or {}).items()}}
            for n, a in result.answers.items()}


def run_drex(case: Case, search: dict, policy: dict | None = None) -> dict:
    from billproof.adapters.drex import get_drex

    drex = get_drex()
    out = {"model": getattr(drex, "model", None), "simulated": bool(getattr(drex, "simulated", True))}
    for tag, srch in (("without_documents", None), ("with_documents", search)):
        qs = drex_questions(srch, policy)
        r = drex.decide(drex_state(case, srch, policy if srch else None), qs, case_id=f"{case.case_id}:{tag}")
        out[tag] = {"answers": _answers_json(r), "latency_ms": round(r.latency_ms, 1),
                    "input_tokens": r.input_tokens, "cost_usd": round(r.cost_usd, 6)}
    return out


# ------------------------------------------------------------------ letter


def letter_support(search: dict, drex: dict, policy: dict | None = None, threshold: float = 0.6) -> list[dict]:
    """The clause Drex picked (plus any record facts) as letter support, if Drex says the plan supports coverage."""
    with_docs = drex["with_documents"]["answers"]
    sup = with_docs.get("plan_supports_coverage", {})
    if sup.get("choice") != "yes" or sup.get("confidence", 0) < threshold:
        return []
    pick = with_docs.get("cite_clause", {})
    by_id = {q["id"]: q for q in search["clauses"] if q["verbatim"]}
    clause = by_id.get(pick.get("choice")) or next(iter(by_id.values()), None)
    if clause is None:
        return []
    facts = [f for f in search["record_facts"] if f["verbatim"]][:3]
    return [{"doc_label": clause["doc_label"], "section": clause.get("section") or "", "page": clause["page"],
             "quote": clause["quote"], "facts": [{"doc_label": f["doc_label"], "page": f["page"], "quote": f["quote"]}
                                                for f in facts],
             "policy": policy if with_docs.get("guideline_supports_service", {}).get("choice") == "yes" else None,
             "picked_by": "cite_clause", "probability": pick.get("confidence")}]


def build_letter(case: Case, support: list[dict], showcase_id: str | None = None) -> dict | None:
    """Re-assemble the showcase letter with the cited clause. Uses the recorded verdict and decide answers."""
    from billproof.config import DATA_DIR
    from billproof.letters.assembler import assemble_letter
    from billproof.schema import Answer, Verdict

    sid = showcase_id or _showcase_id(case.case_id)
    p = DATA_DIR / "showcase" / "replay" / f"{sid}.json"
    if not sid or not p.exists():
        return None
    events = {e["stage"]: e.get("data") or {} for e in json.loads(p.read_text())["events"] if e.get("status") == "ok"}
    try:
        verdict = Verdict.model_validate(events["verdict"]["verdict"])
    except Exception:  # e.g. owed amount not verified (null) in the showcase replay
        return None
    answers = {n: Answer(name=n, **{k: v for k, v in a.items() if k in ("choice", "confidence", "probabilities")})
               for n, a in (events.get("decide", {}).get("answers") or {}).items()}
    letter = assemble_letter(case, verdict, answers, support=support)
    return letter.model_dump()


def _showcase_id(case_id: str) -> str | None:
    try:
        from billproof.showcase import showcase_entries

        return next((e["case_id"] for e in showcase_entries() if e.get("source_case_id") == case_id), None)
    except Exception:
        return None


# ------------------------------------------------------------------ record / replay


def run_chain(case_id: str, *, policy: dict | None = None, record: bool = True) -> dict:
    case = load_case(case_id)
    t0 = time.monotonic()
    client = WorkspaceClient()
    if policy:
        corpus.write_policy_pdf(case_id, policy)
    ws = ensure_workspace(case_id, client)
    search = run_search(case, client)
    drex = run_drex(case, search, policy)
    support = letter_support(search, drex, policy)
    letter = build_letter(case, support)
    chain = {
        "case_id": case_id, "showcase_id": _showcase_id(case_id),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provenance": "recorded live Nace calls (Drex Search + Drex); replayed without network",
        "chain": ["ndi", "search", "browser" if policy else None, "drex", "letter"],
        "workspace": ws, "search": search, "policy": policy, "drex": drex,
        "letter_support": support, "letter": letter,
        "seconds_total": round(time.monotonic() - t0, 1),
    }
    chain["chain"] = [s for s in chain["chain"] if s]
    if record:
        RECORDED_DIR.mkdir(parents=True, exist_ok=True)
        (RECORDED_DIR / f"{case_id}.json").write_text(json.dumps(chain, indent=1, default=str))
    return chain


def recorded_chain(case_id: str) -> dict | None:
    p = RECORDED_DIR / f"{case_id}.json"
    return json.loads(p.read_text()) if p.exists() else None
