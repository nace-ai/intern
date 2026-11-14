"""Read-only results hub.

GET /api/results/summary
GET /api/results/{bench}
GET /api/results/{bench}/cases?lane=&only_wrong=&offset=&limit=
GET /api/results/{bench}/case/{case_id}
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from billproof.results.cases import get_case, list_cases
from billproof.results.catalog import SUMMARY_PATH, read_json

router = APIRouter(prefix="/api/results", tags=["results"])


def _summary() -> dict | None:
    if not SUMMARY_PATH.exists():
        return None
    return read_json(SUMMARY_PATH)


def _bench(bench: str) -> dict | None:
    document = _summary()
    if document is None:
        return None
    for item in document.get("benchmarks") or []:
        if item.get("id") == bench:
            return item
    return None


@router.get("/summary")
def results_summary() -> JSONResponse:
    document = _summary()
    if document is None:
        return JSONResponse(
            status_code=404,
            content={"error": "data/metrics/all_benchmarks.json is not written yet"},
        )
    return JSONResponse(document)


@router.get("/{bench}/cases")
def results_cases(
    bench: str,
    lane: str | None = None,
    only_wrong: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    if _bench(bench) is None and _summary() is not None:
        return JSONResponse(status_code=404, content={"error": f"unknown benchmark {bench}"})
    if _summary() is None:
        return JSONResponse(status_code=404, content={"error": "summary file missing"})
    try:
        payload = list_cases(bench, lane=lane or None, only_wrong=only_wrong, offset=offset, limit=limit)
    except KeyError:
        return JSONResponse(status_code=404, content={"error": f"unknown benchmark {bench}"})
    return JSONResponse(payload)


@router.get("/{bench}/case/{case_id}")
def results_case(bench: str, case_id: str) -> JSONResponse:
    if _summary() is None:
        return JSONResponse(status_code=404, content={"error": "summary file missing"})
    if _bench(bench) is None:
        return JSONResponse(status_code=404, content={"error": f"unknown benchmark {bench}"})
    try:
        payload = get_case(bench, case_id)
    except KeyError:
        return JSONResponse(status_code=404, content={"error": f"unknown benchmark {bench}"})
    if payload is None:
        return JSONResponse(status_code=404, content={"error": f"case {case_id} not in {bench}"})
    return JSONResponse(payload)


@router.get("/{bench}")
def results_bench(bench: str) -> JSONResponse:
    if _summary() is None:
        return JSONResponse(status_code=404, content={"error": "summary file missing"})
    item = _bench(bench)
    if item is None:
        return JSONResponse(status_code=404, content={"error": f"unknown benchmark {bench}"})
    return JSONResponse(item)
