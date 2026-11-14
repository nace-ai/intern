"""PA full-chain demo routes . Replay-first: everything serves from
billproof/data/pa_demo/<slug>/ written by scripts/pa_demo_record.py.

  GET /api/pa/cases                          -> [{slug,title,decision,...}]
  GET /api/pa/{case}                         -> evidence.json
  GET /api/pa/{case}/page/{doc}/{n}.png      -> page image (doc=policy|packet)
  GET /api/pa/{case}/check.png               -> static box-alignment check
  POST /api/pa/{case}/live                   -> optional live Drex re-decision

Server must start with data missing: empty lists / 404 JSON, never crashes.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from billproof.pa_demo.showcase import PA_DEMO_DIR, SHOWCASE, load_row

router = APIRouter(prefix="/api/pa", tags=["pa-demo"])


def _evidence_path(slug: str) -> Path:
    return PA_DEMO_DIR / slug / "evidence.json"


def _load(slug: str) -> dict:
    p = _evidence_path(slug)
    if not p.is_file():
        raise HTTPException(404, f"no recorded evidence for {slug!r} — run pa_demo_record.py")
    return json.loads(p.read_text())


@router.get("/cases")
def list_cases() -> JSONResponse:
    cases = []
    for slug, spec in SHOWCASE.items():
        p = _evidence_path(slug)
        entry = {"case": slug, "title": spec["title"], "drug": spec["drug"],
                 "beat": spec["beat"], "recorded": p.is_file()}
        if p.is_file():
            try:
                ev = json.loads(p.read_text())
                entry.update(decision=ev.get("decision"), gold_decision=ev.get("gold_decision"),
                             correct=ev.get("correct"), payer=ev.get("payer"),
                             total_cost_usd=ev.get("total_cost_usd"))
            except (json.JSONDecodeError, OSError):
                pass
        cases.append(entry)
    return JSONResponse({"cases": cases})


@router.get("/{slug}")
def case_detail(slug: str) -> JSONResponse:
    slug = slug.upper()
    if slug not in SHOWCASE:
        raise HTTPException(404, f"unknown case {slug!r}")
    return JSONResponse(_load(slug))


@router.get("/{slug}/page/{doc}/{n}.png")
def page_image(slug: str, doc: str, n: int) -> FileResponse:
    slug = slug.upper()
    if doc not in ("policy", "packet") or n < 1 or n > 99:
        raise HTTPException(404, "bad page request")
    p = PA_DEMO_DIR / slug / "pages" / f"{doc}_p{n}.png"
    if not p.is_file():
        raise HTTPException(404, f"page image {doc}_p{n}.png not recorded")
    return FileResponse(p, media_type="image/png")


@router.get("/{slug}/check.png")
def check_image(slug: str) -> FileResponse:
    p = PA_DEMO_DIR / slug.upper() / "check.png"
    if not p.is_file():
        raise HTTPException(404, "check.png not recorded")
    return FileResponse(p, media_type="image/png")


@router.post("/{slug}/live")
def live_decision(slug: str) -> JSONResponse:
    """Optional live mode: re-run Drex per-fact questions + code decision."""
    slug = slug.upper()
    if slug not in SHOWCASE:
        raise HTTPException(404, f"unknown case {slug!r}")
    try:
        from billproof.bench.pa import columns as pc
        case = load_row(SHOWCASE[slug]["row_id"])
        out = pc.drex_column(case)
        pred = out.get("prediction") or {}
        return JSONResponse({"case": slug, "live": True, "model": out.get("model"),
                             "decision": pred.get("decision"), "scope": pred.get("scope"),
                             "criteria": {k: v.get("state") for k, v in
                                          (pred.get("criteria") or {}).items()},
                             "cost_usd": out.get("cost_usd"), "error": out.get("error")})
    except Exception as exc:
        return JSONResponse({"case": slug, "live": True, "error": str(exc)}, status_code=502)
