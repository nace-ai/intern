"""Drop-your-bill route (W4b) — `POST /api/ingest`.

Accepts up to 4 PDFs either as repeated `files` form fields or as named
`bill` / `eob` / `denial` / `plan` fields. Returns
`{"case_id", "mode", "fields_found", "warnings"}`; the case then works with
`/api/cases/{id}`, page PNGs, `/api/analyze` and `/api/packet` once
`data.load_case` falls back to `ingest.load_uploaded` for UP-* ids.

app.py includes this router inside try/except, so the server still starts
if a sibling is mid-build. The ingest itself is blocking file/parse work and
runs in a thread.
"""

from __future__ import annotations

import asyncio
import importlib

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from billproof.config import DATA_DIR
from billproof.ingest import MAX_FILES, ingest

router = APIRouter()

_MAX_BYTES = 15 * 1024 * 1024


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


@router.get("/api/ingest/demo-pdfs")
async def api_ingest_demo_pdfs():
    """Merged packet PDFs for the drop-a-packet demo (`data/ndi_demo/`)."""
    demo = DATA_DIR / "ndi_demo"
    if not demo.is_dir():
        return []
    return [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(demo.glob("*.pdf"))]


@router.get("/api/doccheck/{case_id}")
async def api_doccheck(case_id: str):
    """doccheck.check_case over any stored case (SYN-* or UP-*)."""
    try:
        doccheck = importlib.import_module("billproof.doccheck")  # may not exist yet
        if not callable(getattr(doccheck, "check_case", None)):
            raise AttributeError("billproof.doccheck.check_case")
    except Exception as exc:
        missing = getattr(exc, "name", None) or "billproof.doccheck"
        return JSONResponse(status_code=503, content={"error": str(exc), "missing": missing})
    from billproof.data import load_case

    try:
        case = await asyncio.to_thread(load_case, case_id)
    except Exception as exc:
        return _err(404, str(exc))
    try:
        checks = await asyncio.to_thread(doccheck.check_case, case)
    except Exception as exc:
        return _err(500, f"{type(exc).__name__}: {exc}")
    return {"case_id": case_id, "checks": checks}


@router.post("/api/ingest")
async def api_ingest(
    files: list[UploadFile] | None = File(default=None),
    bill: UploadFile | None = File(default=None),
    eob: UploadFile | None = File(default=None),
    denial: UploadFile | None = File(default=None),
    plan: UploadFile | None = File(default=None),
):
    items: dict[str, bytes] = {}
    for kind, up in (("bill", bill), ("eob", eob), ("denial", denial), ("plan", plan)):
        if up is not None:
            items[f"{kind}:{up.filename or kind + '.pdf'}"] = await up.read()
    for i, up in enumerate(files or []):
        name = up.filename or f"document{i + 1}.pdf"
        while name in items:
            name = f"~{name}"
        items[name] = await up.read()

    if not items:
        return _err(400, "attach at least one PDF (files[] or bill/eob/denial/plan fields)")
    if len(items) > MAX_FILES:
        return _err(400, f"at most {MAX_FILES} PDFs per drop")
    for name, blob in items.items():
        if len(blob) > _MAX_BYTES:
            return _err(413, f"{name}: larger than {_MAX_BYTES // (1024 * 1024)} MB")

    try:
        return await asyncio.to_thread(ingest, items)
    except ValueError as exc:
        return _err(400, str(exc))
    except Exception as exc:
        return _err(500, f"{type(exc).__name__}: {exc}")
