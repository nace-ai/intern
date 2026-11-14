"""Nace Browser runs for BillProof: read a public page, return a structured, checked answer.

The picked use: fetch a published clinical imaging guideline for the denied service and
quote the criterion that supports it (appeal ground). Every `*_quote` field is checked
by code against the page text we fetch ourselves. Runs are recorded under
`recorded/<case_id>.json` and replayed with no network; `live=True` runs again.
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from billproof.config import settings

RECORDED_DIR = Path(__file__).resolve().parent / "recorded"
MAX_COST_USD = 0.10
_FINAL = ("succeeded", "partial", "failed", "cancelled", "timed_out")

_CARELON = "https://guidelines.carelonmedicalbenefitsmanagement.com"
TASKS: dict[str, dict[str, Any]] = {
    "policy_ct_abdomen": {
        "kind": "clinical_policy",
        "title": "Published imaging criteria for the denied CT",
        "stand_in": "Public Carelon guideline; the case payer is fictional, so this is a real stand-in.",
        "task": (
            "You are on a public Carelon clinical guideline for imaging of the abdomen and pelvis. Stay on this "
            "website. Do not log in, do not open other websites, and do not download files. Find the section headed "
            "\"Abdominal and/or pelvic pain, undifferentiated\". Copy, word for word, the sentence or bullet that names "
            "CT abdomen and/or pelvis for lower quadrant (right or left) or generalized abdominal pain, as "
            "criterion_quote. Also copy, word for word, the adult bullet about acute abdominal pain with clinical "
            "findings of a surgical abdomen as indication_quote. Record the policy title, the Doc ID as policy_number, "
            "the effective date printed on the page, the section heading, and the final page URL. Do not invent text "
            "that is not visible on the page."),
        "start_url": f"{_CARELON}/imaging-of-the-abdomen-and-pelvis-2025-03-23/",
        "allowed_origins": [_CARELON, "https://guidelines.aimspecialtyhealth.com"],
        "answer_schema": {"type": "object", "properties": {
            "policy_title": {"type": "string"}, "policy_number": {"type": "string"}, "page_url": {"type": "string"},
            "effective_date": {"type": "string"}, "criterion_quote": {"type": "string"},
            "indication_quote": {"type": "string"}, "section_or_heading": {"type": "string"}},
            "required": ["policy_title", "policy_number", "page_url", "effective_date", "criterion_quote",
                         "section_or_heading", "indication_quote"]},
        "max_cost_usd": 0.06, "max_seconds": 240,
    },
}
CASE_TASKS = {"SYN-D08": "policy_ct_abdomen"}


class BrowserUnavailable(RuntimeError):
    pass


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def page_text(url: str) -> str:
    raw = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 BillProof-verify"}, timeout=30,
                    follow_redirects=True).text
    raw = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", raw)
    return _norm(html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def verify(answer: dict | None, url: str | None) -> dict:
    """Check each `*_quote` field is word for word on the page; keep ~300 chars of context."""
    quotes = {k: v for k, v in (answer or {}).items() if k.endswith("_quote") and isinstance(v, str) and v}
    if not quotes or not url:
        return {"checked": False, "quotes": []}
    try:
        text = page_text(url)
    except httpx.HTTPError as exc:
        return {"checked": False, "error": str(exc), "quotes": []}
    out = []
    for field, q in quotes.items():
        i = text.find(_norm(q))
        out.append({"field": field, "quote": q, "verbatim": i >= 0,
                    "context": text[max(0, i - 200): i + len(q) + 200] if i >= 0 else None})
    return {"checked": True, "url": url, "fetched_at": _now(), "quotes": out}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_and_wait(spec: dict, *, client: httpx.Client | None = None) -> tuple[dict, list, float]:
    s = settings()
    if not s.nace_api_key:
        raise BrowserUnavailable("no NACE_API_KEY")
    h = {"Authorization": f"Bearer {s.nace_api_key}"}
    body = {k: spec[k] for k in ("task", "start_url", "allowed_origins", "answer_schema") if spec.get(k)}
    body |= {"limits": {"max_cost_usd": min(float(spec.get("max_cost_usd", 0.05)), MAX_COST_USD),
                        "max_seconds": int(spec.get("max_seconds", 240))},
             "risky_actions": "deny", "artifacts": {"screenshots": True}}
    c = client or httpx.Client(timeout=60)
    t0 = time.monotonic()
    r = c.post(f"{s.nace_base_url}/v1/browser/runs", headers=h, json=body)
    if r.status_code >= 300:
        raise BrowserUnavailable(f"start -> {r.status_code} {r.text[:300]}")
    run = r.json()
    while run.get("status") not in _FINAL and time.monotonic() - t0 < body["limits"]["max_seconds"] + 120:
        time.sleep(3)
        run = c.get(f"{s.nace_base_url}/v1/browser/runs/{run['id']}", headers=h).json()
    secs = round(time.monotonic() - t0, 1)
    a = c.get(f"{s.nace_base_url}/v1/browser/runs/{run['id']}/artifacts", headers=h)
    arts = (a.json().get("items") or []) if a.status_code == 200 else []
    return run, arts, secs


def run_task(task_id: str, case_id: str, *, record: bool = True) -> dict:
    spec = TASKS[task_id]
    run, arts, secs = start_and_wait(spec)
    final_url = run.get("final_url") or spec["start_url"]
    rec = {
        "case_id": case_id, "task_id": task_id, "kind": spec["kind"], "title": spec["title"],
        "stand_in": spec.get("stand_in"), "run_id": run.get("id"), "status": run.get("status"),
        "outcome": (run.get("outcome") or {}).get("kind"), "reason": (run.get("outcome") or {}).get("reason"),
        "seconds": secs, "cost_usd": (run.get("usage") or {}).get("cost_usd"), "usage": run.get("usage"),
        "start_url": spec["start_url"], "final_url": final_url, "allowed_origins": spec["allowed_origins"],
        "task": spec["task"], "answer": run.get("answer"), "steps": run.get("steps") or [],
        "evidence": (run.get("outcome") or {}).get("evidence") or [],
        "screenshots": [{"kind": x.get("kind"), "step": x.get("step")} for x in arts],
        "verification": verify(run.get("answer"), final_url),
        "recorded_at": _now(),
        "provenance": "recorded live Nace browser run; replayed without network",
    }
    if record:
        RECORDED_DIR.mkdir(parents=True, exist_ok=True)
        (RECORDED_DIR / f"{case_id}.json").write_text(json.dumps(rec, indent=1))
    return rec


def recorded_run(case_id: str) -> dict | None:
    p = RECORDED_DIR / f"{case_id}.json"
    return json.loads(p.read_text()) if p.exists() else None


def policy_for_chain(rec: dict | None) -> dict | None:
    """The browser answer as Drex state / letter support, only if its quote checked out."""
    if not rec or rec.get("status") != "succeeded":
        return None
    ok = {q["field"]: q for q in (rec.get("verification") or {}).get("quotes", []) if q["verbatim"]}
    a = rec.get("answer") or {}
    quote_field = next((f for f in ("indication_quote", "criterion_quote") if f in ok), None)
    if quote_field is None:
        return None
    study = a.get("criterion_quote") if "criterion_quote" in ok and quote_field != "criterion_quote" else None
    return {"title": a.get("policy_title"), "policy_number": a.get("policy_number"),
            "effective_date": a.get("effective_date"), "section": a.get("section_or_heading"),
            "url": rec.get("final_url"), "quote": a[quote_field], "study": study,
            "contexts": [q["context"] for q in ok.values()],
            "retrieved_at": rec.get("recorded_at"), "run_id": rec.get("run_id"), "stand_in": rec.get("stand_in")}
