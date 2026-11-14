"""Audit desk routes (W5b) — `GET /api/audit` and `GET /api/audit/queue`.

app.py includes this router inside a try/except ImportError, so the server still
starts if audit.py's siblings are mid-build. All computation is delegated to
billproof.audit.run_batch and runs in a thread (it does blocking file/model work).
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _err(exc: Exception) -> JSONResponse:
    from billproof.audit import AuditError

    if isinstance(exc, AuditError):
        return JSONResponse(status_code=404, content={"error": str(exc)})
    if isinstance(exc, ValueError):
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if isinstance(exc, ModuleNotFoundError):
        return JSONResponse(status_code=503, content={"error": str(exc), "missing": exc.name})
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})


@router.get("")
async def audit_portfolio(
    split: Literal["dev", "test", "demo"] = "test",
    lane: Literal["frontier", "drex", "hybrid"] = "hybrid",
    source: Literal["replay", "sim"] = "replay",
    run_id: str | None = None,
    minutes_per_claim: float = Query(default=12.0, ge=0, le=240),
):
    """The whole portfolio: billed, recoverable $, error mix, automation rate,
    the exceptions queue, hours saved and the cost of the run."""
    try:
        from billproof.audit import run_batch

        return await asyncio.to_thread(
            run_batch, split, lane, source,
            run_id=run_id, minutes_per_claim=minutes_per_claim,
        )
    except Exception as exc:
        return _err(exc)


@router.get("/queue")
async def audit_queue(
    split: Literal["dev", "test", "demo"] = "test",
    lane: Literal["frontier", "drex", "hybrid"] = "hybrid",
    source: Literal["replay", "sim"] = "replay",
    run_id: str | None = None,
):
    """Only the cases a human must touch, sorted by $ at stake then confidence."""
    try:
        from billproof.audit import exceptions_queue

        return await asyncio.to_thread(exceptions_queue, split, lane, source, run_id=run_id)
    except Exception as exc:
        return _err(exc)
