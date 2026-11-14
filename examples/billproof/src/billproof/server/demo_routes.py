"""Showcase demo routes (T4) — the staged end-to-end flow for the stage demo.

    GET /api/demo/cases                    -> showcase case list (manifest)
    GET /api/demo/run/{case_id}?mode=      -> SSE: start, stage*, complete | error
    GET /api/demo/numbers                  -> data/metrics/*.json aggregate

`mode=replay` (default) is fully offline: measured on-disk caches first, then
fixtures/seeded simulators, always labelled via `simulated` + `provenance`.
`mode=live` is opt-in: real calls only where credentials exist; a stage with
no credentials reports `blocked` — nothing silently falls back to simulated.

The showcase manifest `data/showcase/cases.json` is written by the data agent;
this reader is permissive: `{schema_version: 1, cases: [...]}` or a bare array,
each entry `{case_id, source_case_id, title, story, expected_outcome,
dollars_at_stake_cents, products, documents, ...}`. While the manifest is
absent, the demo split of the synthetic set is served with source="fallback".

app.py includes this router inside try/except; everything heavy is imported
lazily so a mid-build sibling never keeps the server down.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import queue
import threading
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from billproof.config import DATA_DIR, PKG_ROOT

router = APIRouter()

SHOWCASE_PATH = DATA_DIR / "showcase" / "cases.json"
METRICS_DIR = DATA_DIR / "metrics"

# Metric stems we expect other threads to land under data/metrics/. Anything
# present is aggregated as-is; the expected-but-absent ones are reported, never
# invented.
_EXPECTED_METRICS = (
    "bench_synthetic_test",
    "bench_imr_test",
    "ndi_extract",
    "ndi_ground",
    "search_ask",
    "browser_checks",
    "demo_showcase",
)

_HEARTBEAT_S = 10.0


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _j404(msg: str) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": msg})


# ---------------------------------------------------------------- manifest


def _norm_entry(raw: dict) -> dict:
    """One manifest entry: stable fields defaulted, every other key passed through."""
    entry = dict(raw)
    cid = entry.get("case_id") or entry.get("id") or entry.get("source_case_id")
    src = entry.get("source_case_id") or entry.get("source") or cid
    known = {
        "case_id": str(cid) if cid is not None else "",
        "source_case_id": str(src) if src is not None else "",
        "title": entry.get("title") or "",
        "story": entry.get("story") or "",
        "expected_outcome": entry.get("expected_outcome"),
        "dollars_at_stake_cents": entry.get("dollars_at_stake_cents"),
        "products": list(entry.get("products") or []),
        "documents": list(entry.get("documents") or []),
        "evidence": entry.get("evidence") if isinstance(entry.get("evidence"), dict) else {},
    }
    # Forward-compat: manifest fields we don't model (expected_color,
    # verified_owed_cents, ...) ride along at top level.
    extra = {k: v for k, v in entry.items() if k not in known and k not in {"id", "source"}}
    return {**extra, **known}


def _manifest_from_file() -> tuple[list[dict], str | None]:
    """Read data/showcase/cases.json permissively; (entries, error)."""
    try:
        payload = json.loads(SHOWCASE_PATH.read_text())
    except FileNotFoundError:
        return [], None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    if isinstance(payload, dict):
        rows = payload.get("cases")
        if not isinstance(rows, list):
            return [], "manifest has no 'cases' array"
    elif isinstance(payload, list):
        rows = payload
    else:
        return [], "manifest is neither an object nor an array"
    return [_norm_entry(r) for r in rows if isinstance(r, dict)], None


def _manifest_from_module() -> list[dict] | None:
    """Prefer the data agent's billproof.showcase helpers when they exist."""
    try:
        mod = importlib.import_module("billproof.showcase")
    except Exception:
        return None
    for name in (
        "showcase_entries", "load_showcase_manifest", "list_cases",
        "load_manifest", "manifest", "showcase_cases", "cases",
    ):
        fn = getattr(mod, name, None)
        if not callable(fn):
            continue
        try:
            rows = fn()
        except Exception:
            continue
        if isinstance(rows, dict):
            rows = rows.get("cases")
        if isinstance(rows, list):
            return [_norm_entry(r) for r in rows if isinstance(r, dict)]
    return None


def _fallback_cases() -> list[dict]:
    """Demo-split synthetic cases as provisional showcase entries."""
    try:
        data = importlib.import_module("billproof.data")
        out = []
        for c in data.list_cases("demo"):
            out.append(
                _norm_entry(
                    {
                        "case_id": c.case_id,
                        "source_case_id": c.case_id,
                        "title": c.title,
                        "story": c.title,
                        "dollars_at_stake_cents": c.bill.patient_balance_cents,
                        "documents": [d.kind for d in c.docs],
                        "products": ["ndi", "drex"],
                        "provisional": True,
                    }
                )
            )
        return out
    except Exception:
        return []


def load_showcase_cases() -> dict:
    """Case list + where it came from. Never raises."""
    entries = _manifest_from_module()
    if entries is not None:
        return {"cases": entries, "source": "billproof.showcase", "manifest_present": SHOWCASE_PATH.is_file()}
    entries, error = _manifest_from_file()
    if error:
        return {
            "cases": _fallback_cases(),
            "source": "fallback",
            "manifest_present": True,
            "manifest_error": error,
        }
    if entries:
        return {"cases": entries, "source": "manifest", "manifest_present": True}
    return {"cases": _fallback_cases(), "source": "fallback", "manifest_present": False}


def _find_entry(case_id: str) -> dict | None:
    try:
        mod = importlib.import_module("billproof.showcase")
        entry = mod.showcase_case_entry(case_id)
        if isinstance(entry, dict):
            return _norm_entry(entry)
    except Exception:
        pass
    for entry in load_showcase_cases()["cases"]:
        if case_id in (entry["case_id"], entry["source_case_id"]):
            return entry
    return None


def _resolve_case(case_id: str):
    """(entry, Case) — entry may be None when the case id is loadable directly.

    ``showcase.load_showcase_case`` covers showcase-only cases under
    ``data/showcase/cases/``; ``data.load_case`` is the permissive fallback.
    """
    entry = _find_entry(case_id)
    try:
        mod = importlib.import_module("billproof.showcase")
        return entry, mod.load_showcase_case(case_id)
    except Exception:
        pass
    data = importlib.import_module("billproof.data")
    for candidate in (entry or {}).get("source_case_id"), case_id:
        if not candidate:
            continue
        try:
            return entry, data.load_case(candidate)
        except Exception:
            continue
    return entry, None


# ---------------------------------------------------------------- routes


@router.get("/api/demo/cases")
async def api_demo_cases():
    payload = await asyncio.to_thread(load_showcase_cases)
    return {
        "schema_version": 1,
        "n": len(payload["cases"]),
        "manifest_path": str(SHOWCASE_PATH.relative_to(PKG_ROOT)),
        **payload,
    }


@router.get("/api/demo/run/{case_id}")
async def api_demo_run(
    request: Request, case_id: str,
    mode: Literal["replay", "live"] = "replay",
):
    try:
        entry, case = await asyncio.to_thread(_resolve_case, case_id)
    except Exception as exc:
        return JSONResponse(status_code=503, content={"error": f"{type(exc).__name__}: {exc}"})
    if case is None:
        return _j404(f"no showcase case or loadable case for {case_id!r}")
    gen = _demo_stream(request, case, entry or {}, mode)
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _demo_stream(request: Request, case, entry: dict, mode: str) -> AsyncIterator[str]:
    pipeline = importlib.import_module("billproof.pipeline")
    yield _sse(
        "start",
        {
            "case_id": case.case_id,
            "showcase_case_id": entry.get("case_id") or case.case_id,
            "title": entry.get("title") or case.title,
            "story": entry.get("story") or "",
            "expected_outcome": entry.get("expected_outcome"),
            "dollars_at_stake_cents": entry.get("dollars_at_stake_cents"),
            "products": entry.get("products") or [],
            "mode": mode,
            "stages": list(pipeline.DEMO_STAGES),
        },
    )

    q: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            for ev in pipeline.iter_demo_events(case, mode=mode, manifest=entry):
                q.put(("stage", ev))
            q.put(("__end__", None))
        except Exception as exc:
            q.put(("__error__", f"{type(exc).__name__}: {exc}"))

    threading.Thread(target=work, daemon=True, name=f"demo-{case.case_id}").start()

    last_events: dict[str, dict] = {}
    ended = False
    idle = 0.0
    while not ended:
        if await request.is_disconnected():
            return
        try:
            kind, payload = q.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.05)
            idle += 0.05
            if idle >= _HEARTBEAT_S:
                yield ": hb\n\n"
                idle = 0.0
            continue
        idle = 0.0
        if kind == "__end__":
            ended = True
        elif kind == "__error__":
            yield _sse("error", {"error": payload, "case_id": case.case_id, "mode": mode})
            return
        else:
            last_events[payload["stage"]] = payload
            yield _sse("stage", payload)

    yield _sse("complete", pipeline._complete_payload(case, mode, list(last_events.values())))


# ---------------------------------------------------------------- numbers


def _aggregate_metrics() -> dict:
    """Fold every data/metrics/*.json into one honest object."""
    metrics: dict[str, Any] = {}
    invalid: list[dict] = []
    if METRICS_DIR.is_dir():
        for path in sorted(METRICS_DIR.glob("*.json")):
            try:
                payload = json.loads(path.read_text())
            except Exception as exc:
                invalid.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if not isinstance(payload, dict):
                invalid.append({"file": path.name, "error": "top level is not an object"})
                continue
            entry = dict(payload)
            entry["file"] = path.name
            for required in ("value", "n", "source_run", "simulated", "computed_at"):
                entry.setdefault(required, None)
            metrics[path.stem] = entry
    missing = [stem for stem in _EXPECTED_METRICS if stem not in metrics]
    cases = load_showcase_cases()
    dollars = [
        c["dollars_at_stake_cents"]
        for c in cases["cases"]
        if isinstance(c.get("dollars_at_stake_cents"), (int, float))
    ]
    return {
        "schema_version": 1,
        "metrics_dir": str(METRICS_DIR.relative_to(PKG_ROOT)),
        "metrics_dir_present": METRICS_DIR.is_dir(),
        "n_metrics": len(metrics),
        "metrics": metrics,
        "missing": missing,
        "invalid": invalid,
        "showcase": {
            "source": cases["source"],
            "n_cases": len(cases["cases"]),
            "dollars_at_stake_cents": int(sum(dollars)) if dollars else None,
        },
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "honesty": (
            "Only files that exist under data/metrics are reported; "
            "'missing' names metrics expected but not yet computed."
        ),
    }


@router.get("/api/demo/numbers")
async def api_demo_numbers():
    return await asyncio.to_thread(_aggregate_metrics)
