"""PA what-if routes (UI-2 evidence studio).

  POST /api/pa/{case}/whatif        edits -> re-decision from recorded Drex answers (no model call)
  POST /api/pa/{case}/whatif/live   {criterion, text} -> one live Drex answer for an edited record
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/pa", tags=["pa-whatif"])


@router.post("/{slug}/whatif")
def whatif(slug: str, edits: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
    from billproof.pa_whatif.engine import whatif as run

    try:
        return JSONResponse(run(slug.upper(), edits or {}))
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(404, f"no recorded case {slug!r}: {exc}")


@router.post("/{slug}/whatif/live")
def whatif_live(slug: str, body: dict[str, Any] = Body(...)) -> JSONResponse:
    from billproof.pa_whatif.engine import ask_drex_live

    try:
        return JSONResponse(ask_drex_live(slug.upper(), str(body.get("criterion") or ""), str(body.get("text") or "")))
    except KeyError as exc:
        raise HTTPException(404, f"unknown case or criterion: {exc}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # live call must never break the stage
        return JSONResponse({"live": True, "error": f"{type(exc).__name__}: {exc}"}, status_code=502)
