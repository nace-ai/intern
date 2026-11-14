"""Recorded Nace browser run for the prior-auth demo.

GET /api/pa-browser  -> data/pa_demo/browser_run.json, plus per-case coverage.

Missing file -> 404 {"error": ...}. The module does not read the file at import.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from billproof.pa_demo.showcase import PA_DEMO_DIR, SHOWCASE

router = APIRouter()

# Filenames the recorded run actually found, and the answer field that holds each URL.
_RUN_DOCS = {
    "PACriteria.pdf": "pacriteria_url",
    "PAD_Criteria.pdf": "pad_url",
}


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def _read_json(path) -> Any:
    return json.loads(path.read_text())


def _cases(answer: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for slug, spec in SHOWCASE.items():
        source_pdf = spec.get("source_pdf")
        field = _RUN_DOCS.get(source_pdf)
        doc_url = (answer.get(field) or None) if field else None
        criteria_pages = None
        prep_path = PA_DEMO_DIR / slug / "prepare.json"
        if prep_path.is_file():
            try:
                criteria_pages = _read_json(prep_path).get("criteria_pages")
            except (OSError, json.JSONDecodeError, AttributeError):
                criteria_pages = None
        policy_source_url = None
        ev_path = PA_DEMO_DIR / slug / "evidence.json"
        if ev_path.is_file():
            try:
                prov = _read_json(ev_path).get("provenance") or {}
                policy_source_url = prov.get("policy_source_url")
            except (OSError, json.JSONDecodeError, AttributeError):
                policy_source_url = None
        out[slug] = {
            "source_pdf": source_pdf,
            "criteria_pages": criteria_pages,
            "covered_by_run": source_pdf in _RUN_DOCS,
            "doc_url": doc_url,
            "policy_source_url": policy_source_url,
        }
    return out


@router.get("/api/pa-browser")
def pa_browser() -> JSONResponse:
    path = PA_DEMO_DIR / "browser_run.json"
    if not path.is_file():
        return _err(404, "recorded PA browser run not found")
    try:
        data = _read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return _err(500, f"recorded PA browser run unreadable: {exc}")
    if not isinstance(data, dict):
        return _err(500, "recorded PA browser run is not a JSON object")
    answer = data.get("answer") or {}
    if not isinstance(answer, dict):
        answer = {}
    data["cases"] = _cases(answer)
    return JSONResponse(data)
