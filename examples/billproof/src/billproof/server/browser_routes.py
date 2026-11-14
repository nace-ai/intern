"""Browser-run routes (T3).

GET  /api/browser/{case_id}        recorded run: answer, verified quotes, steps, cost (replay, no network)
POST /api/browser/{case_id}/run    run the case's task live (~30 s, <= $0.10 cap); not recorded

Case ids: SYN-D08 or its showcase id SC-05.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from billproof.browser import CASE_TASKS, TASKS, run_case
from billproof.server.search_routes import _source_id

router = APIRouter()


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


@router.get("/api/browser/tasks")
async def api_browser_tasks():
    return {"tasks": {k: {f: v[f] for f in ("kind", "title", "stand_in", "start_url", "allowed_origins")}
                      for k, v in TASKS.items()}, "cases": CASE_TASKS}


@router.get("/api/browser/{case_id}")
async def api_browser_recorded(case_id: str):
    cid = _source_id(case_id)
    if cid is None:
        return _err(404, "unknown case")
    rec = run_case(cid)
    if rec.get("status") == "not_recorded":
        return _err(404, "no recorded browser run for this case")
    return rec


@router.post("/api/browser/{case_id}/run")
async def api_browser_live(case_id: str):
    cid = _source_id(case_id)
    if cid is None or cid not in CASE_TASKS:
        return _err(404, "no browser task for this case")
    try:
        return await asyncio.to_thread(run_case, cid, True)
    except Exception as exc:
        return _err(502, f"{type(exc).__name__}: {exc}")
