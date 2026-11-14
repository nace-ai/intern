"""Trust dial, simulated from recorded answers.

GET /api/results/{bench}/dial                    curve + gpt_only + drex_only + measured
GET /api/results/{bench}/dial?t=0.9              one point + routed question ids per case
GET /api/results/{bench}/dial?t=0.95&router=family   hard only: the per-family map
GET /api/results/{bench}/dial?escalate=deepinfra  recorded DeepInfra dial file
GET /api/results/{bench}/dial?escalate=qwen       Qwen3.8-Max curve (Drex, then Qwen)
GET /api/results/{bench}/dial?escalate=qwen&model=Qwen/Qwen3.8-27B

Benches: hard, pa_criteria_gym, persius, synthetic_v1. The GPT curve is read from
data/metrics/dial_<bench>.json (written by `python -m billproof.dial.build`).
escalate=deepinfra reads data/metrics/dial_<bench>_deepinfra.json.
escalate=qwen reads data/metrics/dial_<bench>_<model>.json written by
`python scripts/qcombo.py`. The default model is Qwen/Qwen3.8-Max. 27B and
DeepSeek V4.1 Flash are the same route with `model`. GPT is unchanged.
"""

from __future__ import annotations

import json
from functools import lru_cache

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from billproof.dial.build import path_for
from billproof.dial.simulate import BENCHES, THRESHOLDS, point_at

router = APIRouter(prefix="/api/results", tags=["dial"])


@lru_cache(maxsize=8)
def _curve(bench: str, mtime: float) -> dict:
    return json.loads(path_for(bench).read_text())


@lru_cache(maxsize=64)
def _point(bench: str, threshold: float, mode: str) -> dict:
    return point_at(bench, threshold, mode)


@router.get("/{bench}/dial")
def results_dial(
    bench: str,
    t: float | None = Query(None, ge=THRESHOLDS[0], le=THRESHOLDS[-1]),
    router_: str = Query("global", alias="router", pattern="^(global|family)$"),
    escalate: str = Query("gpt", pattern="^(gpt|deepinfra|qwen)$"),
    model: str | None = Query(None),
) -> JSONResponse:
    if bench not in BENCHES:
        return JSONResponse(
            status_code=404,
            content={"error": f"no trust dial for {bench}", "benches": list(BENCHES)},
        )
    if router_ == "family" and bench != "hard":
        return JSONResponse(status_code=400, content={"error": "router=family exists only on hard"})
    if escalate == "qwen":
        from billproof.dial.qcombo import DEFAULT_T, MODELS, dial_path, point_at, spec_for

        chosen = model or MODELS[0]["model"]
        try:
            spec_for(chosen)
        except KeyError:
            return JSONResponse(
                status_code=400,
                content={"error": f"unknown model {chosen}", "models": [item["model"] for item in MODELS]},
            )
        if router_ == "family":
            return JSONResponse(status_code=400, content={"error": "router=family is the GPT dial on hard; escalate=qwen uses one global threshold"})
        if t is None:
            path = dial_path(bench, chosen)
            if not path.exists():
                return JSONResponse(
                    status_code=404,
                    content={"error": f"{path.name} is not written yet; run python scripts/qcombo.py"},
                )
            return JSONResponse(json.loads(path.read_text()))
        try:
            point = point_at(bench, round(t, 2), chosen)
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})
        except KeyError:
            return JSONResponse(status_code=404, content={"error": f"no Qwen dial for {bench}"})
        point["threshold_requested"] = t
        point["default_threshold"] = DEFAULT_T
        return JSONResponse(point)
    if escalate == "deepinfra":
        from billproof.dial.deepinfra import ACTIVE, path_for as deepinfra_path, point_at as deepinfra_point

        if bench not in ACTIVE:
            return JSONResponse(status_code=404, content={"error": f"no DeepInfra dial for {bench}", "benches": list(ACTIVE)})
        if t is None:
            path = deepinfra_path(bench)
            if not path.exists():
                return JSONResponse(
                    status_code=404,
                    content={"error": f"{path.name} is not written yet; run python -m billproof.dial.deepinfra"},
                )
            return JSONResponse(json.loads(path.read_text()))
        try:
            point = deepinfra_point(bench, round(t, 2), router_)
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})
        except KeyError:
            return JSONResponse(status_code=400, content={"error": "router=family exists only on hard"})
        point["threshold_requested"] = t
        return JSONResponse(point)
    if t is None:
        path = path_for(bench)
        if not path.exists():
            return JSONResponse(
                status_code=404,
                content={"error": f"{path.name} is not written yet; run python -m billproof.dial.build"},
            )
        return JSONResponse(_curve(bench, path.stat().st_mtime))
    threshold = round(t, 2)
    try:
        point = dict(_point(bench, threshold, router_))
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": f"recorded run file missing: {exc.filename}"})
    point["threshold_requested"] = t
    return JSONResponse(point)
